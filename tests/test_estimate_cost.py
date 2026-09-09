"""Tests for the estimate_cost MCP tool (single-model cost estimate).

Covers the divide-by-1000 regression: cost_per_input_token / cost_per_output_token
are dollars-per-single-token, so input_cost/output_cost must equal rate * token
count exactly (within rounding), not that value divided by an additional 1000.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.pricing import PricingMetrics
from mcp.tools.estimate_cost import EstimateCostTool


def _make_model(name, provider, inp, out):
    return PricingMetrics(
        model_name=name,
        provider=provider,
        cost_per_input_token=inp,
        cost_per_output_token=out,
        pricing_model="per_token",
    )


SAMPLE_MODEL = _make_model("cheap-model", "ProviderA", 0.000003, 0.000015)  # $3 / $15 per 1M tokens


@pytest.fixture
def tool():
    t = EstimateCostTool()
    mock_svc = MagicMock()
    mock_svc.find_model_pricing = AsyncMock(return_value=SAMPLE_MODEL)
    t.service = mock_svc
    return t


@pytest.mark.asyncio
async def test_estimate_cost_absolute_dollar_values(tool):
    """Absolute dollar assertion — catches a future regression that scales
    all models equally, not just relative ordering."""
    result = await tool.execute({
        "model_name": "cheap-model",
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
    })
    assert result["success"] is True
    # $3/1M input tokens * 1M tokens = $3.00; $15/1M output tokens * 1M = $15.00
    assert abs(result["input_cost"] - 3.0) < 1e-6
    assert abs(result["output_cost"] - 15.0) < 1e-6
    assert abs(result["total_cost"] - 18.0) < 1e-6


@pytest.mark.asyncio
async def test_estimate_cost_absolute_dollar_values_small_volume(tool):
    result = await tool.execute({
        "model_name": "cheap-model",
        "input_tokens": 12345,
        "output_tokens": 6789,
    })
    assert result["success"] is True
    expected_input = round(0.000003 * 12345, 6)
    expected_output = round(0.000015 * 6789, 6)
    assert abs(result["input_cost"] - expected_input) < 1e-9
    assert abs(result["output_cost"] - expected_output) < 1e-9
    assert abs(result["total_cost"] - round(expected_input + expected_output, 6)) < 1e-9


@pytest.mark.asyncio
async def test_estimate_cost_zero_tokens(tool):
    result = await tool.execute({
        "model_name": "cheap-model",
        "input_tokens": 0,
        "output_tokens": 0,
    })
    assert result["success"] is True
    assert result["input_cost"] == 0.0
    assert result["output_cost"] == 0.0
    assert result["total_cost"] == 0.0


@pytest.mark.asyncio
async def test_estimate_cost_model_not_found(tool):
    tool.service.find_model_pricing = AsyncMock(return_value=None)
    result = await tool.execute({
        "model_name": "nonexistent-model-xyz",
        "input_tokens": 100,
        "output_tokens": 100,
    })
    assert result["success"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_estimate_cost_missing_model_name(tool):
    result = await tool.execute({"input_tokens": 100, "output_tokens": 100})
    assert result["success"] is False
    assert "model_name" in result["error"]


@pytest.mark.asyncio
async def test_estimate_cost_negative_tokens(tool):
    result = await tool.execute({
        "model_name": "cheap-model",
        "input_tokens": -1,
        "output_tokens": 100,
    })
    assert result["success"] is False
