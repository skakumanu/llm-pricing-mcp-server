"""Tests for price provenance: price_as_of, staleness, and unconfirmed-price handling.

Background: STATIC_PRICING is hand-maintained and the live sync used to be
subtractive only, so newly released models never appeared and prices aged
silently. These tests cover the provenance fields and the additive discovery
that replaced that behaviour.
"""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
# Aggregator withholds drifted prices, not just reports them
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAggregatorDemotesDrift:
    """A drifted price must stop being served the moment it's detected, not
    whenever a human next reads a weekly report. Uses a controlled fake oracle
    so this is deterministic regardless of whether anything in the live
    registry actually drifts today.
    """

    async def test_drifted_curated_price_is_withheld_from_default_output(self):
        from src.services.pricing_aggregator import PricingAggregatorService
        import src.services.pricing_aggregator as agg_module

        class FakeOracle:
            loaded = True

            async def load(self, force=False):
                pass

            def fill_missing_prices(self, models):
                return 0

            def demote_drifted(self, models, threshold_pct=5.0):
                for m in models:
                    if m.model_name == "gpt-4o":
                        m.price_confirmed = False
                return []

        svc = PricingAggregatorService()
        with patch.object(agg_module, "get_price_oracle", return_value=FakeOracle()):
            models, _ = await svc.get_all_pricing_async()

        assert "gpt-4o" not in {m.model_name for m in models}

    async def test_untouched_models_are_unaffected(self):
        """The fake oracle only demotes gpt-4o; everything else must still be
        present, proving demote_drifted's effect isn't applied wholesale."""
        from src.services.pricing_aggregator import PricingAggregatorService
        import src.services.pricing_aggregator as agg_module

        class FakeOracle:
            loaded = True

            async def load(self, force=False):
                pass

            def fill_missing_prices(self, models):
                return 0

            def demote_drifted(self, models, threshold_pct=5.0):
                for m in models:
                    if m.model_name == "gpt-4o":
                        m.price_confirmed = False
                return []

        svc = PricingAggregatorService()
        with patch.object(agg_module, "get_price_oracle", return_value=FakeOracle()):
            models, _ = await svc.get_all_pricing_async()

        assert len(models) > 1


# ---------------------------------------------------------------------------
# Current-generation catalogue coverage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCurrentGenerationModelsArePriced:
    """Recent releases must be listed AND priced.

    These entries carry input/output 0.0 with price_confirmed=False in
    STATIC_PRICING by design — the price oracle fills them from the registry so
    nobody hand-types a rate. If the oracle stops resolving one (a renamed
    registry key, a provider-alias miss), the model would silently drop out of
    every cost comparison. This catches that.
    """

    CURRENT = [
        "claude-opus-5", "claude-sonnet-5", "claude-fable-5",
        "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.4", "gpt-5.5",
        "gemini-3-pro-preview", "gemini-3-flash-preview",
        "gemini-3.5-flash", "gemini-3.6-flash",
        # "grok-4"/"grok-4-fast-reasoning" dropped: xAI's registry lineup moved
        # to grok-4.20/4.3/4.5/4.6/4.7 naming, so neither name resolves via the
        # oracle anymore. STATIC_PRICING's own grok-4/grok-4-fast-reasoning
        # entries are left as-is (unconfirmed) rather than guessed at here.
        "mistral-medium-latest", "magistral-medium-latest",
        "deepseek-v3",
    ]

    async def test_all_present_in_default_output(self):
        from src.services.pricing_aggregator import PricingAggregatorService
        models, _ = await PricingAggregatorService().get_all_pricing_async()
        names = {m.model_name for m in models}
        missing = [n for n in self.CURRENT if n not in names]
        assert not missing, (
            "current-generation models absent from default output — the oracle "
            f"failed to price them: {missing}"
        )

    async def test_all_have_a_real_price(self):
        from src.services.pricing_aggregator import PricingAggregatorService
        models, _ = await PricingAggregatorService().get_all_pricing_async()
        by = {m.model_name: m for m in models}
        unpriced = [
            n for n in self.CURRENT
            if n in by and (by[n].cost_per_input_token <= 0 or not by[n].price_confirmed)
        ]
        assert not unpriced, f"listed but still unpriced: {unpriced}"


