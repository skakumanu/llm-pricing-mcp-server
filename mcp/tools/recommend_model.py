"""MCP Tool: Recommend a model from a free-text use-case description."""
from typing import Any, Dict, Optional

from src.services.pricing_aggregator import PricingAggregatorService
from src.services.recommendation_cache import RecommendationCache
from src.services.router import ModelRouter, RouterConstraints
from src.services.task_profiles import (
    detect_secondary_task_signal,
    get_task_description,
    infer_task_type,
    list_task_types,
)


# Below this quality_score, ranking purely by quality-per-dollar can pick a
# model that merely clears a quality bar rather than one well-suited to tasks
# where a wrong answer is expensive to discover and fix downstream. See
# Caveat 2 in execute() below.
_QUALITY_TRADEOFF_THRESHOLD = 75.0


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
        # ~5 minute in-memory cache so repeated/equivalent requests (e.g. an
        # IDE re-asking on every keystroke pause) skip full recomputation.
        # Instance-scoped: a fresh RecommendModelTool() (as tests construct)
        # gets its own cache, never sharing hits with /router/recommend's.
        # name="recommend_model" opts this instance into the process-lifetime
        # hit/miss registry exposed by get_cache_stats (aggregated across
        # every RecommendModelTool instance in-process, since this cache is
        # not a true singleton).
        self._cache = RecommendationCache(name="recommend_model")

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

            result = await self._cache.get_or_compute(
                constraints, lambda: self.router.get_optimal_model(constraints)
            )
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

            caveats = []

            # Caveat 1: classification uncertainty. Only meaningful when we
            # did the classifying ourselves (task_type was inferred, not
            # caller-supplied) and it landed on the "chat" fallback, which is
            # infer_task_type's catch-all for anything that didn't match a
            # more specific rule. If the description also carries softer
            # signals of a higher-stakes category, tell the caller both the
            # inferred category and the plausible alternative.
            if inferred and task_type == "chat":
                secondary_signal = detect_secondary_task_signal(description)
                if secondary_signal is not None:
                    caveats.append(
                        {
                            "type": "classification_uncertainty",
                            "message": (
                                f"This description was auto-classified as '{task_type}', but it also "
                                f"contains language suggestive of '{secondary_signal}', a higher-stakes "
                                "task category. If that's a better fit, pass task_type explicitly to "
                                "re-run this recommendation against the right category."
                            ),
                            "inferred_task_type": task_type,
                            "plausible_alternative_task_type": secondary_signal,
                        }
                    )

            # Caveat 2: quality-per-dollar tradeoff. The router ranks by
            # quality-per-dollar, which can surface the cheapest model that
            # merely clears a quality bar. That's a fine tradeoff for tasks
            # where a mediocre answer is a minor inconvenience, but a poor
            # fit for tasks where a wrong answer is costly to discover and
            # fix downstream.
            recommended_quality_score = result.recommended.quality_score
            if (
                recommended_quality_score is not None
                and recommended_quality_score < _QUALITY_TRADEOFF_THRESHOLD
            ):
                caveats.append(
                    {
                        "type": "quality_tradeoff",
                        "message": (
                            f"The top recommendation has a quality_score of {recommended_quality_score} "
                            f"(below {_QUALITY_TRADEOFF_THRESHOLD:g}). Ranking by quality-per-dollar can "
                            "understate risk for tasks where a wrong answer is costly to discover and fix "
                            "downstream, as distinct from tasks where a mediocre answer is only a minor "
                            "inconvenience. Consider min_quality_score if this task is the former."
                        ),
                        "quality_score": recommended_quality_score,
                        "quality_threshold": _QUALITY_TRADEOFF_THRESHOLD,
                    }
                )

            response = {
                "success": True,
                "description": description,
                "task_type": task_type,
                "task_type_inferred": inferred,
                "task_description": get_task_description(task_type),
                "recommended": _model_summary(result.recommended),
                "score": result.score,
                "reason": result.reason,
                "alternatives": [_model_summary(m) for m in result.alternatives],
                "cached": result.cached,
            }
            if caveats:
                response["caveats"] = caveats
            return response

        except (TypeError, ValueError) as e:
            return {"success": False, "error": f"Invalid argument: {e}", "error_type": type(e).__name__}
        except Exception as e:
            return {"success": False, "error": str(e), "error_type": type(e).__name__}
