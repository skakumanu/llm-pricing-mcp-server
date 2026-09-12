"""Tests for AnalyzeSessionUsageTool — on-demand session usage analysis + recommendation.

Covers the Spec's acceptance criteria:
 1. A session_id with recorded usage returns totals + per-model breakdown scoped to that
    session only (not a time window or org-wide aggregate).
 2. The response includes a recommendation (or explicit "already optimal") paired with a
    cost-per-1M rate and estimated savings/increase computed against the session's actual
    token volumes.
 3. The rationale references session-specific facts (request count, tokens, cost profile).
 4. A session_id with zero recorded usage returns success:false, has_data:false, no
    recommendation.
 5. A session that mixed two+ models returns a full per-model breakdown and one
    session-level recommendation.
 6. The tool can be invoked standalone with just a session_id — no scheduling/config needed.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp.tools.analyze_session_usage import AnalyzeSessionUsageTool  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.router import RouterResult  # noqa: E402


def _pricing_metrics(model_name, provider, cost_in, cost_out, quality_score=None):
    return PricingMetrics(
        model_name=model_name, provider=provider,
        cost_per_input_token=cost_in, cost_per_output_token=cost_out,
        quality_score=quality_score,
    )


def _no_data_usage(session_id):
    return {
        "session_id": session_id,
        "total_requests": 0,
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "first_occurred_at": None,
        "last_occurred_at": None,
        "by_model": [],
    }


@pytest.mark.asyncio
async def test_missing_session_id():
    tool = AnalyzeSessionUsageTool()
    result = await tool.execute({})
    assert result["success"] is False
    assert "session_id" in result["error"]


@pytest.mark.asyncio
async def test_empty_session_id_string():
    tool = AnalyzeSessionUsageTool()
    result = await tool.execute({"session_id": "   "})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_no_data_returns_no_recommendation():
    """AC4: zero recorded usage -> success:false, has_data:false, no recommendation object."""
    tool = AnalyzeSessionUsageTool()
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value=_no_data_usage("ghost-session"))
    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=mock_tracker):
        result = await tool.execute({"session_id": "ghost-session"})
    assert result["success"] is False
    assert result["has_data"] is False
    assert "recommendation" not in result
    assert result["session_id"] == "ghost-session"


@pytest.mark.asyncio
async def test_tracker_not_initialized():
    tool = AnalyzeSessionUsageTool()
    with patch(
        "mcp.tools.analyze_session_usage.get_usage_tracker",
        side_effect=RuntimeError("not initialized"),
    ):
        result = await tool.execute({"session_id": "sess-1"})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_single_model_session_reports_totals_and_recommendation():
    """AC1 + AC2 + AC3: session-scoped totals, a recommendation with $/1M + savings, and
    a rationale grounded in this session's facts."""
    tool = AnalyzeSessionUsageTool()
    usage = {
        "session_id": "sess-1",
        "total_requests": 10,
        "total_cost_usd": 1.0,
        "total_input_tokens": 100_000,
        "total_output_tokens": 50_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 2000.0,
        "by_model": [
            {
                "model_name": "gpt-4o", "provider": "openai", "request_count": 10,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 1.0,
            }
        ],
    }
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value=usage)

    cheaper = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006)
    mock_result = RouterResult(recommended=cheaper, score=100.0, reason="test", alternatives=[])
    mock_router = AsyncMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=mock_tracker):
        tool.router = mock_router
        result = await tool.execute({"session_id": "sess-1"})

    assert result["success"] is True
    assert result["has_data"] is True
    # Session-scoped totals, not a time window or org aggregate
    assert result["total_requests"] == 10
    assert result["total_input_tokens"] == 100_000
    assert result["total_output_tokens"] == 50_000
    assert result["total_cost_usd"] == 1.0
    assert len(result["by_model"]) == 1

    rec = result["recommendation"]
    assert rec["recommended_model"] == "gpt-4o-mini"
    assert rec["recommended_provider"] == "openai"
    assert rec["recommended_cost_per_1m_tokens"] > 0
    assert "savings_usd" in rec
    assert rec["is_optimal"] is False
    # Rationale grounded in this session's own facts, not generic boilerplate
    assert "sess-1" in rec["rationale"]
    assert "10 request" in rec["rationale"]
    assert "150000" in rec["rationale"] or "150,000" in rec["rationale"]


