"""MCP Tool: Recommend a model from a free-text use-case description."""
from typing import Any, Dict, Optional

from src.services.pricing_aggregator import PricingAggregatorService
from src.services.router import ModelRouter, RouterConstraints
from src.services.task_profiles import get_task_description, infer_task_type, list_task_types


# task_profiles.infer_task_type() uses a richer, human-facing vocabulary
# (classification, extraction, summarization, qa, code_generation, translation,
# chat, content_generation, reasoning, function_calling, data_analysis, rewrite)
# than ModelRouter._TASK_USE_CASES (code, chat, analysis, summarization,
# code_completion, code_chat, code_refactor, agentic_coding), which is the
# vocabulary the router's task-based hard filter actually understands. Only
# "chat" and "summarization" happen to spell identically across both. Map the
# task_profiles vocabulary onto the router's before building RouterConstraints
# so the task-type filter isn't silently skipped for the other ten task types.
# The task_profiles-vocabulary value is still what's returned to the caller
# (in `task_type`/`task_description`) since it's more descriptive; this map
# only affects what's passed to the routing engine.
_TASK_TYPE_TO_ROUTER_TASK_TYPE = {
    "classification": "analysis",
    "extraction": "summarization",
    "summarization": "summarization",
    "qa": "analysis",
    "code_generation": "code",
    "translation": "chat",
    "chat": "chat",
    "content_generation": "chat",
    "reasoning": "analysis",
    "function_calling": "agentic_coding",
    "data_analysis": "analysis",
    "rewrite": "chat",
}


def _to_router_task_type(task_type: str) -> str:
    """Translate a task_profiles task_type into the router's own vocabulary."""
    return _TASK_TYPE_TO_ROUTER_TASK_TYPE.get(task_type, task_type)


def _model_summary(m) -> Dict[str, Any]:
    """Build a compact JSON-friendly summary of a PricingMetrics model."""
    avg_cost_1m = (m.cost_per_input_token + m.cost_per_output_token) / 2 * 1_000_000
    return {
        "model_name": m.model_name,
        "provider": m.provider,
        "quality_score": m.quality_score,
        "avg_cost_per_1m_tokens_usd": round(avg_cost_1m, 4),
        "cost_per_input_token": m.cost_per_input_token,
        "cost_per_output_token": m.cost_per_output_token,
        "context_window": m.context_window,
        "supports_function_calling": m.supports_function_calling,
        "supports_vision": m.supports_vision,
        "is_reasoning_model": m.is_reasoning_model,
        "pricing_model": m.pricing_model,
        "subscription_monthly_usd": m.subscription_monthly_usd,
    }


class RecommendModelTool:
    """Classify a free-text use case and recommend the optimal model for it.

    Wraps the existing routing engine (ModelRouter.get_optimal_model, already
    live via REST at /router/recommend) with a free-text entry point so a
    caller doesn't need to already know the routing engine's task_type
    vocabulary. Read-only: no feedback submission.
    """

    def __init__(self):
        self.service = PricingAggregatorService()
        self.router = ModelRouter(self.service)

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            description = arguments.get("description")
            if not description or not isinstance(description, str) or not description.strip():
                return {
                    "success": False,
                    "error": "description is required and must be a non-empty string",
                }

            task_type_arg: Optional[str] = arguments.get("task_type")
            valid_types = list_task_types()
            if task_type_arg and task_type_arg not in valid_types:
                return {
                    "success": False,
                    "error": f"Unknown task_type '{task_type_arg}'. Valid options: {valid_types}",
                }

            inferred = task_type_arg is None
            task_type = task_type_arg if task_type_arg else infer_task_type(description)

            max_cost_per_1m_tokens = arguments.get("max_cost_per_1m_tokens")
            if max_cost_per_1m_tokens is not None:
                max_cost_per_1m_tokens = float(max_cost_per_1m_tokens)

            min_quality_score = arguments.get("min_quality_score")
            if min_quality_score is not None:
                min_quality_score = float(min_quality_score)

            min_context_window = arguments.get("min_context_window")
            if min_context_window is not None:
                min_context_window = int(min_context_window)

            monthly_budget_usd = arguments.get("monthly_budget_usd")
            if monthly_budget_usd is not None:
                monthly_budget_usd = float(monthly_budget_usd)

            constraints = RouterConstraints(
                max_cost_per_1m_tokens=max_cost_per_1m_tokens,
                min_quality_score=min_quality_score,
                min_context_window=min_context_window,
                preferred_provider=arguments.get("preferred_provider"),
                task_type=_to_router_task_type(task_type),
                prefer_low_latency=bool(arguments.get("prefer_low_latency", False)),
                exclude_reasoning_models=bool(arguments.get("exclude_reasoning_models", False)),
                ide_context=arguments.get("ide_context"),
                monthly_budget_usd=monthly_budget_usd,
                estimated_monthly_requests=int(arguments.get("estimated_monthly_requests", 1000)),
                avg_input_tokens=int(arguments.get("avg_input_tokens", 500)),
                avg_output_tokens=int(arguments.get("avg_output_tokens", 200)),
            )

            result = await self.router.get_optimal_model(constraints)
            if result is None:
                return {
                    "success": False,
                    "error": (
                        "No model matched the given constraints. Try relaxing cost, "
                        "quality, or context window filters."
                    ),
                    "task_type": task_type,
                    "task_type_inferred": inferred,
                }

            return {
                "success": True,
                "description": description,
                "task_type": task_type,
                "task_type_inferred": inferred,
                "task_description": get_task_description(task_type),
                "recommended": _model_summary(result.recommended),
                "score": result.score,
                "reason": result.reason,
                "alternatives": [_model_summary(m) for m in result.alternatives],
            }

        except (TypeError, ValueError) as e:
            return {"success": False, "error": f"Invalid argument: {e}", "error_type": type(e).__name__}
        except Exception as e:
            return {"success": False, "error": str(e), "error_type": type(e).__name__}
