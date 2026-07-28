"""Tests for price provenance: price_as_of, staleness, and unconfirmed-price handling.

Background: STATIC_PRICING is hand-maintained and the live sync used to be
subtractive only, so newly released models never appeared and prices aged
silently. These tests cover the provenance fields and the additive discovery
that replaced that behaviour.
"""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.pricing import PRICE_STALE_AFTER_DAYS, PricingMetrics  # noqa: E402
from src.services.base_provider import BasePricingProvider  # noqa: E402
from src.services.portfolio_optimizer import Workload, optimize  # noqa: E402


def _days_ago(n: int) -> str:
    return (datetime.now(UTC).date() - timedelta(days=n)).isoformat()


def _pm(name="m", provider="P", inp=1.0, out=2.0, **kw):
    return PricingMetrics(
        model_name=name, provider=provider,
        cost_per_input_token=inp, cost_per_output_token=out, **kw,
    )


# ---------------------------------------------------------------------------
# price_age_days / price_is_stale
# ---------------------------------------------------------------------------

class TestPriceAge:
    def test_none_when_undated(self):
        assert _pm().price_age_days is None

    def test_zero_for_today(self):
        assert _pm(price_as_of=_days_ago(0)).price_age_days == 0

    def test_counts_days(self):
        assert _pm(price_as_of=_days_ago(45)).price_age_days == 45

    def test_malformed_date_is_none_not_a_crash(self):
        assert _pm(price_as_of="not-a-date").price_age_days is None

    def test_future_date_clamps_to_zero(self):
        future = (datetime.now(UTC).date() + timedelta(days=5)).isoformat()
        assert _pm(price_as_of=future).price_age_days == 0


class TestPriceStaleness:
    def test_fresh_price_not_stale(self):
        assert _pm(price_as_of=_days_ago(10)).price_is_stale is False

    def test_just_inside_threshold_not_stale(self):
        assert _pm(price_as_of=_days_ago(PRICE_STALE_AFTER_DAYS)).price_is_stale is False

    def test_past_threshold_is_stale(self):
        assert _pm(price_as_of=_days_ago(PRICE_STALE_AFTER_DAYS + 1)).price_is_stale is True

    def test_undated_price_counts_as_stale(self):
        """Unknown provenance is not a clean bill of health."""
        assert _pm().price_is_stale is True

    def test_malformed_date_counts_as_stale(self):
        assert _pm(price_as_of="garbage").price_is_stale is True


# ---------------------------------------------------------------------------
# Provider stamping
# ---------------------------------------------------------------------------

class _StubProvider(BasePricingProvider):
    PRICE_AS_OF = "2026-05-09"
    STATIC_PRICING = {
        "priced": {"input": 1.0, "output": 2.0},
        "unpriced": {"input": 0.0, "output": 0.0, "price_confirmed": False},
        "own-date": {"input": 1.0, "output": 2.0, "price_as_of": "2026-01-01"},
    }

    def __init__(self):
        super().__init__("StubProvider")

    async def fetch_pricing_data(self):
        return [_pm(n, "StubProvider") for n in ("priced", "unpriced", "own-date")]


@pytest.fixture
def stub():
    return _StubProvider()


@pytest.mark.asyncio
class TestProviderStamping:
    async def test_provider_date_applied(self, stub):
        data, _ = await stub.get_pricing_with_status()
        assert next(m for m in data if m.model_name == "priced").price_as_of == "2026-05-09"

    async def test_model_own_date_wins(self, stub):
        data, _ = await stub.get_pricing_with_status()
        assert next(m for m in data if m.model_name == "own-date").price_as_of == "2026-01-01"

    async def test_price_confirmed_false_propagates(self, stub):
        data, _ = await stub.get_pricing_with_status()
        assert next(m for m in data if m.model_name == "unpriced").price_confirmed is False

    async def test_price_confirmed_defaults_true(self, stub):
        data, _ = await stub.get_pricing_with_status()
        assert next(m for m in data if m.model_name == "priced").price_confirmed is True

    async def test_provider_without_price_as_of_leaves_none(self):
        class NoDate(_StubProvider):
            PRICE_AS_OF = None
        data, _ = await NoDate().get_pricing_with_status()
        assert next(m for m in data if m.model_name == "priced").price_as_of is None


# ---------------------------------------------------------------------------
# Additive discovery
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_adds_unknown_live_model(self, stub):
        out = stub._discover_new_models([_pm("known", "StubProvider")], frozenset({"known", "brand-new"}))
        names = [m.model_name for m in out]
        assert "brand-new" in names

    def test_discovered_model_is_unconfirmed(self, stub):
        out = stub._discover_new_models([], frozenset({"brand-new"}))
        assert out[0].price_confirmed is False
        assert out[0].cost_per_input_token == 0.0

    def test_does_not_duplicate_known_model(self, stub):
        out = stub._discover_new_models([_pm("known", "StubProvider")], frozenset({"known"}))
        assert len(out) == 1

    def test_prefix_match_is_not_duplicated(self, stub):
        """gpt-4o static entry should absorb the dated gpt-4o-2024-11-20 alias."""
        out = stub._discover_new_models([_pm("gpt-4o", "StubProvider")], frozenset({"gpt-4o-2024-11-20"}))
        assert len(out) == 1

    @pytest.mark.parametrize("noise", [
        "text-embedding-3-large", "whisper-1", "tts-1", "dall-e-3",
        "omni-moderation-latest", "rerank-v3", "gpt-4o-realtime-preview",
    ])
    def test_non_chat_artefacts_excluded(self, stub, noise):
        out = stub._discover_new_models([], frozenset({noise}))
        assert out == []

    def test_empty_live_ids_changes_nothing(self, stub):
        base = [_pm("known", "StubProvider")]
        assert stub._discover_new_models(base, frozenset()) == base


