"""Tests for the compare_costs MCP tool."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.pricing import PricingMetrics
from mcp.tools.compare_costs import CompareCostsTool


def _make_model(name, provider, inp, out):
    return PricingMetrics(
        model_name=name,
        provider=provider,
        cost_per_input_token=inp,
        cost_per_output_token=out,
        pricing_model="per_token",
    )


SAMPLE_MODELS = [
    _make_model("cheap-model", "ProviderA", 0.000003, 0.000015),  # $3 / $15 per 1M tokens
    _make_model("mid-model", "Anthropic", 0.000001, 0.000005),
]


@pytest.fixture
def tool():
    t = CompareCostsTool()
    mock_svc = MagicMock()
    mock_svc.get_all_pricing_async = AsyncMock(return_value=(SAMPLE_MODELS, []))
    t.service = mock_svc
    return t


@pytest.mark.asyncio
async def test_compare_costs_absolute_dollar_values(tool):
    """input_cost/output_cost must equal rate * token count exactly (within
    rounding) — not that value divided by an additional 1000. Uses an
    absolute dollar assertion so a future regression that scales all models
    equally is caught, not just relative ordering."""
    result = await tool.execute({
        "model_names": ["cheap-model"],
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
    })
    assert result["success"] is True
    entry = result["models"][0]
    assert entry["is_available"] is True
    # $3/1M input tokens * 1M tokens = $3.00; $15/1M output tokens * 1M = $15.00
    assert abs(entry["input_cost"] - 3.0) < 1e-6
    assert abs(entry["output_cost"] - 15.0) < 1e-6
    assert abs(entry["total_cost"] - 18.0) < 1e-6


@pytest.mark.asyncio
async def test_compare_costs_absolute_dollar_values_small_volume(tool):
    """Same check at a smaller, non-round token count."""
    result = await tool.execute({
        "model_names": ["mid-model"],
        "input_tokens": 12345,
        "output_tokens": 6789,
    })
    assert result["success"] is True
    entry = result["models"][0]
    expected_input = 0.000001 * 12345
    expected_output = 0.000005 * 6789
    assert abs(entry["input_cost"] - round(expected_input, 6)) < 1e-9
    assert abs(entry["output_cost"] - round(expected_output, 6)) < 1e-9
    assert abs(entry["total_cost"] - round(expected_input + expected_output, 6)) < 1e-9


@pytest.mark.asyncio
async def test_compare_costs_multiple_models_cheapest_and_most_expensive(tool):
    result = await tool.execute({
        "model_names": ["cheap-model", "mid-model"],
        "input_tokens": 1000,
        "output_tokens": 1000,
    })
    assert result["success"] is True
    assert result["cheapest_model"] == "mid-model"
    assert result["most_expensive_model"] == "cheap-model"
    assert result["cost_range"]["min"] < result["cost_range"]["max"]


@pytest.mark.asyncio
async def test_compare_costs_model_not_found(tool):
    """compare_costs' exact-match-by-name lookup limitation is unchanged by
    this fix — an unknown name still reports not-found rather than a
    fuzzy/suggestion match."""
    result = await tool.execute({
        "model_names": ["nonexistent-model-xyz"],
        "input_tokens": 100,
        "output_tokens": 100,
    })
    assert result["success"] is True
    entry = result["models"][0]
    assert entry["is_available"] is False
    assert "not found" in entry["error"]


@pytest.mark.asyncio
async def test_compare_costs_case_insensitive_exact_match(tool):
    """Existing exact-match (case-insensitive) lookup behavior is preserved."""
    result = await tool.execute({
        "model_names": ["CHEAP-MODEL"],
        "input_tokens": 1000,
        "output_tokens": 1000,
    })
    assert result["success"] is True
    assert result["models"][0]["is_available"] is True
    assert result["models"][0]["model_name"] == "cheap-model"


@pytest.mark.asyncio
async def test_compare_costs_zero_tokens(tool):
    result = await tool.execute({
        "model_names": ["cheap-model"],
        "input_tokens": 0,
        "output_tokens": 0,
    })
    assert result["success"] is True
    entry = result["models"][0]
    assert entry["input_cost"] == 0.0
    assert entry["output_cost"] == 0.0
    assert entry["cost_per_1m_tokens"] == 0


@pytest.mark.asyncio
async def test_compare_costs_missing_model_names(tool):
    result = await tool.execute({"input_tokens": 100, "output_tokens": 100})
    assert result["success"] is False
    assert "model_names" in result["error"]


@pytest.mark.asyncio
async def test_compare_costs_missing_token_counts(tool):
    result = await tool.execute({"model_names": ["cheap-model"]})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_compare_costs_negative_tokens(tool):
    result = await tool.execute({
        "model_names": ["cheap-model"],
        "input_tokens": -1,
        "output_tokens": 100,
    })
    assert result["success"] is False
