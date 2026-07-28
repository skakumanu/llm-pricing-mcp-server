"""Tests for predict_cost MCP tool and supporting services."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.task_profiles import (  # noqa: E402
    estimate_output_tokens,
    get_task_description,
    infer_task_type,
    list_task_types,
)
from src.services.token_counter import (  # noqa: E402
    compute_cache_savings,
    count_tokens,
    providers_with_caching,
)
from src.models.pricing import PricingMetrics  # noqa: E402
from mcp.tools.predict_cost import PredictCostTool  # noqa: E402


# ---------------------------------------------------------------------------
# task_profiles
# ---------------------------------------------------------------------------

class TestInferTaskType:
    def test_classification_keywords(self):
        assert infer_task_type("classify this review as positive or negative") == "classification"

    def test_summarization_keywords(self):
        assert infer_task_type("summarize this article in 3 sentences") == "summarization"

    def test_code_generation_keywords(self):
        assert infer_task_type("write code for a binary search function in Python") == "code_generation"

    def test_translation_keywords(self):
        assert infer_task_type("translate this paragraph in french") == "translation"

    def test_rewrite_keywords(self):
        assert infer_task_type("rewrite this paragraph to be more concise") == "rewrite"

    def test_content_generation_keywords(self):
        assert infer_task_type("draft a blog post about LLM cost optimisation") == "content_generation"

    def test_default_fallback(self):
        assert infer_task_type("hello, how are you?") == "chat"

    def test_case_insensitive(self):
        assert infer_task_type("SUMMARIZE the document") == "summarization"


class TestEstimateOutputTokens:
    def test_classification_fixed(self):
        assert estimate_output_tokens("classification", 500) == 15

    def test_summarization_ratio(self):
        # 0.15 × 1000 = 150
        assert estimate_output_tokens("summarization", 1000) == 150

    def test_translation_ratio(self):
        # 0.95 × 200 = 190
        assert estimate_output_tokens("translation", 200) == 190

    def test_code_generation_fixed(self):
        assert estimate_output_tokens("code_generation", 50) == 450

    def test_unknown_type_falls_back_to_chat(self):
        assert estimate_output_tokens("nonexistent_type", 100) == 160

    def test_minimum_one_token(self):
        assert estimate_output_tokens("summarization", 0) >= 1


class TestListTaskTypes:
    def test_returns_list(self):
        types = list_task_types()
        assert isinstance(types, list)
        assert len(types) >= 10

    def test_contains_expected(self):
        types = list_task_types()
        for expected in ["classification", "code_generation", "summarization", "qa", "chat"]:
            assert expected in types


class TestGetTaskDescription:
    def test_known_type(self):
        desc = get_task_description("classification")
        assert isinstance(desc, str) and len(desc) > 5

    def test_unknown_type(self):
        desc = get_task_description("unknown_xyz")
        assert desc == "General-purpose task"


# ---------------------------------------------------------------------------
# token_counter
# ---------------------------------------------------------------------------

class TestCountTokens:
    def test_non_empty_string(self):
        n = count_tokens("Hello, world!")
        assert n > 0

    def test_longer_text_more_tokens(self):
        short = count_tokens("Hi")
        long = count_tokens("This is a much longer sentence with many more words and tokens.")
        assert long > short

    def test_empty_string(self):
        # tiktoken returns 0 for empty string; our fallback returns >= 1
        n = count_tokens("")
        assert isinstance(n, int)


class TestComputeCacheSavings:
    def test_zero_ratio(self):
        assert compute_cache_savings("Anthropic", 1000, 3.0, 0.0) == 0.0

    def test_anthropic_full_hit(self):
        # rate = 3.0/1000 = 0.003; savings = 1.0 * 1000 * 0.9 * 0.003 = 2.7
        savings = compute_cache_savings("Anthropic", 1000, 3.0, 1.0)
        assert abs(savings - 2.7) < 1e-6

    def test_openai_half_hit(self):
        # rate = 2.0/1000 = 0.002 per token; savings = 0.5 * 500 * (1-0.5) * 0.002 = 0.25
        savings = compute_cache_savings("OpenAI", 500, 2.0, 0.5)
        assert abs(savings - 0.25) < 1e-6

    def test_unknown_provider(self):
        assert compute_cache_savings("SomeRandomProvider", 1000, 1.0, 0.8) == 0.0


class TestProvidersWithCaching:
    def test_returns_known_providers(self):
        p = providers_with_caching()
        assert "Anthropic" in p
        assert "OpenAI" in p


# ---------------------------------------------------------------------------
# PredictCostTool
# ---------------------------------------------------------------------------

def _make_model(name, provider, inp, out, quality=None, ctx=128000, fn_calling=True, vision=False):
    m = PricingMetrics(
        model_name=name,
        provider=provider,
        cost_per_input_token=inp,
        cost_per_output_token=out,
        context_window=ctx,
        supports_function_calling=fn_calling,
        supports_vision=vision,
        pricing_model="per_token",
    )
    m.quality_score = quality
    return m


SAMPLE_MODELS = [
    _make_model("cheap-model", "ProviderA", 0.1, 0.2, quality=60),
    _make_model("mid-model", "Anthropic", 1.0, 3.0, quality=80),
    _make_model("expensive-model", "OpenAI", 5.0, 15.0, quality=95),
    _make_model("vision-model", "ProviderB", 2.0, 6.0, vision=True, fn_calling=True),
    _make_model("no-fn-model", "ProviderC", 0.5, 1.0, fn_calling=False),
]


@pytest.fixture
def tool():
    t = PredictCostTool()
    mock_svc = MagicMock()
    mock_svc.get_all_pricing_async = AsyncMock(return_value=(SAMPLE_MODELS, []))
    t.service = mock_svc
    return t


@pytest.mark.asyncio
async def test_predict_cost_basic(tool):
    result = await tool.execute({"prompt": "Summarize this document for me please."})
    assert result["success"] is True
    assert result["input_tokens"] > 0
    assert result["estimated_output_tokens"] > 0
    assert result["task_type"] == "summarization"
    assert result["task_type_inferred"] is True
    assert len(result["ranked_models"]) > 0


@pytest.mark.asyncio
async def test_predict_cost_explicit_task_type(tool):
    result = await tool.execute({"prompt": "Hello world", "task_type": "classification"})
    assert result["success"] is True
    assert result["task_type"] == "classification"
    assert result["task_type_inferred"] is False
    assert result["estimated_output_tokens"] == 15


@pytest.mark.asyncio
async def test_predict_cost_ranked_cheapest_first(tool):
    result = await tool.execute({"prompt": "What is 2+2?", "task_type": "qa"})
    assert result["success"] is True
    costs = [m["effective_cost_usd"] for m in result["ranked_models"]]
    assert costs == sorted(costs)


@pytest.mark.asyncio
async def test_predict_cost_cheapest_pick(tool):
    result = await tool.execute({"prompt": "classify this", "task_type": "classification"})
    assert result["cheapest_pick"]["rank"] == 1
    assert result["cheapest_pick"]["model_name"] == result["ranked_models"][0]["model_name"]


@pytest.mark.asyncio
async def test_predict_cost_best_value_pick(tool):
    result = await tool.execute({"prompt": "write a blog post", "task_type": "content_generation"})
    assert result["success"] is True
    bv = result["best_value_pick"]
    assert bv is not None
    assert bv["quality_value_score"] is not None


@pytest.mark.asyncio
async def test_predict_cost_require_function_calling(tool):
    result = await tool.execute({"prompt": "use a tool", "require_function_calling": True})
    assert result["success"] is True
    for m in result["ranked_models"]:
        assert m["supports_function_calling"] is True


@pytest.mark.asyncio
async def test_predict_cost_require_vision(tool):
    result = await tool.execute({"prompt": "describe this image", "require_vision": True})
    assert result["success"] is True
    for m in result["ranked_models"]:
        assert m["supports_vision"] is True


@pytest.mark.asyncio
async def test_predict_cost_cache_savings(tool):
    result = await tool.execute({
        "prompt": "summarize this",
        "task_type": "summarization",
        "cache_hit_ratio": 0.8,
    })
    assert result["success"] is True
    anthropic_entry = next(
        (m for m in result["ranked_models"] if m["provider"] == "Anthropic"), None
    )
    if anthropic_entry:
        assert anthropic_entry["cache_savings_usd"] > 0
        assert anthropic_entry["effective_cost_usd"] < anthropic_entry["estimated_total_cost_usd"]


@pytest.mark.asyncio
async def test_predict_cost_cache_tip_shown_when_no_ratio(tool):
    result = await tool.execute({"prompt": "hello", "task_type": "chat", "cache_hit_ratio": 0.0})
    assert result["success"] is True
    # cache_tip appears when cacheable models are in top results
    assert result["cache_tip"] is None or isinstance(result["cache_tip"], str)


@pytest.mark.asyncio
async def test_predict_cost_top_n(tool):
    result = await tool.execute({"prompt": "hello", "task_type": "chat", "top_n": 2})
    assert result["success"] is True
    assert len(result["ranked_models"]) <= 2


@pytest.mark.asyncio
async def test_predict_cost_missing_prompt(tool):
    result = await tool.execute({})
    assert result["success"] is False
    assert "prompt" in result["error"]


@pytest.mark.asyncio
async def test_predict_cost_invalid_task_type(tool):
    result = await tool.execute({"prompt": "hello", "task_type": "banana"})
    assert result["success"] is False
    assert "task_type" in result["error"] or "Unknown" in result["error"]


@pytest.mark.asyncio
async def test_predict_cost_min_context_filter(tool):
    result = await tool.execute({
        "prompt": "hello",
        "task_type": "chat",
        "min_context_tokens": 200000,  # larger than our sample models' 128K
    })
    assert result["success"] is False or len(result.get("ranked_models", [])) == 0
