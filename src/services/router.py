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
    # Supported values: "code" | "chat" | "analysis" | "summarization" |
    #   "code_completion" | "code_chat" | "code_refactor" | "agentic_coding"
    task_type: Optional[str] = None
    prefer_low_latency: bool = False
    exclude_reasoning_models: bool = False
    ide_context: Optional[str] = None  # "copilot"|"cursor"|"windsurf"|"claude_code"|"jetbrains"|"amazon_q"
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

    # ide_context -> provider names that are native to that IDE (get a score boost)
    _IDE_NATIVE_PROVIDERS = {
        "copilot": ["github copilot"],
        "cursor": ["cursor"],
        "windsurf": ["windsurf", "codeium"],
        "claude_code": ["anthropic"],
        "jetbrains": ["jetbrains ai"],
        "amazon_q": ["amazon q developer"],
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

            if constraints.exclude_reasoning_models and m.is_reasoning_model:
                continue

            if constraints.monthly_budget_usd is not None:
                pricing_model = getattr(m, "pricing_model", "per_token")
                sub_price = getattr(m, "subscription_monthly_usd", None)
                if pricing_model == "subscription" and sub_price is not None:
                    # subscription tool: monthly cost IS the subscription price
                    if sub_price > constraints.monthly_budget_usd:
                        continue
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
                        continue

            candidates.append(m)

        if not candidates:
            return None

        # --- Scoring ---
        _latency_sensitive = constraints.task_type in ("code_completion",) or constraints.prefer_low_latency

        def score_model(m) -> float:
            base = m.quality_value_score or 0.0

            # Preferred provider gets a 10% boost
            if (
                constraints.preferred_provider
                and m.provider.lower() == constraints.preferred_provider.lower()
            ):
                base *= 1.10

            # ide_context: native IDE tools get a strong boost; same provider, non-native gets a mild one
            if constraints.ide_context:
                native_providers = self._IDE_NATIVE_PROVIDERS.get(constraints.ide_context.lower(), [])
                if any(np in m.provider.lower() for np in native_providers):
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

        candidates.sort(key=score_model, reverse=True)
        best = candidates[0]
        alternatives = candidates[1:4]

        best_score = score_model(best)
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
        )


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
    if c.ide_context:
        parts.append(f"ide_context={c.ide_context}")
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
