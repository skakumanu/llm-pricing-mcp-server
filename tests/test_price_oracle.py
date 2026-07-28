"""Tests for the external price oracle: gap filling, drift detection, fallback."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.price_oracle import (  # noqa: E402
    SNAPSHOT_PATH,
    PriceOracle,
    get_price_oracle,
    reset_price_oracle,
)


RAW = {
    # Same model name under two different hosts, at genuinely different rates.
    # This is the shape that made name-only matching unsafe.
    "fireworks_ai/accounts/fireworks/models/shared-name": {
        "mode": "chat", "litellm_provider": "fireworks_ai",
        "input_cost_per_token": 5e-07, "output_cost_per_token": 5e-07,
    },
    "mistral/shared-name": {
        "mode": "chat", "litellm_provider": "mistral",
        "input_cost_per_token": 2e-06, "output_cost_per_token": 6e-06,
    },
    "cheap-model": {
        "mode": "chat",
        "litellm_provider": "acme",
        "input_cost_per_token": 1e-06,      # $1.00 / 1M
        "output_cost_per_token": 2e-06,     # $2.00 / 1M
        "cache_read_input_token_cost": 1e-07,
        "max_input_tokens": 128000,
        "supports_vision": True,
        "supports_function_calling": True,
    },
    "anthropic/namespaced-model": {
        "mode": "chat",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
    },
    "an-embedding": {
        "mode": "embedding",
        "input_cost_per_token": 1e-08,
        "output_cost_per_token": 0.0,
    },
    "no-price": {"mode": "chat", "litellm_provider": "acme"},
}


def _pm(name, inp=0.0, out=0.0, confirmed=False, provider="Acme", source=None, ctx=None):
    return PricingMetrics(
        model_name=name, provider=provider,
        cost_per_input_token=inp, cost_per_output_token=out,
        price_confirmed=confirmed, source=source, context_window=ctx,
    )


@pytest.fixture(autouse=True)
def _reset():
    reset_price_oracle()
    yield
    reset_price_oracle()


@pytest.fixture
async def oracle():
    o = PriceOracle()
    with patch.object(PriceOracle, "_fetch_remote", new=AsyncMock(return_value=RAW)):
        await o.load()
    return o


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestParsing:
    async def test_loads_chat_models(self, oracle):
        assert oracle.loaded
        assert oracle.lookup("cheap-model") is not None

    async def test_converts_per_token_to_per_1k(self, oracle):
        rec = oracle.lookup("cheap-model")
        assert rec.input_per_1k == pytest.approx(0.001)     # 1e-06 * 1000
        assert rec.output_per_1k == pytest.approx(0.002)

    async def test_captures_cache_rate(self, oracle):
        assert oracle.lookup("cheap-model").cache_read_per_1k == pytest.approx(0.0001)

    async def test_skips_non_chat_modes(self, oracle):
        assert oracle.lookup("an-embedding") is None

    async def test_skips_entries_without_price(self, oracle):
        assert oracle.lookup("no-price") is None

    async def test_captures_capabilities(self, oracle):
        rec = oracle.lookup("cheap-model")
        assert rec.supports_vision is True
        assert rec.supports_function_calling is True
        assert rec.context_window == 128000


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestLookup:
    async def test_exact_match(self, oracle):
        assert oracle.lookup("cheap-model").model_key == "cheap-model"

    async def test_case_insensitive(self, oracle):
        assert oracle.lookup("CHEAP-MODEL") is not None

    async def test_namespaced_key_matched_by_tail(self, oracle):
        assert oracle.lookup("namespaced-model") is not None

    async def test_unknown_returns_none(self, oracle):
        assert oracle.lookup("does-not-exist") is None

    async def test_empty_name_returns_none(self, oracle):
        assert oracle.lookup("") is None


@pytest.mark.asyncio
class TestProviderAwareLookup:
    """The same model name under two hosts carries different rates.

    Matching on name alone picked whichever entry came first, which reported false
    drift and would have replaced correct prices with another vendor's.
    """

    async def test_prefers_matching_provider(self, oracle):
        rec = oracle.lookup("shared-name", "Mistral AI")
        assert rec.model_key == "mistral/shared-name"
        assert rec.input_per_1k == pytest.approx(0.002)

    async def test_other_provider_gets_its_own_entry(self, oracle):
        rec = oracle.lookup("shared-name", "Fireworks AI")
        assert rec.model_key == "fireworks_ai/accounts/fireworks/models/shared-name"

    async def test_falls_back_to_any_entry_when_not_strict(self, oracle):
        assert oracle.lookup("shared-name", "Snowflake") is not None

    async def test_strict_mode_refuses_cross_vendor_match(self, oracle):
        """Snowflake has no first-party registry presence — better nothing than wrong."""
        assert oracle.lookup("shared-name", "Snowflake", require_provider_match=True) is None

    async def test_strict_mode_allows_matching_provider(self, oracle):
        rec = oracle.lookup("shared-name", "Mistral AI", require_provider_match=True)
        assert rec.model_key == "mistral/shared-name"

    async def test_unknown_provider_name_is_not_a_match(self, oracle):
        assert oracle.lookup("shared-name", "Nonexistent Co", require_provider_match=True) is None


@pytest.mark.asyncio
class TestCrossVendorSafety:
    async def test_drift_not_reported_across_vendors(self, oracle):
        """A Snowflake-hosted model must not be compared to Fireworks' rate."""
        m = _pm("shared-name", inp=9e-06, out=9e-06, confirmed=True, provider="Snowflake")
        assert oracle.find_drift([m]) == []

    async def test_drift_reported_within_same_vendor(self, oracle):
        m = _pm("shared-name", inp=4e-06, out=6e-06, confirmed=True, provider="Mistral AI")
        found = oracle.find_drift([m])
        assert len(found) == 1
        assert found[0].pct_difference == pytest.approx(100.0)

    async def test_fill_refuses_cross_vendor_price(self, oracle):
        m = _pm("shared-name", provider="Snowflake")
        assert oracle.fill_missing_prices([m]) == 0
        assert m.price_confirmed is False

    async def test_fill_uses_same_vendor_price(self, oracle):
        m = _pm("shared-name", provider="Mistral AI")
        assert oracle.fill_missing_prices([m]) == 1
        assert m.cost_per_input_token == pytest.approx(2e-06)