@pytest.mark.asyncio
async def test_is_optimal_when_current_choice_matches_recommendation():
    """AC2: explicit 'current choice is optimal' verdict when applicable."""
    tool = AnalyzeSessionUsageTool()
    usage = {
        "session_id": "sess-optimal",
        "total_requests": 5,
        "total_cost_usd": 0.5,
        "total_input_tokens": 50_000,
        "total_output_tokens": 25_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 1500.0,
        "by_model": [
            {
                "model_name": "gpt-4o-mini", "provider": "openai", "request_count": 5,
                "input_tokens": 50_000, "output_tokens": 25_000, "cost_usd": 0.5,
            }
        ],
    }
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value=usage)

    same_model = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006)
    mock_result = RouterResult(recommended=same_model, score=100.0, reason="test", alternatives=[])
    mock_router = AsyncMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=mock_tracker):
        tool.router = mock_router
        result = await tool.execute({"session_id": "sess-optimal"})

    rec = result["recommendation"]
    assert rec["is_optimal"] is True
    assert rec["savings_usd"] == 0.0
    assert "sess-optimal" in rec["rationale"]


@pytest.mark.asyncio
async def test_multi_model_session_full_breakdown_and_session_level_recommendation():
    """AC5: 2+ distinct models used -> full per-model breakdown, one session-level recommendation."""
    tool = AnalyzeSessionUsageTool()
    usage = {
        "session_id": "sess-multi",
        "total_requests": 4,
        "total_cost_usd": 2.0,
        "total_input_tokens": 200_000,
        "total_output_tokens": 100_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 3000.0,
        "by_model": [
            {
                "model_name": "gpt-4o", "provider": "openai", "request_count": 2,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 1.5,
            },
            {
                "model_name": "claude-haiku", "provider": "anthropic", "request_count": 2,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 0.5,
            },
        ],
    }
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value=usage)

    recommended = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006)
    mock_result = RouterResult(recommended=recommended, score=100.0, reason="test", alternatives=[])
    mock_router = AsyncMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=mock_tracker):
        tool.router = mock_router
        result = await tool.execute({"session_id": "sess-multi"})

    assert len(result["by_model"]) == 2
    names = {row["model_name"] for row in result["by_model"]}
    assert names == {"gpt-4o", "claude-haiku"}
    # Exactly one recommendation object addressing the session as a whole
    assert "recommendation" in result
    assert isinstance(result["recommendation"], dict)
    # A multi-model session can never trivially be "is_optimal" (no single model to match)
    assert result["recommendation"]["is_optimal"] is False


@pytest.mark.asyncio
async def test_no_router_match_returns_data_without_recommendation():
    tool = AnalyzeSessionUsageTool()
    usage = {
        "session_id": "sess-nomatch",
        "total_requests": 1,
        "total_cost_usd": 0.1,
        "total_input_tokens": 1000,
        "total_output_tokens": 500,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 1000.0,
        "by_model": [
            {
                "model_name": "some-model", "provider": "openai", "request_count": 1,
                "input_tokens": 1000, "output_tokens": 500, "cost_usd": 0.1,
            }
        ],
    }
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value=usage)
    mock_router = AsyncMock()
    mock_router.get_optimal_model = AsyncMock(return_value=None)

    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=mock_tracker):
        tool.router = mock_router
        result = await tool.execute({"session_id": "sess-nomatch"})

    assert result["success"] is True
    assert result["has_data"] is True
    assert "recommendation" not in result
    assert "recommendation_error" in result


