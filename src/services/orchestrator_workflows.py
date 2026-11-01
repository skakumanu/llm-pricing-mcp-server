"""Orchestrator workflow definitions, routing and untrusted-input handling (v1.83.0).

Pure module: no I/O, no store, no settings. Everything the run engine
(orchestrator_service.py) needs to decide *which* workflow runs and *what* each
step is allowed to do lives here as code, so it can be validated at import time
and pinned by tests:

- ``WORKFLOWS``: the finite, ordered step graphs. Agents cannot add, reorder or skip
  steps at run time (FR3); ``validate_registry()`` rejects any definition that names a
  forbidden action (merge, refund, send_email, ...) or exceeds ``MAX_STEPS`` (AC8).
- ``route()``: derives the workflow from the trigger class, the event type and
  owner-controlled signals only (owner-applied labels, owner identity, schedules).
  Titles, bodies and messages never reach it (FR18, AC19).
- ``summarize_untrusted()``: redacts and truncates untrusted text and flags
  instruction-like content; the flag is advisory and changes nothing about routing.
- Comment templates keyed by (run id, issue number, state, PR URL); untrusted text is
  never interpolated.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.services.pricing_alerts import redact_alert_tokens

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# v1.84.0: ``payment_event`` = a Stripe webhook event the server itself classified (refund,
# dispute, failed renewal). Event types are set by server code, never by customer text.
TRIGGER_CLASSES = ("customer_message", "github_event", "schedule", "owner_request", "payment_event")

STEP_KINDS = ("deterministic", "external_action", "checkpoint", "llm")

# Checkpoint kinds that gate in every mode (FR6). ``sdlc_run`` and ``step_review`` are
# checkpoints too, but of a different nature: sdlc_run is "owner, run the pipeline";
# step_review is the supervised-mode gate on a non-mandatory external action.
# ``owner_handoff`` (v1.84.0) is a customer escalation waiting for the owner's reply.
MANDATORY_CHECKPOINT_KINDS = frozenset({"merge", "money", "outbound", "publish", "audit_flag", "missing_specialist",
                                        "owner_handoff"})
CHECKPOINT_KINDS = MANDATORY_CHECKPOINT_KINDS | {"sdlc_run", "step_review"}

# Event types (set by server code) that open a support_escalation run (v1.84.0).
SUPPORT_ESCALATION_EVENT = "support.escalation"
# v1.87.0: opened by the account-deletion handler (actor_kind 'account_service') when a paying
# account is deleted inside the refund window; routed like a support escalation, category refund.
ACCOUNT_DELETION_EVENT = "account.deletion_refund_review"
# v1.88.0: the weekly compliance-audit.yml posts this schedule event; routed to compliance_audit.
COMPLIANCE_AUDIT_EVENT = "compliance-audit.requested"
PAYMENT_ESCALATION_EVENTS = ("charge.refunded", "charge.dispute.created", "invoice.payment_failed")
ESCALATION_CATEGORIES = ("billing_dispute", "refund", "double_charge", "payment_failure", "key_recovery",
                         "bug", "feature", "other")

# Owner GitHub actions that count as the decision on an approval (recorded, never implied).
OWNER_SIGNALS = ("github_label", "github_pr_marker")

# Modes from least to most supervision. A mode can only ever add supervision (FR19).
MODES = ("standard", "supervised", "dry_run")
_MODE_RANK = {m: i for i, m in enumerate(MODES)}

# Definition-time refusals (AC8). A step naming any of these never loads.
FORBIDDEN_ACTIONS = frozenset({
    "merge_pr", "refund", "charge", "send_email", "publish", "push_branch", "grant_permission",
})
# The only external action an executor may be handed in v1.
ALLOWED_ACTION_KINDS = frozenset({"github_issue_comment_upsert"})

MAX_STEPS = 20
DEFAULT_STEP_TIMEOUT_S = 30
DEFAULT_MAX_ATTEMPTS = 3
RUN_DEADLINE_S = 24 * 3600  # of executing time; waiting on the owner does not count

RUN_ID_RE = re.compile(r"^orc_[a-z0-9]{12}$")
DEDUP_KEY_RE = re.compile(r"^(gh|schedule|owner|msg|stripe):[A-Za-z0-9][A-Za-z0-9:_.#/-]{0,199}$")
PR_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/(\d+)$")

TITLE_MAX = 200
BODY_MAX = 500

# The comment author the executor trusts for marker lookup. The workflow YAML carries the same
# literal; tests/test_orchestrator_intake_workflow.py pins the two to each other.
GITHUB_BOT_LOGIN = "github-actions[bot]"


class WorkflowDefinitionError(ValueError):
    """A workflow definition breaks a rule (forbidden action, unknown kind, too many steps)."""


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    name: str
    kind: str
    agent: str
    # external_action: the action kind the executor performs (ALLOWED_ACTION_KINDS) and which
    # executor runs it; checkpoint: the approval kind created when the step is reached.
    action: Optional[str] = None
    executor: Optional[str] = None
    checkpoint_kind: Optional[str] = None
    # Steps that must be ``done`` before this one may start (FR4). A failed prerequisite
    # parks the run at an owner checkpoint; a missing one blocks with a reason naming it.
    requires: Tuple[str, ...] = ()
    # Owner GitHub action that, when recorded on the run, decides this step's approval.
    approved_by_signal: Optional[str] = None
    timeout_s: int = DEFAULT_STEP_TIMEOUT_S
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    description: str = ""


@dataclass(frozen=True)
class Workflow:
    workflow_id: str
    trigger_classes: Tuple[str, ...]
    steps: Tuple[Step, ...]
    specialist_available: bool
    per_run_budget_usd: float
    # Trigger class recorded when the run is started through POST /orchestrator/runs.
    owner_request_trigger_class: str = "owner_request"
    description: str = ""
    # Default monthly USD cap for the workflow (budget.workflow_monthly_usd.<id>) when the owner has
    # not set one; None = no workflow-level cap (the global ceiling still applies).
    monthly_budget_usd: Optional[float] = None

    def step(self, name: str) -> Step:
        for s in self.steps:
            if s.name == name:
                return s
        raise KeyError(name)


WORKFLOWS: Dict[str, Workflow] = {
    "github_accepted_bug": Workflow(
        workflow_id="github_accepted_bug",
        trigger_classes=("github_event",),
        specialist_available=True,  # the SDLC pipeline counts as available from day one (FR20)
        per_run_budget_usd=15.0,
        description="Owner-labelled 'accepted' bug issue -> SDLC pipeline (owner checkpoint) -> PR -> "
                    "one status comment.",
        steps=(
            Step("intake", "deterministic", "orchestrator",
                 description="Record the event, redacted summary and labels."),
            Step("annotate", "deterministic", "orchestrator", requires=("intake",),
                 description="Record owner signals and the instruction-like flag; routing is unaffected."),
            Step("sdlc_pipeline", "checkpoint", "sdlc_pipeline", checkpoint_kind="sdlc_run",
                 requires=("annotate",),
                 description="Owner runs /release-feature with the exact command shown; the PR marker completes it."),
            Step("status_comment", "external_action", "orchestrator", action="github_issue_comment_upsert",
                 executor="github", checkpoint_kind="outbound", requires=("sdlc_pipeline",),
                 approved_by_signal="github_pr_marker",
                 description="One templated status comment on the issue naming the PR (never merged here)."),
        ),
    ),
    "release_notes_draft": Workflow(
        workflow_id="release_notes_draft",
        trigger_classes=("github_event", "owner_request"),
        specialist_available=False,  # document agent not built: forced dry_run, owner checkpoint
        per_run_budget_usd=2.0,
        description="Merged release -> release-notes draft (owner checkpoint: specialist missing) -> "
                    "publish checkpoint.",
        steps=(
            Step("release_record", "deterministic", "orchestrator",
                 description="Record the release; blocked unless the SDLC pull request is known."),
            Step("draft_release_notes", "checkpoint", "document_agent", checkpoint_kind="missing_specialist",
                 requires=("release_record",)),
            Step("publish", "checkpoint", "orchestrator", checkpoint_kind="publish",
                 requires=("draft_release_notes",)),
        ),
    ),
    "support_triage": Workflow(
        workflow_id="support_triage",
        trigger_classes=("customer_message", "owner_request"),
        specialist_available=False,  # the live SupportAgent is not wired into this workflow yet (phase 2)
        per_run_budget_usd=2.0,
        monthly_budget_usd=40.0,  # v1.84.0: live support chat spend lands here (budget_gate refuses past it)
        owner_request_trigger_class="customer_message",
        description="Owner-pasted customer message -> intake -> advisory tags -> owner checkpoint "
                    "(specialist missing). Also the ledger workflow_id for live support chat spend "
                    "(agent/support_agent.py records every LLM call here).",
        steps=(
            Step("intake", "deterministic", "orchestrator"),
            Step("annotate", "deterministic", "orchestrator", requires=("intake",),
                 description="Advisory 'billing' keyword tag, shown only in the queue; never unlocks a step."),
            Step("support_agent", "checkpoint", "support_agent", checkpoint_kind="missing_specialist",
                 requires=("annotate",)),
        ),
    ),
    "support_escalation": Workflow(
        workflow_id="support_escalation",
        trigger_classes=("customer_message", "payment_event", "owner_request"),
        specialist_available=True,  # the support agent (v1.84.0) hands off deterministically
        per_run_budget_usd=1.0,
        owner_request_trigger_class="customer_message",
        description="Customer escalation (support chat) or Stripe payment event -> intake -> owner_handoff "
                    "checkpoint; the owner replies from the queue, nothing is refunded or changed here.",
        steps=(
            Step("intake", "deterministic", "support_agent",
                 description="Record category, priority, tier, identified flag, org prefix and conversation ref."),
            Step("owner_handoff", "checkpoint", "orchestrator", checkpoint_kind="owner_handoff",
                 requires=("intake",),
                 description="Waits for the owner; done_manually with a reply note the customer can read."),
        ),
    ),
    "weekly_price_audit": Workflow(
        workflow_id="weekly_price_audit",
        trigger_classes=("schedule",),
        specialist_available=True,
        per_run_budget_usd=2.0,
        description="Mirror of the price-audit.yml jobs; any failed job parks the run for the owner.",
        steps=(
            Step("record_jobs", "deterministic", "price_audit"),
            Step("summarize", "deterministic", "orchestrator", requires=("record_jobs",)),
        ),
    ),
    # v1.88.0: report-only compliance audit. run_checks persists the report first and, when any check
    # fails, returns gate_result='fail'; review_report requires run_checks, so the engine parks the run
    # at an audit_flag owner checkpoint. Pass / warn-only reports finish with no approval.
    "compliance_audit": Workflow(
        workflow_id="compliance_audit",
        trigger_classes=("schedule", "owner_request"),
        specialist_available=True,
        per_run_budget_usd=0.25,
        monthly_budget_usd=5.0,
        description="Weekly (and on-demand) mechanical audit of the whole system; report-only. A failing "
                    "check opens one owner checkpoint; the optional LLM summary is one bounded haiku call.",
        steps=(
            Step("run_checks", "deterministic", "audit_agent", timeout_s=180, max_attempts=2,
                 description="Run the check catalogue and persist the report (read-only against everything audited)."),
            Step("executive_summary", "deterministic", "audit_agent", timeout_s=60, max_attempts=1,
                 description="Template summary; one optional bounded LLM call when AUDIT_LLM_SUMMARY is on."),
            Step("review_report", "deterministic", "orchestrator", requires=("run_checks",),
                 description="Completes at once on a pass/warn report; a failing report waits for the owner."),
        ),
    ),
}


def validate_workflow(wf: Workflow) -> None:
    if not wf.steps:
        raise WorkflowDefinitionError(f"{wf.workflow_id}: a workflow needs at least one step")
    if len(wf.steps) > MAX_STEPS:
        raise WorkflowDefinitionError(f"{wf.workflow_id}: {len(wf.steps)} steps exceeds MAX_STEPS={MAX_STEPS}")
    for tc in wf.trigger_classes:
        if tc not in TRIGGER_CLASSES:
            raise WorkflowDefinitionError(f"{wf.workflow_id}: unknown trigger class {tc!r}")
    if wf.owner_request_trigger_class not in TRIGGER_CLASSES:
        raise WorkflowDefinitionError(f"{wf.workflow_id}: unknown owner_request_trigger_class")
    seen = set()
    for s in wf.steps:
        if s.name in seen:
            raise WorkflowDefinitionError(f"{wf.workflow_id}: duplicate step name {s.name!r}")
        seen.add(s.name)
        if s.kind not in STEP_KINDS:
            raise WorkflowDefinitionError(f"{wf.workflow_id}.{s.name}: unknown step kind {s.kind!r}")
        if s.action is not None:
            if s.action in FORBIDDEN_ACTIONS:
                raise WorkflowDefinitionError(f"{wf.workflow_id}.{s.name}: forbidden action {s.action!r}")
            if s.action not in ALLOWED_ACTION_KINDS:
                raise WorkflowDefinitionError(f"{wf.workflow_id}.{s.name}: action {s.action!r} is not allowed in v1")
        if s.kind == "external_action" and (s.action is None or not s.executor):
            raise WorkflowDefinitionError(
                f"{wf.workflow_id}.{s.name}: external_action needs an action and an executor"
            )
        if s.kind != "external_action" and s.action is not None:
            raise WorkflowDefinitionError(f"{wf.workflow_id}.{s.name}: only external_action steps carry an action")
        if s.kind == "checkpoint" and s.checkpoint_kind not in CHECKPOINT_KINDS:
            raise WorkflowDefinitionError(f"{wf.workflow_id}.{s.name}: unknown checkpoint kind {s.checkpoint_kind!r}")
        if s.checkpoint_kind is not None and s.checkpoint_kind not in CHECKPOINT_KINDS:
            raise WorkflowDefinitionError(f"{wf.workflow_id}.{s.name}: unknown checkpoint kind {s.checkpoint_kind!r}")
        if s.approved_by_signal is not None and s.approved_by_signal not in OWNER_SIGNALS:
            raise WorkflowDefinitionError(f"{wf.workflow_id}.{s.name}: unknown owner signal {s.approved_by_signal!r}")
        for req in s.requires:
            if req not in seen or req == s.name:
                raise WorkflowDefinitionError(
                    f"{wf.workflow_id}.{s.name}: requires {req!r}, which is not an earlier step"
                )
        if s.max_attempts < 1 or s.timeout_s < 1:
            raise WorkflowDefinitionError(f"{wf.workflow_id}.{s.name}: max_attempts and timeout_s must be >= 1")


def validate_registry(registry: Dict[str, Workflow]) -> None:
    for wid, wf in registry.items():
        if wid != wf.workflow_id:
            raise WorkflowDefinitionError(f"registry key {wid!r} != workflow_id {wf.workflow_id!r}")
        validate_workflow(wf)


validate_registry(WORKFLOWS)


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def stricter_mode(a: str, b: str) -> str:
    return a if _MODE_RANK[a] >= _MODE_RANK[b] else b


def resolve_mode(wf: Workflow, default_mode: str, requested: Optional[str] = None) -> str:
    """Effective mode = the strictest of (workflow default, requested, forced floor).

    Missing-specialist workflows are pinned to dry_run; nothing can lower that.
    """
    mode = default_mode if default_mode in _MODE_RANK else "supervised"
    if requested in _MODE_RANK:
        mode = stricter_mode(mode, requested)
    if not wf.specialist_available:
        mode = "dry_run"
    return mode


# ---------------------------------------------------------------------------
# Routing — trigger class + owner-controlled signals only
# ---------------------------------------------------------------------------

ACCEPT_LABEL = "accepted"
BUG_LABEL = "bug"


def route(trigger_class: str, event_type: str, owner_signals: Dict[str, Any],
          registry: Optional[Dict[str, Workflow]] = None) -> Tuple[Optional[str], str]:
    """Return (workflow_id, outcome). outcome is 'routed' or a 'refused:<reason>' code.

    ``owner_signals`` carries only owner-controlled facts: ``actor_is_owner`` (asserted by
    the Actions workflow from sender.login == repository_owner), ``label`` (the label just
    applied), ``labels`` (all label names), ``workflow_id`` (owner requests). No free text.
    """
    registry = WORKFLOWS if registry is None else registry
    if trigger_class not in TRIGGER_CLASSES:
        return None, "refused:no_route"
    is_owner = bool(owner_signals.get("actor_is_owner"))

    if trigger_class == "github_event":
        if event_type == "issues.labeled":
            if str(owner_signals.get("label") or "").lower() != ACCEPT_LABEL:
                return None, "refused:no_route"
            if not is_owner:
                return None, "refused:not_owner"
            labels = {str(x).lower() for x in (owner_signals.get("labels") or [])}
            if BUG_LABEL not in labels:
                return None, "refused:no_route"
            return _if_present("github_accepted_bug", registry)
        if event_type == "push.master":
            if not is_owner:
                return None, "refused:not_owner"
            return _if_present("release_notes_draft", registry)
        if event_type == "pull_request.opened":
            # Continuation of an existing run (handled by the engine via the PR marker);
            # it never starts a workflow by itself.
            if not is_owner:
                return None, "refused:not_owner"
            return None, "continuation"
        return None, "refused:no_route"

    if trigger_class == "schedule":
        if event_type == "price-audit.completed":
            return _if_present("weekly_price_audit", registry)
        if event_type == COMPLIANCE_AUDIT_EVENT:
            return _if_present("compliance_audit", registry)
        return None, "refused:no_route"

    if trigger_class == "owner_request":
        if not is_owner:
            return None, "refused:not_owner"
        wid = owner_signals.get("workflow_id")
        wf = registry.get(wid) if isinstance(wid, str) else None
        if wf is None or "owner_request" not in wf.trigger_classes:
            return None, "refused:no_route"
        return wf.workflow_id, "routed"

    if trigger_class == "payment_event":
        # Stripe-originated escalations (v1.84.0): the server classifies the event type itself.
        if event_type in PAYMENT_ESCALATION_EVENTS:
            return _if_present("support_escalation", registry)
        return None, "refused:no_route"

    # customer_message (v1.84.0): the support agent's escalation hand-off is the one event type
    # that starts a run; everything else on this class stays owner-request-only (owner decision 1).
    if event_type in (SUPPORT_ESCALATION_EVENT, ACCOUNT_DELETION_EVENT):
        return _if_present("support_escalation", registry)
    return None, "refused:no_route"


def _if_present(wid: str, registry: Dict[str, Workflow]) -> Tuple[Optional[str], str]:
    return (wid, "routed") if wid in registry else (None, "refused:no_route")


# ---------------------------------------------------------------------------
# Untrusted text: redact, truncate, flag
# ---------------------------------------------------------------------------

REDACTED = "[redacted]"

_SECRET_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bpk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bwhsec_[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:cus|sub|evt|pi|ch|in|price|prod)_[A-Za-z0-9]{8,}\b"),  # Stripe ids
    re.compile(r"\blpa_[A-Za-z0-9_-]{8,}"),  # alert tokens (also handled by redact_alert_tokens)
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")  # email-shaped

# Conservative: catches the obvious "steer the agent" phrases and the exact strings that
# would mean something to this system if they were obeyed. Advisory only.
INSTRUCTION_LIKE_RE = re.compile(
    r"(ignore (all |the )?(previous|prior|above) instructions|system prompt|you are now an? |"
    r"\bdisregard\b|/release-feature|\bapprove (the|and)\b|\bmerge (the|this|it)\b|\blabel accepted\b|"
    r"<!--\s*orchestrator:|send the newsletter|\bapprove and merge\b)",
    re.IGNORECASE,
)


def redact_untrusted_text(text: Any, keep_emails: bool = False) -> Any:
    """Replace secret-, Stripe-id- and email-shaped tokens with '[redacted]'. Non-str unchanged.

    ``keep_emails=True`` (v1.84.0) is for the operator-only support conversation store, where
    the customer's own address is kept so a key-recovery request can be verified; every run
    artifact, action record and customer-visible text still goes through the default.
    """
    if not isinstance(text, str):
        return text
    out = redact_alert_tokens(text)
    for rx in _SECRET_RES:
        out = rx.sub(REDACTED, out)
    if not keep_emails:
        out = _EMAIL_RE.sub(REDACTED, out)
    return out


def is_instruction_like(text: Any) -> bool:
    return isinstance(text, str) and bool(INSTRUCTION_LIKE_RE.search(text))


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def summarize_untrusted(title: Any = None, body: Any = None) -> Dict[str, Any]:
    """Redacted, truncated carrier for untrusted text plus the advisory instruction_like flag."""
    t = title if isinstance(title, str) else ""
    b = body if isinstance(body, str) else ""
    return {
        "title": _truncate(redact_untrusted_text(t), TITLE_MAX),
        "body": _truncate(redact_untrusted_text(b), BODY_MAX),
        "instruction_like": is_instruction_like(t) or is_instruction_like(b),
    }


ADVISORY_TAG_RES = {"billing": re.compile(r"\b(invoice|refund|charge|billing|subscription|payment|stripe)\b", re.I)}


def advisory_tags(text: Any) -> List[str]:
    """Content classification shown only in the queue; it never unlocks a gated step (FR18)."""
    if not isinstance(text, str):
        return []
    return [tag for tag, rx in ADVISORY_TAG_RES.items() if rx.search(text)]


# ---------------------------------------------------------------------------
# Artifacts and templates — never contain untrusted text
# ---------------------------------------------------------------------------

def canonical_hash(obj: Any) -> str:
    """sha256 of canonical JSON (sorted keys, compact separators)."""
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def comment_marker(run_id: str, step_name: str) -> str:
    return f"<!-- orchestrator:{run_id}:{step_name} -->"


def validate_pr_url(url: Any) -> Optional[str]:
    """The PR URL as given when it is https://github.com/<owner>/<repo>/pull/<int>, else None."""
    if isinstance(url, str) and PR_URL_RE.match(url):
        return url
    return None


