"""Catalogue-wide integrity guards for STATIC_PRICING (v1.74.0).

Each guard here corresponds to a failure the October 2026 reconfirmation pass
found had been live for months without any test noticing:

- **Registry coverage floor.** The price oracle's key matching silently
  skipped every model name containing "." or "/", so ~40% of the catalogue was
  never drift-checked. Coverage is measured against the *vendored* snapshot so
  the number is deterministic in CI; a matching regression drops it well
  below the floor.
- **Plausibility bounds.** Cloudflare's table held per-token numbers that the
  builder divided by 1000 again, serving every price 1000x too low. No real
  per-token chat price is below a fraction of a cent or above a few hundred
  dollars per million tokens, so anything outside the band is a units bug.
- **No retired model served as confirmed.** The registry carries
  ``deprecation_date``; a confirmed price for a model its provider has shut
  down is a price nobody can pay.
"""
import asyncio
import importlib
import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.base_provider import BasePricingProvider  # noqa: E402
from src.services.price_oracle import PriceOracle  # noqa: E402

SERVICES_DIR = project_root / "src" / "services"

# Floor for same-provider registry matches among confirmed per-token models,
# measured against data/model_prices_snapshot.json. 53% at v1.74.0; the
# matching bug this guards against would put it near 35%. Raise it as
# coverage improves — never lower it to make a failure go away.
REGISTRY_COVERAGE_FLOOR_PCT = 50.0

# USD per 1M tokens. Wide on purpose: this catches units mistakes, not
# expensive models. Embedding models legitimately have an output price of 0.
MIN_INPUT_PER_1M = 0.005
MAX_INPUT_PER_1M = 300.0
MAX_OUTPUT_PER_1M = 1000.0


def _provider_classes():
    """Every BasePricingProvider subclass with a STATIC_PRICING table."""
    out = []
    for path in sorted(SERVICES_DIR.glob("*_pricing.py")):
        mod = importlib.import_module(f"src.services.{path.stem}")
        for _, cls in inspect.getmembers(mod, inspect.isclass):
            if (issubclass(cls, BasePricingProvider) and cls is not BasePricingProvider
                    and cls.__module__ == mod.__name__ and getattr(cls, "STATIC_PRICING", None)):
                out.append((path.name, cls))
    return out


def _static_entries():
    for fname, cls in _provider_classes():
        for key, info in cls.STATIC_PRICING.items():
            if isinstance(info, dict):
                yield fname, key, info


@pytest.fixture(scope="module")
def snapshot_oracle():
    o = PriceOracle()
    with patch.object(PriceOracle, "_fetch_remote", new=AsyncMock(return_value=None)):
        asyncio.run(o.load())
    assert o.loaded, "vendored snapshot must load"
    return o


@pytest.fixture(scope="module")
def catalogue(snapshot_oracle):
    """The full per-token catalogue priced against the vendored snapshot only."""
    from src.services import pricing_aggregator as pa
    with patch.object(pa, "get_price_oracle", return_value=snapshot_oracle):
        models, _ = asyncio.run(
            pa.PricingAggregatorService().get_all_pricing_async(include_unconfirmed=True)
        )
    return [m for m in models if m.pricing_model == "per_token"]


class TestRegistryCoverageFloor:
    def test_confirmed_prices_are_mostly_registry_checkable(self, snapshot_oracle, catalogue):
        confirmed = [m for m in catalogue if m.price_confirmed]
        assert confirmed
        matched = [
            m for m in confirmed
            if snapshot_oracle.lookup(m.model_name, m.provider, require_provider_match=True)
        ]
        pct = len(matched) / len(confirmed) * 100
        unmatched = sorted(f"{m.provider}/{m.model_name}" for m in confirmed if m not in matched)
        assert pct >= REGISTRY_COVERAGE_FLOOR_PCT, (
            f"only {pct:.1f}% of {len(confirmed)} confirmed prices have a same-provider registry "
            f"entry (floor {REGISTRY_COVERAGE_FLOOR_PCT}%). Either the oracle's key matching "
            f"regressed or the catalogue grew with models the registry cannot vouch for. "
            f"Unmatched sample: {unmatched[:10]}"
        )


class TestPlausibilityBounds:
    def test_every_confirmed_static_price_is_a_plausible_per_1k_value(self):
        bad = []
        for fname, key, info in _static_entries():
            if info.get("price_confirmed") is False:
                continue
            inp = float(info.get("input", 0)) * 1000   # per 1k -> per 1M
            out = float(info.get("output", 0)) * 1000
            if not (MIN_INPUT_PER_1M <= inp <= MAX_INPUT_PER_1M):
                bad.append(f"{fname}:{key} input ${inp:.6f}/1M")
            if not (0 <= out <= MAX_OUTPUT_PER_1M):
                bad.append(f"{fname}:{key} output ${out:.6f}/1M")
        assert not bad, (
            "prices outside the plausible band — almost certainly a units mistake "
            "(STATIC_PRICING is USD per 1k tokens; the builder divides by 1000):\n  "
            + "\n  ".join(bad)
        )

    def test_unconfirmed_placeholders_carry_no_price(self):
        bad = [
            f"{fname}:{key}" for fname, key, info in _static_entries()
            if info.get("price_confirmed") is False and (info.get("input") or info.get("output"))
        ]
        assert not bad, f"unconfirmed entries should hold 0.0 placeholders, not a stale number: {bad}"


class TestNoRetiredModelServed:
    def test_no_confirmed_model_is_past_its_registry_deprecation_date(self, snapshot_oracle, catalogue):
        # Only meaningful once the snapshot carries deprecation_date (added to
        # the weekly refresh in v1.74.0); until then it passes vacuously.
        import datetime
        today = datetime.date.today().isoformat()
        served_retired = []
        for m in catalogue:
            if not m.price_confirmed:
                continue
            rec = snapshot_oracle.lookup(m.model_name, m.provider, require_provider_match=True)
            if rec and rec.deprecation_date and rec.deprecation_date <= today:
                served_retired.append(f"{m.provider}/{m.model_name} (retired {rec.deprecation_date})")
        assert not served_retired, (
            "confirmed prices for models the provider has shut down — withhold_retired() "
            "should have caught these:\n  " + "\n  ".join(served_retired)
        )
