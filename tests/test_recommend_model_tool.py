"""Tests for the recommend_model MCP tool."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.pricing import PricingMetrics  # noqa: E402
from mcp.tools.recommend_model import RecommendModelTool  # noqa: E402


from src.services.router import ModelRouter  # noqa: E402


def _pm(name, provider, inp, out, quality=None, context=None, use_cases=None):
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
SAMPLE_MODELS = [
    _pm("gpt-4o", "openai", 0.000005, 0.000015, quality=87, context=128000),
    _pm("gpt-4o-mini", "openai", 0.00000015, 0.0000006, quality=72, context=128000),
    _pm("claude-opus", "anthropic", 0.000015, 0.000075, quality=90, context=200000),
    _pm("gemini-flash", "google", 0.00000035, 0.00000105, quality=70, context=1000000),
    _pm("cheap-unknown", "groq", 0.00000001, 0.00000002, quality=None, context=8000),
]


@pytest.fixture
def tool():
    t = RecommendModelTool()
    mock_svc = MagicMock()
    mock_svc.get_all_pricing_async = AsyncMock(return_value=(SAMPLE_MODELS, []))
    t.service = mock_svc
    t.router._aggregator = mock_svc
    return t


@pytest.fixture(autouse=True)
def _no_enrichment():
    # enrich_models normally hits a benchmark service / HF API; keep models as-is.
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        yield


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recommend_model_basic(tool):
    result = await tool.execute({"description": "summarize long customer support tickets"})
    assert result["success"] is True
    assert result["task_type"] == "summarization"
    assert result["task_type_inferred"] is True
    assert result["recommended"] is not None
    assert "model_name" in result["recommended"]
    assert isinstance(result["alternatives"], list)
    assert len(result["alternatives"]) <= 3


@pytest.mark.asyncio
async def test_recommend_model_infers_code_generation(tool):
    result = await tool.execute({"description": "write code for a binary search function in Python"})
    assert result["success"] is True
    assert result["task_type"] == "code_generation"


@pytest.mark.asyncio
async def test_recommend_model_explicit_task_type_overrides_inference(tool):
    result = await tool.execute({
        "description": "some ambiguous text that would normally infer chat",
        "task_type": "classification",
    })
    assert result["success"] is True
    assert result["task_type"] == "classification"
    assert result["task_type_inferred"] is False


@pytest.mark.asyncio
async def test_recommend_model_missing_description(tool):
    result = await tool.execute({})
    assert result["success"] is False
    assert "description" in result["error"]


@pytest.mark.asyncio
async def test_recommend_model_blank_description(tool):
    result = await tool.execute({"description": "   "})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_recommend_model_invalid_task_type(tool):
    result = await tool.execute({"description": "hello", "task_type": "banana"})
    assert result["success"] is False
    assert "task_type" in result["error"] or "Unknown" in result["error"]


# ---------------------------------------------------------------------------
# Constraint pass-through to the router
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recommend_model_max_cost_filter(tool):
    # Only gpt-4o-mini, gemini-flash, cheap-unknown are under $1/1M avg cost.
    result = await tool.execute({
        "description": "chat with the user",
        "max_cost_per_1m_tokens": 1.0,
    })
    assert result["success"] is True
    assert result["recommended"]["avg_cost_per_1m_tokens_usd"] <= 1.0
    for alt in result["alternatives"]:
        assert alt["avg_cost_per_1m_tokens_usd"] <= 1.0


@pytest.mark.asyncio
async def test_recommend_model_min_quality_filter_excludes_unscored(tool):
    result = await tool.execute({
        "description": "chat with the user",
        "min_quality_score": 50,
    })
    assert result["success"] is True
    assert result["recommended"]["model_name"] != "cheap-unknown"


@pytest.mark.asyncio
async def test_recommend_model_no_match(tool):
    result = await tool.execute({
        "description": "chat with the user",
        "min_quality_score": 99.9,
    })
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_recommend_model_preferred_provider(tool):
    result = await tool.execute({
        "description": "chat with the user",
        "preferred_provider": "anthropic",
    })
    assert result["success"] is True
    assert "reason" in result
    assert "score" in result


@pytest.mark.asyncio
async def test_recommend_model_min_context_window(tool):
    result = await tool.execute({
        "description": "chat with the user",
        "min_context_window": 500000,
    })
    assert result["success"] is True
    assert result["recommended"]["model_name"] == "gemini-flash"


@pytest.mark.asyncio
async def test_recommend_model_task_description_present(tool):
    result = await tool.execute({"description": "translate this document in french"})
    assert result["success"] is True
    assert result["task_type"] == "translation"
    assert isinstance(result["task_description"], str) and result["task_description"]


@pytest.mark.asyncio
async def test_recommend_model_no_feedback_field(tool):
    """recommend_model is read-only: no routing_id or feedback-submission surface."""
    result = await tool.execute({"description": "chat with the user"})
    assert "routing_id" not in result


# ---------------------------------------------------------------------------
# Task-type vocabulary translation actually reaches the router's hard filter
# ---------------------------------------------------------------------------

TASK_FILTER_MODELS = [
    _pm("coder-model", "openai", 0.000005, 0.000015, quality=80, context=128000,
        use_cases=["coding", "programming"]),
    _pm("story-model", "openai", 0.000005, 0.000015, quality=95, context=128000,
        use_cases=["creative writing", "storytelling"]),
]


@pytest.fixture
def task_filter_tool():
    t = RecommendModelTool()
    mock_svc = MagicMock()
    mock_svc.get_all_pricing_async = AsyncMock(return_value=(TASK_FILTER_MODELS, []))
    t.service = mock_svc
    t.router._aggregator = mock_svc
    return t


@pytest.mark.asyncio
async def test_recommend_model_code_generation_filters_to_coding_use_case(task_filter_tool):
    # story-model has the higher quality score and would win on raw scoring,
    # but it has no coding-relevant use_cases, so the task_type filter for
    # "code_generation" (mapped to the router's "code" vocabulary) must
    # exclude it in favour of coder-model.
    result = await task_filter_tool.execute({
        "description": "write code for a binary search function in Python",
    })
    assert result["success"] is True
    assert result["task_type"] == "code_generation"
    assert result["recommended"]["model_name"] == "coder-model"
    assert all(a["model_name"] != "story-model" for a in result["alternatives"])


@pytest.mark.asyncio
async def test_task_type_translation_map_covers_every_task_profiles_type():
    """Every task_profiles task_type must map onto a router-recognised task_type
    so the router's hard filter is never silently skipped."""
    from mcp.tools.recommend_model import _TASK_TYPE_TO_ROUTER_TASK_TYPE
    from src.services.task_profiles import list_task_types

    for task_type in list_task_types():
        router_task_type = _TASK_TYPE_TO_ROUTER_TASK_TYPE.get(task_type)
        assert router_task_type in ModelRouter._TASK_USE_CASES, (
            f"{task_type!r} does not map to a recognised router task_type"
        )
