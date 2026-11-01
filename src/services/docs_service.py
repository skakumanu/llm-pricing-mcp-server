"""Document writer service (v1.89.0): the orchestrator workflow handlers for ``document_generate``.

Plugs into the orchestrator core the way the compliance audit does: the run engine gives it idempotent
intake, the append-only action log, the spend ledger, the budget gate and the approval queue; this module
supplies the step handlers and registers them from the application lifespan (``install``).
``orchestrator_service.py`` never imports docs code.

Steps (orchestrator_workflows.WORKFLOWS['document_generate']):
- ``gather``: validate params, make the product data calls, build the provenance manifest and insert the
  artefact row FIRST.
- ``draft``: ONE bounded model call (default haiku, sonnet when requested) after ``check_budget``, spend
  recorded in ``orch_spend``. A refused budget, missing key, backend error, timeout, unparseable answer or a
  data-only request keeps the deterministic template and is labelled on the document.
- ``verify_render``: every number in the model's prose is cross-checked against the manifest (untraceable
  numbers are removed and counted), the document is structurally validated and trial-rendered, then stored.
  A failure stores no document and parks the run.
- ``owner_review``: the existing mandatory ``publish`` checkpoint (engine).
- ``publish``: marks the artefact published. Nothing is sent anywhere. A rejection closes the run and the
  artefact is marked ``discarded`` the next time it is read.
"""
import asyncio
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.services import docs_catalogue as cat
from src.services import docs_render, docs_sources, docs_verify
from src.services.docs_store import DocsStore
from src.services.orchestrator_service import OrchestratorService, StepBlocked, StepContext, _iso

logger = logging.getLogger(__name__)

WORKFLOW_ID = "document_generate"
AGENT = "document_agent"
LLM_TIMEOUT_S = 90
DRAFT_MAX_CHARS = 6000
LLM_INPUT_MAX_CHARS = 8000
MAX_ARTIFACT_BYTES = 400_000
FORMATS = ("html", "markdown", "both")

DRAFT_SYSTEM_PROMPT = (
    "You write the narrative parts of a business document for the owner of a small SaaS. You are given only a "
    "table of figures (id, value, unit, label, status, price_as_of) and, for release briefs, the release "
    "entries. Answer with ONE JSON object and nothing else: {\"summary\": \"<=90 words, plain text\", "
    "\"notes\": {\"<section id>\": \"1-2 sentences of speaker notes\"}}. Use only numbers that appear in the "
    "table, written exactly as shown there. Never state a price for a model whose status is withheld, "
    "unpriced or retired; say 'price not available'. Do not invent facts, do not give legal or financial "
    "advice, and do not mention anything that is not in the table."
)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.S)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


async def _price_call(model: str, tokens_in: int, tokens_out: int, settings: Any) -> float:
    """Price a call through the product's own catalogue (the pricing MCP's rate card for that model)."""
    from src.services.support_budget import estimate_turn_cost
    pricing: Any = None
    try:
        from src.services.pricing_aggregator import get_pricing_aggregator
        pricing = await (await get_pricing_aggregator()).find_model_pricing(model)
    except Exception:  # nosec B110
        pricing = None
    return estimate_turn_cost(tokens_in, tokens_out, pricing, settings)


def _present(secret: Any) -> bool:
    if secret is None:
        return False
    value = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
    return bool(str(value).strip())


def _secret(secret: Any) -> str:
    return secret.get_secret_value() if hasattr(secret, "get_secret_value") else str(secret)


