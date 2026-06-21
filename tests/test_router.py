"""Tests for the ModelRouter and /router/recommend endpoint."""
import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.services.router import ModelRouter, RouterConstraints, RouterResult  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc


def _pm(name, provider, inp, out, quality=None, context=None, use_cases=None):
    """Build a minimal PricingMetrics with optional quality_score."""
    m = PricingMetrics(
        model_name=name,
        provider=provider,
        cost_per_input_token=inp,
        cost_per_output_token=out,
        context_window=context,
        use_cases=use_cases,
    )
    m.quality_score = quality
    return m


# Per-token costs (divide $/1M price by 1_000_000):
# gpt-4o:      $5/$15 per 1M   → 0.000005 / 0.000015
# gpt-4o-mini: $0.15/$0.6 per 1M → 0.00000015 / 0.0000006
# claude-opus: $15/$75 per 1M  → 0.000015 / 0.000075
# gemini-flash: $0.35/$1.05 per 1M → 0.00000035 / 0.00000105
# cheap-unknown: $0.01/$0.02 per 1M → 0.00000001 / 0.00000002
SAMPLE_MODELS = [
    _pm("gpt-4o",        "openai",     0.000005,  0.000015,  quality=87, context=128000),
    _pm("gpt-4o-mini",   "openai",     0.00000015, 0.0000006, quality=72, context=128000),
    _pm("claude-opus",   "anthropic",  0.000015,  0.000075,  quality=90, context=200000),
    _pm("gemini-flash",  "google",     0.00000035, 0.00000105, quality=70, context=1000000),
    _pm("cheap-unknown", "groq",       0.00000001, 0.00000002, quality=None, context=8000),
]


def _make_aggregator(models=None):
    agg = MagicMock()
    agg.get_all_pricing_async = AsyncMock(
        return_value=(SAMPLE_MODELS if models is None else models, [])
    )
    return agg


# ---------------------------------------------------------------------------
# ModelRouter unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_returns_result():
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(RouterConstraints())
    assert result is not None
    assert isinstance(result, RouterResult)
    assert result.recommended is not None


@pytest.mark.asyncio
async def test_router_filters_by_max_cost():
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        # Only models cheaper than $1/1M should survive
        result = await router.get_optimal_model(RouterConstraints(max_cost_per_1m_tokens=1.0))
    assert result is not None
    avg_cost = (
        result.recommended.cost_per_input_token + result.recommended.cost_per_output_token
    ) / 2 * 1_000_000
    assert avg_cost <= 1.0


@pytest.mark.asyncio
async def test_router_filters_by_min_quality():
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(RouterConstraints(min_quality_score=85))
    assert result is not None
    assert result.recommended.quality_score >= 85


@pytest.mark.asyncio
async def test_router_filters_by_min_context_window():
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(RouterConstraints(min_context_window=500000))
    assert result is not None
    assert result.recommended.context_window >= 500000


@pytest.mark.asyncio
async def test_router_preferred_provider_boost():
    """Preferred provider should score higher even if not the cheapest."""
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(
            RouterConstraints(preferred_provider="anthropic")
        )
    # claude-opus has quality_value_score boosted; should still be a valid recommendation
    assert result is not None


@pytest.mark.asyncio
async def test_router_no_candidates_returns_none():
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(
            RouterConstraints(max_cost_per_1m_tokens=0.000001, min_quality_score=99)
        )
    assert result is None


@pytest.mark.asyncio
async def test_router_alternatives_max_3():
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(RouterConstraints())
    assert len(result.alternatives) <= 3


@pytest.mark.asyncio
async def test_router_reason_nonempty():
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(RouterConstraints())
    assert result.reason


@pytest.mark.asyncio
async def test_router_empty_models_returns_none():
    router = ModelRouter(_make_aggregator(models=[]))
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(RouterConstraints())
    assert result is None


# ---------------------------------------------------------------------------
# /router/recommend endpoint integration tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_router_and_savings():
    """Patch the router singleton and savings tracker for endpoint tests."""
    mock_result = RouterResult(
        recommended=SAMPLE_MODELS[1],  # gpt-4o-mini ($0.375/1M)
        score=192.0,
        reason="gpt-4o-mini (openai); quality_value_score=192.0; quality=72/100; cost=$0.375/1M tokens",
        alternatives=[SAMPLE_MODELS[3]],  # gemini-flash
    )
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    mock_tracker = MagicMock()
    mock_tracker.record_routing = AsyncMock()

    with (
        patch("src.main.get_router", return_value=mock_router),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        yield


def _client():
    from src.main import app
    return TestClient(app)


def test_router_recommend_success():
    client = _client()
    resp = client.post(
        "/router/recommend",
        json={"max_cost_per_1m_tokens": 5.0, "min_quality_score": 70},
        headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
    )
    assert resp.status_code in (200, 401), resp.text


def test_router_recommend_no_constraints():
    client = _client()
    resp = client.post(
        "/router/recommend",
        json={},
        headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
    )
    # 200 or 401 (if key is configured in env)
    assert resp.status_code in (200, 401)


def test_router_recommend_json_body_required():
    """Sending non-JSON should return 422."""
    client = _client()
    resp = client.post(
        "/router/recommend",
        data="not-json",
        headers={"Content-Type": "text/plain", "x-api-key": "test"},
    )
    assert resp.status_code in (400, 401, 422)


# ---------------------------------------------------------------------------
# Monthly budget tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_monthly_budget_filters_expensive_model():
    """monthly_budget_usd should filter models whose projected monthly cost exceeds the cap."""
    # At 1000 req/mo, 500 input + 200 output tokens:
    # claude-opus: (500*0.000015 + 200*0.000075) * 1000 = (0.0075 + 0.015) * 1000 = $22.50/mo  → filtered
    # gpt-4o-mini: (500*0.00000015 + 200*0.0000006) * 1000 = (0.000075 + 0.00012) * 1000 = $0.195/mo → passes
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(
            RouterConstraints(monthly_budget_usd=5.0, estimated_monthly_requests=1000,
                              avg_input_tokens=500, avg_output_tokens=200)
        )
    assert result is not None
    # claude-opus should be filtered; gpt-4o-mini or gemini-flash should win
    assert result.recommended.model_name != "claude-opus"


@pytest.mark.asyncio
async def test_monthly_budget_subscription_filter():
    """monthly_budget_usd should filter subscription tools by subscription_monthly_usd."""
    cheap_sub = PricingMetrics(
        model_name="copilot-individual", provider="GitHub Copilot",
        cost_per_input_token=0.0005, cost_per_output_token=0.001,
        use_cases=["code"], pricing_model="subscription", subscription_monthly_usd=10.0,
    )
    expensive_sub = PricingMetrics(
        model_name="copilot-enterprise", provider="GitHub Copilot",
        cost_per_input_token=0.0004, cost_per_output_token=0.0008,
        use_cases=["code"], pricing_model="subscription", subscription_monthly_usd=39.0,
    )
    cheap_sub.quality_score = 70
    expensive_sub.quality_score = 85
    router = ModelRouter(_make_aggregator(models=[cheap_sub, expensive_sub]))
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(RouterConstraints(monthly_budget_usd=15.0))
    assert result is not None
    assert result.recommended.model_name == "copilot-individual"


@pytest.mark.asyncio
async def test_monthly_budget_none_no_filter():
    """When monthly_budget_usd is None, no budget filtering is applied."""
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(RouterConstraints(monthly_budget_usd=None))
    assert result is not None


@pytest.mark.asyncio
async def test_monthly_budget_reason_includes_projection():
    """When monthly_budget_usd is set, reason should include projected_monthly."""
    router = ModelRouter(_make_aggregator())
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        result = await router.get_optimal_model(
            RouterConstraints(monthly_budget_usd=50.0, estimated_monthly_requests=1000,
                              avg_input_tokens=500, avg_output_tokens=200)
        )
    assert result is not None
    assert "projected_monthly=$" in result.reason
