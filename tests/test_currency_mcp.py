"""AC13 — optional ``currency`` / ``country`` on the five pricing-output MCP tools (v1.76.0).

Each tool is driven with a mocked aggregator so two calls are deterministic and
"omitted arguments" can be asserted as dict equality against a baseline call.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp.tools._locale import resolve_tool_locale  # noqa: E402
from mcp.tools.compare_costs import CompareCostsTool  # noqa: E402
from mcp.tools.estimate_cost import EstimateCostTool  # noqa: E402
from mcp.tools.get_all_pricing import GetAllPricingTool  # noqa: E402
from mcp.tools.get_use_cases import GetUseCasesTool  # noqa: E402
from mcp.tools.recommend_model import RecommendModelTool  # noqa: E402
from mcp.tools.tool_manager import ToolManager  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402
from src.services import currency as cur  # noqa: E402

SUPPORTED = list(cur.SUPPORTED_CURRENCIES)


def _pm(name, provider, inp, out, quality=None, context=128000):
    # use_cases stays None so the router's task-type filter keeps every model
    # (the same convention as tests/test_recommend_model_tool.py).
    m = PricingMetrics(model_name=name, provider=provider, cost_per_input_token=inp,
                       cost_per_output_token=out, context_window=context, pricing_model="per_token",
                       strengths=["fast"], best_for="general")
    m.quality_score = quality
    return m


MODELS = [
    _pm("gpt-4o", "OpenAI", 0.000005, 0.000015, quality=87),
    _pm("gpt-4o-mini", "OpenAI", 0.00000015, 0.0000006, quality=72),
    _pm("claude-opus", "Anthropic", 0.000015, 0.000075, quality=90),
    _pm("tiny-embed", "Groq", 0.00000001, 0.00000002, quality=50),
]
RATE = cur.load_rate_table().rates


def _mock_service():
    svc = MagicMock()
    svc.get_all_pricing_async = AsyncMock(return_value=(MODELS, []))
    svc.get_pricing_by_provider_async = AsyncMock(return_value=([m for m in MODELS if m.provider == "OpenAI"], []))
    svc.find_model_pricing = AsyncMock(
        side_effect=lambda name: next((m for m in MODELS if m.model_name == name), None)
    )
    return svc


@pytest.fixture
def pricing_tool():
    t = GetAllPricingTool()
    t.service = _mock_service()
    return t


@pytest.fixture
def estimate_tool():
    t = EstimateCostTool()
    t.service = _mock_service()
    return t


@pytest.fixture
def compare_tool():
    t = CompareCostsTool()
    t.service = _mock_service()
    return t


@pytest.fixture
def use_cases_tool():
    t = GetUseCasesTool()
    t.service = _mock_service()
    return t


@pytest.fixture
def recommend_tool():
    t = RecommendModelTool()
    svc = _mock_service()
    t.service = svc
    t.router._aggregator = svc
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
        yield t


def _walk(obj):
    yield obj
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _has_local(payload):
    return any(isinstance(n, dict) and any(k.startswith("local_") or k == "currency_conversion" for k in n)
               for n in _walk(payload))


def _assert_unsupported(result):
    assert result["success"] is False
    assert result["error_type"] == "unsupported_currency"
    assert "XYZ" in result["error"]
    assert result["supported_currencies"] == SUPPORTED
    assert not _has_local(result)


ESTIMATE = {"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}
COMPARE = {"model_names": ["gpt-4o", "gpt-4o-mini", "missing"], "input_tokens": 1000, "output_tokens": 500}
RECOMMEND = {"description": "summarize long support tickets"}


class TestHelper:
    def test_omitted_is_inactive(self):
        ctx = resolve_tool_locale({})
        assert isinstance(ctx, cur.LocaleContext) and ctx.active is False

    def test_eur(self):
        ctx = resolve_tool_locale({"currency": "eur"})
        assert ctx.currency == "EUR" and ctx.active

    def test_country_only(self):
        ctx = resolve_tool_locale({"country": "in"})
        assert ctx.currency == "INR" and ctx.country == "IN"

    def test_unsupported(self):
        _assert_unsupported(resolve_tool_locale({"currency": "XYZ"}))

    def test_non_string_arguments_are_ignored(self):
        ctx = resolve_tool_locale({"currency": 5, "country": ["DE"]})
        assert ctx.active is False

    def test_mcp_never_detects(self):
        # No detected_country path exists for tools: a bare call is USD even with geo noise elsewhere
        assert resolve_tool_locale({"description": "x"}).country is None


class TestGetAllPricing:
    @pytest.mark.asyncio
    async def test_omitted_equals_baseline_and_explicit_usd(self, pricing_tool):
        base = await pricing_tool.execute({})
        again = await pricing_tool.execute({})
        usd = await pricing_tool.execute({"currency": "USD"})
        assert base["success"] and base == again == usd
        assert not _has_local(base)

    @pytest.mark.asyncio
    async def test_eur_adds_local_prices_plus_usd_plus_metadata(self, pricing_tool):
        r = await pricing_tool.execute({"currency": "EUR"})
        assert r["success"] and r["currency_conversion"]["currency"] == "EUR"
        for m in r["models"]:
            assert m["currency"] == "USD" and "cost_per_input_token" in m
            assert m["local_prices"]["currency"] == "EUR"
            assert m["local_prices"]["cost_per_input_token"] == pytest.approx(m["cost_per_input_token"] * RATE["EUR"])
            assert m["local_prices"]["cost_per_input_token"] > 0

    @pytest.mark.asyncio
    async def test_unsupported(self, pricing_tool):
        _assert_unsupported(await pricing_tool.execute({"currency": "XYZ"}))

    @pytest.mark.asyncio
    async def test_country_fallback(self, pricing_tool):
        r = await pricing_tool.execute({"country": "ZW"})
        assert r["success"] and r["currency_conversion"]["fallback_to_usd"] is True
        assert r["models"][0]["local_prices"]["currency"] == "USD"


class TestEstimateCost:
    @pytest.mark.asyncio
    async def test_omitted_equals_baseline(self, estimate_tool):
        base = await estimate_tool.execute(ESTIMATE)
        again = await estimate_tool.execute(ESTIMATE)
        usd = await estimate_tool.execute({**ESTIMATE, "currency": "USD"})
        assert base == again == usd
        assert base["currency"] == "USD" and not _has_local(base)

    @pytest.mark.asyncio
    async def test_eur(self, estimate_tool):
        r = await estimate_tool.execute({**ESTIMATE, "currency": "EUR"})
        assert r["success"] and r["currency"] == "USD"
        assert r["local_cost"]["currency"] == "EUR"
        assert r["local_cost"]["total_cost"] == pytest.approx(r["total_cost"] * RATE["EUR"], rel=1e-4, abs=1e-6)
        assert r["currency_conversion"]["rate"] == RATE["EUR"]

    @pytest.mark.asyncio
    async def test_country_in_and_currency_beats_country(self, estimate_tool):
        inr = await estimate_tool.execute({**ESTIMATE, "country": "IN"})
        assert inr["local_cost"]["currency"] == "INR" and inr["currency_conversion"]["country"] == "IN"
        gbp = await estimate_tool.execute({**ESTIMATE, "country": "IN", "currency": "GBP"})
        assert gbp["local_cost"]["currency"] == "GBP" and gbp["currency_conversion"]["country"] == "IN"

    @pytest.mark.asyncio
    async def test_unsupported_checked_before_model_lookup(self, estimate_tool):
        _assert_unsupported(await estimate_tool.execute({**ESTIMATE, "currency": "XYZ"}))
        estimate_tool.service.find_model_pricing.assert_not_called()

    @pytest.mark.asyncio
    async def test_jpy_zero_tokens(self, estimate_tool):
        args = {"model_name": "gpt-4o", "input_tokens": 0, "output_tokens": 0, "currency": "JPY"}
        r = await estimate_tool.execute(args)
        assert r["local_cost"] == {"currency": "JPY", "input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}


class TestCompareCosts:
    @pytest.mark.asyncio
    async def test_omitted_equals_baseline(self, compare_tool):
        base = await compare_tool.execute(COMPARE)
        again = await compare_tool.execute(COMPARE)
        usd = await compare_tool.execute({**COMPARE, "currency": "USD"})
        assert base == again == usd
        assert base["currency"] == "USD" and not _has_local(base)

    @pytest.mark.asyncio
    async def test_eur_per_available_model_and_range(self, compare_tool):
        base = await compare_tool.execute(COMPARE)
        r = await compare_tool.execute({**COMPARE, "currency": "EUR"})
        assert r["cheapest_model"] == base["cheapest_model"] and r["cost_range"] == base["cost_range"]
        for m in r["models"]:
            if m["is_available"]:
                assert m["local_cost"]["currency"] == "EUR"
                expected_1m = m["cost_per_1m_tokens"] * RATE["EUR"]
                assert m["local_cost"]["cost_per_1m_tokens"] == pytest.approx(expected_1m, rel=1e-3, abs=1e-6)
            else:
                assert "local_cost" not in m and m["candidates"] == []
        lr, cr = r["local_cost_range"], base["cost_range"]
        assert lr["currency"] == "EUR"
        assert lr["min"] == pytest.approx(cr["min"] * RATE["EUR"], rel=1e-4, abs=1e-6)
        assert lr["difference"] == pytest.approx(cr["difference"] * RATE["EUR"], rel=1e-4, abs=1e-6)
        assert r["currency_conversion"]["currency"] == "EUR"

    @pytest.mark.asyncio
    async def test_unsupported(self, compare_tool):
        _assert_unsupported(await compare_tool.execute({**COMPARE, "currency": "XYZ"}))

    @pytest.mark.asyncio
    async def test_no_available_models_has_null_range(self, compare_tool):
        args = {"model_names": ["missing"], "input_tokens": 1, "output_tokens": 1, "currency": "EUR"}
        r = await compare_tool.execute(args)
        assert r["success"] and r["local_cost_range"] is None and "currency_conversion" in r


class TestGetUseCases:
    @pytest.mark.asyncio
    async def test_omitted_equals_baseline(self, use_cases_tool):
        base = await use_cases_tool.execute({})
        again = await use_cases_tool.execute({})
        usd = await use_cases_tool.execute({"currency": "USD"})
        assert base == again == usd
        assert not _has_local(base)

    @pytest.mark.asyncio
    async def test_eur_same_models_same_order_same_tiers(self, use_cases_tool):
        base = await use_cases_tool.execute({})
        r = await use_cases_tool.execute({"currency": "EUR"})
        assert [m["model_name"] for m in r["models"]] == [m["model_name"] for m in base["models"]]
        assert [m["cost_tier"] for m in r["models"]] == [m["cost_tier"] for m in base["models"]]
        assert r["providers"] == base["providers"]
        for m in r["models"]:
            assert m["local_cost"]["currency"] == "EUR"
            assert m["local_cost"]["avg_cost_per_1m_tokens"] == pytest.approx(
                m["local_cost"]["avg_cost_per_1m_tokens_usd"] * RATE["EUR"], rel=1e-4, abs=1e-6)
        assert r["currency_conversion"]["currency"] == "EUR"

    @pytest.mark.asyncio
    async def test_provider_filter_with_country(self, use_cases_tool):
        r = await use_cases_tool.execute({"provider": "openai", "country": "DE"})
        assert all(m["provider"] == "OpenAI" for m in r["models"])
        assert r["currency_conversion"]["country"] == "DE" and r["currency_conversion"]["currency"] == "EUR"

    @pytest.mark.asyncio
    async def test_unsupported(self, use_cases_tool):
        _assert_unsupported(await use_cases_tool.execute({"currency": "XYZ"}))


class TestRecommendModel:
    @pytest.mark.asyncio
    async def test_omitted_equals_baseline(self, recommend_tool):
        base = await recommend_tool.execute(RECOMMEND)
        assert base["success"], base
        again = await recommend_tool.execute(RECOMMEND)
        usd = await recommend_tool.execute({**RECOMMEND, "currency": "USD"})
        # the cache marks repeats; compare everything else
        for d in (base, again, usd):
            d.pop("cached", None)
        assert base == again == usd
        assert not _has_local(base)

    @pytest.mark.asyncio
    async def test_eur_same_recommendation_with_local_prices(self, recommend_tool):
        base = await recommend_tool.execute(RECOMMEND)
        r = await recommend_tool.execute({**RECOMMEND, "currency": "EUR"})
        assert r["success"]
        assert r["recommended"]["model_name"] == base["recommended"]["model_name"]
        assert [a["model_name"] for a in r["alternatives"]] == [a["model_name"] for a in base["alternatives"]]
        assert r["recommended"]["local_prices"]["currency"] == "EUR"
        assert r["recommended"]["avg_cost_per_1m_tokens_usd"] == base["recommended"]["avg_cost_per_1m_tokens_usd"]
        assert all(a["local_prices"]["currency"] == "EUR" for a in r["alternatives"])
        assert r["currency_conversion"]["currency"] == "EUR"

    @pytest.mark.asyncio
    async def test_cached_result_is_not_polluted(self, recommend_tool):
        await recommend_tool.execute({**RECOMMEND, "currency": "EUR"})
        plain = await recommend_tool.execute(RECOMMEND)
        assert "local_prices" not in plain["recommended"] and "currency_conversion" not in plain

    @pytest.mark.asyncio
    async def test_unsupported(self, recommend_tool):
        _assert_unsupported(await recommend_tool.execute({**RECOMMEND, "currency": "XYZ"}))

    @pytest.mark.asyncio
    async def test_country_labelled(self, recommend_tool):
        r = await recommend_tool.execute({**RECOMMEND, "country": "JP"})
        assert r["currency_conversion"]["country"] == "JP" and r["recommended"]["local_prices"]["currency"] == "JPY"


class TestInputSchemas:
    FIVE = ("get_all_pricing", "estimate_cost", "compare_costs", "get_use_cases", "recommend_model")

    def test_five_tools_accept_optional_currency_and_country(self):
        registry = ToolManager()
        for name in self.FIVE:
            schema = registry.tools[name]["input_schema"]
            assert schema["properties"]["currency"]["enum"] == SUPPORTED, name
            assert schema["properties"]["country"]["type"] == "string", name
            assert "currency" not in schema["required"] and "country" not in schema["required"], name

    def test_other_tools_untouched_and_count_unchanged(self):
        registry = ToolManager()
        assert len(registry.tools) == 32  # v1.81.0: + manage_price_alert; v1.84.0: + support_chat
        for name, meta in registry.tools.items():
            if name not in self.FIVE:
                assert "currency" not in meta["input_schema"].get("properties", {}), name