def release_feature_command(issue_number: int, run_id: str) -> str:
    return f"/release-feature Fix issue #{int(issue_number)} (orchestrator run {run_id})"


def render_status_comment(run_id: str, step_name: str, issue_number: int, state: str,
                          pr_url: Optional[str] = None) -> Dict[str, Any]:
    """Templated issue comment. Keys: run id, issue number, state, validated PR URL only."""
    marker = comment_marker(run_id, step_name)
    safe_pr = validate_pr_url(pr_url)
    if state == "pr_opened" and safe_pr:
        text = (
            f"A fix for #{int(issue_number)} is in review: {safe_pr}\n\n"
            "The pull request targets `develop` and is merged only by the repository owner. "
            f"Reference: `{run_id}`."
        )
    elif state == "accepted":
        text = (
            f"Issue #{int(issue_number)} was accepted and queued for a fix. "
            f"Reference: `{run_id}`."
        )
    else:
        raise ValueError("unknown comment state or missing/invalid PR URL")
    return {
        "kind": "github_issue_comment_upsert",
        "issue_number": int(issue_number),
        "marker": marker,
        "body": f"{marker}\n{text}",
        "state": state,
        "pr_url": safe_pr,
    }


# Public (customer-facing) result sentences: never mode, budget, errors, agents or step names.
PUBLIC_SUMMARIES = {
    "done": "Done. Reference: {run_id}.",
    "awaiting_owner": "Received and waiting for the owner's review. Reference: {run_id}.",
    "failed": "Could not be completed automatically; the owner has been notified. Reference: {run_id}.",
    "deliberately_not_done": "Reviewed and closed without further action. Reference: {run_id}.",
}


def public_summary(result: str, run_id: str) -> str:
    return PUBLIC_SUMMARIES.get(result, PUBLIC_SUMMARIES["awaiting_owner"]).format(run_id=run_id)


def result_for_status(status: str) -> str:
    """Every run ends with one of four coherent results (FR8)."""
    if status == "done":
        return "done"
    if status in ("failed",):
        return "failed"
    if status in ("closed",):
        return "deliberately_not_done"
    return "awaiting_owner"


def workflow_catalogue(registry: Optional[Dict[str, Workflow]] = None) -> List[Dict[str, Any]]:
    registry = WORKFLOWS if registry is None else registry
    out = []
    for wf in registry.values():
        out.append({
            "workflow_id": wf.workflow_id,
            "trigger_classes": list(wf.trigger_classes),
            "specialist_available": wf.specialist_available,
            "forced_mode": None if wf.specialist_available else "dry_run",
            "per_run_budget_usd": wf.per_run_budget_usd,
            "description": wf.description,
            "steps": [
                {"name": s.name, "kind": s.kind, "agent": s.agent, "action": s.action,
                 "checkpoint_kind": s.checkpoint_kind, "requires": list(s.requires)}
                for s in wf.steps
            ],
        })
    return out
