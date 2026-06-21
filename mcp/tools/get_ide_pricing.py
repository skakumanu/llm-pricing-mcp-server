"""MCP Tool: Get IDE / subscription-based AI coding tool pricing."""
from typing import Any, Dict, List, Optional

from src.services.ide_pricing import IDEPricingService


class GetIDEPricingTool:
    """Tool to fetch subscription pricing for AI coding IDE tools."""

    def __init__(self):
        self._service = IDEPricingService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return subscription pricing for AI coding IDEs (Copilot, Cursor, Windsurf, etc.).

        Optional filters:
          provider      - e.g. "GitHub Copilot", "Cursor", "Windsurf"
          max_monthly   - only return plans at or below this monthly USD cost
          inline_only   - if true, only return tools that support inline completion
        """
        try:
            models = await self._service.fetch_pricing_data()

            provider_filter: Optional[str] = arguments.get("provider")
            max_monthly: Optional[float] = arguments.get("max_monthly")
            inline_only: bool = bool(arguments.get("inline_only", False))

            if provider_filter:
                models = [m for m in models if provider_filter.lower() in m.provider.lower()]
            if max_monthly is not None:
                models = [
                    m for m in models
                    if m.subscription_monthly_usd is not None and m.subscription_monthly_usd <= max_monthly
                ]
            if inline_only:
                models = [m for m in models if m.supports_inline_completion]

            return {
                "success": True,
                "total_tools": len(models),
                "tools": [self._serialize(m) for m in models],
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }

    @staticmethod
    def _serialize(m) -> Dict[str, Any]:
        return {
            "model_name": m.model_name,
            "provider": m.provider,
            "pricing_model": m.pricing_model,
            "subscription_monthly_usd": m.subscription_monthly_usd,
            "context_window": m.context_window,
            "latency_ms": m.latency_ms,
            "supports_inline_completion": m.supports_inline_completion,
            "supports_vision": m.supports_vision,
            "supports_function_calling": m.supports_function_calling,
            "ide_native": m.ide_native,
            "is_reasoning_model": m.is_reasoning_model,
            "use_cases": m.use_cases,
            "strengths": m.strengths,
            "best_for": m.best_for,
            # Derived per-token rates (estimates for comparison only)
            "estimated_cost_per_1k_input": round(m.cost_per_input_token, 6),
            "estimated_cost_per_1k_output": round(m.cost_per_output_token, 6),
        }
