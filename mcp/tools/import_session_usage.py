"""MCP Tool: Bulk-import a completed session's usage in a single call."""
import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple

from src.config.settings import settings
from src.services.budget_alerts import get_budget_alert_service
from src.services.pricing_aggregator import PricingAggregatorService
from src.services.token_counter import count_tokens
from src.services.usage_tracker import get_usage_tracker

# Matches UsageBatchRequest's existing cap on POST /usage/batch.
MAX_ENTRIES = 500


class ImportSessionUsageTool:
    """Retroactively load a completed session's usage in one call.

    Solves the case record_usage can't: a calling agent (e.g. VS Code Copilot Chat)
    that has a session's turn history sitting in its own local store but never
    instrumented that session live. Loops the same per-event path record_usage.py
    uses — PricingAggregatorService.find_model_pricing() then
    UsageTrackerService.record_event() — once per entry, rather than building a
    second ingestion pipeline, so rows land in the same usage_events table and are
    immediately readable via analyze_session_usage / GET /usage/session/{session_id}.

    Each entry resolves its input and output sides independently, each either from
    an exact integer count or by estimating from raw text via this server's existing
    token_counter.count_tokens() (the same estimator predict_cost.py already uses).
    An entry is marked is_estimated=true if either side was text-derived. A
    malformed entry (neither a count nor text on either side, or an unresolvable
    model_name) is reported as its own per-entry failure and does not fail the rest
    of the batch — mirroring POST /usage/batch's best-effort semantics.

    Idempotent like record_usage: when request_id is omitted, a deterministic one is
    derived from session_id/model_name/occurred_at/token-or-text source, so
    resubmitting an identical batch is safely a no-op via the existing
    (org_id, request_id) UNIQUE index.
    """

    def __init__(self):
        self.service = PricingAggregatorService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        session_id = arguments.get("session_id")
        if not session_id or not isinstance(session_id, str) or not session_id.strip():
            return {"success": False, "error": "session_id is required and must be a non-empty string"}

        entries = arguments.get("entries")
        if not isinstance(entries, list) or not entries:
            return {"success": False, "error": "entries is required and must be a non-empty list"}
        if len(entries) > MAX_ENTRIES:
            return {"success": False, "error": f"entries exceeds the maximum batch size of {MAX_ENTRIES}"}

        org_id = arguments.get("org_id")

        try:
            tracker = get_usage_tracker()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        results: List[Dict[str, Any]] = []
        recorded_exact = 0
        recorded_estimated = 0
        duplicates = 0
        failed = 0

        any_recorded = False
        for index, entry in enumerate(entries):
            outcome = await self._process_entry(tracker, session_id, org_id, index, entry)
            results.append(outcome)
            if not outcome["success"]:
                failed += 1
                continue
            if outcome["duplicate"]:
                duplicates += 1
            elif outcome["is_estimated"]:
                recorded_estimated += 1
                any_recorded = True
            else:
                recorded_exact += 1
                any_recorded = True

        if any_recorded:
            # Best-effort, mirrors record_usage.py: a bulk/backfill import can push an
            # org over a configured budget threshold just as a live record_usage call
            # can, so check once per batch rather than skipping it entirely.
            try:
                budget_alerts = get_budget_alert_service()
                await budget_alerts.check_and_fire(tracker, secret=settings.webhook_secret)
            except Exception:
                pass  # nosec B110 — best-effort; usage was already recorded successfully

        return {
            "success": True,
            "session_id": session_id,
            "results": results,
            "summary": {
                "total_entries": len(entries),
                "recorded_exact": recorded_exact,
                "recorded_estimated": recorded_estimated,
                "duplicates": duplicates,
                "failed": failed,
            },
        }

    async def _process_entry(
        self,
        tracker,
        session_id: str,
        org_id: Optional[str],
        index: int,
        entry: Any,
    ) -> Dict[str, Any]:
        if not isinstance(entry, dict):
            return {"index": index, "success": False, "error": "entry must be an object"}

        model_name = entry.get("model_name")
        if not model_name or not isinstance(model_name, str):
            return {"index": index, "success": False, "error": "model_name is required"}

        input_tokens, input_estimated, input_error = self._resolve_side(
            entry.get("input_tokens"), entry.get("input_text")
        )
        if input_error:
            return {"index": index, "success": False, "error": f"input_tokens/input_text: {input_error}"}

        output_tokens, output_estimated, output_error = self._resolve_side(
            entry.get("output_tokens"), entry.get("output_text")
        )
        if output_error:
            return {"index": index, "success": False, "error": f"output_tokens/output_text: {output_error}"}

        pricing = await self.service.find_model_pricing(model_name)
        if not pricing:
            return {
                "index": index,
                "success": False,
                "error": f"Model '{model_name}' not found",
                "available_models_hint": "Use get_all_pricing tool to see available models",
            }

        cost_usd = (
            input_tokens * pricing.cost_per_input_token
            + output_tokens * pricing.cost_per_output_token
        )
        is_estimated = input_estimated or output_estimated

        raw_occurred_at = entry.get("occurred_at")
        occurred_at = raw_occurred_at if raw_occurred_at is not None else time.time()

        request_id = entry.get("request_id")
        if not request_id:
            # Derive from the caller-supplied occurred_at (or, when omitted, a stable
            # placeholder plus this entry's position in the batch) — never from the
            # wall-clock `occurred_at` computed above, which changes on every call and
            # would make the derived id different (and thus never dedupe) on resubmission.
            request_id = self._derive_request_id(
                session_id=session_id,
                index=index,
                model_name=model_name,
                occurred_at=raw_occurred_at,
                input_tokens=entry.get("input_tokens"),
                input_text=entry.get("input_text"),
                output_tokens=entry.get("output_tokens"),
                output_text=entry.get("output_text"),
            )

        try:
            record_result = await tracker.record_event(
                provider=pricing.provider,
                model_name=pricing.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                org_id=org_id,
                occurred_at=occurred_at,
                request_id=request_id,
                session_id=session_id,
                is_estimated=is_estimated,
            )
        except Exception as exc:
            return {
                "index": index,
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        duplicate = record_result["duplicate"]
        return {
            "index": index,
            "success": True,
            "recorded": not duplicate,
            "duplicate": duplicate,
            "is_estimated": is_estimated,
            "model_name": pricing.model_name,
            "provider": pricing.provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "request_id": request_id,
        }

    @staticmethod
    def _resolve_side(
        tokens: Any, text: Any
    ) -> Tuple[Optional[int], Optional[bool], Optional[str]]:
        """Resolve one side (input or output) of an entry to (tokens, is_estimated, error).

        Exact counts win when supplied; text is only used to estimate when no exact
        count was given. Exactly one of tokens/error will be non-None on return.
        """
        if tokens is not None:
            if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
                return None, None, "must be a non-negative integer when supplied"
            return tokens, False, None
        if text:
            if not isinstance(text, str):
                return None, None, "must be a string when supplied"
            return count_tokens(text), True, None
        return None, None, "requires either a non-negative token count or text to estimate from"

    @staticmethod
    def _derive_request_id(
        session_id: str,
        index: int,
        model_name: str,
        occurred_at: Optional[float],
        input_tokens: Optional[int],
        input_text: Optional[str],
        output_tokens: Optional[int],
        output_text: Optional[str],
    ) -> str:
        """Deterministic idempotency key for entries without a caller-supplied request_id.

        Built entirely from caller-supplied data (never from a defaulted wall-clock
        timestamp, which would change on every call and defeat dedup): the raw
        occurred_at as given (or a stable placeholder when omitted entirely, since
        "not supplied" is itself a stable fact about the entry), the entry's position
        in the batch (so two distinct entries with otherwise-identical content in the
        same batch don't collide with each other), and the model/token-or-text source.
        Resubmitting an identical batch reproduces the same keys and collides on the
        existing (org_id, request_id) UNIQUE index, reported back as a duplicate rather
        than double-counted.
        """
        occurred_key = occurred_at if occurred_at is not None else "unset"
        input_source = f"tok:{input_tokens}" if input_tokens is not None else f"txt:{input_text}"
        output_source = f"tok:{output_tokens}" if output_tokens is not None else f"txt:{output_text}"
        key = f"{session_id}|{index}|{model_name}|{occurred_key}|{input_source}|{output_source}"
        return "auto-" + hashlib.sha256(key.encode("utf-8")).hexdigest()
