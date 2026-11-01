"""Compliance audit service (v1.88.0): the orchestrator workflow handlers for ``compliance_audit``.

The audit plugs into the orchestrator core instead of being a second subsystem: the run engine
gives it idempotent intake (one run per UTC day for the schedule, a fresh id per owner request),
the append-only action log, the spend ledger and budget gate, and the approval queue. This module
supplies the three step handlers and registers them from the application lifespan
(``install``); ``orchestrator_service.py`` never imports audit code.

Steps (orchestrator_workflows.WORKFLOWS['compliance_audit']):
- ``run_checks``: run the catalogue (audit_checks.run_all), persist the report FIRST, and when any
  check failed return ``gate_result='fail'`` so the engine parks ``review_report`` at an
  ``audit_flag`` owner checkpoint. Pass / warn-only reports never open one.
- ``executive_summary``: a deterministic template; with AUDIT_LLM_SUMMARY on, ONE bounded call on
  the configured cheap model, gated by the orchestrator budget and recorded in ``orch_spend``.
  Any refusal or error keeps the template.
- ``review_report``: completes at once (or after the owner's decision).

Report-only and independent: the audit reads state (billing.db strictly read-only, Stripe through a
distinct restricted read-only key passed per call) and writes nothing but its own report tables.
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

from src.services import audit_checks as checks
from src.services.audit_store import AuditStore
from src.services.orchestrator_service import OrchestratorService, StepContext, _iso

logger = logging.getLogger(__name__)

WORKFLOW_ID = "compliance_audit"
LLM_TIMEOUT_S = 40
SUMMARY_MAX_CHARS = 1500
LLM_INPUT_MAX_CHARS = 6000
STRIPE_LIST_CAP = 1000

SUMMARY_SYSTEM_PROMPT = (
    "You write a short executive summary (at most 120 words, plain text, no markdown) of a weekly "
    "compliance audit report for the owner of a small SaaS. You are given only a table of check ids, "
    "statuses and short reasons. State the overall result, list failing checks first, then warnings, "
    "and mention how many checks could not run. Do not give legal advice, do not claim compliance with "
    "any law or standard, and do not invent facts that are not in the table."
)


def _get(obj: Any, key: str) -> Any:
    """Read a field from a Stripe object or a plain dict (``items`` collides with dict.items)."""
    try:
        return obj[key]
    except (KeyError, TypeError, AttributeError, IndexError):
        return getattr(obj, key, None)


def _id_of(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value if isinstance(value, str) else _get(value, "id")


# ---------------------------------------------------------------------------
# Adapters to the outside world (injected into AuditContext; replaced by fakes in tests)
# ---------------------------------------------------------------------------

async def stripe_list_subscriptions(key: str) -> List[Dict[str, Any]]:
    """All subscriptions through the restricted read-only key, normalised to plain dicts.

    The key is passed per call (``api_key=``); the global ``stripe.api_key`` used by checkout is never
    touched. Paging stops at STRIPE_LIST_CAP.
    """
    import stripe

    def fetch() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        page = stripe.Subscription.list(api_key=key, status="all", limit=100)
        for sub in page.auto_paging_iter():
            items = _get(_get(sub, "items"), "data") or []
            price_id = _id_of(_get(items[0], "price")) if items else None
            out.append({"id": _get(sub, "id"), "customer": _id_of(_get(sub, "customer")),
                        "status": _get(sub, "status"), "price_id": price_id})
            if len(out) >= STRIPE_LIST_CAP:
                break
        return out

    return await asyncio.to_thread(fetch)


async def stripe_list_webhook_endpoints(key: str) -> List[Dict[str, Any]]:
    import stripe

    def fetch() -> List[Dict[str, Any]]:
        page = stripe.WebhookEndpoint.list(api_key=key, limit=100)
        return [{"status": _get(e, "status"), "enabled_events": list(_get(e, "enabled_events") or [])}
                for e in page.auto_paging_iter()]

    return await asyncio.to_thread(fetch)


async def load_data_quality() -> Dict[str, Any]:
    from mcp.tools.get_data_quality import GetDataQualityTool
    return await GetDataQualityTool().execute({})


def _default_litestream_path() -> Path:
    prod = Path("/etc/litestream.yml")
    return prod if prod.exists() else checks.PROJECT_ROOT / "litestream.yml"


async def _price_call(model: str, tokens_in: int, tokens_out: int, settings: Any) -> float:
    from src.services.support_budget import estimate_turn_cost
    pricing: Any = None
    try:
        from src.services.pricing_aggregator import get_pricing_aggregator
        pricing = await (await get_pricing_aggregator()).find_model_pricing(model)
    except Exception:  # nosec B110
        pricing = None
    return estimate_turn_cost(tokens_in, tokens_out, pricing, settings)


# ---------------------------------------------------------------------------
# Summary template
# ---------------------------------------------------------------------------

def template_summary(report: Dict[str, Any]) -> str:
    cks = report.get("checks") or []
    counts = report.get("counts") or {}
    ids = lambda status: [c["id"] for c in cks if c["status"] == status]  # noqa: E731
    parts = [f"Compliance audit {_iso(report.get('created_at') or 0)[:10]}: {str(report.get('status')).upper()}. "
             f"{counts.get('pass', 0)} passed, {counts.get('warn', 0)} warnings, {counts.get('fail', 0)} failed, "
             f"{counts.get('n_a', 0)} not applicable."]
    if ids("fail"):
        parts.append("Failing: " + ", ".join(ids("fail")) + ".")
    if ids("warn"):
        parts.append("Warnings: " + ", ".join(ids("warn")) + ".")
    if ids("n/a"):
        parts.append("Could not run: " + ", ".join(ids("n/a")) + ".")
    return " ".join(parts)


def _llm_table(report: Dict[str, Any]) -> str:
    lines = []
    for c in report.get("checks") or []:
        reason = (c.get("evidence") or {}).get("reason") or (c.get("evidence") or {}).get("problem") or ""
        lines.append(f"{c['id']} | {c['status']} | {c.get('severity')} | {str(reason)[:80]}")
    return "\n".join(lines)[:LLM_INPUT_MAX_CHARS]


class AuditService:
    """Step handlers plus the owner-facing entry points. One instance per process."""

    def __init__(self, store: AuditStore, orchestrator: OrchestratorService, settings: Any,
                 clock: Callable[[], float] = time.time, *,
                 stripe_subscriptions: Optional[checks.StripeLister] = stripe_list_subscriptions,
                 stripe_webhooks: Optional[checks.StripeLister] = stripe_list_webhook_endpoints,
                 data_quality: Optional[checks.DataQualityLoader] = load_data_quality,
                 llm_factory: Optional[Callable[[str, str, str], Any]] = None,
                 price_call: Optional[Callable[..., Awaitable[float]]] = None,
                 litestream_config_path: Optional[Path] = None,
                 legal_docs_dir: Optional[Path] = None,
                 checklist_path: Optional[Path] = None,
                 whats_new_path: Optional[Path] = None,
                 env: Optional[Mapping[str, str]] = None) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.settings = settings
        self.clock = clock
        self._stripe_subscriptions = stripe_subscriptions
        self._stripe_webhooks = stripe_webhooks
        self._data_quality = data_quality
        self._llm_factory = llm_factory
        self._price_call = price_call or _price_call
        self._litestream = litestream_config_path
        self._legal_docs_dir = legal_docs_dir
        self._checklist_path = checklist_path
        self._whats_new_path = whats_new_path
        self._env = env

    # ------------------------------------------------------------------ wiring
    def install(self) -> None:
        """Register the handlers with the orchestrator (called from the lifespan)."""
        self.orchestrator.register_handler(WORKFLOW_ID, "run_checks", self.run_checks)
        self.orchestrator.register_handler(WORKFLOW_ID, "executive_summary", self.executive_summary)
        self.orchestrator.register_handler(WORKFLOW_ID, "review_report", self.review_report)

    def build_context(self, inputs: Dict[str, Any]) -> checks.AuditContext:
        s = self.settings
        kwargs: Dict[str, Any] = {}
        if self._legal_docs_dir is not None:
            kwargs["legal_docs_dir"] = self._legal_docs_dir
        if self._checklist_path is not None:
            kwargs["checklist_path"] = self._checklist_path
        if self._whats_new_path is not None:
            kwargs["whats_new_path"] = self._whats_new_path
        if self._env is not None:
            kwargs["env"] = self._env
        repo_checks = inputs.get("repo_checks")
        return checks.AuditContext(
            settings=s, now=self.clock(), orchestrator=self.orchestrator,
            billing_db_path=getattr(s, "billing_db_path", None),
            repo_checks=dict(repo_checks) if isinstance(repo_checks, dict) and repo_checks else None,
            repo_version=inputs.get("repo_version"), repo_sha=inputs.get("sha"),
            stripe_list_subscriptions=self._stripe_subscriptions,
            stripe_list_webhook_endpoints=self._stripe_webhooks,
            load_data_quality=self._data_quality,
            litestream_config_path=self._litestream or _default_litestream_path(),
            disabled=checks.parse_disabled(getattr(s, "audit_disabled_checks", "")),
            **kwargs,
        )

    # ------------------------------------------------------------------ step: run_checks
    async def run_checks(self, ctx: StepContext) -> Dict[str, Any]:
        run = ctx.run
        started = self.clock()
        existing = await self.store.get_report_by_run(run["run_id"])
        if existing is not None:  # a retried step: never a second report for one run
            report_id, status, counts = existing["report_id"], existing["status"], existing["counts"] or {}
            results = (await self.store.get_report(report_id) or {}).get("checks", [])
        else:
            actx = self.build_context(ctx.inputs)
            actx.previous_numbers = await self.store.previous_numbers(before_ts=actx.now)
            results = await checks.run_all(actx)
            status = checks.overall_status(results)
            counts = checks.count_statuses(results)
            s = self.settings
            report = {
                "run_id": run["run_id"], "created_at": actx.now, "trigger_class": run.get("trigger_class"),
                "status": status, "counts": counts, "numbers": actx.numbers,
                "config": {
                    "disabled_checks": sorted(actx.disabled),
                    "stripe_configured": checks._present(getattr(s, "stripe_audit_key", None)),
                    "llm_summary": bool(getattr(s, "audit_llm_summary", False)),
                    "summary_model": str(getattr(s, "audit_summary_model", "")),
                    "repo_checks_supplied": bool(actx.repo_checks), "repo_version": actx.repo_version,
                    # repo.* verdicts are reported by the intake event, not verified here: keep provenance
                    "repo_checks_origin": "reported by intake event (unverified by this service)",
                    "repo_checks_source_ref": run.get("source_ref"),
                    "repo_checks_delivery_ref": (run.get("inputs_json") or {}).get("delivery_ref")
                    if isinstance(run.get("inputs_json"), dict) else None,
                    "deployed_version": getattr(s, "app_version", None),
                    "thresholds": {"stale_price_pct_warn": getattr(s, "audit_stale_price_pct_warn", None),
                                   "escalation_max_age_days": getattr(s, "audit_escalation_max_age_days", None)},
                },
                "duration_ms": int((self.clock() - started) * 1000),
                "summary_source": "deterministic",
            }
            report["summary_text"] = template_summary({**report, "checks": results})
            report_id = await self.store.save_report(report, results)
            await self.store.purge_expired(now=actx.now)
        # Data only: the run remembers its report so the owner's approval card can link to it.
        fresh = await self.orchestrator.store.get_run(run["run_id"])
        summary = dict((fresh or run).get("summary_json") or {})
        summary["report_id"] = report_id
        await self.orchestrator.store.update_run(run["run_id"], summary_json=summary, updated_at=self.clock())
        failing = [r["id"] for r in results if r["status"] == "fail"]
        output: Dict[str, Any] = {
            "decision": f"audit report {report_id}: {status}", "data_as_of": _iso(self.clock()),
            "report_id": report_id, "status": status, "counts": counts,
        }
        if failing:
            output["gate_result"] = "fail"
            output["gate_reason"] = f"report {report_id}; {len(failing)} failing: " + ", ".join(failing)
        return output

    # ------------------------------------------------------------------ step: executive_summary
    async def executive_summary(self, ctx: StepContext) -> Dict[str, Any]:
        report = await self.store.get_report_by_run(ctx.run["run_id"])
        if report is None:
            return {"decision": "no report to summarise", "data_as_of": _iso(self.clock())}
        full = await self.store.get_report(report["report_id"])
        template = template_summary(full)
        text, source = template, "deterministic"
        extra: Dict[str, Any] = {}
        if getattr(self.settings, "audit_llm_summary", False):
            text, source, extra = await self._llm_summary(ctx, full, template)
        await self.store.set_summary(report["report_id"], text, source)
        return {"decision": f"executive summary recorded ({source})", "data_as_of": _iso(self.clock()),
                "summary_source": source, **extra}

    async def _llm_summary(self, ctx: StepContext, report: Dict[str, Any], template: str):
        s = self.settings
        provider = str(getattr(s, "agent_llm_provider", "anthropic"))
        api_key = getattr(s, "anthropic_api_key" if provider == "anthropic" else "openai_api_key", None)
        if not checks._present(api_key):
            return template, "llm_skipped:no_api_key", {}
        ok, reason, _info = await self.orchestrator.check_budget(ctx.run, ctx.step)
        if not ok:
            return template, f"llm_skipped:budget:{reason}", {}
        model = str(getattr(s, "audit_summary_model", "claude-haiku-5-5"))
        try:
            factory = self._llm_factory
            if factory is None:
                from agent.llm_backend import create_llm_backend as factory
            backend = factory(provider, checks._secret(api_key), model)
            response = await asyncio.wait_for(
                backend.complete(messages=[{"role": "user", "content": _llm_table(report)}], tools=[],
                                 system=SUMMARY_SYSTEM_PROMPT),
                timeout=LLM_TIMEOUT_S)
        except Exception as exc:
            logger.warning("audit summary LLM call skipped: %s", type(exc).__name__)
            return template, f"llm_skipped:error:{type(exc).__name__}", {}
        text = (getattr(response, "content", "") or "").strip()[:SUMMARY_MAX_CHARS]
        usage = getattr(response, "usage", None) or {}
        tokens_in, tokens_out = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
        try:
            cost = await self._price_call(model, tokens_in, tokens_out, s)
        except Exception:
            cost = 0.0
        # The call happened, so its spend is recorded even if the text turns out to be unusable.
        await self.orchestrator.record_spend(ctx.run, ctx.step, cost, model=model, tokens_in=tokens_in,
                                             tokens_out=tokens_out)
        if not text:
            return template, "llm_skipped:empty_response", {}
        return text, "llm", {}

    # ------------------------------------------------------------------ step: review_report
    async def review_report(self, ctx: StepContext) -> Dict[str, Any]:
        reviewed = bool((ctx.step.get("io_summary_json") or {}).get("gate_override"))
        return {"decision": "report reviewed by owner" if reviewed else "no failing checks; nothing to review",
                "data_as_of": _iso(self.clock())}

    # ------------------------------------------------------------------ owner entry point
    async def run_now(self, request_id: str) -> Dict[str, Any]:
        """Start an on-demand audit (owner request). The id is the idempotency key."""
        result = await self.orchestrator.intake_owner_request(WORKFLOW_ID, request_id, {})
        report = await self.store.get_report_by_run(result["run_id"]) if result.get("run_id") else None
        return {**result, "report_id": report["report_id"] if report else None,
                "report_status": report["status"] if report else None}


_service: Optional[AuditService] = None


def get_audit_service() -> AuditService:
    if _service is None:
        raise RuntimeError("AuditService has not been initialized")
    return _service


def init_audit_service(service: AuditService) -> AuditService:
    global _service
    _service = service
    return _service
