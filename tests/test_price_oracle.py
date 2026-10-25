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
    # One first party, a bare canonical key beside two regional variants that
    # agree with each other at +10% — the real shape of Azure's o1-mini (and,
    # after Oct 2026, of gpt-4o/o1/o3-mini too). The regionals are a different
    # SKU (Data Zone tier), not a majority opinion about the bare price.
    "bedrock/disputed-model": {
        "mode": "chat", "litellm_provider": "bedrock",
        "input_cost_per_token": 1.1e-06, "output_cost_per_token": 4.4e-06,
    },
    "bedrock/eu/disputed-model": {
        "mode": "chat", "litellm_provider": "bedrock",
        "input_cost_per_token": 1.21e-06, "output_cost_per_token": 4.84e-06,
    },
    "bedrock/us/disputed-model": {
        "mode": "chat", "litellm_provider": "bedrock",
        "input_cost_per_token": 1.21e-06, "output_cost_per_token": 4.84e-06,
    },
    # No bare key at all: three regionals, two agreeing. The only sensible
    # answer is the majority among equals.
    "bedrock/eu/only-regional": {
        "mode": "chat", "litellm_provider": "bedrock",
        "input_cost_per_token": 1.21e-06, "output_cost_per_token": 4.84e-06,
    },
    "bedrock/us/only-regional": {
        "mode": "chat", "litellm_provider": "bedrock",
        "input_cost_per_token": 1.21e-06, "output_cost_per_token": 4.84e-06,
    },
    "bedrock/apac/only-regional": {
        "mode": "chat", "litellm_provider": "bedrock",
        "input_cost_per_token": 1.1e-06, "output_cost_per_token": 4.4e-06,
    },
    # The exact gpt-4o-mini shape: bare + global-standard at $0.15 vs eu + us
    # at $0.165 — a 2-2 tie that a flat vote resolves by dict order.
    "azure/tie-model": {
        "mode": "chat", "litellm_provider": "azure",
        "input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07,
    },
    "azure/global-standard/tie-model": {
        "mode": "chat", "litellm_provider": "azure",
        "input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07,
    },
    "azure/eu/tie-model": {
        "mode": "chat", "litellm_provider": "azure",
        "input_cost_per_token": 1.65e-07, "output_cost_per_token": 6.6e-07,
    },
    "azure/us/tie-model": {
        "mode": "chat", "litellm_provider": "azure",
        "input_cost_per_token": 1.65e-07, "output_cost_per_token": 6.6e-07,
    },
    # Bedrock's dotted cross-region keys have no slash: the bare and the
    # `us.` profile share depth 0, and a GovCloud key sits deeper.
    "anthropic.dotted-model": {
        "mode": "chat", "litellm_provider": "bedrock",
        "input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05,
    },
    "us.anthropic.dotted-model": {
        "mode": "chat", "litellm_provider": "bedrock",
        "input_cost_per_token": 3e-06, "output_cost_per_token": 1.5e-05,
    },
    "bedrock/us-gov-east-1/dotted-model": {
        "mode": "chat", "litellm_provider": "bedrock",
        "input_cost_per_token": 3.9e-06, "output_cost_per_token": 1.95e-05,
    },
    # Key shapes the pre-v1.73.11 tail match could never see. The curated name
    # is multi-segment (Fireworks, Together, Cloudflare) or contains a dot
    # (Cerebras, Vertex), so neither "last slash segment" nor "last dot
    # segment" equals it; only an exact or suffix match does.
    "fireworks_ai/accounts/fireworks/models/multi-seg-model": {
        "mode": "chat", "litellm_provider": "fireworks_ai",
        "input_cost_per_token": 9e-07, "output_cost_per_token": 9e-07,
    },
    "cerebras/llama9.9-1b": {
        "mode": "chat", "litellm_provider": "cerebras",
        "input_cost_per_token": 1e-07, "output_cost_per_token": 1e-07,
    },
    "cloudflare/@cf/meta/dotted-slashed-model": {
        "mode": "chat", "litellm_provider": "cloudflare",
        "input_cost_per_token": 2.7e-08, "output_cost_per_token": 2.01e-07,
    },
    # Vertex publishes its own rate for a model the Developer API prices
    # differently. When both exist a "Google" lookup must take the gemini/
    # entry; when only Vertex has it (preview models), Vertex is the fallback.
    "gemini/pref-model": {
        "mode": "chat", "litellm_provider": "gemini",
        "input_cost_per_token": 1e-07, "output_cost_per_token": 4e-07,
    },
    "vertex_ai/pref-model": {
        "mode": "chat", "litellm_provider": "vertex_ai",
        "input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07,
    },
    "vertex_ai/vertex-only-model": {
        "mode": "chat", "litellm_provider": "vertex_ai",
        "input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07,
    },
    "snowflake/cortex-model": {
        "mode": "chat", "litellm_provider": "snowflake",
        "input_cost_per_token": 7.2e-07, "output_cost_per_token": 7.2e-07,
    },
    "oci/oci.vendor-model": {
        "mode": "chat", "litellm_provider": "oci",
        "input_cost_per_token": 1.5e-07, "output_cost_per_token": 1.5e-07,
    },
    # v1.74.0: retirement and output-price drift.
    "openai/retired-model": {
        "mode": "chat", "litellm_provider": "openai",
        "input_cost_per_token": 1e-05, "output_cost_per_token": 3e-05,
        "deprecation_date": "2026-01-01",
    },
    "openai/sunsetting-model": {
        "mode": "chat", "litellm_provider": "openai",
        "input_cost_per_token": 1e-05, "output_cost_per_token": 3e-05,
        "deprecation_date": "2099-01-01",
    },
    # Input agrees, output is 4x off — the Replicate Mixtral shape.
    "replicate/output-drift-model": {
        "mode": "chat", "litellm_provider": "replicate",
        "input_cost_per_token": 3e-07, "output_cost_per_token": 1e-06,
    },
}