@pytest.mark.asyncio
async def test_quality_tradeoff_caveat_fires_below_threshold_regression_case():
    """Live-verified regression: a session that used gpt-4o-mini with 100k input / 50k
    output tokens, recommended to @cf/mistral/mistral-7b-instruct-v0.1 with a ~$0.045
    savings figure, used to return silently with no caveat at all. It must now surface a
    quality_tradeoff caveat, using the identical wording/threshold as recommend_model.py's
    caveat (see test_recommend_model_tool.py's quality_tradeoff tests)."""
    tool = AnalyzeSessionUsageTool()
    usage = {
        "session_id": "sess-regression",
        "total_requests": 1,
        "total_cost_usd": 0.045,
        "total_input_tokens": 100_000,
        "total_output_tokens": 50_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 1000.0,
        "by_model": [
            {
                "model_name": "gpt-4o-mini", "provider": "openai", "request_count": 1,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 0.045,
            }
        ],
    }
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value=usage)

    cheaper_lower_quality = _pricing_metrics(
        "@cf/mistral/mistral-7b-instruct-v0.1", "cloudflare", 0.0000000022, 0.0000000022, quality_score=58.0
    )
    mock_result = RouterResult(recommended=cheaper_lower_quality, score=100.0, reason="test", alternatives=[])
    mock_router = AsyncMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=mock_tracker):
        tool.router = mock_router
        result = await tool.execute({"session_id": "sess-regression"})

    rec = result["recommendation"]
    assert rec["recommended_model"] == "@cf/mistral/mistral-7b-instruct-v0.1"
    assert rec["is_optimal"] is False
    assert round(rec["savings_usd"], 3) == 0.045
    quality_caveats = [c for c in rec.get("caveats", []) if c["type"] == "quality_tradeoff"]
    assert len(quality_caveats) == 1
    assert quality_caveats[0]["quality_score"] == 58.0
    assert quality_caveats[0]["quality_threshold"] == 75.0
    assert "quality_score of 58.0" in quality_caveats[0]["message"]
    assert "below 75" in quality_caveats[0]["message"]


@pytest.mark.asyncio
async def test_quality_tradeoff_caveat_absent_when_quality_at_or_above_threshold():
    """AC4 (no false positives): recommended model's quality_score >= 75 -> no caveats key."""
    tool = AnalyzeSessionUsageTool()
    usage = {
        "session_id": "sess-high-quality",
        "total_requests": 10,
        "total_cost_usd": 1.0,
        "total_input_tokens": 100_000,
        "total_output_tokens": 50_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 2000.0,
        "by_model": [
            {
                "model_name": "gpt-4o", "provider": "openai", "request_count": 10,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 1.0,
            }
        ],
    }
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value=usage)

    high_quality = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006, quality_score=90.0)
    mock_result = RouterResult(recommended=high_quality, score=100.0, reason="test", alternatives=[])
    mock_router = AsyncMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=mock_tracker):
        tool.router = mock_router
        result = await tool.execute({"session_id": "sess-high-quality"})

    rec = result["recommendation"]
    assert "caveats" not in rec


@pytest.mark.asyncio
async def test_quality_tradeoff_caveat_absent_when_is_optimal():
    """No caveat even when the (unchanged) current model's quality_score is below
    threshold — the caveat only applies to a change being recommended for cost reasons."""
    tool = AnalyzeSessionUsageTool()
    usage = {
        "session_id": "sess-optimal-low-quality",
        "total_requests": 5,
        "total_cost_usd": 0.5,
        "total_input_tokens": 50_000,
        "total_output_tokens": 25_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 1500.0,
        "by_model": [
            {
                "model_name": "gpt-4o-mini", "provider": "openai", "request_count": 5,
                "input_tokens": 50_000, "output_tokens": 25_000, "cost_usd": 0.5,
            }
        ],
    }
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value=usage)

    same_model_low_quality = _pricing_metrics(
        "gpt-4o-mini", "openai", 0.00000015, 0.0000006, quality_score=58.0
    )
    mock_result = RouterResult(recommended=same_model_low_quality, score=100.0, reason="test", alternatives=[])
    mock_router = AsyncMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=mock_tracker):
        tool.router = mock_router
        result = await tool.execute({"session_id": "sess-optimal-low-quality"})

    rec = result["recommendation"]
    assert rec["is_optimal"] is True
    assert "caveats" not in rec


@pytest.mark.asyncio
async def test_standalone_invocation_no_extra_config_required():
    """AC6: only a session_id is required — no scheduling/webhook/opt-in config."""
    tool = AnalyzeSessionUsageTool()
    usage = {
        "session_id": "sess-standalone",
        "total_requests": 1,
        "total_cost_usd": 0.01,
        "total_input_tokens": 100,
        "total_output_tokens": 50,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 1000.0,
        "by_model": [
            {
                "model_name": "gpt-4o-mini", "provider": "openai", "request_count": 1,
                "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01,
            }
        ],
    }
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value=usage)
    same_model = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006)
    mock_result = RouterResult(recommended=same_model, score=100.0, reason="test", alternatives=[])
    mock_router = AsyncMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=mock_tracker):
        tool.router = mock_router
        result = await tool.execute({"session_id": "sess-standalone"})
    assert result["success"] is True
