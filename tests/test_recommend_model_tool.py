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


# ---------------------------------------------------------------------------
# Recommendation caching
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recommend_model_repeated_call_is_served_from_cache(tool):
    """Identical requests within the freshness window return the same
    recommendation and are flagged as served from cache."""
    args = {"description": "chat with the user"}

    first = await tool.execute(args)
    second = await tool.execute(args)

    assert first["success"] is True
    assert second["success"] is True
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["recommended"] == first["recommended"]
    assert second["score"] == first["score"]
    assert second["reason"] == first["reason"]
    assert second["alternatives"] == first["alternatives"]


@pytest.mark.asyncio
async def test_recommend_model_cache_expires_after_ttl(tool):
    """After the ~5 minute freshness window, the request is recomputed."""
    args = {"description": "chat with the user"}
    first = await tool.execute(args)
    assert first["cached"] is False

    # Force every cache entry to look stale.
    for entry in tool._cache._entries.values():
        entry.created_at -= 301

    second = await tool.execute(args)
    assert second["cached"] is False


@pytest.mark.asyncio
async def test_recommend_model_different_inputs_cached_independently(tool):
    """Genuinely different requests must not reuse each other's cached result."""
    result_a = await tool.execute({"description": "chat with the user"})
    result_b = await tool.execute({
        "description": "chat with the user",
        "min_context_window": 500000,
    })

    assert result_a["cached"] is False
    assert result_b["cached"] is False
    assert result_a["recommended"]["model_name"] != result_b["recommended"]["model_name"]
    assert result_b["recommended"]["model_name"] == "gemini-flash"

    # Repeating the first again should still hit its own cache entry.
    result_a_again = await tool.execute({"description": "chat with the user"})
    assert result_a_again["cached"] is True
    assert result_a_again["recommended"] == result_a["recommended"]


@pytest.mark.asyncio
async def test_recommend_model_no_match_is_not_cached(tool):
    """A `no model matched` response must never be served stale from cache."""
    args = {"description": "chat with the user", "min_quality_score": 99.9}
    first = await tool.execute(args)
    second = await tool.execute(args)

    assert first["success"] is False
    assert second["success"] is False
    assert "cached" not in first
    assert "cached" not in second


@pytest.mark.asyncio
async def test_recommend_model_cache_is_per_tool_instance():
    """Two separate RecommendModelTool() instances (e.g. per-request
    construction) must not share cache state."""
    mock_svc = MagicMock()
    mock_svc.get_all_pricing_async = AsyncMock(return_value=(SAMPLE_MODELS, []))

    tool_a = RecommendModelTool()
    tool_a.service = mock_svc
    tool_a.router._aggregator = mock_svc

    tool_b = RecommendModelTool()
    tool_b.service = mock_svc
    tool_b.router._aggregator = mock_svc

    args = {"description": "chat with the user"}
    result_a = await tool_a.execute(args)
    result_b = await tool_b.execute(args)

    assert result_a["cached"] is False
    assert result_b["cached"] is False  # fresh cache on tool_b, not a hit from tool_a


# ---------------------------------------------------------------------------
# Caveats: classification uncertainty + quality-per-dollar tradeoff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recommend_model_classification_uncertainty_caveat_fires_for_chat_with_reasoning_language(tool):
    """Description falls through to the 'chat' fallback but also carries softer
    reasoning-style language -> caveat must name both 'chat' and the plausible
    higher-stakes alternative."""
    result = await tool.execute({
        "description": "just chatting, but could you help me think through the logic of this?",
    })
    assert result["success"] is True
    assert result["task_type"] == "chat"
    assert result["task_type_inferred"] is True

    classification_caveats = [c for c in result.get("caveats", []) if c["type"] == "classification_uncertainty"]
    assert len(classification_caveats) == 1
    caveat = classification_caveats[0]
    assert caveat["inferred_task_type"] == "chat"
    assert caveat["plausible_alternative_task_type"] == "reasoning"
    assert "chat" in caveat["message"]
    assert "reasoning" in caveat["message"]


@pytest.mark.asyncio
async def test_recommend_model_classification_uncertainty_caveat_names_code_generation(tool):
    result = await tool.execute({
        "description": "chatting about our codebase and the algorithm we should use",
    })
    assert result["success"] is True
    assert result["task_type"] == "chat"

    classification_caveats = [c for c in result.get("caveats", []) if c["type"] == "classification_uncertainty"]
    assert len(classification_caveats) == 1
    assert classification_caveats[0]["plausible_alternative_task_type"] == "code_generation"


@pytest.mark.asyncio
async def test_recommend_model_classification_uncertainty_caveat_absent_when_task_type_explicit(tool):
    """Same ambiguous language, but the caller supplied task_type explicitly ->
    no classification-uncertainty caveat."""
    result = await tool.execute({
        "description": "just chatting, but could you help me think through the logic of this?",
        "task_type": "chat",
    })
    assert result["success"] is True
    assert result["task_type_inferred"] is False
    caveats = result.get("caveats", [])
    assert not any(c["type"] == "classification_uncertainty" for c in caveats)