# ---------------------------------------------------------------------------
# price_confirmed must survive every code path, not just the aggregator's
# ---------------------------------------------------------------------------

class TestUnconfirmedFlagSurvivesDirectPaths:
    """Providers build PricingMetrics in several places and callers use all of them.

    The aggregator stamps provenance centrally, which masked the fact that most
    providers dropped ``price_confirmed`` in their own builders: a direct call to
    ``fetch_pricing_data()`` or ``get_pricing_data()`` returned a $0.00 placeholder
    marked *confirmed*, which would sort as the cheapest option anywhere. CI caught
    it on OpenAI; it was latent in four other providers.
    """

    @staticmethod
    def _providers_with_unconfirmed_entries():
        import importlib
        from src.services.base_provider import BasePricingProvider
        out = []
        for path in sorted((project_root / "src" / "services").glob("*_pricing.py")):
            mod = importlib.import_module(f"src.services.{path.stem}")
            for attr in dir(mod):
                cls = getattr(mod, attr)
                if not (isinstance(cls, type) and issubclass_safe(cls, BasePricingProvider)):
                    continue
                static = getattr(cls, "STATIC_PRICING", None) or {}
                unconfirmed = [
                    k for k, v in static.items()
                    if isinstance(v, dict) and v.get("price_confirmed") is False
                ]
                if unconfirmed:
                    out.append((path.stem, cls, unconfirmed))
        return out

    @pytest.mark.asyncio
    async def test_async_path_preserves_flag(self):
        leaks = []
        for stem, cls, unconfirmed in self._providers_with_unconfirmed_entries():
            by = {m.model_name: m for m in await cls().fetch_pricing_data()}
            leaks += [
                f"{stem}.fetch_pricing_data(): {n}"
                for n in unconfirmed if n in by and by[n].price_confirmed
            ]
        assert not leaks, "0.0 prices marked confirmed:\n" + "\n".join(leaks)

    def test_sync_path_preserves_flag(self):
        leaks = []
        for stem, cls, unconfirmed in self._providers_with_unconfirmed_entries():
            if not hasattr(cls, "get_pricing_data"):
                continue
            by = {m.model_name: m for m in cls.get_pricing_data()}
            leaks += [
                f"{stem}.get_pricing_data(): {n}"
                for n in unconfirmed if n in by and by[n].price_confirmed
            ]
        assert not leaks, "0.0 prices marked confirmed:\n" + "\n".join(leaks)

    def test_the_guard_has_something_to_check(self):
        """Fail loudly if the fixtures stop covering any unconfirmed model."""
        assert self._providers_with_unconfirmed_entries(), (
            "no provider declares an unconfirmed entry — this guard is now vacuous"
        )


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


# ---------------------------------------------------------------------------
# v1.75.0 — machine-readable provenance: price_source_kind / registry_key
# ---------------------------------------------------------------------------