class DocsService:
    """Step handlers plus the owner-facing entry points. One instance per process."""

    def __init__(self, store: DocsStore, orchestrator: OrchestratorService, settings: Any,
                 clock: Callable[[], float] = time.time, *,
                 sources: Optional[docs_sources.Sources] = None,
                 llm_factory: Optional[Callable[[str, str, str], Any]] = None,
                 price_call: Optional[Callable[..., Awaitable[float]]] = None,
                 whats_new_path: Optional[Path] = None,
                 audit_store: Any = None) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.settings = settings
        self.clock = clock
        self._llm_factory = llm_factory
        self._price_call = price_call or _price_call
        self._audit_store = audit_store
        self.sources = sources or self._default_sources(whats_new_path)

    # ------------------------------------------------------------------ wiring
    def _default_sources(self, whats_new_path: Optional[Path]) -> docs_sources.Sources:
        import src
        orch = self.orchestrator

        async def audit_report() -> Optional[Dict[str, Any]]:
            store = self._audit_store
            if store is None:
                from src.services.audit_store import get_audit_store
                store = get_audit_store()
            return await store.latest_report()

        async def list_runs(since: float) -> List[Dict[str, Any]]:
            return await orch.store.list_runs(since=since, limit=1000)

        async def open_approvals() -> int:
            return len(await orch.store.list_approvals(status="waiting"))

        text = (lambda: whats_new_path.read_text(encoding="utf-8")) if whats_new_path else None
        return docs_sources.Sources(whats_new_text=text, audit_report=audit_report, budget_view=orch.budget_view,
                                    list_runs=list_runs, open_approvals=open_approvals, app_version=src.__version__)

    def install(self) -> None:
        """Register the handlers with the orchestrator (called from the lifespan)."""
        self.orchestrator.register_handler(WORKFLOW_ID, "gather", self.gather)
        self.orchestrator.register_handler(WORKFLOW_ID, "draft", self.draft)
        self.orchestrator.register_handler(WORKFLOW_ID, "verify_render", self.verify_render)
        self.orchestrator.register_handler(WORKFLOW_ID, "publish", self.publish)

    # ------------------------------------------------------------------ helpers
    async def _record(self, run_id: Optional[str], action: str, outcome: str, target: Optional[str] = None,
                      trigger: Optional[str] = None, provenance: Optional[Dict[str, Any]] = None) -> None:
        """Operator generations and downloads are logged as action records (counts and ids only)."""
        try:
            await self.orchestrator.store.append_action({
                "run_id": run_id, "actor": AGENT, "at": self.clock(), "action": action[:60],
                "target": target, "trigger": trigger, "outcome": outcome[:100],
                "provenance_json": provenance or None})
        except Exception as exc:  # an audit-log failure must not become a 500
            logger.warning("docs action %s not recorded: %s", action, type(exc).__name__)

    async def _set_summary(self, run_id: str, **fields: Any) -> None:
        fresh = await self.orchestrator.store.get_run(run_id)
        summary = dict((fresh or {}).get("summary_json") or {})
        summary.update(fields)
        await self.orchestrator.store.update_run(run_id, summary_json=summary, updated_at=self.clock())

    def _model(self, premium: bool) -> str:
        s = self.settings
        return str(getattr(s, "docs_premium_model" if premium else "docs_default_model",
                           "claude-sonnet-5-5" if premium else "claude-haiku-5-5"))

    # ------------------------------------------------------------------ step: gather
    async def gather(self, ctx: StepContext) -> Dict[str, Any]:
        run = ctx.run
        existing = await self.store.get_by_run(run["run_id"])
        if existing is not None and existing["status"] != "failed":  # a retried step: never a second artefact
            return {"decision": f"artefact {existing['artifact_id']} already gathering",
                    "data_as_of": _iso(self.clock()), "artifact_id": existing["artifact_id"]}
        doc_type = str(ctx.inputs.get("doc_type") or "")
        try:
            t = cat.get_type(doc_type)
            params, defaulted = cat.validate_params(doc_type, ctx.inputs.get("doc_params") or {})
        except cat.ParamError as exc:
            raise StepBlocked(f"invalid request: {exc}")
        fmt = ctx.inputs.get("doc_format") if ctx.inputs.get("doc_format") in FORMATS else "both"
        await self.store.purge_expired(now=self.clock())
        try:
            manifest = await docs_sources.gather(doc_type, params, self.sources, now=self.clock())
        except cat.ParamError as exc:
            raise StepBlocked(f"invalid request: {exc}")
        except Exception as exc:
            logger.warning("document gather failed: %s", type(exc).__name__)
            artifact_id = await self.store.insert_artifact(run["run_id"], doc_type, t.kind, fmt, t.title,
                                                           {"resolved": params, "defaulted": defaulted},
                                                           {}, now=self.clock())
            await self.store.update(artifact_id, status="failed", error=f"gather failed: {type(exc).__name__}")
            raise StepBlocked(f"gather failed: {type(exc).__name__}")
        artifact_id = await self.store.insert_artifact(
            run["run_id"], doc_type, t.kind, fmt, t.title, {"resolved": params, "defaulted": defaulted},
            manifest.to_dict(), now=self.clock())
        await self._set_summary(run["run_id"], artifact_id=artifact_id, doc_type=doc_type, title=t.title,
                                figures=len(manifest.figures))
        await self._record(run["run_id"], "docs.generate", "gathered", target=artifact_id, trigger=doc_type,
                           provenance={"figures": len(manifest.figures), "calls": len(manifest.calls),
                                       "data_only_requested": bool(ctx.inputs.get("data_only"))})
        return {"decision": f"gathered {len(manifest.figures)} figures for {doc_type}",
                "data_as_of": manifest.generated_at, "artifact_id": artifact_id, "figures": len(manifest.figures)}

    # ------------------------------------------------------------------ step: draft
    async def draft(self, ctx: StepContext) -> Dict[str, Any]:
        row = await self.store.get_by_run(ctx.run["run_id"])
        if row is None or row["status"] != "gathering":
            raise StepBlocked("no artefact to draft")
        if row.get("llm"):  # retried: keep the first outcome (one ledger row per call)
            return {"decision": f"draft already recorded ({row['llm'].get('mode')})", "data_as_of": _iso(self.clock())}
        manifest = cat.Manifest.from_dict(row["manifest"])
        llm = await self._llm_draft(ctx, manifest)
        await self.store.update(row["artifact_id"], llm_json=llm)
        mode = "llm" if llm["mode"] == "llm" else f"data_only:{llm['reason']}"
        await self._set_summary(ctx.run["run_id"], llm_mode=mode)
        return {"decision": f"draft recorded ({mode})", "data_as_of": _iso(self.clock()), "llm_mode": mode}

    async def _llm_draft(self, ctx: StepContext, manifest: cat.Manifest) -> Dict[str, Any]:
        s = self.settings
        premium = bool(ctx.inputs.get("doc_premium"))
        model = self._model(premium)
        requested = ctx.inputs.get("data_only")
        data_only = bool(requested) if requested is not None else bool(getattr(s, "docs_data_only_default", False))
        fallback = {"mode": "data_only", "model": None, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "prose": None}
        if data_only:
            return {**fallback, "reason": "requested"}
        provider = str(getattr(s, "agent_llm_provider", "anthropic"))
        api_key = getattr(s, "anthropic_api_key" if provider == "anthropic" else "openai_api_key", None)
        if not _present(api_key):
            return {**fallback, "reason": "no_api_key"}
        ok, reason, _info = await self.orchestrator.check_budget(ctx.run, ctx.step)
        if not ok:
            return {**fallback, "reason": f"budget:{reason}"}
        started = {"model": model}
        try:
            factory = self._llm_factory
            if factory is None:
                from agent.llm_backend import create_llm_backend as factory
            backend = factory(provider, _secret(api_key), model)
            content = docs_sources.llm_table(manifest, LLM_INPUT_MAX_CHARS)
            response = await asyncio.wait_for(
                backend.complete(messages=[{"role": "user", "content": content}], tools=[],
                                 system=DRAFT_SYSTEM_PROMPT),
                timeout=LLM_TIMEOUT_S)
        except Exception as exc:
            logger.warning("document draft call skipped: %s", type(exc).__name__)
            return {**fallback, "reason": f"error:{type(exc).__name__}", **{"attempted_model": model}}
        usage = getattr(response, "usage", None) or {}
        tokens_in, tokens_out = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
        try:
            cost = await self._price_call(model, tokens_in, tokens_out, s)
        except Exception:
            cost = 0.0
        # The call happened, so its spend is recorded even if the text turns out to be unusable.
        await self.orchestrator.record_spend(ctx.run, ctx.step, cost, model=model, tokens_in=tokens_in,
                                             tokens_out=tokens_out)
        info = {**started, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost}
        data = _extract_json(str(getattr(response, "content", "") or "")[:DRAFT_MAX_CHARS * 2])
        summary = data.get("summary") if data else None
        if not isinstance(summary, str) or not summary.strip():
            return {**fallback, **info, "reason": "unusable_response"}
        notes = data.get("notes") if isinstance(data.get("notes"), dict) else {}
        return {"mode": "llm", "reason": None, **info,
                "prose": {"summary": summary[:DRAFT_MAX_CHARS], "notes": {str(k): str(v)[:600] for k, v in notes.items()}}}

    # ------------------------------------------------------------------ step: verify_render
    async def verify_render(self, ctx: StepContext) -> Dict[str, Any]:
        run_id = ctx.run["run_id"]
        row = await self.store.get_by_run(run_id)
        if row is None or row["status"] not in ("gathering", "draft"):
            raise StepBlocked("no artefact to verify")
        manifest = cat.Manifest.from_dict(row["manifest"])
        llm = row.get("llm") or {"mode": "data_only", "reason": "requested", "prose": None}
        params = row["params"]["resolved"]
        defaulted = row["params"]["defaulted"]
        corrections = 0
        prose = None
        if llm.get("mode") == "llm" and llm.get("prose"):
            prose, corrections = docs_verify.verify_prose_block(llm["prose"], manifest)
            if not prose["summary"].strip():  # every sentence was untraceable: the template summary stands
                prose["summary"] = ""
        import src
        product = str(getattr(self.settings, "app_name", "LLM Pricing API"))
        doc = cat.build_document(row["doc_type"], params, defaulted, manifest, prose, llm, product, src.__version__)
        try:
            docs_verify.validate_document(doc, manifest)
            sizes = {fmt: len(render(doc, "draft").encode("utf-8"))
                     for fmt, render in (("html", docs_render.render_html), ("markdown", docs_render.render_markdown))}
            if max(sizes.values()) > MAX_ARTIFACT_BYTES:
                raise docs_verify.ValidationError("rendered document exceeds the size limit")
        except Exception as exc:
            reason = f"validation failed: {str(exc)[:200]}"
            await self.store.update(row["artifact_id"], status="failed", error=reason, manifest_json={},
                                    llm_json=None, document_json=None)
            await self._record(run_id, "docs.generate", "failed", target=row["artifact_id"], trigger=row["doc_type"])
            raise StepBlocked(reason)
        meta = manifest.to_dict()
        meta["corrections_count"] = corrections
        await self.store.update(row["artifact_id"], status="draft", document_json=doc, title=doc["title"],
                                corrections=corrections, size_bytes=max(sizes.values()), manifest_json=meta)
        mode = "llm" if llm.get("mode") == "llm" else f"data_only:{llm.get('reason')}"
        await self._set_summary(run_id, title=doc["title"][:200], corrections=corrections, llm_mode=mode)
        return {"decision": f"document verified ({corrections} correction(s)) and stored as a draft",
                "data_as_of": _iso(self.clock()), "corrections": corrections}

    # ------------------------------------------------------------------ step: publish
    async def publish(self, ctx: StepContext) -> Dict[str, Any]:
        row = await self.store.get_by_run(ctx.run["run_id"])
        if row is None or row["status"] not in ("draft", "published"):
            raise StepBlocked("no draft artefact to publish")
        await self.store.update(row["artifact_id"], status="published", published_at=self.clock())
        await self._record(ctx.run["run_id"], "docs.publish", "published", target=row["artifact_id"],
                           trigger=row["doc_type"])
        return {"decision": f"artefact {row['artifact_id']} published (stored only; nothing was sent)",
                "data_as_of": _iso(self.clock())}

    # ------------------------------------------------------------------ owner entry points
    async def generate(self, doc_type: str, params: Dict[str, Any], fmt: str = "both",
                       data_only: Optional[bool] = None, premium: bool = False,
                       request_id: Optional[str] = None) -> Dict[str, Any]:
        """Start one generation (operator request). Raises ``cat.ParamError`` before any run exists."""
        cat.get_type(doc_type)
        resolved, defaulted = cat.validate_params(doc_type, params)
        request_id = request_id or time.strftime("doc-%Y%m%dT%H%M%S", time.gmtime()) + "-" + secrets.token_hex(3)
        inputs: Dict[str, Any] = {"doc_type": doc_type, "doc_params": params or {}, "doc_format": fmt,
                                  "doc_premium": bool(premium)}
        if data_only is not None:
            inputs["data_only"] = bool(data_only)
        result = await self.orchestrator.intake_owner_request(WORKFLOW_ID, request_id, inputs)
        run_id = result.get("run_id")
        row = await self.store.get_by_run(run_id) if run_id else None
        approval = None
        if run_id:
            for a in await self.orchestrator.store.list_approvals(status="waiting", run_id=run_id):
                approval = {"approval_id": a["approval_id"], "kind": a["kind"]}
        run = await self.orchestrator.store.get_run(run_id) if run_id else None
        view = await self.reconcile(row) if row else None
        failed = view is None or view["status"] == "failed"
        failure = ((view or {}).get("error") or (run or {}).get("parked_reason")) if failed else None
        return {**result, "artifact_id": view["artifact_id"] if view else None,
                "artifact_status": view["status"] if view else None,
                "doc_type": doc_type, "resolved_params": resolved, "defaulted_params": defaulted,
                "title": view["title"] if view else None,
                "corrections": view["corrections"] if view else None,
                "llm_mode": self.llm_mode(view),
                "manifest": self.manifest_summary(view),
                "outline": [s["id"] for s in ((view or {}).get("document") or {}).get("sections", [])],
                "approval": approval, "failure": failure}

    @staticmethod
    def llm_mode(row: Optional[Dict[str, Any]]) -> Optional[str]:
        llm = (row or {}).get("llm")
        if not llm:
            return None
        return "llm" if llm.get("mode") == "llm" else f"data_only:{llm.get('reason')}"

    @staticmethod
    def manifest_summary(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        m = (row or {}).get("manifest")
        if not m:
            return None
        return {"figures": len(m.get("figures", [])), "data_quality": m.get("data_quality", {}),
                "calls": m.get("calls", []), "corrections_count": m.get("corrections_count", 0)}

    async def reconcile(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """A rejected publish checkpoint closes the run without telling the store: mark the artefact discarded."""
        if row["status"] == "draft":
            run = await self.orchestrator.store.get_run(row["run_id"])
            if run and run.get("status") == "closed" and run.get("result") == "deliberately_not_done":
                await self.store.update(row["artifact_id"], status="discarded")
                row = {**row, "status": "discarded"}
        return row

    async def list_artifacts(self, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        # status=None on purpose: reconcile() may flip draft -> discarded, so filter after reconciling.
        fetch = 1000 if status else limit
        rows = [await self.reconcile(r) for r in await self.store.list(limit=fetch, status=None)]
        if status:
            rows = [r for r in rows if r["status"] == status]
        return rows[:limit]

    async def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        row = await self.store.get(artifact_id)
        return await self.reconcile(row) if row else None

    async def render(self, row: Dict[str, Any], fmt: str) -> Tuple[str, str, str]:
        """(body, content type, filename) for one artefact, rendered with its current status banner."""
        doc = row.get("document")
        if not doc:
            raise LookupError("artefact has no document")
        if fmt == "html":
            body, ext = docs_render.render_html(doc, row["status"]), "html"
        else:
            body, ext = docs_render.render_markdown(doc, row["status"]), "md"
        stamp = time.strftime("%Y%m%d", time.gmtime(row["created_at"]))
        name = f"{row['doc_type'].replace('_', '-')}-{stamp}-{row['artifact_id']}.{ext}"
        await self._record(row["run_id"], "docs.download", "served", target=row["artifact_id"], trigger=row["doc_type"],
                           provenance={"format": ext})
        return body, docs_render.content_type(fmt), name


_service: Optional[DocsService] = None


def get_docs_service() -> DocsService:
    if _service is None:
        raise RuntimeError("DocsService has not been initialized")
    return _service


def init_docs_service(service: DocsService) -> DocsService:
    global _service
    _service = service
    return _service
