"""MCP Tool: Analyze one session's actual recorded usage and recommend a model."""
from typing import Any, Dict

from src.services.pricing_aggregator import PricingAggregatorService
from src.services.router import ModelRouter
from src.services.session_recommendation import compute_session_recommendation
from src.services.usage_tracker import get_usage_tracker


class AnalyzeSessionUsageTool:
    """Turn a specific session's actual observed usage into a grounded recommendation.

    Reads back rows previously recorded via record_usage(session_id=...) and hands the
    session's own aggregate token profile to the existing ModelRouter — the same engine
    behind recommend_model and POST /router/recommend — for a recommendation, then
    estimates the dollar savings/increase that model would have produced applied to
    this session's actual token volumes. Read-only, advisory only: never re-prices
    history or switches anything. No task_type is inferred or passed — there is no
    free-text description here, only observed numbers.

    `org_id` is optional and self-reported, matching get_usage_summary's/record_usage's
    existing trust model for this MCP tool layer (no authenticated-identity context is
    plumbed into tool execution here) — pass it to scope the lookup to usage events that
    were themselves recorded with that org_id.
    """

    def __init__(self):
        self.service = PricingAggregatorService()
        self.router = ModelRouter(self.service)

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        session_id = arguments.get("session_id")
        if not session_id or not isinstance(session_id, str) or not session_id.strip():
            return {"success": False, "error": "session_id is required and must be a non-empty string"}
        org_id = arguments.get("org_id")

        try:
            tracker = get_usage_tracker()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            usage = await tracker.get_session_usage(session_id, org_id=org_id)
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}

        total_requests = usage["total_requests"]
        if total_requests == 0:
            return {
                "success": False,
                "error": f"No usage recorded for session_id '{session_id}'",
                "session_id": session_id,
                "has_data": False,
            }

        total_input_tokens = usage["total_input_tokens"]
        total_output_tokens = usage["total_output_tokens"]
        total_cost_usd = usage["total_cost_usd"]
        by_model = usage["by_model"]

        response: Dict[str, Any] = {
            "success": True,
            "has_data": True,
            "session_id": session_id,
            "total_requests": total_requests,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost_usd": total_cost_usd,
            "by_model": by_model,
            "first_occurred_at": usage["first_occurred_at"],
            "last_occurred_at": usage["last_occurred_at"],
        }

        # Build constraints from this session's own observed averages. No task_type —
        # there's no free-text description to infer one from, and this tool deliberately
        # avoids adding a second inference heuristic (e.g. from token I/O ratio). The
        # actual recommendation math/rationale lives in session_recommendation.py, shared
        # with GET /usage/session/{session_id}, so the two entry points can't drift.
        try:
            recommendation = await compute_session_recommendation(usage, self.router)
        except Exception as exc:
            response["recommendation_error"] = f"{type(exc).__name__}: {exc}"
            return response

        if recommendation is None:
            response["recommendation_error"] = "No model matched this session's usage pattern"
            return response

        response["recommendation"] = recommendation
        return response