class TestPriceSourceKind:
    def test_defaults_to_provider_with_no_key(self):
        m = _pm()
        assert m.price_source_kind == "provider"
        assert m.registry_key is None

    def test_build_metrics_honours_entry_provenance(self, stub):
        info = {"input": 1.0, "output": 2.0, "price_source_kind": "registry",
                "registry_key": "acme/x", "price_as_of": "2026-10-25"}
        m = stub.build_metrics("x", info, source="s")
        assert m.price_source_kind == "registry"
        assert m.registry_key == "acme/x"
        assert m.price_confirmed is True        # usable; the kind carries the distinction
        assert m.price_as_of == "2026-10-25"

    def test_build_metrics_default_is_provider(self, stub):
        m = stub.build_metrics("x", {"input": 1.0, "output": 2.0}, source="s")
        assert m.price_source_kind == "provider" and m.registry_key is None

    def test_rejects_unknown_kind(self):
        with pytest.raises(Exception):
            _pm(price_source_kind="guess")

    def test_fill_missing_prices_marks_kind_and_key(self):
        from src.services.price_oracle import PriceOracle
        o = PriceOracle()
        o.load_from_dict({"acme/new-model": {
            "mode": "chat", "litellm_provider": "acme",
            "input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06,
        }}, "test")
        m = _pm("new-model", "Acme", 0.0, 0.0, price_confirmed=False)
        assert o.fill_missing_prices([m]) == 1
        assert m.price_source_kind == "registry"
        assert m.registry_key == "acme/new-model"
        assert m.price_confirmed is True
        assert m.source == "LiteLLM price registry (acme/new-model)"

    def test_fill_missing_prices_leaves_curated_kind_alone(self):
        from src.services.price_oracle import PriceOracle
        o = PriceOracle()
        o.load_from_dict({"acme/curated": {
            "mode": "chat", "litellm_provider": "acme",
            "input_cost_per_token": 9e-06, "output_cost_per_token": 9e-06,
        }}, "test")
        m = _pm("curated", "Acme", 1e-06, 2e-06)
        assert o.fill_missing_prices([m]) == 0
        assert m.price_source_kind == "provider" and m.cost_per_input_token == 1e-06


# ---------------------------------------------------------------------------
# v1.75.0 — registry-sourced models merged from data/registry_models/<slug>.json
# ---------------------------------------------------------------------------

def _write_registry_models(tmp_path, provider="StubProvider", models=None):
    import json
    from src.services.base_provider import registry_models_slug
    doc = {"provider": provider, "registry_provider": "stub", "generated_by": "test",
           "models": models if models is not None else {}}
    (tmp_path / f"{registry_models_slug(provider)}.json").write_text(json.dumps(doc), encoding="utf-8")


REGISTRY_ENTRY = {
    "input": 0.0005, "output": 0.0015, "context_window": 64000,
    "supports_vision": True, "supports_function_calling": False,
    "price_source_kind": "registry", "registry_key": "stub/from-registry",
    "price_as_of": "2026-10-25", "added_on": "2026-10-25",
}


@pytest.mark.asyncio
class TestRegistryModelsMerge:
    async def test_missing_file_merges_nothing(self, tmp_path):
        class P(_StubProvider):
            REGISTRY_MODELS_DIR = tmp_path
        data, _ = await P().get_pricing_with_status()
        assert {m.model_name for m in data} == {"priced", "unpriced", "own-date"}

    async def test_json_entries_are_appended_as_registry_sourced(self, tmp_path):
        _write_registry_models(tmp_path, models={"from-registry": REGISTRY_ENTRY})

        class P(_StubProvider):
            REGISTRY_MODELS_DIR = tmp_path
        data, _ = await P().get_pricing_with_status()
        m = next(x for x in data if x.model_name == "from-registry")
        assert m.provider == "StubProvider"
        assert m.price_source_kind == "registry"
        assert m.registry_key == "stub/from-registry"
        assert m.price_confirmed is True
        assert m.price_as_of == "2026-10-25"        # its own date, not PRICE_AS_OF
        assert m.cost_per_input_token == pytest.approx(5e-07)   # per-1k -> per-token
        assert m.context_window == 64000 and m.supports_vision is True
        assert m.source == "LiteLLM price registry (stub/from-registry)"

    async def test_curated_entry_wins_a_name_collision(self, tmp_path):
        collide = dict(REGISTRY_ENTRY, input=99.0, output=99.0)
        _write_registry_models(tmp_path, models={"priced": collide, "PRICED": collide})

        class P(_StubProvider):
            REGISTRY_MODELS_DIR = tmp_path
        data, _ = await P().get_pricing_with_status()
        priced = [m for m in data if m.model_name.lower() == "priced"]
        assert len(priced) == 1
        assert priced[0].price_source_kind == "provider"
        assert priced[0].cost_per_input_token == 1.0  # the stub's _pm value, not 99/1000

    async def test_registry_price_note_lands_in_best_for(self, tmp_path):
        _write_registry_models(tmp_path, models={"from-registry": REGISTRY_ENTRY})

        class P(_StubProvider):
            REGISTRY_MODELS_DIR = tmp_path
            REGISTRY_PRICE_NOTE = "Billed in DBUs; USD figure is indicative."
        data, _ = await P().get_pricing_with_status()
        assert next(m for m in data if m.model_name == "from-registry").best_for == P.REGISTRY_PRICE_NOTE
        # curated entries are not annotated
        assert next(m for m in data if m.model_name == "priced").best_for is None

    async def test_note_does_not_override_an_entry_with_its_own_best_for(self, tmp_path):
        _write_registry_models(tmp_path, models={"from-registry": dict(REGISTRY_ENTRY, best_for="own text")})

        class P(_StubProvider):
            REGISTRY_MODELS_DIR = tmp_path
            REGISTRY_PRICE_NOTE = "note"
        data, _ = await P().get_pricing_with_status()
        assert next(m for m in data if m.model_name == "from-registry").best_for == "own text"

    async def test_rewritten_file_is_picked_up_without_restart(self, tmp_path):
        import os
        import time
        _write_registry_models(tmp_path, models={"a": REGISTRY_ENTRY})

        class P(_StubProvider):
            REGISTRY_MODELS_DIR = tmp_path
        p = P()
        assert "a" in {m.model_name for m in (await p.get_pricing_with_status())[0]}
        _write_registry_models(tmp_path, models={"b": REGISTRY_ENTRY})
        path = tmp_path / "stubprovider.json"
        os.utime(path, ns=(time.time_ns() + 2_000_000_000, time.time_ns() + 2_000_000_000))
        names = {m.model_name for m in (await p.get_pricing_with_status())[0]}
        assert "b" in names and "a" not in names

    async def test_unreadable_file_degrades_to_curated_only(self, tmp_path):
        (tmp_path / "stubprovider.json").write_text("{not json", encoding="utf-8")

        class P(_StubProvider):
            REGISTRY_MODELS_DIR = tmp_path
        data, status = await P().get_pricing_with_status()
        assert status.is_available
        assert {m.model_name for m in data} == {"priced", "unpriced", "own-date"}

    @pytest.mark.parametrize("bad", [
        dict(REGISTRY_ENTRY, input="0.0005"),                 # string price: TypeError in build_metrics
        dict(REGISTRY_ENTRY, price_source_kind="Registry"),   # Literal validation
        dict(REGISTRY_ENTRY, context_window="big"),           # non-numeric
    ])
    async def test_one_malformed_entry_is_skipped_not_the_whole_provider(self, tmp_path, bad):
        """The whole-file contract (unreadable -> curated-only) must hold per row
        too: one bad entry must not empty the provider's catalogue."""
        _write_registry_models(tmp_path, models={"good": REGISTRY_ENTRY, "bad": bad})

        class P(_StubProvider):
            REGISTRY_MODELS_DIR = tmp_path
        data, status = await P().get_pricing_with_status()
        assert status.is_available
        names = {m.model_name for m in data}
        assert "good" in names and "bad" not in names
        assert {"priced", "unpriced", "own-date"} <= names

    async def test_merged_models_are_known_to_discovery(self, tmp_path):
        """A registry-sourced model must not be re-discovered as an unpriced duplicate."""
        _write_registry_models(tmp_path, models={"from-registry": REGISTRY_ENTRY})

        class P(_StubProvider):
            REGISTRY_MODELS_DIR = tmp_path
        p = P()
        data, _ = await p.get_pricing_with_status()
        out = p._discover_new_models(data, frozenset({"from-registry", "brand-new"}))
        assert [m.model_name for m in out if m.model_name == "from-registry"] == ["from-registry"]
        assert "brand-new" in {m.model_name for m in out}
