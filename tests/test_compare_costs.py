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

# A richer catalogue used to exercise candidate-suggestion behavior: several
# "claude"-family names (a mix that also contain "sonnet"), one unrelated
# model, and enough claude variants to verify capping.
CLAUDE_LIKE_MODELS = [
    _make_model("claude-3-opus-20240229", "Anthropic", 0.000015, 0.000075),
    _make_model("claude-3-sonnet-20240229", "Anthropic", 0.000003, 0.000015),
    _make_model("claude-3-haiku-20240307", "Anthropic", 0.00000025, 0.00000125),
    _make_model("claude-3-5-sonnet-20240620", "Anthropic", 0.000003, 0.000015),
    _make_model("claude-3-5-sonnet-20241022", "Anthropic", 0.000003, 0.000015),
    _make_model("claude-3-7-sonnet-20250219", "Anthropic", 0.000003, 0.000015),
    _make_model("claude-2.1", "Anthropic", 0.000008, 0.000024),
    _make_model("claude-2.0", "Anthropic", 0.000008, 0.000024),
    _make_model("claude-instant-1.2", "Anthropic", 0.0000008, 0.0000024),
    _make_model("claude-sonnet-4-20250514", "Anthropic", 0.000003, 0.000015),
    _make_model("claude-sonnet-4-6", "Anthropic", 0.000003, 0.000015),
    _make_model("gpt-4o", "OpenAI", 0.000005, 0.000015),
]


@pytest.fixture
def tool():
    t = CompareCostsTool()
    mock_svc = MagicMock()
    mock_svc.get_all_pricing_async = AsyncMock(return_value=(SAMPLE_MODELS, []))
    t.service = mock_svc
    return t


@pytest.fixture
def tool_with_claude_catalogue():
    t = CompareCostsTool()
    mock_svc = MagicMock()
    mock_svc.get_all_pricing_async = AsyncMock(return_value=(CLAUDE_LIKE_MODELS, []))
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


@pytest.mark.asyncio
async def test_compare_costs_partial_name_returns_capped_candidates(
    tool_with_claude_catalogue,
):
    """A brand-only query like 'claude' has no exact match but relates to
    many real catalogued models — the response must surface some of them
    (not silently pick one to price), capped to a reasonable number even
    though more than that many real models match."""
    result = await tool_with_claude_catalogue.execute({
        "model_names": ["claude"],
        "input_tokens": 100,
        "output_tokens": 100,
    })
    assert result["success"] is True
    entry = result["models"][0]
    assert entry["is_available"] is False
    assert "candidates" in entry
    # 10 sample models contain "claude" — candidates must be capped.
    assert 1 <= len(entry["candidates"]) <= 8
    assert all("claude" in c.lower() for c in entry["candidates"])
    # Never silently priced as a match.
    assert "total_cost" not in entry
    assert result["cheapest_model"] is None
    assert result["most_expensive_model"] is None


@pytest.mark.asyncio
async def test_compare_costs_partial_name_multiple_candidates_not_resolved(
    tool_with_claude_catalogue,
):
    """A more specific but still partial query like 'claude sonnet' must
    surface more than one candidate and must not silently resolve to one
    of them to compute a cost."""
    result = await tool_with_claude_catalogue.execute({
        "model_names": ["claude sonnet"],
        "input_tokens": 100,
        "output_tokens": 100,
    })
    assert result["success"] is True
    entry = result["models"][0]
    assert entry["is_available"] is False
    assert len(entry["candidates"]) > 1
    assert "total_cost" not in entry


@pytest.mark.asyncio
async def test_compare_costs_no_related_model_returns_empty_candidates(
    tool_with_claude_catalogue,
):
    """A query with no relation to any real catalogued model must clearly
    report not-found with an empty candidate list — never a fabricated
    guess."""
    result = await tool_with_claude_catalogue.execute({
        "model_names": ["totally-fake-model-xyz"],
        "input_tokens": 100,
        "output_tokens": 100,
    })
    assert result["success"] is True
    entry = result["models"][0]
    assert entry["is_available"] is False
    assert entry["candidates"] == []


@pytest.mark.asyncio
async def test_compare_costs_mixed_exact_and_partial_names(
    tool_with_claude_catalogue,
):
    """One exact match and one unresolved name in the same call must each
    be handled independently: the exact match still prices correctly, and
    the unresolved entry gets the improved not-found information."""
    result = await tool_with_claude_catalogue.execute({
        "model_names": ["claude-3-opus-20240229", "claude"],
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
    })
    assert result["success"] is True
    exact_entry, partial_entry = result["models"]

    assert exact_entry["is_available"] is True
    assert exact_entry["model_name"] == "claude-3-opus-20240229"
    assert abs(exact_entry["input_cost"] - 15.0) < 1e-6
    assert abs(exact_entry["output_cost"] - 75.0) < 1e-6
    assert abs(exact_entry["total_cost"] - 90.0) < 1e-6

    assert partial_entry["is_available"] is False
    assert "not found" in partial_entry["error"]
    assert len(partial_entry["candidates"]) > 0
    assert "total_cost" not in partial_entry


@pytest.mark.asyncio
async def test_compare_costs_not_found_still_has_empty_candidates_key(tool):
    """Regression guard: the not-found branch's response shape always
    includes a 'candidates' key (never omitted), even when empty."""
    result = await tool.execute({
        "model_names": ["nonexistent-model-xyz"],
        "input_tokens": 100,
        "output_tokens": 100,
    })
    entry = result["models"][0]
    assert entry["candidates"] == []


@pytest.mark.asyncio
async def test_compare_costs_short_query_yields_no_candidates(
    tool_with_claude_catalogue,
):
    """A very short/ambiguous query (normalized length < 2) must not match
    broadly against the catalogue — guards against near-universal
    substrings producing a noisy candidate list."""
    result = await tool_with_claude_catalogue.execute({
        "model_names": ["a"],
        "input_tokens": 100,
        "output_tokens": 100,
    })
    entry = result["models"][0]
    assert entry["is_available"] is False
    assert entry["candidates"] == []
