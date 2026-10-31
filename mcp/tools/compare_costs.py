"""MCP Tool: Compare Costs for Multiple Models"""
import re
from typing import Any, Dict, List, Optional

from mcp.tools._client import client_response_fields, resolve_tool_client
from mcp.tools._locale import resolve_tool_locale
from src.services.client_models import is_runnable
from src.services.cost_calculator import MAX_COMPARE_MODELS, compute_token_cost
from src.services.currency import conversion_metadata, convert, localize_cost
from src.services.pricing_aggregator import PricingAggregatorService

MAX_CANDIDATES = 8


def _normalize(name: str) -> str:
    """Lowercase and strip non-alphanumeric characters for loose matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _find_candidates(model_name: str, all_pricing) -> List[str]:
    """Find real catalogued model names related to an unresolved query.

    Uses deterministic normalized-substring matching (not fuzzy/edit-distance):
    a catalogued model is a candidate if its normalized name contains the
    normalized query as a substring. Guards against very short queries (< 2
    normalized chars) matching too broadly. Results are sorted by name length
    then alphabetically, and capped to MAX_CANDIDATES.
    """
    normalized_query = _normalize(model_name)
    if len(normalized_query) < 2:
        return []

    matches = [
        pricing.model_name
        for pricing in all_pricing
        if normalized_query in _normalize(pricing.model_name)
    ]
    matches.sort(key=lambda name: (len(name), name.lower()))
    return matches[:MAX_CANDIDATES]


class CompareCostsTool:
    """Tool to compare costs across multiple LLM models."""

    def __init__(self, aggregator: Optional[PricingAggregatorService] = None):
        """Initialize the tool with the pricing service.

        Args:
            aggregator: Shared PricingAggregatorService instance (process-wide
                singleton from ToolManager). Falls back to a fresh instance
                when constructed directly (e.g. in tests), matching prior
                behavior.
        """
        self.service = aggregator or PricingAggregatorService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the cost comparison tool.

        Args:
            arguments: Tool arguments containing:
                - model_names: List[str] (required)
                - input_tokens: int (required, >= 0)
                - output_tokens: int (required, >= 0)
                - currency: str (optional) — ISO-4217 code to convert into
                - country: str (optional) — ISO-3166 alpha-2 whose currency to use
                - client: str (optional) — AI coding client; never filters, but each
                  found model is flagged ``runnable_in_client``

        Returns:
            Dictionary with cost comparison results. cheapest/most_expensive and
            cost_range are always USD; with a currency/country each available
            model also carries local_cost and the result local_cost_range.
        """
        try:
            model_names = arguments.get("model_names")
            input_tokens = arguments.get("input_tokens")
            output_tokens = arguments.get("output_tokens")

            locale = resolve_tool_locale(arguments)
            if isinstance(locale, dict):
                return locale

            client_res = resolve_tool_client(arguments)

            # Validate arguments
            if not model_names or not isinstance(model_names, list):
                return {
                    "success": False,
                    "error": "model_names must be a non-empty list",
                }

            if len(model_names) > MAX_COMPARE_MODELS:
                return {
                    "success": False,
                    "error": f"model_names accepts at most {MAX_COMPARE_MODELS} models",
                }

            if input_tokens is None or output_tokens is None:
                return {
                    "success": False,
                    "error": "input_tokens and output_tokens are required",
                }

            if input_tokens < 0 or output_tokens < 0:
                return {
                    "success": False,
                    "error": "input_tokens and output_tokens must be non-negative",
                }

            # Fetch all pricing to find models
            all_pricing, _ = await self.service.get_all_pricing_async()

            # Create a map for easy lookup
            pricing_map = {
                p.model_name.lower(): p for p in all_pricing
            }

            # Estimate costs for each model
            comparisons = []
            costs = []

            for model_name in model_names:
                pricing = pricing_map.get(model_name.lower())

                if not pricing:
                    comparisons.append({
                        "model_name": model_name,
                        "is_available": False,
                        "error": f"Model '{model_name}' not found",
                        "candidates": _find_candidates(model_name, all_pricing),
                    })
                    continue

                # Calculate costs. cost_per_input_token / cost_per_output_token are
                # dollars-per-single-token, so multiply directly by the token count.
                token_cost = compute_token_cost(
                    pricing.cost_per_input_token, pricing.cost_per_output_token, input_tokens, output_tokens
                )
                input_cost = token_cost.input_cost
                output_cost = token_cost.output_cost
                total_cost = token_cost.total_cost
                costs.append((model_name, total_cost, input_cost, output_cost))

                cost_per_1m = (
                    (total_cost / (input_tokens + output_tokens)) * 1_000_000
                    if (input_tokens + output_tokens) > 0 else 0
                )
                row = {
                    "model_name": pricing.model_name,
                    "provider": pricing.provider,
                    "input_cost": round(input_cost, 6),
                    "output_cost": round(output_cost, 6),
                    "total_cost": round(total_cost, 6),
                    "cost_per_1m_tokens": round(cost_per_1m, 2) if cost_per_1m else 0,
                    "is_available": True,
                }
                if client_res.entry is not None:
                    row["runnable_in_client"] = is_runnable(client_res.entry, pricing)
                if locale.active:
                    row["local_cost"] = localize_cost(
                        input_cost, output_cost, total_cost, locale, cost_per_1m
                    ).model_dump()
                comparisons.append(row)

            # Find cheapest and most expensive
            cheapest = None
            most_expensive = None

            if costs:
                costs_sorted = sorted(costs, key=lambda x: x[1])
                cheapest = costs_sorted[0][0]
                most_expensive = costs_sorted[-1][0]

            # Calculate cost range
            cost_range = None
            if costs:
                costs_values = [c[1] for c in costs]
                cost_range = {
                    "min": round(min(costs_values), 6),
                    "max": round(max(costs_values), 6),
                    "difference": round(max(costs_values) - min(costs_values), 6),
                }

            result = {
                "success": True,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "models": comparisons,
                "cheapest_model": cheapest,
                "most_expensive_model": most_expensive,
                "cost_range": cost_range,
                "currency": "USD",
            }
            result.update(client_response_fields(client_res))
            if locale.active:
                result["local_cost_range"] = (
                    {
                        "currency": locale.currency,
                        "min": convert(cost_range["min"], locale),
                        "max": convert(cost_range["max"], locale),
                        "difference": convert(cost_range["difference"], locale),
                    }
                    if cost_range is not None else None
                )
                result["currency_conversion"] = conversion_metadata(locale)
            return result
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }
