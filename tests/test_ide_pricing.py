"""Tests for IDEPricingService and the new RouterConstraints fields."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.ide_pricing import IDEPricingService, _IDE_TOOLS  # noqa: E402
from src.services.router import ModelRouter, RouterConstraints  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC_DT = None  # let PricingMetrics use its default factory


def _pm(
    name,
    provider,
    inp,
    out,
    quality=None,
    context=None,
    use_cases=None,
    latency_ms=None,
    is_reasoning=False,
    ide_native=False,
):
    m = PricingMetrics(
        model_name=name,
        provider=provider,
        cost_per_input_token=inp,
        cost_per_output_token=out,
        context_window=context,
        use_cases=use_cases or [],
        latency_ms=latency_ms,
        is_reasoning_model=is_reasoning,
        ide_native=ide_native,
    )
    m.quality_score = quality
    return m


SAMPLE_MODELS = [
    _pm("gpt-4o",        "openai",    0.000005,   0.000015,  quality=87,  context=128000, use_cases=["Advanced coding", "chat"], latency_ms=500),
    _pm("gpt-4o-mini",   "openai",    0.00000015, 0.0000006, quality=72,  context=128000, use_cases=["chat", "Fast chat"],       latency_ms=250),
    _pm("claude-opus",   "anthropic", 0.000015,   0.000075,  quality=90,  context=200000, use_cases=["Advanced coding"],         latency_ms=800),
    _pm("o1-preview",    "openai",    0.000015,   0.000060,  quality=92,  context=128000, use_cases=["reasoning", "code"],       latency_ms=3000, is_reasoning=True),
    _pm("copilot-pro",   "github copilot", 0.0005, 0.0010,  quality=70,  context=8000,   use_cases=["code", "inline", "completion"], latency_ms=200, ide_native=True),
    _pm("cursor-pro",    "cursor",    0.0002,     0.0004,    quality=75,  context=128000, use_cases=["code", "agentic"],         latency_ms=350, ide_native=True),
]


def _make_aggregator(models=None):
    agg = MagicMock()
    agg.get_all_pricing_async = AsyncMock(
        return_value=(SAMPLE_MODELS if models is None else models, [])
    )
    return agg


# ---------------------------------------------------------------------------
# IDEPricingService tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ide_service_returns_models():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    assert len(models) > 0


@pytest.mark.asyncio
async def test_ide_service_count_matches_tool_list():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    assert len(models) == len(_IDE_TOOLS)


@pytest.mark.asyncio
async def test_ide_service_all_are_pricing_metrics():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    for m in models:
        assert isinstance(m, PricingMetrics)


@pytest.mark.asyncio
async def test_ide_service_subscription_monthly_set():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    for m in models:
        assert m.subscription_monthly_usd is not None, f"{m.model_name} missing subscription_monthly_usd"


@pytest.mark.asyncio
async def test_ide_service_pricing_model_is_subscription():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    for m in models:
        assert m.pricing_model == "subscription", f"{m.model_name} has wrong pricing_model"


@pytest.mark.asyncio
async def test_ide_service_providers_present():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    providers = {m.provider for m in models}
    assert "GitHub Copilot" in providers
    assert "Cursor" in providers
    assert "Windsurf" in providers
    assert "JetBrains AI" in providers
    assert "Amazon Q Developer" in providers
    assert "Tabnine" in providers


@pytest.mark.asyncio
async def test_ide_service_copilot_tiers():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    copilot = [m for m in models if m.provider == "GitHub Copilot"]
    assert len(copilot) == 3
    names = {m.model_name for m in copilot}
    assert "copilot-individual" in names
    assert "copilot-business" in names
    assert "copilot-enterprise" in names


@pytest.mark.asyncio
async def test_ide_service_cursor_tiers():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    cursor = [m for m in models if m.provider == "Cursor"]
    assert len(cursor) == 3


@pytest.mark.asyncio
async def test_ide_service_inline_completion_flags():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    # All native IDE tools should support inline completion
    ide_tools = [m for m in models if m.ide_native]
    assert all(m.supports_inline_completion for m in ide_tools)


@pytest.mark.asyncio
async def test_ide_service_use_cases_nonempty():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    for m in models:
        assert m.use_cases, f"{m.model_name} has no use_cases"


@pytest.mark.asyncio
async def test_ide_service_positive_costs():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    for m in models:
        assert m.cost_per_input_token >= 0
        assert m.cost_per_output_token >= 0


@pytest.mark.asyncio
async def test_ide_service_free_tiers_exist():
    svc = IDEPricingService()
    models = await svc.fetch_pricing_data()
    free = [m for m in models if m.subscription_monthly_usd == 0.0]
    assert len(free) >= 3, "Expected at least 3 free-tier IDE tools"


# ---------------------------------------------------------------------------
# RouterConstraints — exclude_reasoning_models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exclude_reasoning_models_removes_o1():
    router = ModelRouter(_make_aggregator())
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(
            RouterConstraints(exclude_reasoning_models=True)
        )
    assert result is not None
    assert not result.recommended.is_reasoning_model


@pytest.mark.asyncio
async def test_exclude_reasoning_models_false_keeps_o1():
    router = ModelRouter(_make_aggregator())
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(
            RouterConstraints(min_quality_score=91, exclude_reasoning_models=False)
        )
    # o1-preview (quality=92) should be reachable
    assert result is not None


@pytest.mark.asyncio
async def test_exclude_reasoning_all_models_are_reasoning_returns_none():
    only_reasoning = [
        _pm("o1", "openai", 0.001, 0.003, quality=92, is_reasoning=True),
    ]
    router = ModelRouter(_make_aggregator(models=only_reasoning))
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(
            RouterConstraints(exclude_reasoning_models=True)
        )
    assert result is None


# ---------------------------------------------------------------------------
# RouterConstraints — prefer_low_latency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prefer_low_latency_boosts_fast_model():
    """With prefer_low_latency, the 250 ms gpt-4o-mini should beat 800 ms claude-opus
    even though claude-opus has a higher quality score."""
    # Narrow the field to just these two to make the assertion deterministic
    low_latency_models = [
        _pm("gpt-4o-mini", "openai",    0.00000015, 0.0000006, quality=72, latency_ms=250, use_cases=["chat"]),
        _pm("claude-opus",  "anthropic", 0.000015,   0.000075,  quality=90, latency_ms=800, use_cases=["chat"]),
    ]
    router = ModelRouter(_make_aggregator(models=low_latency_models))
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(
            RouterConstraints(prefer_low_latency=True)
        )
    assert result is not None
    assert result.recommended.model_name == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_prefer_low_latency_false_returns_result():
    router = ModelRouter(_make_aggregator())
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(RouterConstraints(prefer_low_latency=False))
    assert result is not None


# ---------------------------------------------------------------------------
# RouterConstraints — ide_context
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ide_context_copilot_boosts_copilot_provider():
    """ide_context='copilot' should strongly boost ide_native GitHub Copilot models.

    Both models use the same cost scale so quality_value_score is comparable.
    The 5× boost for ide_native tools ensures Copilot wins regardless.
    """
    models = [
        _pm("gpt-4o",      "openai",         0.000005, 0.000015, quality=87, use_cases=["code"]),
        _pm("copilot-pro", "github copilot", 0.000008, 0.000015, quality=70, use_cases=["code", "inline"], ide_native=True),
    ]
    router = ModelRouter(_make_aggregator(models=models))
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(
            RouterConstraints(ide_context="copilot")
        )
    assert result is not None
    assert result.recommended.provider.lower() == "github copilot"


@pytest.mark.asyncio
async def test_ide_context_cursor_boosts_cursor_provider():
    """ide_context='cursor' should strongly boost ide_native Cursor models."""
    models = [
        _pm("gpt-4o",     "openai", 0.000005, 0.000015, quality=87, use_cases=["code"]),
        _pm("cursor-pro", "cursor", 0.000008, 0.000015, quality=75, use_cases=["code", "agentic"], ide_native=True),
    ]
    router = ModelRouter(_make_aggregator(models=models))
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(RouterConstraints(ide_context="cursor"))
    assert result is not None
    assert result.recommended.provider.lower() == "cursor"


@pytest.mark.asyncio
async def test_ide_context_unknown_value_does_not_crash():
    router = ModelRouter(_make_aggregator())
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(RouterConstraints(ide_context="nonexistent_ide"))
    assert result is not None


@pytest.mark.asyncio
async def test_ide_context_none_no_boost():
    """No ide_context → result should be the same as a plain call."""
    router = ModelRouter(_make_aggregator())
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result_plain = await router.get_optimal_model(RouterConstraints())
        result_no_ctx = await router.get_optimal_model(RouterConstraints(ide_context=None))
    assert result_plain is not None
    assert result_no_ctx is not None
    assert result_plain.recommended.model_name == result_no_ctx.recommended.model_name


# ---------------------------------------------------------------------------
# RouterConstraints — code_completion task type
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_code_completion_task_type_penalises_reasoning():
    """code_completion should score reasoning models lower than fast non-reasoning models."""
    models = [
        _pm("o1-preview",  "openai", 0.000015, 0.00006, quality=92,
            use_cases=["code", "coding"], is_reasoning=True, latency_ms=3000),
        _pm("gpt-4o-mini", "openai", 0.00000015, 0.0000006, quality=72,
            use_cases=["code", "coding", "completion"], latency_ms=250),
    ]
    router = ModelRouter(_make_aggregator(models=models))
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(
            RouterConstraints(task_type="code_completion", exclude_reasoning_models=False)
        )
    assert result is not None
    # gpt-4o-mini should win despite lower quality due to reasoning penalty + latency boost
    assert result.recommended.model_name == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_agentic_coding_task_type_filters_correctly():
    """agentic_coding task should match models with 'agentic' in use_cases."""
    models = [
        _pm("cursor-pro",  "cursor",  0.0002, 0.0004, quality=75,
            use_cases=["agentic", "code", "coding", "multi-step"]),
        _pm("gpt-4o-mini", "openai",  0.00000015, 0.0000006, quality=72,
            use_cases=["chat", "general"]),
    ]
    router = ModelRouter(_make_aggregator(models=models))
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(RouterConstraints(task_type="agentic_coding"))
    assert result is not None
    assert result.recommended.model_name == "cursor-pro"


# ---------------------------------------------------------------------------
# Reason string reflects new constraint signals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reason_mentions_ide_context():
    router = ModelRouter(_make_aggregator())
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(RouterConstraints(ide_context="cursor"))
    assert result is not None
    assert "ide_context=cursor" in result.reason


@pytest.mark.asyncio
async def test_reason_mentions_reasoning_excluded():
    router = ModelRouter(_make_aggregator())
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(RouterConstraints(exclude_reasoning_models=True))
    assert result is not None
    assert "reasoning models excluded" in result.reason


@pytest.mark.asyncio
async def test_reason_shows_subscription_for_ide_tool():
    ide_models = [
        _pm("copilot-pro", "github copilot", 0.0005, 0.001, quality=70,
            use_cases=["code"]),
    ]
    # Give it a subscription_monthly_usd so the reason branch triggers
    ide_models[0].pricing_model = "subscription"
    ide_models[0].subscription_monthly_usd = 19.0

    router = ModelRouter(_make_aggregator(models=ide_models))
    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)
    ):
        result = await router.get_optimal_model(RouterConstraints())
    assert result is not None
    assert "subscription=$19.0/mo" in result.reason