# ---------------------------------------------------------------------------
# Gap filling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestFillMissingPrices:
    async def test_fills_unconfirmed_model(self, oracle):
        m = _pm("cheap-model")
        assert oracle.fill_missing_prices([m]) == 1
        assert m.cost_per_input_token == pytest.approx(1e-06)
        assert m.price_confirmed is True

    async def test_records_registry_as_source(self, oracle):
        m = _pm("cheap-model")
        oracle.fill_missing_prices([m])
        assert "LiteLLM price registry" in m.source

    async def test_stamps_fetch_date(self, oracle):
        m = _pm("cheap-model")
        oracle.fill_missing_prices([m])
        assert m.price_as_of == oracle.fetched_at

    async def test_never_overwrites_a_confirmed_price(self, oracle):
        m = _pm("cheap-model", inp=9.9, out=9.9, confirmed=True)
        assert oracle.fill_missing_prices([m]) == 0
        assert m.cost_per_input_token == 9.9

    async def test_backfills_missing_context_window(self, oracle):
        m = _pm("cheap-model")
        oracle.fill_missing_prices([m])
        assert m.context_window == 128000

    async def test_keeps_existing_context_window(self, oracle):
        m = _pm("cheap-model", ctx=999)
        oracle.fill_missing_prices([m])
        assert m.context_window == 999

    async def test_unknown_model_left_alone(self, oracle):
        m = _pm("not-in-registry")
        assert oracle.fill_missing_prices([m]) == 0
        assert m.price_confirmed is False


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDriftDetection:
    async def test_reports_overstated_price(self, oracle):
        m = _pm("cheap-model", inp=2e-06, out=2e-06, confirmed=True)  # 2x registry
        found = oracle.find_drift([m])
        assert len(found) == 1
        assert found[0].direction == "overstated"
        assert found[0].pct_difference == pytest.approx(100.0)

    async def test_reports_understated_price(self, oracle):
        m = _pm("cheap-model", inp=5e-07, out=1e-06, confirmed=True)  # half registry
        found = oracle.find_drift([m])
        assert found[0].direction == "understated"

    async def test_ignores_within_threshold(self, oracle):
        m = _pm("cheap-model", inp=1.02e-06, out=2e-06, confirmed=True)  # 2% off
        assert oracle.find_drift([m]) == []

    async def test_never_mutates(self, oracle):
        m = _pm("cheap-model", inp=2e-06, out=2e-06, confirmed=True)
        oracle.find_drift([m])
        assert m.cost_per_input_token == 2e-06  # untouched

    async def test_skips_unconfirmed_models(self, oracle):
        m = _pm("cheap-model", inp=9e-06, out=9e-06, confirmed=False)
        assert oracle.find_drift([m]) == []

    async def test_skips_registry_sourced_models(self, oracle):
        """A filled model would otherwise be compared against itself."""
        m = _pm("cheap-model", inp=1e-06, out=2e-06, confirmed=True,
                source="LiteLLM price registry (cheap-model)")
        assert oracle.find_drift([m]) == []

    async def test_sorted_worst_first(self, oracle):
        a = _pm("cheap-model", inp=1.5e-06, out=2e-06, confirmed=True)       # 50%
        b = _pm("namespaced-model", inp=9e-06, out=1.5e-05, confirmed=True)  # 200%
        found = oracle.find_drift([a, b])
        assert [f.pct_difference for f in found] == sorted(
            [f.pct_difference for f in found], reverse=True
        )

    async def test_custom_threshold(self, oracle):
        m = _pm("cheap-model", inp=1.1e-06, out=2e-06, confirmed=True)  # 10%
        assert oracle.find_drift([m], threshold_pct=20) == []
        assert len(oracle.find_drift([m], threshold_pct=5)) == 1


