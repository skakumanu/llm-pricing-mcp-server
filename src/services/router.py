"""Arbitrage Routing Engine.

Selects the optimal LLM model for a caller's constraints (cost, quality,
context window, provider preference, task type) and returns the best match
plus up to 3 alternatives.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from src.services.benchmark_service import enrich_models

logger = logging.getLogger(__name__)


@dataclass
class RouterConstraints:
    """Hard and soft constraints for model selection."""

    max_cost_per_1m_tokens: Optional[float] = None
    min_quality_score: Optional[float] = None
    min_context_window: Optional[int] = None
    preferred_provider: Optional[str] = None
    task_type: Optional[str] = None  # "code" | "chat" | "analysis" | "summarization"


@dataclass
class RouterResult:
    """Result of a routing decision."""

    recommended: object  # PricingMetrics
    score: float
    reason: str
    alternatives: List[object] = field(default_factory=list)


class ModelRouter:
    """Selects the optimal model from live pricing + quality enrichment."""

    # task_type -> list of use_case substrings to match
    _TASK_USE_CASES = {
        "code": ["code", "coding", "programming", "developer"],
        "chat": ["chat", "conversation", "general", "assistant"],
        "analysis": ["analysis", "reasoning", "research", "data"],
        "summarization": ["summar", "extract", "document"],
    }

    def __init__(self, aggregator) -> None:
        self._aggregator = aggregator

    async def get_optimal_model(
        self, constraints: RouterConstraints
    ) -> Optional[RouterResult]:
        """
        Fetch live pricing, enrich with quality scores, apply constraints,
        and return the best model + up to 3 alternatives.
        """
        models, _ = await self._aggregator.get_all_pricing_async()
        if not models:
            return None

        models = await enrich_models(models)

        # --- Hard filters ---
        candidates = []
        for m in models:
            avg_cost_1m = (m.cost_per_input_token + m.cost_per_output_token) / 2 * 1_000_000

            if constraints.max_cost_per_1m_tokens is not None:
                if avg_cost_1m > constraints.max_cost_per_1m_tokens:
                    continue

            if constraints.min_quality_score is not None:
                if m.quality_score is None or m.quality_score < constraints.min_quality_score:
                    continue

            if constraints.min_context_window is not None:
                if m.context_window is None or m.context_window < constraints.min_context_window:
                    continue

            if constraints.task_type is not None:
                task_keywords = self._TASK_USE_CASES.get(constraints.task_type.lower(), [])
                if task_keywords and m.use_cases:
                    use_case_text = " ".join(m.use_cases).lower()
                    if not any(kw in use_case_text for kw in task_keywords):
                        continue

            candidates.append(m)

        if not candidates:
            return None

        # --- Scoring ---
        def score_model(m) -> float:
            base = m.quality_value_score or 0.0
            # Preferred provider gets a 10% boost
            if (
                constraints.preferred_provider
                and m.provider.lower() == constraints.preferred_provider.lower()
            ):
                base *= 1.10
            return base

        candidates.sort(key=score_model, reverse=True)
        best = candidates[0]
        alternatives = candidates[1:4]

        best_score = score_model(best)
        avg_cost_1m = (best.cost_per_input_token + best.cost_per_output_token) / 2 * 1_000_000
        reason = _build_reason(best, best_score, avg_cost_1m, constraints)

        return RouterResult(
            recommended=best,
            score=round(best_score, 4),
            reason=reason,
            alternatives=alternatives,
        )


def _build_reason(model, score: float, cost_1m: float, c: RouterConstraints) -> str:
    parts = [
        f"{model.model_name} ({model.provider})",
        f"quality_value_score={score:.2f}",
    ]
    if model.quality_score is not None:
        parts.append(f"quality={model.quality_score:.0f}/100")
    parts.append(f"cost=${cost_1m:.2f}/1M tokens")
    if c.preferred_provider and model.provider.lower() == c.preferred_provider.lower():
        parts.append("preferred provider bonus applied")
    return "; ".join(parts)


# ---- Singleton wiring -------------------------------------------------------

_router: Optional[ModelRouter] = None


def init_router(aggregator) -> ModelRouter:
    """Create and register the singleton ModelRouter."""
    global _router
    _router = ModelRouter(aggregator)
    return _router


def get_router() -> ModelRouter:
    """Return the singleton router (must call init_router() first)."""
    if _router is None:
        raise RuntimeError("ModelRouter has not been initialized")
    return _router