@pytest.mark.asyncio
async def test_recommend_model_classification_uncertainty_caveat_absent_without_secondary_signal(tool):
    """Falls through to 'chat' but has no reasoning/code-generation-style
    language -> no classification-uncertainty caveat."""
    result = await tool.execute({"description": "chat with the user"})
    assert result["success"] is True
    assert result["task_type"] == "chat"
    assert result["task_type_inferred"] is True
    caveats = result.get("caveats", [])
    assert not any(c["type"] == "classification_uncertainty" for c in caveats)


@pytest.mark.asyncio
async def test_recommend_model_classification_uncertainty_caveat_absent_for_non_chat_inference(tool):
    """Inferred task type is not 'chat' (e.g. code_generation) -> no
    classification-uncertainty caveat, even if secondary-signal words appear."""
    result = await tool.execute({
        "description": "write code for a binary search function in Python, think about the algorithm",
    })
    assert result["success"] is True
    assert result["task_type"] == "code_generation"
    caveats = result.get("caveats", [])
    assert not any(c["type"] == "classification_uncertainty" for c in caveats)


@pytest.mark.asyncio
async def test_recommend_model_quality_tradeoff_caveat_fires_below_threshold(tool):
    """Default constraints on SAMPLE_MODELS pick gpt-4o-mini (quality=72),
    which is below the ~75 threshold -> quality_tradeoff caveat must fire."""
    result = await tool.execute({"description": "chat with the user"})
    assert result["success"] is True
    assert result["recommended"]["model_name"] == "gpt-4o-mini"
    assert result["recommended"]["quality_score"] < 75

    quality_caveats = [c for c in result.get("caveats", []) if c["type"] == "quality_tradeoff"]
    assert len(quality_caveats) == 1
    assert quality_caveats[0]["quality_score"] == 72
    assert "quality-per-dollar" in quality_caveats[0]["message"]


@pytest.mark.asyncio
async def test_recommend_model_quality_tradeoff_caveat_absent_at_or_above_threshold(tool):
    """min_quality_score=80 forces the recommendation to a model whose
    quality_score is well above the ~75 threshold -> no quality caveat."""
    result = await tool.execute({
        "description": "chat with the user",
        "min_quality_score": 80,
    })
    assert result["success"] is True
    assert result["recommended"]["quality_score"] >= 75

    caveats = result.get("caveats", [])
    assert not any(c["type"] == "quality_tradeoff" for c in caveats)


@pytest.mark.asyncio
async def test_recommend_model_no_caveats_key_when_neither_condition_fires(tool):
    """When neither caveat condition is met, `caveats` must be entirely
    absent (not an empty list) so the response is byte-identical to
    pre-feature behaviour."""
    result = await tool.execute({
        "description": "chat with the user",
        "min_quality_score": 80,
    })
    assert result["success"] is True
    assert "caveats" not in result


@pytest.mark.asyncio
async def test_recommend_model_existing_fields_unchanged_shape_with_caveats_present(tool):
    """Caveats must be purely additive: every field the tool returned before
    this feature is still present and correctly shaped even when a caveat
    fires."""
    result = await tool.execute({"description": "chat with the user"})
    assert result["success"] is True
    for field_name in (
        "recommended", "alternatives", "score", "reason", "task_type",
        "task_type_inferred", "task_description", "description", "cached",
    ):
        assert field_name in result
    assert isinstance(result["caveats"], list) and result["caveats"]


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


# ---------------------------------------------------------------------------
# detect_secondary_task_signal (task_profiles.py)
# ---------------------------------------------------------------------------

def test_detect_secondary_task_signal_matches_reasoning_and_code_generation():
    from src.services.task_profiles import detect_secondary_task_signal

    assert detect_secondary_task_signal("can you help me figure out the logic here?") == "reasoning"
    assert detect_secondary_task_signal("let's talk about the codebase and pseudocode") == "code_generation"
    assert detect_secondary_task_signal("just a normal chat, nothing special") is None


def test_detect_secondary_task_signal_prefers_reasoning_when_both_present():
    from src.services.task_profiles import detect_secondary_task_signal

    assert detect_secondary_task_signal("figure out the logic, then refactor the algorithm") == "reasoning"


def test_secondary_task_signal_keywords_disjoint_from_primary_inference_rules():
    """The secondary keyword set must never overlap with _INFERENCE_RULES'
    reasoning/code_generation entries, or a description containing those
    keywords would already have been classified away from 'chat' and this
    caveat could never fire in the first place."""
    from src.services.task_profiles import _INFERENCE_RULES, _SECONDARY_TASK_SIGNAL_KEYWORDS

    primary_reasoning = set()
    primary_code_generation = set()
    for keywords, task_type in _INFERENCE_RULES:
        if task_type == "reasoning":
            primary_reasoning.update(keywords)
        elif task_type == "code_generation":
            primary_code_generation.update(keywords)

    for task_type, keywords in _SECONDARY_TASK_SIGNAL_KEYWORDS:
        primary_set = primary_reasoning if task_type == "reasoning" else primary_code_generation
        assert primary_set.isdisjoint(keywords), (
            f"secondary keyword(s) for {task_type!r} overlap with _INFERENCE_RULES: "
            f"{primary_set.intersection(keywords)}"
        )
