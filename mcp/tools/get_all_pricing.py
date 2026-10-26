"""MCP Tool: Get All Pricing Data"""
from typing import Any, Dict, Optional

from mcp.tools._locale import resolve_tool_locale
from src.services.currency import conversion_metadata, local_prices_for
from src.services.pricing_aggregator import PricingAggregatorService


class GetAllPricingTool:
    """Tool to fetch pricing data from all providers."""

    def __init__(self, aggregator: Optional[PricingAggregatorService] = None):
        """Initialize the tool with the pricing service."""
        self.service = aggregator or PricingAggregatorService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the get all pricing tool.

        Args:
            arguments: Tool arguments:
                - currency: str (optional) — ISO-4217 code to convert into
                - country: str (optional) — ISO-3166 alpha-2 whose currency to use
              Omitted -> exactly the USD-only output.

        Returns:
            Dictionary with pricing data and provider status
        """
        try:
            locale = resolve_tool_locale(arguments)
            if isinstance(locale, dict):
                return locale

            # Fetch pricing data asynchronously
            pricing_data, provider_statuses = await self.service.get_all_pricing_async()

            # Convert to JSON-serializable format
            models = [self._serialize_pricing(p) for p in pricing_data]
            if locale.active:
                for row, pricing in zip(models, pricing_data):
                    row["local_prices"] = local_prices_for(pricing, locale).model_dump()
            result = {
                "success": True,
                "total_models": len(pricing_data),
                "models": models,
                "providers": [self._serialize_status(s) for s in provider_statuses],
                "timestamp": str(pricing_data[0].last_updated) if pricing_data else None,
            }
            if locale.active:
                result["currency_conversion"] = conversion_metadata(locale)
            return result
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }

    @staticmethod
    def _serialize_pricing(pricing) -> Dict[str, Any]:
        """Convert PricingMetrics to JSON-serializable dict."""
        return {
            "model_name": pricing.model_name,
            "provider": pricing.provider,
            "cost_per_input_token": pricing.cost_per_input_token,
            "cost_per_output_token": pricing.cost_per_output_token,
            "throughput": pricing.throughput,
            "latency_ms": pricing.latency_ms,
            "context_window": pricing.context_window,
            "currency": pricing.currency,
            "unit": pricing.unit,
            "use_cases": pricing.use_cases,
            "strengths": pricing.strengths,
            "best_for": pricing.best_for,
            "cost_at_10k_tokens": {
                "input_cost": pricing.cost_at_10k_tokens.input_cost,
                "output_cost": pricing.cost_at_10k_tokens.output_cost,
                "total_cost": pricing.cost_at_10k_tokens.total_cost,
            },
            "cost_at_100k_tokens": {
                "input_cost": pricing.cost_at_100k_tokens.input_cost,
                "output_cost": pricing.cost_at_100k_tokens.output_cost,
                "total_cost": pricing.cost_at_100k_tokens.total_cost,
            },
            "cost_at_1m_tokens": {
                "input_cost": pricing.cost_at_1m_tokens.input_cost,
                "output_cost": pricing.cost_at_1m_tokens.output_cost,
                "total_cost": pricing.cost_at_1m_tokens.total_cost,
            },
            "estimated_time_1m_tokens": pricing.estimated_time_1m_tokens,
            # Provenance (v1.75.0): where the number came from and how fresh it
            # is, so a caller can tell a provider-confirmed price from one taken
            # from the reference registry without a second call.
            "source": pricing.source,
            "price_as_of": pricing.price_as_of,
            "price_is_stale": pricing.price_is_stale,
            "price_confirmed": pricing.price_confirmed,
            "price_source_kind": pricing.price_source_kind,
            "registry_key": pricing.registry_key,
        }

    @staticmethod
    def _serialize_status(status) -> Dict[str, Any]:
        """Convert ProviderStatusInfo to JSON-serializable dict."""
        return {
            "provider_name": status.provider_name,
            "is_available": status.is_available,
            "error_message": status.error_message,
            "models_count": status.models_count,
        }
