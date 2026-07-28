"""MCP Tool: Predict real cost from a prompt before making any API call."""
from typing import Any, Dict, Optional

from src.services.pricing_aggregator import PricingAggregatorService
from src.services.task_profiles import (
    estimate_output_tokens,
    get_task_description,
    infer_task_type,
    list_task_types,
)
from src.services.token_counter import (
    compute_cache_savings,
    count_tokens,
    providers_with_caching,
)


class PredictCostTool:
    """Tool that counts tokens in an actual prompt and returns ranked model costs."""

    def __init__(self):
        self.service = PricingAggregatorService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            prompt = arguments.get("prompt", "")
            if not prompt:
                return {"success": False, "error": "prompt is required"}

            task_type_arg: Optional[str] = arguments.get("task_type")
            top_n: int = min(int(arguments.get("top_n", 10)), 50)
            cache_hit_ratio: float = float(arguments.get("cache_hit_ratio", 0.0))
            cache_hit_ratio = max(0.0, min(1.0, cache_hit_ratio))
            require_fn_calling: bool = bool(arguments.get("require_function_calling", False))
            require_vision: bool = bool(arguments.get("require_vision", False))
            min_context: Optional[int] = arguments.get("min_context_tokens")

            # Validate task_type if provided
            valid_types = list_task_types()
            if task_type_arg and task_type_arg not in valid_types:
                return {
                    "success": False,
                    "error": f"Unknown task_type '{task_type_arg}'. Valid options: {valid_types}",
                }

            # Count input tokens from the actual prompt
            input_tokens = count_tokens(prompt)

            # Resolve task type
            inferred = task_type_arg is None
            task_type = task_type_arg if task_type_arg else infer_task_type(prompt)
            output_tokens = estimate_output_tokens(task_type, input_tokens)

            # Fetch all models
            all_pricing, _ = await self.service.get_all_pricing_async()

            # Filter by capability constraints
            candidates = [
                m for m in all_pricing
                if m.pricing_model == "per_token"
                and (not require_fn_calling or m.supports_function_calling)
                and (not require_vision or m.supports_vision)
                and (min_context is None or (m.context_window or 0) >= min_context)
            ]

            if not candidates:
                return {
                    "success": False,
                    "error": "No models match the specified capability constraints",
                }

            # Compute cost for each candidate
            ranked = []
            for m in candidates:
                input_cost = (m.cost_per_input_token / 1000) * input_tokens
                output_cost = (m.cost_per_output_token / 1000) * output_tokens
                total_cost = input_cost + output_cost
                cache_savings = compute_cache_savings(
                    m.provider, input_tokens, m.cost_per_input_token, cache_hit_ratio
                )
                effective_cost = max(0.0, total_cost - cache_savings)

                ranked.append({
                    "model_name": m.model_name,
                    "provider": m.provider,
                    "estimated_input_cost_usd": round(input_cost, 8),
                    "estimated_output_cost_usd": round(output_cost, 8),
                    "estimated_total_cost_usd": round(total_cost, 8),
                    "cache_savings_usd": round(cache_savings, 8),
                    "effective_cost_usd": round(effective_cost, 8),
                    "quality_score": m.quality_score,
                    "quality_value_score": m.quality_value_score,
                    "context_window": m.context_window,
                    "supports_function_calling": m.supports_function_calling,
                    "supports_vision": m.supports_vision,
                    "batch_available": m.batch_available,
                })

            # Sort by effective cost ascending
            ranked.sort(key=lambda x: x["effective_cost_usd"])

            # Assign ranks
            for i, entry in enumerate(ranked):
                entry["rank"] = i + 1

            top = ranked[:top_n]
            cheapest = top[0] if top else None

            # Best value: highest quality_value_score among top-5 with a known score
            top5_with_quality = [
                e for e in ranked[:5] if e["quality_value_score"] is not None
            ]
            best_value = (
                max(top5_with_quality, key=lambda x: x["quality_value_score"])
                if top5_with_quality
                else None
            )

            # Cache tip
            cache_tip = None
            if cache_hit_ratio == 0.0:
                cacheable = [e for e in top if e["provider"] in providers_with_caching()]
                if cacheable:
                    sample = cacheable[0]
                    hypothetical_savings = compute_cache_savings(
                        sample["provider"], input_tokens,
                        next(m.cost_per_input_token for m in candidates if m.model_name == sample["model_name"]),
                        0.8,
                    )
                    cache_tip = (
                        f"Enable prompt caching on {sample['provider']} models — "
                        f"at 80% hit rate this call would save ~${hypothetical_savings:.6f} "
                        f"on {sample['model_name']} alone."
                    )

            return {
                "success": True,
                "input_tokens": input_tokens,
                "estimated_output_tokens": output_tokens,
                "task_type": task_type,
                "task_type_inferred": inferred,
                "task_description": get_task_description(task_type),
                "cache_hit_ratio": cache_hit_ratio,
                "total_candidates": len(candidates),
                "ranked_models": top,
                "cheapest_pick": cheapest,
                "best_value_pick": best_value,
                "cache_tip": cache_tip,
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }
