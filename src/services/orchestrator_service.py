"""Orchestrator run engine (v1.83.0).

One logical orchestrator: this service owns the run-id space, the run log, the approval
queue, the budget ledger and the settings. Executors (the GitHub Actions workflow, the
owner in the admin page) call it over HTTP and never each other; it is the only caller
of any step (FR15).

What the engine guarantees, independent of any workflow definition:
- intake is idempotent on ``dedup_key`` (FR2); a duplicate attaches visibly and causes
  no new side effect;
- steps run in definition order; ``requires`` gates are enforced here, never by a step
  (FR4); a failed prerequisite parks the run at an owner checkpoint (audit_flag);
- every hand-off carries a provenance envelope and the next step refuses to start
  without a complete one (FR11);
- checkpoints never time out (FR6); an approval binds exactly one (run, step, artifact
  hash, recipient) and a changed artifact needs a fresh approval (FR7);
- external actions are at-most-once: dispatched once, result idempotent per action id,
  artifact hash echoed back and checked (FR12);
- anything that fails parks in a visible ``parked`` state with reason and prior outputs;
  nothing is auto-closed (FR13);
- budget gates run before any llm step; deterministic steps continue (FR14);
- nothing processed inside a run can change settings: ``update_settings`` refuses while a
  run context is active and logs the attempt (FR9, AC9);
- the run log is redacted (FR16/AC17) and the customer-facing summary is a template
  (FR8/AC24).

No LLM is called in v1 (every shipped step is deterministic); the llm step kind, budget
checks and spend ledger exist so tests can pin the behaviour with a fake step.
"""
import asyncio
import contextvars
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.services import orchestrator_workflows as wfdef
from src.services.orchestrator_store import OrchestratorStore, new_run_id
from src.services.tiers import PRIORITY_RANK

logger = logging.getLogger(__name__)

# The run currently being advanced in this task. Settings changes are refused while it
# is set, so no step output can ever raise a budget, change a mode or pause/unpause.
_run_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("orch_run_context", default=None)


# ---------------------------------------------------------------------------
# Errors (mapped to HTTP by main.py)
# ---------------------------------------------------------------------------

class OrchestratorError(Exception):
    http_status = 400

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class NotFound(OrchestratorError):
    http_status = 404


class Conflict(OrchestratorError):
    http_status = 409


class Invalid(OrchestratorError):
    http_status = 422


class RefusedInsideRun(OrchestratorError):
    """A step (or anything running inside a run) tried to change settings or request a forbidden action."""
    http_status = 403


class StepBlocked(Exception):
    """Raised by a step handler: a precondition is missing. Not retried; parks with the reason."""


class ForbiddenAction(Exception):
    """Raised when a step requests an action kind outside the authority ceiling."""


# ---------------------------------------------------------------------------
# Step context handed to handlers
# ---------------------------------------------------------------------------

@dataclass
class StepContext:
    run: Dict[str, Any]
    step: Dict[str, Any]
    inputs: Dict[str, Any]
    service: "OrchestratorService"

    def request_action(self, kind: str) -> str:
        """Ask for an external action kind. Anything forbidden or unknown is refused and logged (AC8)."""
        if kind in wfdef.FORBIDDEN_ACTIONS or kind not in wfdef.ALLOWED_ACTION_KINDS:
            raise ForbiddenAction(kind)
        return kind

    async def update_setting(self, key: str, value: Any) -> None:
        """Always refused: settings change only through the owner control surface (AC9)."""
        await self.service.update_settings({key: value}, updated_by=f"step:{self.step['name']}")


Handler = Callable[[StepContext], Awaitable[Dict[str, Any]]]

ENVELOPE_FIELDS = ("requester", "trigger_event_id", "agent", "decision", "data_as_of", "mode", "human_decisions")

DEFAULT_GLOBAL_MONTHLY_USD = 150.0
DEFAULT_SOFT_ALERT_PCT = 80
DEFAULT_MODE = "supervised"
BACKEND_ERRORS = (ConnectionError, TimeoutError, OSError, asyncio.TimeoutError)
_BACKOFF_S = (0.5, 2.0, 5.0)

_OK_STEP_STATUSES = frozenset({"done", "done_manually"})
# The one approval-decision policy: which checkpoint kinds the owner may mark done manually or
# skip. decide_approval enforces it and _allowed_decisions renders the admin queue's buttons
# from it, so the two can never disagree.
_DONE_MANUALLY_KINDS = frozenset({"missing_specialist", "sdlc_run", "outbound", "publish", "step_review",
                                  "owner_handoff"})
_SKIPPABLE_KINDS = frozenset({"missing_specialist", "sdlc_run"})
# v1.84.0: an owner_handoff is answered (done_manually with a reply) or rejected; "approve" means nothing.
_NO_APPROVE_KINDS = frozenset({"owner_handoff"})
_FINISHED_STEP_STATUSES = _OK_STEP_STATUSES | {"skipped_dry_run", "skipped_by_owner", "failed"}
_INPUT_KEYS = ("issue_number", "labels", "label", "pr_number", "pr_url", "sha", "jobs", "org_id", "repo",
               "orchestrator_run_id", "customer_ref",
               # v1.84.0 support escalations (all set by server code, never by customer text)
               "category", "priority", "tier", "conversation_ref", "identified", "suggested_reply")
_SUPPORT_STR_KEYS = ("category", "priority", "tier", "conversation_ref")
# Every stored string input is bounded; the intake key must not be able to fill the volume.
_INPUT_STR_MAX = 200
# The support agent's suggested reply is untrusted-adjacent (LLM output): redacted and capped like a body.
_SUGGESTED_REPLY_MAX = wfdef.BODY_MAX

# v1.84.0: live support chat records its LLM spend under this workflow id (the workflow itself
# still parks at missing_specialist until the agent is wired into it in phase 2).
SUPPORT_SPEND_WORKFLOW = "support_triage"
SUPPORT_SPEND_AGENT = "support_agent"


def _redact_deep(value: Any) -> Any:
    if isinstance(value, str):
        return wfdef.redact_untrusted_text(value)
    if isinstance(value, dict):
        return {str(k): _redact_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_deep(v) for v in value]
    return value


def _pick_inputs(data: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only known structured fields; free text goes through summarize_untrusted separately."""
    out: Dict[str, Any] = {}
    for k in _INPUT_KEYS:
        if k in data and data[k] is not None:
            value = data[k]
            if isinstance(value, str):
                value = value[:_INPUT_STR_MAX]
            elif not isinstance(value, (int, float, bool, list, dict)):
                value = str(value)[:_INPUT_STR_MAX]
            out[k] = _redact_deep(value)
    for k in ("sha", "repo", "org_id", "customer_ref", "label", "orchestrator_run_id") + _SUPPORT_STR_KEYS:
        if k in out and not isinstance(out[k], str):
            out[k] = str(out[k])[:_INPUT_STR_MAX]
    if "category" in out and out["category"] not in wfdef.ESCALATION_CATEGORIES:
        out["category"] = "other"
    if "priority" in out and out["priority"] not in ("standard", "pro", "enterprise"):
        out["priority"] = "standard"
    if "identified" in out:
        out["identified"] = bool(out["identified"])
    if "suggested_reply" in out:
        # Taken from the raw data again: the generic loop above capped it at _INPUT_STR_MAX.
        reply = data.get("suggested_reply")
        out["suggested_reply"] = wfdef.redact_untrusted_text(str(reply))[:_SUGGESTED_REPLY_MAX] if reply else None
        if out["suggested_reply"] is None:
            out.pop("suggested_reply")
    if "labels" in out and not isinstance(out["labels"], list):
        out["labels"] = [str(out["labels"])[:50]]
    if isinstance(out.get("labels"), list):
        out["labels"] = [str(x)[:50] for x in out["labels"][:30]]
    if "issue_number" in out:
        try:
            out["issue_number"] = int(out["issue_number"])
        except (TypeError, ValueError):
            out.pop("issue_number")
    if "pr_number" in out:
        try:
            out["pr_number"] = int(out["pr_number"])
        except (TypeError, ValueError):
            out.pop("pr_number")
    if "pr_url" in out:
        out["pr_url"] = wfdef.validate_pr_url(out["pr_url"]) if isinstance(out["pr_url"], str) else None
        if out["pr_url"] is None:
            out.pop("pr_url")
    if "jobs" in out:
        if isinstance(out["jobs"], dict):
            out["jobs"] = {str(k)[:60]: str(v)[:30] for k, v in list(out["jobs"].items())[:30]}
        else:
            out.pop("jobs")
    return out


class OrchestratorService:
    """The run engine. One instance per process; constructed at startup (init_orchestrator_service)."""

    def __init__(self, store: OrchestratorStore, registry: Optional[Dict[str, wfdef.Workflow]] = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.store = store
        self.registry = wfdef.WORKFLOWS if registry is None else registry
        wfdef.validate_registry(self.registry)
        self.clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._handlers: Dict[Tuple[str, str], Handler] = {}
        self._llm_handlers: Dict[Tuple[str, str], Handler] = {}
        self._register_builtin_handlers()

    # ------------------------------------------------------------------ handler registry
    def register_handler(self, workflow_id: str, step_name: str, handler: Handler, llm: bool = False) -> None:
        (self._llm_handlers if llm else self._handlers)[(workflow_id, step_name)] = handler

    def _register_builtin_handlers(self) -> None:
        async def gh_intake(ctx: StepContext) -> Dict[str, Any]:
            return {
                "decision": "accepted-label event recorded; untrusted text stored redacted as data",
                "issue_number": ctx.inputs.get("issue_number"),
                "labels": ctx.inputs.get("labels", []),
                "data_as_of": _iso(ctx.service.clock()),
            }

        async def gh_annotate(ctx: StepContext) -> Dict[str, Any]:
            summary = (ctx.run.get("summary_json") or {}).get("text") or {}
            return {
                "decision": "owner signals recorded; instruction_like=%s (advisory, routing unaffected)"
                            % bool(summary.get("instruction_like")),
                "owner_signals": (ctx.run.get("summary_json") or {}).get("owner_signals", {}),
                "instruction_like": bool(summary.get("instruction_like")),
                "data_as_of": _iso(ctx.service.clock()),
            }

        async def support_intake(ctx: StepContext) -> Dict[str, Any]:
            return {"decision": "message recorded (redacted); acknowledgement reference is the run id",
                    "reference": ctx.run["run_id"], "data_as_of": _iso(ctx.service.clock())}

        async def support_annotate(ctx: StepContext) -> Dict[str, Any]:
            tags = (ctx.run.get("summary_json") or {}).get("advisory_tags", [])
            return {"decision": "advisory tags recorded (shown in the queue only)", "advisory_tags": tags,
                    "data_as_of": _iso(ctx.service.clock())}

        async def escalation_intake(ctx: StepContext) -> Dict[str, Any]:
            # v1.84.0: structured, server-set facts only. The customer's text lives (redacted,
            # truncated) in summary_json.text; no email, key or Stripe id can reach this output.
            i = ctx.inputs
            return {
                "decision": "customer escalation recorded; waiting for the owner (owner_handoff)",
                "category": i.get("category") or "other",
                "priority": i.get("priority") or "standard",
                "tier": i.get("tier"),
                "identified": bool(i.get("identified")),
                "org_ref": i.get("customer_ref"),
                "conversation_ref": i.get("conversation_ref"),
                "reference": ctx.run["run_id"],
                "data_as_of": _iso(ctx.service.clock()),
            }

        async def release_record(ctx: StepContext) -> Dict[str, Any]:
            pr_number = ctx.inputs.get("pr_number")
            pr_url = ctx.inputs.get("pr_url")
            if pr_number is None and pr_url is None:
                sha = ctx.inputs.get("sha") or "unknown"
                raise StepBlocked(
                    f"release content requires the SDLC pull request; no PR is recorded for release {sha}"
                )
            return {"decision": "release recorded with its SDLC pull request", "sha": ctx.inputs.get("sha"),
                    "pr_number": pr_number, "pr_url": pr_url, "data_as_of": _iso(ctx.service.clock())}

        async def record_jobs(ctx: StepContext) -> Dict[str, Any]:
            jobs = ctx.inputs.get("jobs") or {}
            failed = sorted(n for n, r in jobs.items() if str(r) not in ("success", "skipped"))
            if failed:
                raise StepBlocked("audit jobs failed: " + ", ".join(failed))
            return {"decision": "all weekly audit jobs succeeded", "jobs": jobs,
                    "data_as_of": _iso(ctx.service.clock())}

        async def summarize(ctx: StepContext) -> Dict[str, Any]:
            return {"decision": "weekly audit recorded", "job_count": len(ctx.inputs.get("jobs") or {}),
                    "data_as_of": _iso(ctx.service.clock())}

        self._handlers.update({
            ("github_accepted_bug", "intake"): gh_intake,
            ("github_accepted_bug", "annotate"): gh_annotate,
            ("support_triage", "intake"): support_intake,
            ("support_triage", "annotate"): support_annotate,
            ("support_escalation", "intake"): escalation_intake,
            ("release_notes_draft", "release_record"): release_record,
            ("weekly_price_audit", "record_jobs"): record_jobs,
            ("weekly_price_audit", "summarize"): summarize,
        })

    # ------------------------------------------------------------------ settings
    async def setting(self, key: str, default: Any = None) -> Any:
        return await self.store.get_setting(key, default)

    async def effective_settings(self) -> Dict[str, Any]:
        raw = await self.store.all_settings()
        out: Dict[str, Any] = {
            "global_pause": bool(raw.get("global_pause", False)),
            "mode_default": raw.get("mode_default", DEFAULT_MODE),
            "budget.global_monthly_usd": float(raw.get("budget.global_monthly_usd", DEFAULT_GLOBAL_MONTHLY_USD)),
            "budget.soft_alert_pct": float(raw.get("budget.soft_alert_pct", DEFAULT_SOFT_ALERT_PCT)),
        }
        for wid, wf in self.registry.items():
            out[f"mode_default.{wid}"] = wfdef.resolve_mode(wf, raw.get(f"mode_default.{wid}", out["mode_default"]))
            out[f"budget.per_run_usd.{wid}"] = float(raw.get(f"budget.per_run_usd.{wid}", wf.per_run_budget_usd))
            cap = raw.get(f"budget.workflow_monthly_usd.{wid}", wf.monthly_budget_usd)
            out[f"budget.workflow_monthly_usd.{wid}"] = float(cap) if cap is not None else None
        return out

    async def update_settings(self, changes: Dict[str, Any], updated_by: str = "operator") -> Dict[str, Any]:
        """Owner control surface only. Refused (and logged) when called from inside a run."""
        inside = _run_context.get()
        if inside is not None:
            await self.store.append_action({
                "run_id": inside, "actor": updated_by, "at": self.clock(), "action": "settings.change",
                "target": ",".join(sorted(str(k) for k in changes)), "outcome": "refused:inside_run",
            })
            raise RefusedInsideRun("settings_locked_inside_run",
                                   "settings can only change from the owner control surface")
        if not isinstance(changes, dict) or not changes:
            raise Invalid("no_changes", "no settings given")
        pause_now: Optional[bool] = None
        for key, value in changes.items():
            if key == "global_pause":
                if not isinstance(value, bool):
                    raise Invalid("invalid_value", "global_pause must be a boolean")
                pause_now = value
            elif key == "mode_default" or key.startswith("mode_default."):
                if value not in wfdef.MODES:
                    raise Invalid("invalid_value", f"{key} must be one of {', '.join(wfdef.MODES)}")
                if key != "mode_default":
                    wid = key[len("mode_default."):]
                    wf = self.registry.get(wid)
                    if wf is None:
                        raise Invalid("unknown_workflow", f"unknown workflow {wid}")
                    if not wf.specialist_available and value != "dry_run":
                        raise Invalid("specialist_missing",
                                      f"{wid}: its specialist is not available, so the mode is pinned to dry_run")
            elif key in ("budget.global_monthly_usd",) or key.startswith("budget.per_run_usd.") \
                    or key.startswith("budget.workflow_monthly_usd."):
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                    raise Invalid("invalid_value", f"{key} must be a positive number")
                wid = key.split(".", 2)[2] if key.count(".") >= 2 else None
                if wid is not None and wid not in self.registry:
                    raise Invalid("unknown_workflow", f"unknown workflow {wid}")
            elif key == "budget.soft_alert_pct":
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not (1 <= value <= 100):
                    raise Invalid("invalid_value", "budget.soft_alert_pct must be between 1 and 100")
            else:
                raise Invalid("unknown_setting", f"unknown setting {key}")
        for key, value in changes.items():
            await self.store.set_setting(key, value, updated_by=updated_by)
            await self.store.append_action({
                "actor": updated_by, "at": self.clock(), "action": "settings.change", "target": key,
                "outcome": "applied",
            })
        if pause_now is True:
            await self._pause_all()
        elif pause_now is False:
            await self._unpause_all()
        return await self.effective_settings()

    # ------------------------------------------------------------------ intake
    async def intake_event(self, payload: Dict[str, Any], actor_kind: str = "executor") -> Dict[str, Any]:
        trigger_class = payload.get("trigger_class")
        event_type = str(payload.get("event_type") or "")[:100]
        dedup_key = str(payload.get("dedup_key") or "")
        if trigger_class not in wfdef.TRIGGER_CLASSES:
            raise Invalid("invalid_trigger_class",
                          "trigger_class must be one of " + ", ".join(wfdef.TRIGGER_CLASSES))
        if not wfdef.DEDUP_KEY_RE.match(dedup_key):
            raise Invalid("invalid_dedup_key", "dedup_key must look like gh:issue:12:accepted")
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        actor = wfdef.redact_untrusted_text(str(payload.get("actor") or "")[:100]) or None
        actor_is_owner = bool(payload.get("actor_is_owner"))
        delivery_ref = str(payload.get("delivery_ref") or "")[:200] or None
        source = payload.get("source") or {}
        source_ref = None
        if isinstance(source, dict):
            source_ref = ":".join(str(source.get(k) or "")[:100] for k in ("kind", "ref")).strip(":") or None
        text = wfdef.summarize_untrusted(data.get("title"), data.get("body") or data.get("message"))
        inputs = _pick_inputs(data)
        if text["title"] or text["body"]:
            inputs["text"] = text
        owner_signals = {
            "actor_is_owner": actor_is_owner,
            "label": str(data.get("label") or "")[:50] or None,
            "labels": inputs.get("labels", []),
        }
        event_base = {
            "dedup_key": dedup_key, "trigger_class": trigger_class, "event_type": event_type,
            "received_at": self.clock(), "actor": actor, "actor_is_owner": actor_is_owner,
            "delivery_ref": delivery_ref, "instruction_like": text["instruction_like"],
            "payload_summary_json": {"text": text, "owner_signals": owner_signals,
                                     "inputs": {k: v for k, v in inputs.items() if k != "text"}},
        }
        if text["instruction_like"]:
            logger.warning("orchestrator intake: instruction-like content in %s (logged as data, routing unaffected)",
                           dedup_key)

        if trigger_class == "github_event" and event_type == "pull_request.opened":
            return await self._intake_pr_opened(event_base, inputs, actor_is_owner)

        # v1.84.0: escalations into the owner's queue come only from in-process callers (the support
        # agent for customer_message/support.escalation, the Stripe webhook handler for payment_event).
        # Neither the executor intake key (GitHub Actions) nor the operator surface may forge one via
        # POST /orchestrator/events; the owner starts support_escalation runs as owner_request instead.
        untrusted_escalation = (
            (trigger_class == "payment_event" and actor_kind != "stripe")
            or (trigger_class == "customer_message" and event_type == wfdef.SUPPORT_ESCALATION_EVENT
                and actor_kind != "support_agent")
        )
        if untrusted_escalation:
            return await self._refuse_intake(event_base, dedup_key, trigger_class, event_type, actor_kind,
                                             "refused:untrusted_actor")

        if trigger_class == "owner_request" and actor_kind != "operator":
            # Only the operator key may assert owner identity; the executor's intake key
            # cannot start an owner-request workflow by claiming to be the owner (AC2).
            owner_signals["actor_is_owner"] = False
        if trigger_class == "owner_request":
            owner_signals["workflow_id"] = event_type
        workflow_id, outcome = wfdef.route(trigger_class, event_type, owner_signals, self.registry)
        if workflow_id is None:
            if outcome == "continuation":
                outcome = "refused:no_route"
            return await self._refuse_intake(event_base, dedup_key, trigger_class, event_type, actor_kind, outcome)

        wf = self.registry[workflow_id]
        requester = _requester_for(trigger_class, actor, inputs)
        return await self._create_or_attach(wf, trigger_class, dedup_key, requester, source_ref, inputs, text,
                                            owner_signals, event_base, actor_kind, event_actor=actor)

    async def _refuse_intake(self, event_base: Dict[str, Any], dedup_key: str, trigger_class: str,
                             event_type: str, actor_kind: str, outcome: str) -> Dict[str, Any]:
        """Record a refused intake (event row + action) and return the no-run result."""
        await self.store.insert_event({**event_base, "run_id": None, "duplicate": False, "outcome": outcome})
        await self.store.append_action({
            "actor": f"intake:{actor_kind}", "at": self.clock(), "action": "intake.refused",
            "target": dedup_key, "trigger": f"{trigger_class}/{event_type}", "outcome": outcome,
        })
        return self._intake_result(None, False, outcome)

    async def intake_owner_request(self, workflow_id: str, request_id: str, inputs_in: Dict[str, Any],
                                   mode: Optional[str] = None) -> Dict[str, Any]:
        wf = self.registry.get(workflow_id)
        if wf is None:
            raise Invalid("unknown_workflow", f"unknown workflow {workflow_id}")
        wid, outcome = wfdef.route("owner_request", workflow_id, {"actor_is_owner": True, "workflow_id": workflow_id},
                                   self.registry)
        if wid is None:
            raise Invalid("not_owner_startable", f"{workflow_id} cannot be started by an owner request")
        data = inputs_in if isinstance(inputs_in, dict) else {}
        text = wfdef.summarize_untrusted(data.get("title"), data.get("body") or data.get("message"))
        inputs = _pick_inputs(data)
        if text["title"] or text["body"]:
            inputs["text"] = text
        trigger_class = wf.owner_request_trigger_class
        dedup_key = f"owner:{request_id}"
        owner_signals = {"actor_is_owner": True, "workflow_id": workflow_id}
        event_base = {
            "dedup_key": dedup_key, "trigger_class": trigger_class, "event_type": f"owner_request.{workflow_id}",
            "received_at": self.clock(), "actor": "owner", "actor_is_owner": True, "delivery_ref": request_id,
            "instruction_like": text["instruction_like"],
            "payload_summary_json": {"text": text, "owner_signals": owner_signals,
                                     "inputs": {k: v for k, v in inputs.items() if k != "text"}},
        }
        requester = _requester_for(trigger_class, "owner", inputs)
        return await self._create_or_attach(wf, trigger_class, dedup_key, requester, None, inputs, text,
                                            owner_signals, event_base, "operator", requested_mode=mode,
                                            event_actor="owner")

    async def _create_or_attach(self, wf: wfdef.Workflow, trigger_class: str, dedup_key: str, requester: str,
                                source_ref: Optional[str], inputs: Dict[str, Any], text: Dict[str, Any],
                                owner_signals: Dict[str, Any], event_base: Dict[str, Any], actor_kind: str,
                                requested_mode: Optional[str] = None, event_actor: Optional[str] = None
                                ) -> Dict[str, Any]:
        now = self.clock()
        global_default = await self.setting("mode_default", DEFAULT_MODE)
        mode_default = await self.setting(f"mode_default.{wf.workflow_id}", global_default)
        mode = wfdef.resolve_mode(wf, mode_default, requested_mode)
        paused = bool(await self.setting("global_pause", False))
        run_id = new_run_id()
        human_decisions: List[Dict[str, Any]] = []
        if trigger_class == "github_event" and owner_signals.get("actor_is_owner") and owner_signals.get("label"):
            human_decisions.append({"kind": "github_label", "label": owner_signals["label"], "actor": event_actor,
                                    "at": _iso(now)})
        advisory = wfdef.advisory_tags((text.get("title") or "") + " " + (text.get("body") or ""))
        run_row = {
            "run_id": run_id, "workflow_id": wf.workflow_id, "trigger_class": trigger_class, "dedup_key": dedup_key,
            "mode": mode, "status": "paused_global" if paused else "active", "result": None,
            "public_summary": wfdef.public_summary("awaiting_owner", run_id), "requester": requester,
            "source_ref": source_ref, "instruction_like": text["instruction_like"], "created_at": now,
            "updated_at": now, "finished_at": None, "step_count": 0, "max_steps": wfdef.MAX_STEPS,
            "deadline_at": now + wfdef.RUN_DEADLINE_S, "cost_usd": 0.0,
            "parked_reason": None,
            "provenance_json": {"requester": requester, "trigger_event_id": dedup_key, "trigger_class": trigger_class,
                                "mode": mode, "human_decisions": human_decisions},
            "summary_json": {"inputs": {k: v for k, v in inputs.items() if k != "text"}, "text": text,
                             "owner_signals": owner_signals, "advisory_tags": advisory,
                             "suggested_follow_ups": [], "resume_status": "active" if paused else None,
                             # Time spent paused at intake is credited back to the deadline on release.
                             "waiting_since": now if paused else None},
            "owner_note": None,
        }
        run, created = await self.store.create_run_or_attach(run_row)
        if not created:
            await self.store.insert_event({**event_base, "run_id": run["run_id"], "duplicate": True,
                                           "outcome": "attached_duplicate"})
            await self.store.append_action({
                "run_id": run["run_id"], "actor": f"intake:{actor_kind}", "at": now,
                "action": "dedup.duplicate_received", "target": dedup_key, "trigger": event_base["event_type"],
                "outcome": "attached_duplicate", "mode": run["mode"],
            })
            await self._refresh_parked_schedule_inputs(run, trigger_class, inputs, actor_kind, now)
            return self._intake_result(run, True, "attached_duplicate")

        # Retention is enforced opportunistically on every new run, not only at startup.
        await self.store.purge_expired(now=now)
        await self.store.insert_event({**event_base, "run_id": run_id, "duplicate": False, "outcome": "created_run"})
        steps = []
        for seq, s in enumerate(wf.steps, start=1):
            steps.append({
                "step_id": f"{run_id}:{seq}", "run_id": run_id, "seq": seq, "name": s.name, "kind": s.kind,
                "agent": s.agent, "status": "pending", "attempt": 0, "max_attempts": s.max_attempts,
                "started_at": None, "finished_at": None, "timeout_s": s.timeout_s, "outcome": None,
                "artifact_hash": None, "external_ref": None, "io_summary_json": None, "cost_usd": 0.0,
                "error_code": None,
            })
        await self.store.insert_steps(steps)
        await self.store.append_action({
            "run_id": run_id, "actor": f"intake:{actor_kind}", "at": now, "action": "run.created",
            "target": wf.workflow_id, "trigger": event_base["event_type"], "outcome": "created_run", "mode": mode,
            "provenance_json": run_row["provenance_json"],
        })
        if text["instruction_like"]:
            await self.store.append_action({
                "run_id": run_id, "actor": f"intake:{actor_kind}", "at": now, "action": "intake.instruction_like",
                "target": dedup_key, "outcome": "logged_as_data", "mode": mode,
            })
        if paused:
            await self.store.append_action({"run_id": run_id, "at": now, "action": "run.paused_global",
                                            "outcome": "parked_at_intake", "mode": mode})
        else:
            await self.advance(run_id)
        run = await self.store.get_run(run_id)
        return self._intake_result(run, False, "created_run")

    async def _refresh_parked_schedule_inputs(self, run: Dict[str, Any], trigger_class: str,
                                              inputs: Dict[str, Any], actor_kind: str, now: float) -> None:
        """A re-run of a scheduled job in the same period refreshes the parked run's structured inputs.

        Data only, no side effect: the run stays parked and the owner still resumes it; the resume
        then sees the latest job results (e.g. the fixed weekly audit) instead of the stale failure.
        """
        if trigger_class != "schedule" or run.get("status") != "parked":
            return
        fresh = {k: v for k, v in inputs.items() if k != "text"}
        if not fresh:
            return
        summary = dict(run.get("summary_json") or {})
        if summary.get("inputs") == fresh:
            return
        summary["inputs"] = fresh
        await self.store.update_run(run["run_id"], summary_json=summary, updated_at=now)
        await self.store.append_action({
            "run_id": run["run_id"], "actor": f"intake:{actor_kind}", "at": now, "action": "dedup.inputs_refreshed",
            "target": ",".join(sorted(fresh)), "trigger": trigger_class, "outcome": "parked_run_inputs_updated",
            "mode": run["mode"],
        })

    @staticmethod
    def _pr_matches_run(run: Dict[str, Any], inputs: Dict[str, Any]) -> bool:
        """The PR must live in the repository the run came from and match the event's own PR number.

        The intake key holder supplies both `pr_url` and `repo`, so the URL is checked against the
        repository recorded when the run was created (by the real workflow), never against the
        continuation event itself. A run with no recorded repository accepts no continuation.
        """
        run_repo = ((run.get("summary_json") or {}).get("inputs") or {}).get("repo")
        pr_url = inputs.get("pr_url")
        if not isinstance(run_repo, str) or not run_repo or not isinstance(pr_url, str):
            return False
        m = wfdef.PR_URL_RE.match(pr_url)
        if m is None:
            return False
        if pr_url != f"https://github.com/{run_repo}/pull/{m.group(1)}":
            return False
        pr_number = inputs.get("pr_number")
        return pr_number is None or str(pr_number) == m.group(1)

    async def _intake_pr_opened(self, event_base: Dict[str, Any], inputs: Dict[str, Any],
                                actor_is_owner: bool) -> Dict[str, Any]:
        """pull_request.opened with an Orchestrator-Run marker completes the sdlc_run checkpoint."""
        async with self._lock:
            return await self._intake_pr_opened_locked(event_base, inputs, actor_is_owner)

    async def _intake_pr_opened_locked(self, event_base: Dict[str, Any], inputs: Dict[str, Any],
                                       actor_is_owner: bool) -> Dict[str, Any]:
        now = self.clock()
        marker_run = inputs.get("orchestrator_run_id")
        run = None
        if isinstance(marker_run, str) and wfdef.RUN_ID_RE.match(marker_run):
            run = await self.store.get_run(marker_run)
        outcome = None
        if run is None:
            outcome = "refused:no_route"
        elif not actor_is_owner:
            outcome = "refused:not_owner"
        elif inputs.get("pr_url") is None:
            outcome = "refused:no_route"
        elif not self._pr_matches_run(run, inputs):
            outcome = "refused:repo_mismatch"
        if outcome:
            await self.store.insert_event({**event_base, "run_id": run["run_id"] if run else None, "duplicate": False,
                                           "outcome": outcome})
            await self.store.append_action({
                "run_id": run["run_id"] if run else None, "actor": "intake:executor", "at": now,
                "action": "intake.refused", "target": event_base["dedup_key"],
                "trigger": "github_event/pull_request.opened", "outcome": outcome,
            })
            return self._intake_result(None, False, outcome)

        # Only an accepted delivery of this PR counts as "seen": a refused one (not owner, foreign
        # repository) is logged against the run but must not block the genuine event afterwards.
        seen = await self.store.list_events(run["run_id"])
        if any(e["dedup_key"] == event_base["dedup_key"] and not str(e.get("outcome") or "").startswith("refused")
               for e in seen):
            await self.store.insert_event({**event_base, "run_id": run["run_id"], "duplicate": True,
                                           "outcome": "attached_duplicate"})
            await self.store.append_action({
                "run_id": run["run_id"], "actor": "intake:executor", "at": now, "action": "dedup.duplicate_received",
                "target": event_base["dedup_key"], "trigger": "github_event/pull_request.opened",
                "outcome": "attached_duplicate", "mode": run["mode"],
            })
            return self._intake_result(run, True, "attached_duplicate")

        pr_url = inputs["pr_url"]
        await self.store.insert_event({**event_base, "run_id": run["run_id"], "duplicate": False,
                                       "outcome": "attached_continuation"})
        provenance = run.get("provenance_json") or {}
        decisions = list(provenance.get("human_decisions") or [])
        decisions.append({"kind": "github_pr_marker", "pr_url": pr_url, "actor": event_base.get("actor"),
                          "at": _iso(now)})
        provenance["human_decisions"] = decisions
        await self.store.update_run(run["run_id"], provenance_json=provenance, updated_at=now)
        await self.store.append_action({
            "run_id": run["run_id"], "actor": "owner", "at": now, "action": "owner_signal.github_pr_marker",
            "target": pr_url, "trigger": "github_event/pull_request.opened", "outcome": "recorded",
            "mode": run["mode"],
        })
        # Complete the waiting sdlc_run checkpoint, if any.
        for step in await self.store.get_steps(run["run_id"]):
            if step["kind"] == "checkpoint" and step["status"] == "waiting_approval":
                approvals = await self.store.list_approvals(status="waiting", run_id=run["run_id"])
                for ap in approvals:
                    if ap["step_id"] == step["step_id"] and ap["kind"] == "sdlc_run":
                        await self.store.update_approval(ap["approval_id"], status="approved", decided_at=now,
                                                         decided_by="github_pr_marker", decision_ref=pr_url)
                        await self._complete_step(run, step, "done", outcome="pr_opened", external_ref=pr_url,
                                                  output={"decision": f"pull request opened by the owner: {pr_url}",
                                                          "pr_url": pr_url, "data_as_of": _iso(now)},
                                                  artifact={"pr_url": pr_url})
                        await self._leave_waiting(run["run_id"])
                break
        await self._advance_nolock(run["run_id"])
        run = await self.store.get_run(run["run_id"])
        return self._intake_result(run, False, "attached_continuation" if run else "refused:no_route")

    def _intake_result(self, run: Optional[Dict[str, Any]], duplicate: bool, outcome: str) -> Dict[str, Any]:
        return {
            "run_id": run["run_id"] if run else None,
            "duplicate": duplicate,
            "outcome": outcome,
            "status": run["status"] if run else None,
            "workflow_id": run["workflow_id"] if run else None,
            "mode": run["mode"] if run else None,
            "public_summary": run["public_summary"] if run else None,
        }

    # ------------------------------------------------------------------ the step loop
    async def advance(self, run_id: str) -> None:
        async with self._lock:
            await self._advance_nolock(run_id)

    async def _advance_nolock(self, run_id: str) -> None:
        """Advance while the caller already holds ``self._lock`` (asyncio.Lock is not re-entrant).

        Every state transition that reads-then-writes a run (approval decisions, executor
        results, resume/close, PR continuations) runs under the one lock and continues the run
        through here, so two identical concurrent requests can never both apply.
        """
        token = _run_context.set(run_id)
        try:
            await self._advance_locked(run_id)
        finally:
            _run_context.reset(token)

    async def _advance_locked(self, run_id: str) -> None:
        for _ in range(wfdef.MAX_STEPS * 4 + 4):  # hard bound on the loop itself
            run = await self.store.get_run(run_id)
            if run is None or run["status"] != "active":
                return
            if await self.setting("global_pause", False):
                await self._pause_run(run)
                return
            wf = self.registry.get(run["workflow_id"])
            if wf is None:
                await self._park(run, "unknown_workflow")
                return
            steps = await self.store.get_steps(run_id)
            by_name = {s["name"]: s for s in steps}
            step = next((s for s in steps if s["status"] == "pending"), None)
            if step is None:
                if any(s["status"] == "running" for s in steps):
                    return  # an external action is with its executor
                if any(s["status"] == "waiting_approval" for s in steps):
                    return  # a checkpoint is with the owner
                await self._finish(run, "failed" if any(s["status"] == "failed" for s in steps) else "done")
                return
            now = self.clock()
            if run["step_count"] >= run["max_steps"]:
                await self._park(run, "max_steps", step=step)
                return
            if run.get("deadline_at") and now > run["deadline_at"]:
                await self._park(run, "deadline", step=step)
                return
            # FR11: the previous hand-off must carry a complete envelope.
            prev = next((s for s in reversed(steps)
                         if s["seq"] < step["seq"] and s["status"] in _FINISHED_STEP_STATUSES), None)
            if prev is not None and prev["status"] in _OK_STEP_STATUSES:
                missing = _missing_envelope_fields((prev.get("io_summary_json") or {}).get("provenance"))
                if missing:
                    await self.store.update_step(step["step_id"], status="failed", finished_at=now,
                                                 outcome="refused:provenance_missing:" + ",".join(missing),
                                                 error_code="provenance_missing")
                    await self._park(run, "provenance_missing:" + ",".join(missing), step=step)
                    return
            # FR4: ordering gates.
            sdef = wf.step(step["name"])
            override = ((step.get("io_summary_json") or {}).get("gate_override"))
            for req in sdef.requires:
                r = by_name.get(req)
                if r is None:
                    continue
                if r["status"] in _OK_STEP_STATUSES:
                    continue
                if r["status"] == "failed" and not override:
                    await self._gate_checkpoint(run, step, req)
                    return
                if r["status"] != "failed":
                    reason = f"blocked: requires {req}" + (" (pull request)" if req == "sdlc_pipeline" else "")
                    await self.store.update_step(step["step_id"], status="failed", finished_at=now, outcome=reason,
                                                 error_code="blocked")
                    await self._park(run, f"blocked:{step['name']}:requires {req}", step=step)
                    return
            if step["kind"] == "deterministic":
                cont = await self._run_deterministic(run, step)
            elif step["kind"] == "llm":
                cont = await self._run_llm(run, step)
            elif step["kind"] == "checkpoint":
                cont = await self._run_checkpoint(run, step, sdef)
            elif step["kind"] == "external_action":
                cont = await self._run_external_action(run, step, sdef)
            else:
                await self._park(run, f"unknown_step_kind:{step['kind']}", step=step)
                return
            if not cont:
                return

    # -- deterministic -------------------------------------------------------
    async def _run_deterministic(self, run: Dict[str, Any], step: Dict[str, Any]) -> bool:
        # Timeout and attempts come from the step row (copied from the definition at run creation).
        handler = self._handlers.get((run["workflow_id"], step["name"]))
        if handler is None:
            await self._park(run, f"no_handler:{step['name']}", step=step)
            return False
        attempt = step["attempt"]
        while attempt < step["max_attempts"]:
            attempt += 1
            now = self.clock()
            await self.store.update_step(step["step_id"], status="running", attempt=attempt,
                                         started_at=step.get("started_at") or now)
            ctx = StepContext(run=run, step={**step, "attempt": attempt},
                              inputs=(run.get("summary_json") or {}).get("inputs", {}), service=self)
            try:
                output = await asyncio.wait_for(handler(ctx), timeout=step["timeout_s"])
            except StepBlocked as exc:
                reason = wfdef.redact_untrusted_text(str(exc))[:300]
                await self.store.update_step(step["step_id"], status="failed", finished_at=self.clock(),
                                             outcome=reason, error_code="blocked", attempt=attempt)
                await self._park(run, f"blocked:{step['name']}:{reason}", step=step)
                return False
            except ForbiddenAction as exc:
                await self.store.append_action({
                    "run_id": run["run_id"], "step_id": step["step_id"], "actor": f"step:{step['name']}",
                    "at": self.clock(), "action": "action.request", "target": str(exc)[:60],
                    "outcome": "refused:forbidden_action", "mode": run["mode"],
                })
                await self.store.update_step(step["step_id"], status="failed", finished_at=self.clock(),
                                             outcome=f"refused:forbidden_action:{str(exc)[:60]}",
                                             error_code="forbidden_action", attempt=attempt)
                await self._park(run, f"forbidden_action:{step['name']}", step=step)
                return False
            except RefusedInsideRun:
                await self.store.update_step(step["step_id"], status="failed", finished_at=self.clock(),
                                             outcome="refused:settings_change_inside_run",
                                             error_code="settings_locked", attempt=attempt)
                await self._park(run, f"refused_settings_change:{step['name']}", step=step)
                return False
            except Exception as exc:  # retryable
                await self.store.append_action({
                    "run_id": run["run_id"], "step_id": step["step_id"], "actor": "orchestrator", "at": self.clock(),
                    "action": "step.retry" if attempt < step["max_attempts"] else "step.failed",
                    "target": step["name"], "outcome": type(exc).__name__, "mode": run["mode"],
                })
                if attempt >= step["max_attempts"]:
                    await self.store.update_step(step["step_id"], status="failed", finished_at=self.clock(),
                                                 outcome="failed after %d attempts" % attempt,
                                                 error_code=type(exc).__name__, attempt=attempt)
                    await self._park(run, f"step_failed:{step['name']}", step=step)
                    return False
                continue
            return await self._accept_output(run, {**step, "attempt": attempt}, output)
        await self._park(run, f"step_failed:{step['name']}", step=step)
        return False

    async def _accept_output(self, run: Dict[str, Any], step: Dict[str, Any], output: Any) -> bool:
        """Scope check, data labelling, follow-ups as data, envelope, then mark the step done."""
        if not isinstance(output, dict):
            output = {"decision": str(output)[:200]}
        scope = (run.get("summary_json") or {}).get("inputs", {}).get("org_id")
        refs = output.get("references") or []
        foreign = [r for r in refs if isinstance(r, dict) and r.get("org_id") not in (None, scope)]
        if foreign:
            now = self.clock()
            await self.store.update_step(step["step_id"], status="failed", finished_at=now,
                                         outcome="scope_violation: output referenced another customer",
                                         error_code="scope_violation", io_summary_json=None)
            await self.store.append_action({
                "run_id": run["run_id"], "step_id": step["step_id"], "actor": f"step:{step['name']}", "at": now,
                "action": "scope.violation", "target": step["name"], "outcome": "failed_and_escalated",
                "mode": run["mode"],
            })
            await self._park(run, f"scope_violation:{step['name']}", step=step)
            return False
        output = dict(output)
        gate = str(output.get("gate_result") or "").lower()
        if gate in ("fail", "flag", "flagged"):
            # The step itself completed; its verdict blocks whatever requires it (FR4/AC3). The
            # dependent step then parks at an audit_flag owner checkpoint instead of running.
            reason = wfdef.redact_untrusted_text(str(output.get("gate_reason") or gate))[:200]
            await self._complete_step(run, step, "failed", outcome=f"gate_failed:{reason}", output=output)
            return True
        follow_ups = output.pop("suggested_follow_ups", None)
        if follow_ups:
            summary = dict(run.get("summary_json") or {})
            existing = list(summary.get("suggested_follow_ups") or [])
            for f in (follow_ups if isinstance(follow_ups, list) else [follow_ups]):
                existing.append({"from_step": step["name"], "suggestion": _redact_deep(f),
                                 "requires": "owner_decision", "invoked": False})
            summary["suggested_follow_ups"] = existing
            await self.store.update_run(run["run_id"], summary_json=summary, updated_at=self.clock())
            run["summary_json"] = summary
            await self.store.append_action({
                "run_id": run["run_id"], "step_id": step["step_id"], "actor": f"step:{step['name']}",
                "at": self.clock(), "action": "follow_up.suggested", "target": step["name"],
                "outcome": "recorded_as_data:owner_decision_required", "mode": run["mode"],
            })
        if isinstance(output.get("data"), list):
            labelled = []
            for item in output["data"]:
                if isinstance(item, dict):
                    item = dict(item)
                    if not item.get("confirmed"):
                        item["label"] = "unconfirmed - not current"
                        item["current"] = False
                        item.pop("value", None)
                labelled.append(item)
            output["data"] = labelled
        await self._complete_step(run, step, "done", outcome="completed", output=output)
        return True

    async def _complete_step(self, run: Dict[str, Any], step: Dict[str, Any], status: str, outcome: str,
                             output: Optional[Dict[str, Any]] = None, external_ref: Optional[str] = None,
                             artifact: Optional[Dict[str, Any]] = None, cost_usd: float = 0.0) -> None:
        now = self.clock()
        output = _redact_deep(output or {})
        envelope = self._envelope(run, step, output)
        io = dict(step.get("io_summary_json") or {})
        io.update({"output": output, "provenance": envelope})
        if artifact is not None:
            io["artifact"] = _redact_deep(artifact)
        artifact_hash = wfdef.canonical_hash(artifact) if artifact is not None else step.get("artifact_hash")
        await self.store.update_step(step["step_id"], status=status, finished_at=now, outcome=outcome[:300],
                                     io_summary_json=io, external_ref=external_ref, artifact_hash=artifact_hash,
                                     cost_usd=(step.get("cost_usd") or 0.0) + cost_usd)
        fresh = await self.store.get_run(run["run_id"])
        if fresh is not None:
            await self.store.update_run(run["run_id"], step_count=fresh["step_count"] + 1, updated_at=now)
            run["step_count"] = fresh["step_count"] + 1
        await self.store.append_action({
            "run_id": run["run_id"], "step_id": step["step_id"], "actor": step["agent"], "at": now,
            "action": f"step.{status}", "target": step["name"], "outcome": outcome[:100], "mode": run["mode"],
            "artifact_hash": artifact_hash if artifact is not None else None,
            "provenance_json": envelope,
        })

    def _envelope(self, run: Dict[str, Any], step: Dict[str, Any], output: Dict[str, Any]) -> Dict[str, Any]:
        prov = run.get("provenance_json") or {}
        return {
            "requester": run.get("requester"),
            "trigger_event_id": prov.get("trigger_event_id") or run.get("dedup_key"),
            "agent": step["agent"],
            "decision": str(output.get("decision") or f"{step['name']} completed")[:300],
            "data_as_of": output.get("data_as_of"),
            "mode": run["mode"],
            "human_decisions": list(prov.get("human_decisions") or []),
        }

    # -- llm -------------------------------------------------------------------
    async def _run_llm(self, run: Dict[str, Any], step: Dict[str, Any]) -> bool:
        ok, reason, _info = await self.check_budget(run, step)
        if not ok:
            await self.store.append_action({
                "run_id": run["run_id"], "step_id": step["step_id"], "actor": "orchestrator", "at": self.clock(),
                "action": "budget.refused", "target": step["name"], "outcome": f"budget exhausted:{reason}",
                "mode": run["mode"],
            })
            await self._park(run, f"budget_hard_stop:{reason}", step=step)
            return False
        handler = self._llm_handlers.get((run["workflow_id"], step["name"]))
        if handler is None:
            await self._park(run, f"no_handler:{step['name']}", step=step)
            return False
        attempt = step["attempt"]
        while attempt < step["max_attempts"]:
            attempt += 1
            await self.store.update_step(step["step_id"], status="running", attempt=attempt,
                                         started_at=step.get("started_at") or self.clock())
            ctx = StepContext(run=run, step={**step, "attempt": attempt},
                              inputs=(run.get("summary_json") or {}).get("inputs", {}), service=self)
            try:
                output = await asyncio.wait_for(handler(ctx), timeout=step["timeout_s"])
            except BACKEND_ERRORS as exc:
                await self.store.append_action({
                    "run_id": run["run_id"], "step_id": step["step_id"], "actor": "orchestrator", "at": self.clock(),
                    "action": "step.retry" if attempt < step["max_attempts"] else "step.failed",
                    "target": step["name"], "outcome": f"backend_unavailable:{type(exc).__name__}",
                    "mode": run["mode"],
                })
                if attempt >= step["max_attempts"]:
                    await self.store.update_step(step["step_id"], status="failed", finished_at=self.clock(),
                                                 outcome="backend unavailable after %d attempts" % attempt,
                                                 error_code="backend_unavailable", attempt=attempt)
                    await self._park(run, "backend_unavailable", step=step, notify_once=True)
                    return False
                await self._sleep(_BACKOFF_S[min(attempt - 1, len(_BACKOFF_S) - 1)])
                continue
            except Exception as exc:
                await self.store.update_step(step["step_id"], status="failed", finished_at=self.clock(),
                                             outcome="failed: %s" % type(exc).__name__, error_code=type(exc).__name__,
                                             attempt=attempt)
                await self._park(run, f"step_failed:{step['name']}", step=step)
                return False
            cost = float((output or {}).get("cost_usd") or 0.0) if isinstance(output, dict) else 0.0
            if cost:
                await self.record_spend(run, step, cost, model=output.get("model"),
                                        tokens_in=output.get("tokens_in"), tokens_out=output.get("tokens_out"))
            if isinstance(output, dict):
                output = {k: v for k, v in output.items() if k not in ("cost_usd", "tokens_in", "tokens_out", "model")}
            return await self._accept_output(run, {**step, "attempt": attempt, "cost_usd": cost}, output)
        await self._park(run, f"step_failed:{step['name']}", step=step)
        return False

    # -- checkpoint --------------------------------------------------------------
    async def _run_checkpoint(self, run: Dict[str, Any], step: Dict[str, Any], sdef: wfdef.Step) -> bool:
        kind = sdef.checkpoint_kind or "missing_specialist"
        inputs = (run.get("summary_json") or {}).get("inputs", {})
        if kind == "sdlc_run":
            issue = inputs.get("issue_number") or 0
            artifact = {
                "kind": "sdlc_run",
                "owner_action": wfdef.release_feature_command(issue, run["run_id"]),
                "issue_number": issue,
                "note": "Run this exact command from develop. The PR the pipeline opens carries an Orchestrator-Run "
                        "marker; its pull_request.opened event completes this checkpoint. Merging stays yours.",
            }
        elif kind == "missing_specialist":
            artifact = {"kind": "missing_specialist", "specialist": sdef.agent,
                        "label": "specialist not available - do manually or skip",
                        "inputs": {k: v for k, v in inputs.items()}}
        elif kind == "publish":
            artifact = {"kind": "publish", "label": "publish requires owner approval",
                        "content_step": sdef.requires[-1] if sdef.requires else None}
        elif kind == "owner_handoff":
            # v1.84.0: the customer escalation card. Body and suggested reply were redacted and
            # truncated at intake; the customer is an opaque org prefix, never an email or key.
            text = (run.get("summary_json") or {}).get("text") or {}
            artifact = {
                "kind": "owner_handoff",
                "label": f"customer escalation: {inputs.get('category') or 'other'} "
                         f"({inputs.get('tier') or 'anonymous'})",
                "priority": inputs.get("priority") or "standard",
                "body": (text.get("body") or "")[:wfdef.BODY_MAX],
                "customer": {"identified": bool(inputs.get("identified")), "org_ref": inputs.get("customer_ref")},
                "conversation_ref": inputs.get("conversation_ref"),
                "instruction_like": bool(run.get("instruction_like")),
                "suggested_reply": inputs.get("suggested_reply"),
            }
        else:
            artifact = {"kind": kind, "label": f"{kind} requires owner approval"}
        approval, is_new = await self.ensure_approval(run["run_id"], step["step_id"], kind, artifact, None)
        if approval["status"] in ("approved", "edited_approved"):
            await self._complete_step(run, step, "done", outcome=f"approved:{approval['decided_by']}",
                                      output={"decision": f"checkpoint {kind} approved by {approval['decided_by']}",
                                              "data_as_of": _iso(self.clock())}, artifact=artifact)
            return True
        now = self.clock()
        await self.store.update_step(step["step_id"], status="waiting_approval",
                                     started_at=step.get("started_at") or now, artifact_hash=approval["artifact_hash"],
                                     io_summary_json={**(step.get("io_summary_json") or {}), "artifact": artifact})
        await self.store.update_run(run["run_id"], status="waiting_owner", updated_at=now,
                                    public_summary=wfdef.public_summary("awaiting_owner", run["run_id"]),
                                    summary_json={**(run.get("summary_json") or {}), "waiting_since": now})
        await self.store.append_action({
            "run_id": run["run_id"], "step_id": step["step_id"], "actor": "orchestrator", "at": now,
            "action": "checkpoint.waiting", "target": kind, "outcome": "waiting_owner", "mode": run["mode"],
            "artifact_hash": approval["artifact_hash"],
        })
        if is_new:
            await self._notify("checkpoint_waiting", run["run_id"], f"{kind} checkpoint on step {step['name']}")
        return False

    async def _gate_checkpoint(self, run: Dict[str, Any], step: Dict[str, Any], failed_req: str) -> None:
        """A prerequisite (e.g. the audit gate) failed: park at an audit_flag owner checkpoint (AC3)."""
        artifact = {"kind": "audit_flag", "label": f"prerequisite {failed_req} failed before {step['name']}",
                    "blocked_step": step["name"], "failed_step": failed_req}
        approval, is_new = await self.ensure_approval(run["run_id"], step["step_id"], "audit_flag", artifact, None)
        now = self.clock()
        await self.store.update_step(step["step_id"], status="waiting_approval", started_at=now,
                                     artifact_hash=approval["artifact_hash"],
                                     io_summary_json={**(step.get("io_summary_json") or {}), "artifact": artifact})
        await self.store.update_run(run["run_id"], status="waiting_owner", updated_at=now,
                                    parked_reason=f"gate_failed:{failed_req}",
                                    summary_json={**(run.get("summary_json") or {}), "waiting_since": now})
        await self.store.append_action({
            "run_id": run["run_id"], "step_id": step["step_id"], "actor": "orchestrator", "at": now,
            "action": "gate.blocked", "target": f"{step['name']} requires {failed_req}", "outcome": "waiting_owner",
            "mode": run["mode"], "artifact_hash": approval["artifact_hash"],
        })
        if is_new:
            await self._notify("audit_gate_blocked", run["run_id"], f"{failed_req} failed before {step['name']}")

    # -- external action --------------------------------------------------------
    async def _run_external_action(self, run: Dict[str, Any], step: Dict[str, Any], sdef: wfdef.Step) -> bool:
        inputs = (run.get("summary_json") or {}).get("inputs", {})
        decisions = (run.get("provenance_json") or {}).get("human_decisions") or []
        pr_url = next((d.get("pr_url") for d in reversed(decisions) if d.get("kind") == "github_pr_marker"), None)
        issue = inputs.get("issue_number")
        if sdef.action != "github_issue_comment_upsert" or not issue:
            await self.store.update_step(step["step_id"], status="failed", finished_at=self.clock(),
                                         outcome="blocked: no issue to comment on", error_code="blocked")
            await self._park(run, f"blocked:{step['name']}:no_target", step=step)
            return False
        state = "pr_opened" if pr_url else "accepted"
        artifact = wfdef.render_status_comment(run["run_id"], step["name"], int(issue), state, pr_url)
        recipient = f"issue:#{int(issue)}"
        artifact_hash = wfdef.canonical_hash(artifact)
        now = self.clock()
        if run["mode"] == "dry_run":
            await self.store.insert_approval({
                "run_id": run["run_id"], "step_id": step["step_id"], "kind": sdef.checkpoint_kind or "step_review",
                "status": "skipped", "artifact_hash": artifact_hash, "artifact_summary_json": artifact,
                "recipient": recipient, "requested_at": now, "decided_at": now, "decided_by": "mode:dry_run",
                "decision_note": "dry_run: rendered, not executed",
            })
            await self.store.append_action({
                "run_id": run["run_id"], "step_id": step["step_id"], "actor": "orchestrator", "at": now,
                "action": "action.skipped_dry_run", "target": recipient, "outcome": "not_executed",
                "mode": run["mode"], "executor": sdef.executor, "artifact_hash": artifact_hash,
            })
            await self._complete_step(run, {**step, "io_summary_json": {"artifact": artifact}}, "skipped_dry_run",
                                      outcome="dry_run: rendered only",
                                      output={"decision": "dry_run: external action rendered, not executed",
                                              "data_as_of": _iso(now)}, artifact=artifact)
            return True
        needs_approval = (sdef.checkpoint_kind in wfdef.MANDATORY_CHECKPOINT_KINDS) or run["mode"] == "supervised"
        if needs_approval:
            kind = sdef.checkpoint_kind or "step_review"
            approval = await self.store.find_approval(run["run_id"], step["step_id"], artifact_hash, recipient)
            if approval is None or approval["status"] == "waiting":
                signal = next((d for d in reversed(decisions) if sdef.approved_by_signal
                               and d.get("kind") == sdef.approved_by_signal), None)
                if approval is None and signal is not None:
                    approval_id = await self.store.insert_approval({
                        "run_id": run["run_id"], "step_id": step["step_id"], "kind": kind, "status": "approved",
                        "artifact_hash": artifact_hash, "artifact_summary_json": artifact, "recipient": recipient,
                        "requested_at": now, "decided_at": now, "decided_by": sdef.approved_by_signal,
                        "decision_ref": signal.get("pr_url") or signal.get("label"),
                    })
                    approval = await self.store.get_approval(approval_id)
                elif approval is None:
                    approval, _new = await self.ensure_approval(run["run_id"], step["step_id"], kind, artifact,
                                                                recipient)
            if approval["status"] not in ("approved", "edited_approved"):
                io = {**(step.get("io_summary_json") or {}), "artifact": artifact, "recipient": recipient}
                await self.store.update_step(step["step_id"], status="waiting_approval", artifact_hash=artifact_hash,
                                             started_at=step.get("started_at") or now, io_summary_json=io)
                await self.store.update_run(run["run_id"], status="waiting_owner", updated_at=now,
                                            summary_json={**(run.get("summary_json") or {}), "waiting_since": now})
                await self.store.append_action({
                    "run_id": run["run_id"], "step_id": step["step_id"], "actor": "orchestrator", "at": now,
                    "action": "checkpoint.waiting", "target": kind, "outcome": "waiting_owner", "mode": run["mode"],
                    "artifact_hash": artifact_hash,
                })
                await self._notify("checkpoint_waiting", run["run_id"], f"{kind} approval for step {step['name']}")
                return False
        await self._dispatch(run, step, sdef, artifact, artifact_hash, recipient)
        return False  # the executor's result continues the run

    async def _dispatch(self, run: Dict[str, Any], step: Dict[str, Any], sdef: wfdef.Step, artifact: Dict[str, Any],
                        artifact_hash: str, recipient: str) -> None:
        now = self.clock()
        envelope = self._envelope(run, step, {"decision": "dispatched"})
        io = {**(step.get("io_summary_json") or {}), "artifact": artifact, "recipient": recipient,
              "executor": sdef.executor, "provenance": envelope}
        await self.store.update_step(step["step_id"], status="running", attempt=step["attempt"] + 1,
                                     started_at=step.get("started_at") or now, artifact_hash=artifact_hash,
                                     io_summary_json=io)
        await self.store.append_action({
            "run_id": run["run_id"], "step_id": step["step_id"], "actor": "orchestrator", "at": now,
            "action": "action.dispatched", "target": recipient, "outcome": "awaiting_executor", "mode": run["mode"],
            "executor": sdef.executor, "artifact_hash": artifact_hash, "provenance_json": envelope,
        })

    # ------------------------------------------------------------------ approvals
    async def ensure_approval(self, run_id: str, step_id: str, kind: str, artifact: Dict[str, Any],
                              recipient: Optional[str]) -> Tuple[Dict[str, Any], bool]:
        """The approval bound to exactly (run, step, artifact hash, recipient); created waiting if absent (FR7)."""
        artifact_hash = wfdef.canonical_hash(artifact)
        existing = await self.store.find_approval(run_id, step_id, artifact_hash, recipient)
        if existing is not None:
            return existing, False
        approval_id = await self.store.insert_approval({
            "run_id": run_id, "step_id": step_id, "kind": kind, "status": "waiting", "artifact_hash": artifact_hash,
            "artifact_summary_json": _redact_deep(artifact), "recipient": recipient, "requested_at": self.clock(),
        })
        return (await self.store.get_approval(approval_id)), True

    async def decide_approval(self, approval_id: str, decision: str, note: Optional[str] = None,
                              artifact: Optional[Dict[str, Any]] = None,
                              expected_hash: Optional[str] = None) -> Dict[str, Any]:
        # Serialized: the "still waiting" check and the write happen under the same lock, so a
        # double-click (two identical POSTs) applies once and the second gets 409.
        async with self._lock:
            return await self._decide_approval_locked(approval_id, decision, note, artifact, expected_hash)

    async def _decide_approval_locked(self, approval_id: str, decision: str, note: Optional[str],
                                      artifact: Optional[Dict[str, Any]],
                                      expected_hash: Optional[str]) -> Dict[str, Any]:
        approval = await self.store.get_approval(approval_id)
        if approval is None:
            raise NotFound("approval_not_found", "no such approval")
        if approval["status"] != "waiting":
            raise Conflict("approval_already_decided", f"approval is {approval['status']}")
        if await self.setting("global_pause", False) and decision != "reject":
            raise Conflict("paused", "only reject is possible while the global pause is on")
        run = await self.store.get_run(approval["run_id"])
        step = await self.store.get_step(approval["step_id"])
        if run is None or step is None:
            raise NotFound("run_not_found", "the run of this approval no longer exists")
        if expected_hash is not None and expected_hash != approval["artifact_hash"]:
            raise Conflict("artifact_hash_mismatch", "the approval is bound to a different artifact")
        if step.get("artifact_hash") and step["artifact_hash"] != approval["artifact_hash"]:
            raise Conflict("artifact_hash_mismatch", "the artifact changed since this approval was requested")
        kind = approval["kind"]
        now = self.clock()
        wf = self.registry.get(run["workflow_id"])
        sdef = wf.step(step["name"]) if wf else None
        note = wfdef.redact_untrusted_text(note or "")[:1000] or None

        if decision == "skip" and kind not in _SKIPPABLE_KINDS:
            raise Invalid("skip_not_allowed", f"a {kind} checkpoint cannot be skipped")
        if decision in ("approve", "edit_approve") and kind in _NO_APPROVE_KINDS:
            raise Invalid("approve_not_allowed", f"a {kind} checkpoint is answered (done_manually) or rejected")
        if decision == "done_manually" and kind not in _DONE_MANUALLY_KINDS:
            raise Invalid("done_manually_not_allowed", f"a {kind} checkpoint cannot be marked done manually")
        if decision == "edit_approve":
            if step["kind"] != "external_action":
                raise Invalid("edit_not_allowed", "only external actions can be edited before approval")
            body_text = artifact.get("body") if isinstance(artifact, dict) else None
            if not isinstance(body_text, str) or not body_text.strip():
                raise Invalid("artifact_required", "edit_approve needs an artifact with a non-empty body")

        human = {"kind": "admin_queue", "approval_id": approval_id, "decision": decision, "step": step["name"],
                 "at": _iso(now)}
        provenance = dict(run.get("provenance_json") or {})
        provenance["human_decisions"] = list(provenance.get("human_decisions") or []) + [human]
        await self.store.update_run(run["run_id"], provenance_json=provenance, updated_at=now)
        run["provenance_json"] = provenance

        if decision == "reject":
            await self.store.update_approval(approval_id, status="rejected", decided_at=now, decided_by="admin_queue",
                                             decision_note=note)
            await self.store.update_step(step["step_id"], status="failed", finished_at=now,
                                         outcome="rejected by owner", error_code="rejected")
            await self.store.append_action({
                "run_id": run["run_id"], "step_id": step["step_id"], "actor": "owner", "at": now,
                "action": "approval.reject", "target": kind, "outcome": "rejected", "mode": run["mode"],
                "artifact_hash": approval["artifact_hash"],
            })
            await self._finish(run, "closed", result="deliberately_not_done", note=note)
            return await self.store.get_approval(approval_id)

        if decision == "edit_approve":
            current = (step.get("io_summary_json") or {}).get("artifact") or {}
            marker = current.get("marker") or wfdef.comment_marker(run["run_id"], step["name"])
            body = wfdef.redact_untrusted_text(artifact["body"])[:4000]
            if marker not in body:
                body = f"{marker}\n{body}"
            new_artifact = {**current, "body": body, "edited_by_owner": True}
            new_hash = wfdef.canonical_hash(new_artifact)
            await self.store.update_approval(approval_id, status="edited_approved", decided_at=now,
                                             decided_by="admin_queue", decision_note=note,
                                             edited_artifact_hash=new_hash)
            # The edited artifact gets its own approval row so the binding stays one artifact per approval.
            await self.store.insert_approval({
                "run_id": run["run_id"], "step_id": step["step_id"], "kind": kind, "status": "approved",
                "artifact_hash": new_hash, "artifact_summary_json": new_artifact,
                "recipient": approval.get("recipient"),
                "requested_at": now, "decided_at": now, "decided_by": "admin_queue", "decision_ref": approval_id,
                "decision_note": "edited then approved",
            })
            io = {**(step.get("io_summary_json") or {}), "artifact": new_artifact}
            await self.store.update_step(step["step_id"], artifact_hash=new_hash, io_summary_json=io)
            step = await self.store.get_step(step["step_id"])
            await self.store.append_action({
                "run_id": run["run_id"], "step_id": step["step_id"], "actor": "owner", "at": now,
                "action": "approval.edit_approve", "target": kind, "outcome": "edited_approved", "mode": run["mode"],
                "artifact_hash": new_hash,
            })
            await self._dispatch(run, step, sdef, new_artifact, new_hash, approval.get("recipient") or "")
            await self._leave_waiting(run["run_id"])
            return await self.store.get_approval(approval_id)

        status_map = {"approve": "approved", "done_manually": "done_manually", "skip": "skipped"}
        if decision not in status_map:
            raise Invalid("invalid_decision", "decision must be approve, reject, edit_approve, done_manually or skip")
        await self.store.update_approval(approval_id, status=status_map[decision], decided_at=now,
                                         decided_by="admin_queue", decision_note=note)
        await self.store.append_action({
            "run_id": run["run_id"], "step_id": step["step_id"], "actor": "owner", "at": now,
            "action": f"approval.{decision}", "target": kind, "outcome": status_map[decision], "mode": run["mode"],
            "artifact_hash": approval["artifact_hash"],
        })
        artifact_now = (step.get("io_summary_json") or {}).get("artifact")
        if decision == "skip":
            await self._complete_step(run, step, "skipped_by_owner", outcome="skipped by owner",
                                      output={"decision": f"{kind} skipped by the owner", "data_as_of": _iso(now)},
                                      artifact=artifact_now)
        elif decision == "done_manually":
            await self._complete_step(run, step, "done_manually", outcome="done manually by owner",
                                      output={"decision": f"{kind} done manually by the owner",
                                              "data_as_of": _iso(now)},
                                      artifact=artifact_now)
        elif kind == "audit_flag":
            # Owner overrides a failed prerequisite: the blocked step goes back to pending with the override recorded.
            io = {**(step.get("io_summary_json") or {}), "gate_override": approval_id}
            await self.store.update_step(step["step_id"], status="pending", io_summary_json=io)
        elif step["kind"] == "external_action":
            await self._dispatch(run, step, sdef, artifact_now or {}, approval["artifact_hash"],
                                 approval.get("recipient") or "")
        else:
            await self._complete_step(run, step, "done", outcome="approved:admin_queue",
                                      output={"decision": f"checkpoint {kind} approved in the admin queue",
                                              "data_as_of": _iso(now)}, artifact=artifact_now)
        await self._leave_waiting(run["run_id"])
        await self._advance_nolock(run["run_id"])
        return await self.store.get_approval(approval_id)

    async def _leave_waiting(self, run_id: str) -> None:
        run = await self.store.get_run(run_id)
        if run is None or run["status"] not in ("waiting_owner", "parked"):
            return
        now = self.clock()
        summary = dict(run.get("summary_json") or {})
        waited = max(0.0, now - float(summary.pop("waiting_since", now) or now))
        deadline = (run.get("deadline_at") or now) + waited  # checkpoints never eat into executing time
        if deadline <= now:
            # The executing window itself ran out (parked `deadline`); the owner's explicit resume
            # grants a fresh window, otherwise resume would re-park immediately and only Close ends it.
            deadline = now + wfdef.RUN_DEADLINE_S
        await self.store.update_run(run_id, status="active", deadline_at=deadline, summary_json=summary,
                                    parked_reason=None, updated_at=now)

    # ------------------------------------------------------------------ executors
    async def pending_actions(self, run_id: str, executor: Optional[str] = None) -> List[Dict[str, Any]]:
        run = await self.store.get_run(run_id)
        if run is None:
            raise NotFound("run_not_found", "no such run")
        if run["status"] != "active" or await self.setting("global_pause", False):
            return []
        wf = self.registry.get(run["workflow_id"])
        out = []
        for step in await self.store.get_steps(run_id):
            if step["kind"] != "external_action" or step["status"] != "running":
                continue
            sdef = wf.step(step["name"]) if wf else None
            if executor and sdef and sdef.executor != executor:
                continue
            io = step.get("io_summary_json") or {}
            out.append({
                "action_id": step["step_id"], "run_id": run_id, "step": step["name"],
                "kind": sdef.action if sdef else None, "executor": sdef.executor if sdef else None,
                "target": {"issue_number": (io.get("artifact") or {}).get("issue_number"),
                           "recipient": io.get("recipient")},
                "artifact": io.get("artifact"), "artifact_hash": step["artifact_hash"], "attempt": step["attempt"],
                "max_attempts": step["max_attempts"], "mode": run["mode"], "provenance": io.get("provenance"),
            })
        return out

    async def record_action_result(self, run_id: str, action_id: str, body: Dict[str, Any],
                                   actor: str = "executor") -> Dict[str, Any]:
        # Serialized for the same reason as decide_approval: two concurrent identical results
        # must record once (the second sees `done` and answers already_recorded).
        async with self._lock:
            return await self._record_action_result_locked(run_id, action_id, body, actor)

    async def _record_action_result_locked(self, run_id: str, action_id: str, body: Dict[str, Any],
                                           actor: str) -> Dict[str, Any]:
        run = await self.store.get_run(run_id)
        step = await self.store.get_step(action_id)
        if run is None or step is None or step["run_id"] != run_id or step["kind"] != "external_action":
            raise NotFound("action_not_found", "no such action for this run")
        echo = body.get("provenance_echo")
        now = self.clock()
        if not isinstance(echo, dict) or echo.get("run_id") != run_id or echo.get("action_id") != action_id:
            await self.store.append_action({
                "run_id": run_id, "step_id": action_id, "actor": actor, "at": now, "action": "action.result",
                "outcome": "refused:provenance_required", "mode": run["mode"],
            })
            raise Invalid("provenance_required", "the result must echo provenance {run_id, action_id}")
        external_ref = wfdef.redact_untrusted_text(str(body.get("external_ref") or ""))[:300] or None
        if step["status"] == "done":
            await self.store.append_action({
                "run_id": run_id, "step_id": action_id, "actor": actor, "at": now, "action": "action.result",
                "outcome": "duplicate_ignored", "mode": run["mode"], "artifact_hash": step["artifact_hash"],
            })
            return {"status": "already_recorded", "step_status": "done", "external_ref": step.get("external_ref")}
        if step["status"] != "running":
            raise Conflict("action_not_dispatched", f"action is {step['status']}, not awaiting an executor")
        if body.get("artifact_hash") != step["artifact_hash"]:
            await self.store.update_step(action_id, status="failed", finished_at=now,
                                         outcome="artifact hash mismatch on result",
                                         error_code="artifact_hash_mismatch")
            await self.store.append_action({
                "run_id": run_id, "step_id": action_id, "actor": actor, "at": now, "action": "action.result",
                "outcome": "refused:artifact_hash_mismatch", "mode": run["mode"],
                "artifact_hash": step["artifact_hash"],
            })
            await self._park(run, f"artifact_hash_mismatch:{step['name']}", step=step)
            raise Conflict("artifact_hash_mismatch", "the executor reported a different artifact; the run is parked")
        outcome = body.get("outcome")
        if outcome == "success":
            artifact = (step.get("io_summary_json") or {}).get("artifact")
            await self._complete_step(run, step, "done", outcome="executed", external_ref=external_ref,
                                      output={"decision": "external action executed by " + actor,
                                              "data_as_of": _iso(now)}, artifact=artifact)
            await self._advance_nolock(run_id)
            return {"status": "recorded", "step_status": "done", "external_ref": external_ref}
        # failed
        error_code = str(body.get("error_code") or "executor_failed")[:60]
        attempt = step["attempt"]
        if attempt >= step["max_attempts"]:
            await self.store.update_step(action_id, status="failed", finished_at=now,
                                         outcome="executor failed after %d attempts" % attempt, error_code=error_code)
            await self.store.append_action({
                "run_id": run_id, "step_id": action_id, "actor": actor, "at": now, "action": "action.result",
                "outcome": f"failed:{error_code}", "mode": run["mode"], "artifact_hash": step["artifact_hash"],
            })
            await self._park(run, f"step_failed:{step['name']}", step=step)
            return {"status": "recorded", "step_status": "failed", "external_ref": None}
        await self.store.update_step(action_id, attempt=attempt + 1, error_code=error_code)
        await self.store.append_action({
            "run_id": run_id, "step_id": action_id, "actor": actor, "at": now, "action": "action.result",
            "outcome": f"failed_retryable:{error_code}", "mode": run["mode"], "artifact_hash": step["artifact_hash"],
        })
        return {"status": "recorded", "step_status": "running", "retry": True, "attempt": attempt + 1}

    # ------------------------------------------------------------------ parking / pause / finish
    async def _park(self, run: Dict[str, Any], reason: str, step: Optional[Dict[str, Any]] = None,
                    notify_once: bool = False) -> None:
        now = self.clock()
        reason = wfdef.redact_untrusted_text(reason)[:300]
        await self.store.update_run(run["run_id"], status="parked", parked_reason=reason, updated_at=now,
                                    public_summary=wfdef.public_summary("awaiting_owner", run["run_id"]),
                                    summary_json={**(run.get("summary_json") or {}), "waiting_since": now})
        await self.store.append_action({
            "run_id": run["run_id"], "step_id": step["step_id"] if step else None, "actor": "orchestrator", "at": now,
            "action": "run.parked", "target": step["name"] if step else None, "outcome": reason[:100],
            "mode": run["mode"],
        })
        kind = "run_parked:" + reason.split(":", 1)[0]
        if notify_once and await self.store.count_actions(run["run_id"], f"notify.{kind}") > 0:
            return
        await self._notify(kind, run["run_id"], reason)

    async def _pause_run(self, run: Dict[str, Any]) -> None:
        now = self.clock()
        summary = {**(run.get("summary_json") or {}), "resume_status": run["status"], "waiting_since": now}
        await self.store.update_run(run["run_id"], status="paused_global", summary_json=summary, updated_at=now)
        await self.store.append_action({"run_id": run["run_id"], "actor": "orchestrator", "at": now,
                                        "action": "run.paused_global", "outcome": "parked_at_step_boundary",
                                        "mode": run["mode"]})

    async def _pause_all(self) -> None:
        for run in await self.store.list_runs_by_status(("active",)):
            await self._pause_run(run)

    async def _unpause_all(self) -> None:
        for run in await self.store.list_runs_by_status(("paused_global",)):
            summary = dict(run.get("summary_json") or {})
            prior = summary.pop("resume_status", None) or "active"
            now = self.clock()
            # Paused time is waiting on the owner: credit it back so a pause longer than the
            # 24 h executing window cannot strand every in-flight run at `deadline`.
            since = summary.pop("waiting_since", None)
            paused_for = max(0.0, now - float(since if since is not None else run.get("created_at") or now))
            deadline = (run.get("deadline_at") or now) + paused_for
            await self.store.update_run(run["run_id"], status=prior, summary_json=summary, deadline_at=deadline,
                                        updated_at=now)
            await self.store.append_action({"run_id": run["run_id"], "actor": "owner", "at": now,
                                            "action": "run.unpaused", "outcome": prior, "mode": run["mode"]})
        for run in await self.store.list_runs_by_status(("active",)):
            await self.advance(run["run_id"])

    async def _finish(self, run: Dict[str, Any], status: str, result: Optional[str] = None,
                      note: Optional[str] = None) -> None:
        now = self.clock()
        result = result or wfdef.result_for_status(status)
        await self.store.update_run(run["run_id"], status=status, result=result, finished_at=now, updated_at=now,
                                    public_summary=wfdef.public_summary(result, run["run_id"]), owner_note=note,
                                    parked_reason=None)
        await self.store.append_action({"run_id": run["run_id"], "actor": "orchestrator", "at": now,
                                        "action": "run.finished", "outcome": result, "mode": run["mode"]})

    async def resume_run(self, run_id: str) -> Dict[str, Any]:
        async with self._lock:
            run = await self.store.get_run(run_id)
            if run is None:
                raise NotFound("run_not_found", "no such run")
            if run["status"] != "parked":
                raise Conflict("not_parked", f"run is {run['status']}")
            now = self.clock()
            steps = await self.store.get_steps(run_id)
            last_failed = next((s for s in reversed(steps) if s["status"] == "failed"), None)
            if last_failed is not None:
                await self.store.update_step(last_failed["step_id"], status="pending", attempt=0, outcome=None,
                                             error_code=None, finished_at=None)
            await self.store.append_action({"run_id": run_id, "actor": "owner", "at": now, "action": "run.resumed",
                                            "outcome": run.get("parked_reason") or "resumed", "mode": run["mode"]})
            await self._leave_waiting(run_id)
            await self._advance_nolock(run_id)
            return await self.store.get_run(run_id)

    async def close_run(self, run_id: str, note: Optional[str] = None,
                        result: str = "deliberately_not_done") -> Dict[str, Any]:
        async with self._lock:
            return await self._close_run_locked(run_id, note, result)

    async def _close_run_locked(self, run_id: str, note: Optional[str], result: str) -> Dict[str, Any]:
        run = await self.store.get_run(run_id)
        if run is None:
            raise NotFound("run_not_found", "no such run")
        if run["status"] in ("done", "closed", "failed"):
            raise Conflict("already_finished", f"run is {run['status']}")
        if result not in ("deliberately_not_done", "failed"):
            raise Invalid("invalid_result", "result must be deliberately_not_done or failed")
        now = self.clock()
        for ap in await self.store.list_approvals(status="waiting", run_id=run_id):
            await self.store.update_approval(ap["approval_id"], status="rejected", decided_at=now,
                                             decided_by="admin_queue", decision_note="run closed by owner")
        await self.store.append_action({"run_id": run_id, "actor": "owner", "at": now, "action": "run.closed",
                                        "outcome": result, "mode": run["mode"]})
        await self._finish(run, "failed" if result == "failed" else "closed", result=result,
                           note=wfdef.redact_untrusted_text(note or "")[:1000] or None)
        return await self.store.get_run(run_id)

    # ------------------------------------------------------------------ budget
    async def budget_gate(self, workflow_id: str,
                          run_id: Optional[str] = None) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """Global and per-workflow monthly caps, plus the once-per-period soft alert (v1.84.0).

        Factored out of ``check_budget`` so the live support agent can ask the same question
        before every LLM call without a run: every support turn is written to ``orch_spend``
        under ``SUPPORT_SPEND_WORKFLOW``, so the global ceiling and the /admin budget bars cover it.
        """
        eff = await self.effective_settings()
        period = _period(self.clock())
        global_cap = eff["budget.global_monthly_usd"]
        global_spent = await self.store.spend_total(period=period)
        wf_cap = eff.get(f"budget.workflow_monthly_usd.{workflow_id}")
        wf_spent = await self.store.spend_total(period=period, workflow_id=workflow_id)
        info = {"period": period, "global_cap": global_cap, "global_spent": global_spent, "workflow_cap": wf_cap,
                "workflow_spent": wf_spent}
        pct = (global_spent / global_cap * 100.0) if global_cap else 0.0
        soft_alert_key = f"budget.soft_alert:{period}"
        if pct >= eff["budget.soft_alert_pct"] and await self.store.count_actions(None, soft_alert_key) == 0:
            await self.store.append_action({"actor": "orchestrator", "at": self.clock(),
                                            "action": f"budget.soft_alert:{period}", "target": "global",
                                            "outcome": f"{pct:.0f}% of monthly ceiling"})
            await self._notify("budget_soft_threshold", run_id, f"{pct:.0f}% of the global monthly budget used")
        if global_spent >= global_cap:
            return False, "global_monthly_cap", info
        if wf_cap is not None and wf_spent >= wf_cap:
            return False, "workflow_monthly_cap", info
        return True, None, info

    async def check_budget(self, run: Dict[str, Any],
                           step: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        wid = run["workflow_id"]
        ok, reason, info = await self.budget_gate(wid, run_id=run["run_id"])
        eff = await self.effective_settings()
        run_cap = eff.get(f"budget.per_run_usd.{wid}", 0)
        info = {**info, "run_cap": run_cap, "run_spent": run.get("cost_usd") or 0.0}
        if not ok:
            return False, reason, info
        if run_cap and (run.get("cost_usd") or 0.0) >= run_cap:
            return False, "per_run_ceiling", info
        return True, None, info

    async def record_agent_spend(self, workflow_id: str, agent: str, run_id: str, model: Optional[str],
                                 tokens_in: Optional[int], tokens_out: Optional[int], cost_usd: float) -> str:
        """v1.84.0: one ledger row for an LLM call made outside a run (the support agent's
        ``conv:<conversation_id>``). Counted by ``spend_total`` like any run's spend."""
        now = self.clock()
        return await self.store.record_spend({
            "run_id": str(run_id)[:120], "step_id": None, "workflow_id": workflow_id, "agent": agent,
            "model": model, "at": now, "period": _period(now), "tokens_in": tokens_in, "tokens_out": tokens_out,
            "cost_usd": float(cost_usd),
        })

    async def record_spend(self, run: Dict[str, Any], step: Dict[str, Any], cost_usd: float,
                           model: Optional[str] = None, tokens_in: Optional[int] = None,
                           tokens_out: Optional[int] = None) -> None:
        now = self.clock()
        await self.store.record_spend({
            "run_id": run["run_id"], "step_id": step["step_id"], "workflow_id": run["workflow_id"],
            "agent": step["agent"], "model": model, "at": now, "period": _period(now), "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": float(cost_usd),
        })
        fresh = await self.store.get_run(run["run_id"])
        total = (fresh.get("cost_usd") if fresh else 0.0) or 0.0
        await self.store.update_run(run["run_id"], cost_usd=total + float(cost_usd), updated_at=now)
        run["cost_usd"] = total + float(cost_usd)
        await self.store.update_step(step["step_id"], cost_usd=(step.get("cost_usd") or 0.0) + float(cost_usd))

    async def budget_view(self) -> Dict[str, Any]:
        eff = await self.effective_settings()
        period = _period(self.clock())
        global_cap = eff["budget.global_monthly_usd"]
        spent = await self.store.spend_total(period=period)
        by_wf = await self.store.spend_by_workflow(period)
        pct = (spent / global_cap * 100.0) if global_cap else 0.0
        workflows = []
        for wid in self.registry:
            cap = eff.get(f"budget.workflow_monthly_usd.{wid}")
            workflows.append({"workflow_id": wid, "monthly_cap_usd": cap, "spent_usd": by_wf.get(wid, 0.0),
                              "per_run_ceiling_usd": eff.get(f"budget.per_run_usd.{wid}"),
                              "exhausted": cap is not None and by_wf.get(wid, 0.0) >= cap})
        return {
            "period": period,
            "global": {"ceiling_usd": global_cap, "spent_usd": spent, "pct": round(pct, 1),
                       "soft_alert_pct": eff["budget.soft_alert_pct"],
                       "soft_alert_triggered": pct >= eff["budget.soft_alert_pct"],
                       "exhausted": spent >= global_cap},
            "workflows": workflows,
            "notifications": await self.store.count_actions(None, f"budget.soft_alert:{period}"),
        }

    # ------------------------------------------------------------------ notifications (v1: log + counters)
    async def _notify(self, kind: str, run_id: Optional[str], text: str) -> None:
        text = wfdef.redact_untrusted_text(text)[:200]
        logger.warning("orchestrator notification [%s] run=%s: %s", kind, run_id, text)
        await self.store.append_action({"run_id": run_id, "actor": "orchestrator", "at": self.clock(),
                                        "action": f"notify.{kind}", "target": text, "outcome": "recorded"})

    # ------------------------------------------------------------------ views
    async def describe_run(self, run_id: str) -> Dict[str, Any]:
        run = await self.store.get_run(run_id)
        if run is None:
            raise NotFound("run_not_found", "no such run")
        return {
            "run": run,
            "steps": await self.store.get_steps(run_id),
            "approvals": await self.store.list_approvals(status=None, run_id=run_id),
            "events": await self.store.list_events(run_id),
            "actions": await self.store.list_actions(run_id=run_id),
        }

    async def approval_queue(self, status: Optional[str] = "waiting") -> List[Dict[str, Any]]:
        out = []
        for ap in await self.store.list_approvals(status=status):
            run = await self.store.get_run(ap["run_id"])
            step = await self.store.get_step(ap["step_id"])
            item = dict(ap)
            item["workflow_id"] = run["workflow_id"] if run else None
            item["mode"] = run["mode"] if run else None
            item["step_name"] = step["name"] if step else None
            item["advisory_tags"] = ((run or {}).get("summary_json") or {}).get("advisory_tags", [])
            item["instruction_like"] = bool((run or {}).get("instruction_like"))
            art = ap.get("artifact_summary_json") or {}
            item["owner_action"] = art.get("owner_action") if isinstance(art, dict) else None
            # v1.84.0: escalation priority (enterprise > pro > standard) from the run's structured inputs.
            inputs = ((run or {}).get("summary_json") or {}).get("inputs") or {}
            item["priority"] = inputs.get("priority") or "standard"
            item["allowed_decisions"] = _allowed_decisions(ap["kind"], step["kind"] if step else None)
            out.append(item)
        # Paying customers' escalations first, then newest first (list_approvals is already newest-first).
        out.sort(key=lambda it: (PRIORITY_RANK.get(it["priority"], 2), -(it.get("requested_at") or 0.0)))
        return out

    async def public_escalation_view(self, run_id: str) -> Optional[Dict[str, Any]]:
        """v1.84.0: what a customer may see about their escalation — reference, coarse status and the
        owner's reply note (a done_manually decision on the owner_handoff). Nothing else leaves."""
        if not isinstance(run_id, str) or not wfdef.RUN_ID_RE.match(run_id):
            return None
        run = await self.store.get_run(run_id)
        if run is None or run.get("workflow_id") != "support_escalation":
            return None
        owner_reply = None
        for ap in await self.store.list_approvals(status=None, run_id=run_id):
            if ap.get("kind") == "owner_handoff" and ap.get("status") == "done_manually":
                owner_reply = ap.get("decision_note") or None
                break
        return {
            "reference": run_id,
            "status": wfdef.result_for_status(run["status"]),
            "owner_reply": owner_reply,
        }

    async def parked_runs(self) -> List[Dict[str, Any]]:
        out = []
        for run in await self.store.list_runs_by_status(("parked",)):
            steps = await self.store.get_steps(run["run_id"])
            run = dict(run)
            run["prior_outputs"] = [
                {"step": s["name"], "status": s["status"], "outcome": s["outcome"],
                 "output": (s.get("io_summary_json") or {}).get("output")} for s in steps
                if s["status"] in _FINISHED_STEP_STATUSES
            ]
            out.append(run)
        return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _period(ts: float) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts))


def _requester_for(trigger_class: str, actor: Optional[str], inputs: Dict[str, Any]) -> str:
    if trigger_class == "owner_request":
        return "owner"
    if trigger_class == "schedule":
        return "schedule"
    if trigger_class == "customer_message":
        return f"customer:{inputs.get('org_id') or inputs.get('customer_ref') or 'unknown'}"
    if trigger_class == "payment_event":
        # v1.84.0: Stripe-originated; the customer is an opaque org prefix, never a Stripe id.
        return f"stripe:{inputs.get('customer_ref') or 'unknown'}"
    return f"github:{actor or 'unknown'}"


def _missing_envelope_fields(envelope: Any) -> List[str]:
    if not isinstance(envelope, dict):
        return list(ENVELOPE_FIELDS)
    return [f for f in ENVELOPE_FIELDS if f not in envelope]


def _allowed_decisions(kind: str, step_kind: Optional[str]) -> List[str]:
    if kind in _NO_APPROVE_KINDS:
        # v1.84.0: a customer escalation is answered or rejected, never "approved".
        return ["done_manually", "reject"]
    allowed = ["approve", "reject"]
    if step_kind == "external_action":
        allowed.append("edit_approve")
    if kind in _DONE_MANUALLY_KINDS:
        allowed.append("done_manually")
    if kind in _SKIPPABLE_KINDS:
        allowed.append("skip")
    return allowed


_service: Optional[OrchestratorService] = None


def get_orchestrator_service() -> OrchestratorService:
    if _service is None:
        raise RuntimeError("OrchestratorService has not been initialized")
    return _service


def init_orchestrator_service(service: OrchestratorService) -> OrchestratorService:
    global _service
    _service = service
    return _service