def _pm(name, inp=0.0, out=0.0, confirmed=False, provider="Acme", source=None, ctx=None, **kw):
    return PricingMetrics(
        model_name=name, provider=provider,
        cost_per_input_token=inp, cost_per_output_token=out,
        price_confirmed=confirmed, source=source, context_window=ctx, **kw,
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

    # v1.73.11: the three key shapes the old "last dot segment" match missed.
    async def test_multi_segment_name_matches_namespaced_key_by_suffix(self, oracle):
        rec = oracle.lookup("accounts/fireworks/models/multi-seg-model", "Fireworks AI",
                            require_provider_match=True)
        assert rec is not None
        assert rec.model_key == "fireworks_ai/accounts/fireworks/models/multi-seg-model"

    async def test_dotted_single_segment_name_matches_slash_tail(self, oracle):
        rec = oracle.lookup("llama9.9-1b", "Cerebras", require_provider_match=True)
        assert rec is not None and rec.model_key == "cerebras/llama9.9-1b"

    async def test_dotted_and_slashed_name_matches_by_suffix(self, oracle):
        rec = oracle.lookup("@cf/meta/dotted-slashed-model", "Cloudflare AI",
                            require_provider_match=True)
        assert rec is not None and rec.input_per_1k == pytest.approx(2.7e-05)

    async def test_dotted_vendor_prefix_name_matches_slash_tail(self, oracle):
        rec = oracle.lookup("oci.vendor-model", "Oracle OCI", require_provider_match=True)
        assert rec is not None and rec.model_key == "oci/oci.vendor-model"

    async def test_suffix_match_does_not_fire_on_partial_segments(self, oracle):
        # "models/multi-seg-model" is a suffix of the key only after a "/", and
        # "seg-model" is not a segment boundary at all.
        assert oracle.lookup("seg-model") is None
        assert oracle.lookup("ulti-seg-model") is None


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
        """shared-name exists only under Mistral and Fireworks — better nothing than wrong."""
        assert oracle.lookup("shared-name", "Snowflake", require_provider_match=True) is None

    # v1.73.11: alias fixes found by the stale-price reconfirmation pass.
    async def test_snowflake_is_a_first_party_for_its_own_cortex_entries(self, oracle):
        rec = oracle.lookup("cortex-model", "Snowflake", require_provider_match=True)
        assert rec is not None and rec.model_key == "snowflake/cortex-model"

    async def test_google_prefers_the_developer_api_entry_over_vertex(self, oracle):
        # Vertex AI prices the same model differently from the Developer API, so
        # a Google-catalogue price compared with vertex_ai/ would be false drift.
        rec = oracle.lookup("pref-model", "Google", require_provider_match=True)
        assert rec.model_key == "gemini/pref-model"
        assert rec.input_per_1k == pytest.approx(1e-04)

    async def test_google_falls_back_to_vertex_when_nothing_else_exists(self, oracle):
        # Preview models often exist only under vertex_ai/; filling from there
        # beats leaving a current model unpriced.
        rec = oracle.lookup("vertex-only-model", "Google", require_provider_match=True)
        assert rec is not None and rec.model_key == "vertex_ai/vertex-only-model"

    async def test_vertex_ai_takes_its_own_entry_even_when_gemini_exists(self, oracle):
        rec = oracle.lookup("pref-model", "Vertex AI", require_provider_match=True)
        assert rec.model_key == "vertex_ai/pref-model"
        assert rec.input_per_1k == pytest.approx(1.5e-04)

    async def test_catalogue_display_names_resolve_to_registry_providers(self, oracle):
        # The catalogue says "AI21 Labs" / "Oracle OCI"; the alias table used to
        # key these as "ai21" / "oracle cloud", so neither ever matched.
        assert oracle.lookup("oci.vendor-model", "Oracle OCI", require_provider_match=True) is not None
        from src.services.price_oracle import _aliases_for
        assert "ai21" in _aliases_for("AI21 Labs")
        assert "oci" in _aliases_for("Oracle OCI")

    async def test_strict_mode_allows_matching_provider(self, oracle):
        rec = oracle.lookup("shared-name", "Mistral AI", require_provider_match=True)
        assert rec.model_key == "mistral/shared-name"

    async def test_unknown_provider_name_is_not_a_match(self, oracle):
        assert oracle.lookup("shared-name", "Nonexistent Co", require_provider_match=True) is None


@pytest.mark.asyncio
class TestSameProviderDisagreement:
    """One first party, several registry keys for one model, different prices.

    Deeper keys (``azure/eu/x``, ``bedrock/us-gov-east-1/x``) are region or
    tier variants — different SKUs — not noisy duplicates of the bare key.
    v1.72.3 resolved this shape by majority vote, which protected a curated
    o1-mini price that was actually the regional tier and, once the registry
    added regionals for four more Azure models, withheld four *correct* prices
    as drift. lookup() must prefer the bare canonical key; a vote is only the
    fallback among equal-depth candidates.
    """

    async def test_bare_canonical_key_wins_over_regional_variants(self, oracle):
        rec = oracle.lookup("disputed-model", "Amazon Bedrock", require_provider_match=True)
        assert rec.input_per_1k == pytest.approx(0.0011)
        assert rec.output_per_1k == pytest.approx(0.0044)

    async def test_result_is_deterministic_regardless_of_dict_order(self):
        reordered_raw = {
            "bedrock/eu/disputed-model": RAW["bedrock/eu/disputed-model"],
            "bedrock/us/disputed-model": RAW["bedrock/us/disputed-model"],
            "bedrock/disputed-model": RAW["bedrock/disputed-model"],
        }
        o = PriceOracle()
        with patch.object(PriceOracle, "_fetch_remote", new=AsyncMock(return_value=reordered_raw)):
            await o.load()
        rec = o.lookup("disputed-model", "Amazon Bedrock", require_provider_match=True)
        assert rec.input_per_1k == pytest.approx(0.0011)

    async def test_no_bare_key_falls_back_to_majority_among_equals(self, oracle):
        rec = oracle.lookup("only-regional", "Amazon Bedrock", require_provider_match=True)
        assert rec.input_per_1k == pytest.approx(0.00121)

    async def test_two_two_tie_is_settled_by_depth_not_dict_order(self):
        # gpt-4o-mini's real shape. Whichever pair is authored first, the bare
        # key alone sits at depth 1 and must win.
        for order in (
            ["azure/tie-model", "azure/global-standard/tie-model", "azure/eu/tie-model", "azure/us/tie-model"],
            ["azure/eu/tie-model", "azure/us/tie-model", "azure/global-standard/tie-model", "azure/tie-model"],
        ):
            raw = {k: RAW[k] for k in order}
            o = PriceOracle()
            with patch.object(PriceOracle, "_fetch_remote", new=AsyncMock(return_value=raw)):
                await o.load()
            rec = o.lookup("tie-model", "Azure OpenAI", require_provider_match=True)
            assert rec.model_key == "azure/tie-model", order
            assert rec.input_per_1k == pytest.approx(0.00015)

    async def test_dotted_bedrock_keys_outrank_deeper_govcloud_key(self, oracle):
        # Both dotted keys are depth 0 and agree; the depth-2 GovCloud entry
        # must not be considered at all.
        rec = oracle.lookup("dotted-model", "Amazon Bedrock", require_provider_match=True)
        assert rec.input_per_1k == pytest.approx(0.003)
        assert "us-gov" not in rec.model_key


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

    async def test_still_reports_unconfirmed_models_with_a_real_price(self, oracle):
        """A model demote_drifted() just withheld is unconfirmed but still holds
        its original curated price, and must keep showing up here — otherwise the
        audit would go silent on exactly the models it most needs to flag."""
        m = _pm("cheap-model", inp=9e-06, out=9e-06, confirmed=False)
        found = oracle.find_drift([m])
        assert len(found) == 1

    async def test_skips_models_with_no_real_price(self, oracle):
        """Zero cost means never-priced, not disputed — nothing to compare."""
        m = _pm("cheap-model", confirmed=False)  # inp=0.0, out=0.0 by default
        assert oracle.find_drift([m]) == []

    async def test_registry_sourced_model_matching_the_registry_is_silent(self, oracle):
        """A model filled at runtime resolves to the record it was filled from:
        0% drift, nothing reported. (v1.75.0 dropped the source-string skip.)"""
        m = _pm("cheap-model", inp=1e-06, out=2e-06, confirmed=True,
                source="LiteLLM price registry (cheap-model)",
                price_source_kind="registry", registry_key="cheap-model")
        assert oracle.find_drift([m]) == []

    async def test_stored_registry_sourced_price_the_registry_has_moved_is_reported(self, oracle):
        """OWNER DECISION (FR10): a registry-sourced entry stored by the weekly
        pickup carries the registry's price as of that run. If the live registry
        has since moved it by more than the threshold, it is drift — reported
        here and withheld by demote_drifted — until the next pickup re-syncs."""
        m = _pm("cheap-model", inp=2e-06, out=2e-06, confirmed=True,  # 2x registry input
                source="LiteLLM price registry (cheap-model)",
                price_source_kind="registry", registry_key="cheap-model")
        found = oracle.find_drift([m])
        assert len(found) == 1 and found[0].direction == "overstated"
        oracle.demote_drifted([m])
        assert m.price_confirmed is False

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
# Drift withholding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDemoteDrifted:
    """A drifted price must stop being served the moment it's detected, not
    whenever a human next reads a drift report."""

    async def test_withholds_a_drifted_model(self, oracle):
        m = _pm("cheap-model", inp=2e-06, out=2e-06, confirmed=True)  # 2x registry
        oracle.demote_drifted([m])
        assert m.price_confirmed is False

    async def test_leaves_the_price_value_untouched(self, oracle):
        """Withheld, not silently replaced with the registry's number."""
        m = _pm("cheap-model", inp=2e-06, out=2e-06, confirmed=True)
        oracle.demote_drifted([m])
        assert m.cost_per_input_token == 2e-06

    async def test_does_not_touch_models_within_threshold(self, oracle):
        m = _pm("cheap-model", inp=1.02e-06, out=2e-06, confirmed=True)  # 2% off
        oracle.demote_drifted([m])
        assert m.price_confirmed is True

    async def test_returns_the_same_findings_as_find_drift(self, oracle):
        m = _pm("cheap-model", inp=2e-06, out=2e-06, confirmed=True)
        findings = oracle.demote_drifted([m])
        assert len(findings) == 1
        assert findings[0].model_name == "cheap-model"

    async def test_does_not_touch_unrelated_models(self, oracle):
        drifted = _pm("cheap-model", inp=2e-06, out=2e-06, confirmed=True)
        unrelated = _pm("namespaced-model", inp=3e-06, out=1.5e-05, confirmed=True)
        oracle.demote_drifted([drifted, unrelated])
        assert drifted.price_confirmed is False
        assert unrelated.price_confirmed is True

    async def test_no_findings_returns_empty_list(self, oracle):
        m = _pm("cheap-model", inp=1e-06, out=2e-06, confirmed=True)  # matches registry
        assert oracle.demote_drifted([m]) == []


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
        assert o.demote_drifted([_pm("x", confirmed=True)]) == []

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


# ---------------------------------------------------------------------------
# v1.74.0: retirement withholding
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestWithholdRetired:
    async def test_parses_deprecation_date(self, oracle):
        assert oracle.lookup("retired-model", "OpenAI").deprecation_date == "2026-01-01"
        assert oracle.lookup("cheap-model").deprecation_date is None

    async def test_withholds_once_the_date_has_passed(self, oracle):
        m = _pm("retired-model", inp=1e-05, out=3e-05, confirmed=True, provider="OpenAI")
        retired = oracle.withhold_retired([m], today="2026-10-25")
        assert retired == [m]
        assert m.price_confirmed is False
        assert m.retired_on == "2026-01-01"

    async def test_leaves_a_scheduled_retirement_alone_until_the_day(self, oracle):
        m = _pm("sunsetting-model", inp=1e-05, out=3e-05, confirmed=True, provider="OpenAI")
        assert oracle.withhold_retired([m], today="2026-10-25") == []
        assert m.price_confirmed is True and m.retired_on is None
        assert oracle.withhold_retired([m], today="2099-01-01") == [m]

    async def test_price_value_is_kept_for_the_audit(self, oracle):
        m = _pm("retired-model", inp=1e-05, out=3e-05, confirmed=True, provider="OpenAI")
        oracle.withhold_retired([m], today="2026-10-25")
        assert m.cost_per_input_token == 1e-05

    async def test_requires_the_same_provider(self, oracle):
        # Another vendor's retirement says nothing about this host.
        m = _pm("retired-model", inp=1e-05, out=3e-05, confirmed=True, provider="Snowflake")
        assert oracle.withhold_retired([m], today="2026-10-25") == []

    async def test_ignores_subscription_models(self, oracle):
        m = _pm("retired-model", inp=0.0, out=0.0, confirmed=False, provider="OpenAI")
        m.pricing_model = "subscription"
        assert oracle.withhold_retired([m], today="2026-10-25") == []

    async def test_unloaded_oracle_is_a_no_op(self):
        o = PriceOracle()
        assert o.withhold_retired([_pm("retired-model", provider="OpenAI")]) == []


# ---------------------------------------------------------------------------
# v1.74.0: output-price drift
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestOutputDrift:
    async def test_output_disagreement_is_reported_when_input_agrees(self, oracle):
        # Registry: 0.30 in / 1.00 out per 1M. Ours: 0.30 in / 0.25 out.
        m = _pm("output-drift-model", inp=3e-07, out=2.5e-07, confirmed=True, provider="Replicate")
        findings = oracle.find_drift([m])
        assert len(findings) == 1
        f = findings[0]
        assert f.basis == "output"
        assert f.direction == "understated"
        assert f.pct_difference == pytest.approx(75.0)
        d = f.as_dict()
        assert d["our_output_cost_per_1m_usd"] == pytest.approx(0.25)
        assert d["registry_output_cost_per_1m_usd"] == pytest.approx(1.0)

    async def test_input_basis_wins_when_it_is_the_larger_disagreement(self, oracle):
        m = _pm("output-drift-model", inp=3e-06, out=1.1e-06, confirmed=True, provider="Replicate")
        f = oracle.find_drift([m])[0]
        assert f.basis == "input" and f.direction == "overstated"

    async def test_agreement_on_both_sides_is_not_drift(self, oracle):
        m = _pm("output-drift-model", inp=3e-07, out=1e-06, confirmed=True, provider="Replicate")
        assert oracle.find_drift([m]) == []

    async def test_output_drift_is_withheld_like_input_drift(self, oracle):
        m = _pm("output-drift-model", inp=3e-07, out=2.5e-07, confirmed=True, provider="Replicate")
        oracle.demote_drifted([m])
        assert m.price_confirmed is False
