"""Arbitrage Routing Engine.

Selects the optimal LLM model for a caller's constraints (cost, quality,
context window, provider preference, task type) and returns the best match
plus up to 3 alternatives.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from src.services.benchmark_service import enrich_models
from src.services.client_models import (
    ClientEntry,
    is_runnable,
    resolve_client,
    restriction_label,
)

logger = logging.getLogger(__name__)


@dataclass
class RouterConstraints:
    """Hard and soft constraints for model selection."""

    max_cost_per_1m_tokens: Optional[float] = None
    min_quality_score: Optional[float] = None
    min_context_window: Optional[int] = None
    preferred_provider: Optional[str] = None
    # Supported values: "code" | "chat" | "analysis" | "summarization" |
    #   "code_completion" | "code_chat" | "code_refactor" | "agentic_coding"
    task_type: Optional[str] = None
    prefer_low_latency: bool = False
    exclude_reasoning_models: bool = False
    # AI coding client / MCP client (see src/services/client_models.py). A
    # recognised client RESTRICTS the candidate set to models it can run before
    # ranking (v1.85.0); an unrecognised value changes nothing.
    client: Optional[str] = None
    # Deprecated alias of ``client`` (the six legacy values still resolve).
    ide_context: Optional[str] = None
    monthly_budget_usd: Optional[float] = None
    estimated_monthly_requests: int = 1000
    avg_input_tokens: int = 500
    avg_output_tokens: int = 200


@dataclass
class RouterResult:
    """Result of a routing decision."""

    recommended: object  # PricingMetrics
    score: float
    reason: str
    alternatives: List[object] = field(default_factory=list)
    # Set to True by RecommendationCache.get_or_compute() when this result is
    # served from cache rather than freshly computed. get_optimal_model()
    # itself never sets this — it always returns cached=False.
    cached: bool = False
    # Set when a recognised client applied: id, and how many models it can run
    # (counted before the other hard filters).
    client_id: Optional[str] = None
    runnable_models: Optional[int] = None


class ModelRouter:
    """Selects the optimal model from live pricing + quality enrichment."""

    # task_type -> list of use_case substrings to match
    _TASK_USE_CASES = {
        "code": ["code", "coding", "programming", "developer"],
        "chat": ["chat", "conversation", "general", "assistant"],
        "analysis": ["analysis", "reasoning", "research", "data"],
        "summarization": ["summar", "extract", "document"],
        # IDE / coding-specific task types
        "code_completion": ["code", "coding", "completion", "inline", "programming"],
        "code_chat": ["chat", "code", "coding", "assistant", "conversation"],
        "code_refactor": ["refactor", "code", "coding", "improvement", "analysis"],
        "agentic_coding": ["agentic", "code", "coding", "tool", "agent", "multi-step"],
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

        entry = _client_entry(constraints)
        runnable_count = (
            sum(1 for m in models if is_runnable(entry, m)) if entry is not None else None
        )

        candidates = self.rank_models(models, constraints)
        if not candidates:
            return None

        best = candidates[0]
        alternatives = candidates[1:4]

        best_score = self._score_model(best, constraints)
        avg_cost_1m = (best.cost_per_input_token + best.cost_per_output_token) / 2 * 1_000_000

        best_projected = None
        if constraints.monthly_budget_usd is not None:
            pricing_model = getattr(best, "pricing_model", "per_token")
            sub_price = getattr(best, "subscription_monthly_usd", None)
            if pricing_model == "subscription" and sub_price is not None:
                best_projected = sub_price
            else:
                best_projected = (
                    constraints.estimated_monthly_requests
                    * (
                        constraints.avg_input_tokens * best.cost_per_input_token
                        + constraints.avg_output_tokens * best.cost_per_output_token
                    )
                )

        reason = _build_reason(best, best_score, avg_cost_1m, constraints, projected_monthly=best_projected)

        return RouterResult(
            recommended=best,
            score=round(best_score, 4),
            reason=reason,
            alternatives=alternatives,
            client_id=entry.id if entry is not None else None,
            runnable_models=runnable_count,
        )

    def rank_models(self, models: List[object], constraints: RouterConstraints) -> List[object]:
        """Apply the hard filters and return every survivor, best first.

        This is exactly the filter + score + sort step get_optimal_model() has
        always run (recommended = ranked[0], alternatives = ranked[1:4]); it is
        exposed so the dispatch gateway (v1.77.0) can rank an already-enriched,
        already-eligibility-filtered candidate set with the same rules and the
        same order, without a second scoring implementation.
        """
        # --- Hard filters ---
        candidates = [m for m in models if self.hard_filter_reason(m, constraints) is None]

        if not candidates:
            return []

        # --- Scoring ---
        candidates.sort(key=lambda m: self._score_model(m, constraints), reverse=True)
        return candidates

    def hard_filter_reason(self, m, constraints: RouterConstraints) -> Optional[str]:
        """Name the first hard constraint `m` fails, or None when it passes them all.

        Checked in the order the filters have always run. The dispatch gateway
        reports the returned code in its receipt's exclusion list; rank_models()
        keeps exactly the models for which this returns None.
        """
        entry = _client_entry(constraints)
        if entry is not None and not is_runnable(entry, m):
            return "not_runnable_in_client"

        avg_cost_1m = (m.cost_per_input_token + m.cost_per_output_token) / 2 * 1_000_000

        if constraints.max_cost_per_1m_tokens is not None:
            if avg_cost_1m > constraints.max_cost_per_1m_tokens:
                return "above_cost_ceiling"

        if constraints.min_quality_score is not None:
            if m.quality_score is None or m.quality_score < constraints.min_quality_score:
                return "below_quality_floor"

        if constraints.min_context_window is not None:
            if m.context_window is None or m.context_window < constraints.min_context_window:
                return "context_window_too_small"

        if constraints.task_type is not None:
            task_keywords = self._TASK_USE_CASES.get(constraints.task_type.lower(), [])
            if task_keywords and m.use_cases:
                use_case_text = " ".join(m.use_cases).lower()
                if not any(kw in use_case_text for kw in task_keywords):
                    return "task_type_mismatch"

        if constraints.exclude_reasoning_models and m.is_reasoning_model:
            return "reasoning_model_excluded"

        if constraints.monthly_budget_usd is not None:
            pricing_model = getattr(m, "pricing_model", "per_token")
            sub_price = getattr(m, "subscription_monthly_usd", None)
            if pricing_model == "subscription" and sub_price is not None:
                # subscription tool: monthly cost IS the subscription price
                if sub_price > constraints.monthly_budget_usd:
                    return "over_monthly_budget"
            else:
                # per-token tool: project monthly cost from request estimates
                projected = (
                    constraints.estimated_monthly_requests
                    * (
                        constraints.avg_input_tokens * m.cost_per_input_token
                        + constraints.avg_output_tokens * m.cost_per_output_token
                    )
                )
                if projected > constraints.monthly_budget_usd:
                    return "over_monthly_budget"

        return None

    def _score_model(self, m, constraints: RouterConstraints) -> float:
        """quality_value_score with the constraint-driven boosts/penalties applied."""
        _latency_sensitive = constraints.task_type in ("code_completion",) or constraints.prefer_low_latency
        base = m.quality_value_score or 0.0

        # Preferred provider gets a 10% boost
        if (
            constraints.preferred_provider
            and m.provider.lower() == constraints.preferred_provider.lower()
        ):
            base *= 1.10

        # client: inside the restricted set, the client's own plans get a strong
        # boost; same-provider, non-native models get a mild one
        entry = _client_entry(constraints)
        if entry is not None:
            if any(np in m.provider.lower() for np in entry.native_providers):
                if getattr(m, "ide_native", False):
                    base *= 5.0   # strong: user is in this IDE, prefer its own tooling
                else:
                    base *= 1.15  # mild: matching provider but not an IDE-native tool

        # Latency-sensitive tasks: penalise slow models, reward fast ones
        if _latency_sensitive and m.latency_ms is not None:
            if m.latency_ms <= 300:
                base *= 1.20
            elif m.latency_ms >= 2000:
                base *= 0.70

        # Penalise high-token-cost reasoning models for token-efficiency tasks
        if constraints.task_type in ("code_completion", "code_chat"):
            if m.is_reasoning_model:
                base *= 0.60

        return base


def _client_entry(constraints: RouterConstraints) -> Optional[ClientEntry]:
    """The recognised client in effect (``client`` wins over legacy ``ide_context``)."""
    return resolve_client(constraints.client or constraints.ide_context)


def _build_reason(
    model,
    score: float,
    cost_1m: float,
    c: RouterConstraints,
    projected_monthly: Optional[float] = None,
) -> str:
    parts = [
        f"{model.model_name} ({model.provider})",
        f"quality_value_score={score:.2f}",
    ]
    if model.quality_score is not None:
        parts.append(f"quality={model.quality_score:.0f}/100")
    if getattr(model, "pricing_model", "per_token") == "subscription":
        sub = getattr(model, "subscription_monthly_usd", None)
        parts.append(f"subscription=${sub}/mo" if sub else "subscription pricing")
    else:
        parts.append(f"cost=${cost_1m:.2f}/1M tokens")
    if c.preferred_provider and model.provider.lower() == c.preferred_provider.lower():
        parts.append("preferred provider bonus applied")
    entry = _client_entry(c)
    if entry is not None:
        parts.append(f"client={entry.id} (restricted to {restriction_label(entry)})")
    if c.exclude_reasoning_models:
        parts.append("reasoning models excluded")
    if c.prefer_low_latency and model.latency_ms is not None:
        parts.append(f"latency={model.latency_ms:.0f}ms")
    if projected_monthly is not None:
        parts.append(f"projected_monthly=${projected_monthly:.2f}")
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