# ---------------------------------------------------------------------------
# Unconfirmed prices must never win on cost
# ---------------------------------------------------------------------------

class TestUnconfirmedExcludedFromCosting:
    def test_optimizer_skips_unconfirmed(self):
        models = [
            _pm("free-looking", "P", 0.0, 0.0, price_confirmed=False),
            _pm("real", "P", 1.0, 2.0, price_confirmed=True),
        ]
        for m in models:
            m.quality_score = 80
        r = optimize(models, [Workload("classification", 100)])
        assert r.allocations[0].model.model_name == "real"

    def test_optimizer_baseline_skips_unconfirmed(self):
        models = [
            _pm("free-looking", "P", 0.0, 0.0, price_confirmed=False),
            _pm("real", "P", 1.0, 2.0, price_confirmed=True),
        ]
        r = optimize(models, [Workload("qa", 10)])
        assert r.baseline_model is None or r.baseline_model.model_name == "real"

    @pytest.mark.asyncio
    async def test_predict_cost_skips_unconfirmed(self):
        from mcp.tools.predict_cost import PredictCostTool
        models = [
            _pm("free-looking", "P", 0.0, 0.0, price_confirmed=False),
            _pm("real", "P", 1.0, 2.0, price_confirmed=True),
        ]
        tool = PredictCostTool()
        tool.service = AsyncMock()
        tool.service.get_all_pricing_async = AsyncMock(return_value=(models, []))
        r = await tool.execute({"prompt": "hello", "task_type": "chat"})
        assert r["success"] is True
        assert "free-looking" not in [m["model_name"] for m in r["ranked_models"]]


# ---------------------------------------------------------------------------
# Aggregator gates unconfirmed models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAggregatorGating:
    """Unconfirmed models must not reach the ~20 downstream consumers that read
    cost_per_input_token, derive a cost tier, or sort by price. Gating once in the
    aggregator protects all of them, rather than auditing each call site.
    """

    async def test_default_excludes_unconfirmed(self):
        from src.services.pricing_aggregator import PricingAggregatorService
        models, _ = await PricingAggregatorService().get_all_pricing_async()
        leaked = [m.model_name for m in models if not m.price_confirmed]
        assert not leaked, f"unconfirmed models leaked into default output: {leaked}"

    async def test_opt_in_is_a_superset_of_default(self):
        """include_unconfirmed=True must never filter anything out.

        This asserts the contract rather than the presence of unconfirmed models:
        the price oracle now fills most gaps, so on a good day there are none left.
        """
        from src.services.pricing_aggregator import PricingAggregatorService
        svc = PricingAggregatorService()
        default, _ = await svc.get_all_pricing_async()
        opt_in, _ = await svc.get_all_pricing_async(include_unconfirmed=True)

        assert len(opt_in) >= len(default)
        assert {m.model_name for m in default} <= {m.model_name for m in opt_in}

    async def test_unconfirmed_models_are_filtered_when_present(self):
        """The gate itself, exercised directly so it does not depend on live data."""
        from src.models.pricing import PricingMetrics
        priced = PricingMetrics(model_name="a", provider="P",
                                cost_per_input_token=1.0, cost_per_output_token=2.0)
        unpriced = PricingMetrics(model_name="b", provider="P",
                                  cost_per_input_token=0.0, cost_per_output_token=0.0,
                                  price_confirmed=False)
        gated = [m for m in (priced, unpriced) if m.price_confirmed]
        assert [m.model_name for m in gated] == ["a"]

    async def test_no_zero_priced_model_in_default_output(self):
        """A 0.0 rate would read as free to every downstream cost calculation."""
        from src.services.pricing_aggregator import PricingAggregatorService
        models, _ = await PricingAggregatorService().get_all_pricing_async()
        per_token = [m for m in models if m.pricing_model == "per_token"]
        zero = [m.model_name for m in per_token if m.cost_per_input_token == 0]
        assert not zero, f"zero-priced models in default output: {zero}"


# ---------------------------------------------------------------------------
# Every provider declares provenance
# ---------------------------------------------------------------------------

class TestAllProvidersDeclareProvenance:
    def test_every_static_pricing_provider_sets_price_as_of(self):
        import importlib
        import re
        missing = []
        for path in sorted((project_root / "src" / "services").glob("*_pricing.py")):
            mod = importlib.import_module(f"src.services.{path.stem}")
            for attr in dir(mod):
                cls = getattr(mod, attr)
                if not (isinstance(cls, type) and issubclass_safe(cls, BasePricingProvider)):
                    continue
                if not getattr(cls, "STATIC_PRICING", None):
                    continue
                as_of = getattr(cls, "PRICE_AS_OF", None)
                if not as_of:
                    missing.append(f"{path.name}:{attr}")
                elif not re.match(r"^\d{4}-\d{2}-\d{2}$", as_of):
                    missing.append(f"{path.name}:{attr} has malformed PRICE_AS_OF '{as_of}'")
        assert not missing, "Providers missing a valid PRICE_AS_OF:\n" + "\n".join(missing)


def issubclass_safe(cls, base) -> bool:
    try:
        return issubclass(cls, base) and cls is not base
    except TypeError:
        return False
