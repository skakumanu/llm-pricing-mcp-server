"""MCP Tool: Record an actual LLM usage event."""
from typing import Any, Dict

from src.config.settings import settings
from src.services.pricing_aggregator import PricingAggregatorService
from src.services.usage_tracker import get_usage_tracker
from src.services.budget_alerts import get_budget_alert_service


class RecordUsageTool:
    """Record actual token usage for a completed LLM call and compute its cost server-side."""

    def __init__(self):
        self.service = PricingAggregatorService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        model_name = arguments.get("model_name")
        input_tokens = arguments.get("input_tokens")
        output_tokens = arguments.get("output_tokens")

        if not model_name:
            return {"success": False, "error": "model_name is required"}
        if input_tokens is None or output_tokens is None:
            return {"success": False, "error": "input_tokens and output_tokens are required"}
        if input_tokens < 0 or output_tokens < 0:
            return {"success": False, "error": "input_tokens and output_tokens must be non-negative"}

        pricing = await self.service.find_model_pricing(model_name)
        if not pricing:
            return {
                "success": False,
                "error": f"Model '{model_name}' not found",
                "available_models_hint": "Use get_all_pricing tool to see available models",
            }

        cost_usd = (
            input_tokens * pricing.cost_per_input_token
            + output_tokens * pricing.cost_per_output_token
        )

        try:
            tracker = get_usage_tracker()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        org_id = arguments.get("org_id")
        try:
            result = await tracker.record_event(
                provider=pricing.provider,
                model_name=pricing.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                org_id=org_id,
                occurred_at=arguments.get("occurred_at"),
                request_id=arguments.get("request_id"),
            )
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}

        try:
            budget_alerts = get_budget_alert_service()
            await budget_alerts.check_and_fire(tracker, secret=settings.webhook_secret)
        except Exception:
            pass  # nosec B110 — best-effort; usage was already recorded successfully

        return {
            "success": True,
            "recorded": True,
            "duplicate": result["duplicate"],
            "model_name": pricing.model_name,
            "provider": pricing.provider,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost_usd, 6),
            "org_id": org_id,
        }