# ---------------------------------------------------------------------------
# Availability / fallback
# ---------------------------------------------------------------------------

class TestSnapshotFallback:
    def test_vendored_snapshot_exists(self):
        assert SNAPSHOT_PATH.exists(), f"vendored snapshot missing at {SNAPSHOT_PATH}"

    def test_vendored_snapshot_is_valid_and_populated(self):
        with SNAPSHOT_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        assert len(data) > 500, "snapshot suspiciously small"

    @pytest.mark.asyncio
    async def test_falls_back_when_fetch_fails(self):
        o = PriceOracle()
        with patch.object(PriceOracle, "_fetch_remote", new=AsyncMock(return_value=None)):
            await o.load()
        assert o.loaded, "should have loaded from the vendored snapshot"
        assert str(SNAPSHOT_PATH) == o.source

    @pytest.mark.asyncio
    async def test_survives_total_unavailability(self):
        o = PriceOracle()
        with patch.object(PriceOracle, "_fetch_remote", new=AsyncMock(return_value=None)), \
             patch.object(PriceOracle, "_read_snapshot", return_value=None):
            await o.load()
        assert o.loaded is False
        # Must degrade, not raise
        assert o.lookup("anything") is None
        assert o.fill_missing_prices([_pm("x")]) == 0
        assert o.find_drift([_pm("x", confirmed=True)]) == []

    @pytest.mark.asyncio
    async def test_ttl_prevents_refetch(self):
        o = PriceOracle()
        fetch = AsyncMock(return_value=RAW)
        with patch.object(PriceOracle, "_fetch_remote", new=fetch):
            await o.load()
            await o.load()
        assert fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_force_refetches(self):
        o = PriceOracle()
        fetch = AsyncMock(return_value=RAW)
        with patch.object(PriceOracle, "_fetch_remote", new=fetch):
            await o.load()
            await o.load(force=True)
        assert fetch.await_count == 2


class TestSingleton:
    def test_returns_same_instance(self):
        assert get_price_oracle() is get_price_oracle()

    def test_reset_creates_new(self):
        first = get_price_oracle()
        reset_price_oracle()
        assert get_price_oracle() is not first
