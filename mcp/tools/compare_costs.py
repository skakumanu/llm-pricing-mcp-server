"""MCP Tool: Compare Costs for Multiple Models"""
import asyncio
from typing import Any, Dict, List

from src.services.pricing_aggregator import PricingAggregatorService

# Default number of models to include in each parallel processing chunk.
DEFAULT_CHUNK_SIZE = 10

# Number of tokens in one million – used for cost-per-million-tokens calculations.
_TOKENS_PER_MILLION = 1_000_000


def split_into_chunks(items: List[Any], chunk_size: int) -> List[List[Any]]:
    """Split *items* into consecutive sub-lists of at most *chunk_size* elements.

    Args:
        items: The list to partition.
        chunk_size: Maximum number of elements per chunk (must be >= 1).

    Returns:
        A list of sub-lists.  The last chunk may be smaller than *chunk_size*.

    Raises:
        ValueError: If *chunk_size* is less than 1.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    return [items[i: i + chunk_size] for i in range(0, len(items), chunk_size)]


async def _compare_chunk(
    model_names: List[str],
    input_tokens: int,
    output_tokens: int,
    pricing_map: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute cost comparisons for a subset of *model_names*.

    Args:
        model_names: The models to process in this chunk.
        input_tokens: Number of input tokens for the cost estimate.
        output_tokens: Number of output tokens for the cost estimate.
        pricing_map: Pre-built ``{lower_model_name: PricingMetrics}`` dict.

    Returns:
        A dict with ``comparisons`` and ``costs`` lists for the chunk.
    """
    comparisons: List[Dict[str, Any]] = []
    costs: List[tuple] = []

    for model_name in model_names:
        pricing = pricing_map.get(model_name.lower())

        if not pricing:
            comparisons.append({
                "model_name": model_name,
                "is_available": False,
                "error": f"Model '{model_name}' not found",
            })
            continue

        input_cost = (pricing.cost_per_input_token / 1000) * input_tokens
        output_cost = (pricing.cost_per_output_token / 1000) * output_tokens
        total_cost = input_cost + output_cost
        costs.append((model_name, total_cost, input_cost, output_cost))

        comparisons.append({
            "model_name": pricing.model_name,
            "provider": pricing.provider,
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "total_cost": round(total_cost, 6),
            "cost_per_1m_tokens": round(
                (total_cost / (input_tokens + output_tokens)) * _TOKENS_PER_MILLION, 2
            ) if (input_tokens + output_tokens) > 0 else 0,
            "is_available": True,
        })

    return {"comparisons": comparisons, "costs": costs}


class CompareCostsTool:
    """Tool to compare costs across multiple LLM models."""

    def __init__(self):
        """Initialize the tool with the pricing service."""
        self.service = PricingAggregatorService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the cost comparison tool.

        For large model lists the request is automatically split into chunks of
        :data:`DEFAULT_CHUNK_SIZE` and the chunks are processed in parallel via
        :func:`asyncio.gather` to improve throughput.

        Args:
            arguments: Tool arguments containing:
                - model_names: List[str] (required)
                - input_tokens: int (required, >= 0)
                - output_tokens: int (required, >= 0)
                - chunk_size: int (optional, default DEFAULT_CHUNK_SIZE)

        Returns:
            Dictionary with cost comparison results
        """
        try:
            model_names = arguments.get("model_names")
            input_tokens = arguments.get("input_tokens")
            output_tokens = arguments.get("output_tokens")
            chunk_size = int(arguments.get("chunk_size", DEFAULT_CHUNK_SIZE))

            # Validate arguments
            if not model_names or not isinstance(model_names, list):
                return {
                    "success": False,
                    "error": "model_names must be a non-empty list",
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

            if chunk_size < 1:
                return {
                    "success": False,
                    "error": "chunk_size must be at least 1",
                }

            # Fetch all pricing once and build a lookup map
            all_pricing, _ = await self.service.get_all_pricing_async()
            pricing_map = {p.model_name.lower(): p for p in all_pricing}

            # Split model list into chunks and process in parallel
            chunks = split_into_chunks(model_names, chunk_size)
            chunk_results = await asyncio.gather(
                *[
                    _compare_chunk(chunk, input_tokens, output_tokens, pricing_map)
                    for chunk in chunks
                ]
            )

            # Merge chunk results
            comparisons: List[Dict[str, Any]] = []
            costs: List[tuple] = []
            for cr in chunk_results:
                comparisons.extend(cr["comparisons"])
                costs.extend(cr["costs"])

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

            return {
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
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }
