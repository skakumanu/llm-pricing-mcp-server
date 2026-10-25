"""Tests for the five registry-backed providers added in v1.75.0 and the
provider-count/provenance contract they ship with.

DeepInfra, Novita, Vercel AI Gateway, Nebius, Databricks. Each must behave like
any other provider everywhere providers are visible (status list, filters,
every tool that reads the catalogue), and every price must say whether it was
confirmed against the provider or taken from the reference registry.

The catalogue here is priced against the vendored snapshot only, so the
numbers are deterministic in CI.
"""
import asyncio
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import pricing_aggregator as pa  # noqa: E402
from src.services.base_provider import (  # noqa: E402
    REGISTRY_MODELS_DIR,
    registry_models_slug,
    render_registry_models_doc,
)
from src.services.data_quality import compute_data_quality_report  # noqa: E402
from src.services.price_oracle import SNAPSHOT_PATH, PriceOracle, _aliases_for  # noqa: E402
from src.services.pricing_aggregator import PROVIDER_COUNT, PROVIDER_FILTER_ALIASES  # noqa: E402

NEW_PROVIDERS = ["DeepInfra", "Novita", "Vercel AI Gateway", "Nebius", "Databricks"]
HAND_CONFIRMED_REQUIRED = ["DeepInfra", "Novita", "Vercel AI Gateway", "Nebius"]  # Databricks: by exception

# FR2 shortfall: the authoring sandbox could not reach any of the four rate
# cards, so the core entries carry the registry's numbers and are labelled
# registry-sourced. strict=True so promoting them flips this to a failure that
# forces the xfail to be removed rather than passing silently.
CORE_SHORTFALL = pytest.mark.xfail(
    strict=True,
    reason="rate card unreachable from the authoring sandbox; core entries are registry-labelled (FR2 shortfall)",
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@pytest.fixture(scope="module")
def snapshot_oracle():
    o = PriceOracle()
    with patch.object(PriceOracle, "_fetch_remote", new=AsyncMock(return_value=None)):
        asyncio.run(o.load())
    assert o.loaded
    return o


@pytest.fixture(scope="module")
def aggregator():
    return pa.PricingAggregatorService()


@pytest.fixture(scope="module")
def catalogue(snapshot_oracle, aggregator):
    """(all models incl. unconfirmed, provider statuses) against the snapshot."""
    with patch.object(pa, "get_price_oracle", return_value=snapshot_oracle):
        return asyncio.run(aggregator.get_all_pricing_async(include_unconfirmed=True))


@pytest.fixture(scope="module")
def default_output(snapshot_oracle, aggregator):
    with patch.object(pa, "get_price_oracle", return_value=snapshot_oracle):
        models, _ = asyncio.run(aggregator.get_all_pricing_async())
    return models


@pytest.fixture(scope="module")
def snapshot_raw():
    with SNAPSHOT_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _registry_chat_count(raw, provider):
    aliases = _aliases_for(provider)
    return sum(
        1 for v in raw.values()
        if isinstance(v, dict) and str(v.get("litellm_provider", "")).lower() in aliases
        and v.get("mode") in (None, "chat")
        and v.get("input_cost_per_token") is not None and v.get("output_cost_per_token") is not None
    )


# ---------------------------------------------------------------------------
# AC1 / AC2 — presence, status, filters
# ---------------------------------------------------------------------------

class TestPresence:
    def test_provider_count_is_31(self):
        assert PROVIDER_COUNT == 31
        assert len(pa.PROVIDER_SERVICES) == 31

    def test_statuses_list_31_providers_including_the_five(self, catalogue):
        _, statuses = catalogue
        names = [s.provider_name for s in statuses]
        assert len(statuses) == 31
        for p in NEW_PROVIDERS:
            assert p in names

    @pytest.mark.parametrize("provider", NEW_PROVIDERS)
    def test_each_new_provider_is_available_with_models(self, catalogue, provider):
        _, statuses = catalogue
        st = next(s for s in statuses if s.provider_name == provider)
        assert st.is_available is True
        assert st.models_count > 0

    @pytest.mark.parametrize("filter_string,provider", [
        ("deepinfra", "DeepInfra"), ("DeepInfra", "DeepInfra"), ("deep infra", "DeepInfra"),
        ("novita", "Novita"), ("Novita", "Novita"), ("novita ai", "Novita"),
        ("vercel", "Vercel AI Gateway"), ("Vercel AI Gateway", "Vercel AI Gateway"), ("ai gateway", "Vercel AI Gateway"),
        ("nebius", "Nebius"), ("Nebius", "Nebius"), ("nebius ai studio", "Nebius"),
        ("databricks", "Databricks"), ("Databricks", "Databricks"),
    ])
    def test_filter_resolves_display_name_and_short_forms(self, aggregator, filter_string, provider):
        models, statuses = asyncio.run(aggregator.get_pricing_by_provider_async(filter_string))
        assert len(statuses) == 1 and statuses[0].provider_name == provider
        assert models and all(m.provider == provider for m in models)

    def test_existing_filters_still_resolve(self, aggregator):
        for s in ("openai", "mistral ai", "x.ai", "oracle oci", "hugging face"):
            assert s in PROVIDER_FILTER_ALIASES
        models, statuses = asyncio.run(aggregator.get_pricing_by_provider_async("OPENAI"))
        assert models and statuses[0].provider_name == "OpenAI"

    def test_unknown_filter_is_empty(self, aggregator):
        assert asyncio.run(aggregator.get_pricing_by_provider_async("invalid_provider")) == ([], [])

    def test_aggregator_attribute_names_are_stable(self, aggregator):
        for attr, cls in pa.PROVIDER_SERVICES:
            assert isinstance(getattr(aggregator, attr), cls)
        assert aggregator.openai_service.provider_name == "OpenAI"


# ---------------------------------------------------------------------------
# AC3 — hand-confirmed core (shortfall recorded) and the Databricks exception
# ---------------------------------------------------------------------------

class TestHandConfirmedCore:
    @pytest.mark.parametrize("provider", [pytest.param(p, marks=CORE_SHORTFALL) for p in HAND_CONFIRMED_REQUIRED])
    def test_at_least_five_provider_confirmed_models(self, catalogue, provider):
        models, _ = catalogue
        confirmed = [m for m in models if m.provider == provider and m.price_confirmed
                     and m.price_source_kind == "provider"]
        assert len(confirmed) >= 5

    @pytest.mark.parametrize("provider", HAND_CONFIRMED_REQUIRED)
    def test_core_has_at_least_five_entries_each_citing_its_registry_key(self, catalogue, aggregator, provider):
        """Until the rate cards are verified the core is registry-labelled: the
        shortfall is visible per entry, never faked as provider-confirmed."""
        svc = next(s for s in (getattr(aggregator, attr) for attr, _ in pa.PROVIDER_SERVICES)
                   if s.provider_name == provider)
        core = svc.STATIC_PRICING
        assert len(core) >= 5
        for name, info in core.items():
            if info.get("price_source_kind") == "registry":
                assert info.get("registry_key"), f"{provider}/{name} registry-labelled without a key"
                assert DATE_RE.match(info.get("price_as_of", "")), f"{provider}/{name} undated"
        models, _ = catalogue
        for name, info in core.items():
            m = next(x for x in models if x.provider == provider and x.model_name == name)
            assert m.price_source_kind == info.get("price_source_kind", "provider")

    def test_databricks_is_all_registry_sourced_with_the_dbu_note(self, catalogue):
        from src.services.databricks_pricing import DatabricksPricingService
        models, _ = catalogue
        dbx = [m for m in models if m.provider == "Databricks"]
        assert len(dbx) >= 40
        assert all(m.price_source_kind == "registry" and m.registry_key for m in dbx)
        assert all(m.best_for == DatabricksPricingService.REGISTRY_PRICE_NOTE for m in dbx)
        assert "DBU" in DatabricksPricingService.REGISTRY_PRICE_NOTE
        assert DatabricksPricingService.STATIC_PRICING == {}


# ---------------------------------------------------------------------------
# AC4 — catalogue size vs registry; AC5 — entry contents
# ---------------------------------------------------------------------------

class TestCoverage:
    @pytest.mark.parametrize("provider", NEW_PROVIDERS)
    def test_catalogue_is_at_least_80pct_of_registry_chat_count(self, catalogue, snapshot_raw, provider):
        models, _ = catalogue
        ours = sum(1 for m in models if m.provider == provider)
        theirs = _registry_chat_count(snapshot_raw, provider)
        assert theirs > 0
        assert ours >= 0.8 * theirs, f"{provider}: {ours} listed vs {theirs} in the registry"

    def test_five_providers_add_at_least_350_models(self, catalogue):
        models, _ = catalogue
        assert sum(1 for m in models if m.provider in NEW_PROVIDERS) >= 350

    @pytest.mark.parametrize("provider", NEW_PROVIDERS)
    def test_registry_models_file_exists_and_every_entry_is_complete(self, provider):
        path = REGISTRY_MODELS_DIR / f"{registry_models_slug(provider)}.json"
        assert path.exists(), f"missing {path}"
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["provider"] == provider
        assert doc["generated_by"] == "scripts/pickup_registry_models.py"
        assert doc["models"]
        for name, e in doc["models"].items():
            assert e["price_source_kind"] == "registry", name
            assert e["registry_key"] and e["registry_key"].lower().split("/")[0] in _aliases_for(provider), name
            assert DATE_RE.match(e["price_as_of"]) and DATE_RE.match(e["added_on"]), name
            assert e["input"] > 0 and e["output"] > 0, f"{name} has a zero registry price"
            assert not name.lower().startswith(tuple(a + "/" for a in _aliases_for(provider))), name

    def test_registry_models_files_are_byte_stable(self):
        """Committed files must be exactly what the shared renderer (used by the
        pickup and the retire job alike) would write; a hand edit that changed
        the shape would make every weekly run rewrite the file."""
        for provider in NEW_PROVIDERS:
            path = REGISTRY_MODELS_DIR / f"{registry_models_slug(provider)}.json"
            raw = path.read_bytes()
            assert raw == render_registry_models_doc(json.loads(raw)), path

    def test_every_registry_sourced_model_in_catalogue_is_dated_keyed_and_priced(self, catalogue):
        models, _ = catalogue
        reg = [m for m in models if m.price_source_kind == "registry"]
        assert len(reg) >= 350
        for m in reg:
            assert m.registry_key, f"{m.provider}/{m.model_name}"
            assert m.source and m.source.startswith("LiteLLM price registry ("), m.source
            assert m.price_as_of and DATE_RE.match(m.price_as_of), f"{m.provider}/{m.model_name}"
            assert m.cost_per_input_token > 0 and m.cost_per_output_token > 0, f"{m.provider}/{m.model_name}"


# ---------------------------------------------------------------------------
# AC6 / AC7 — data quality split and usability in cost math
# ---------------------------------------------------------------------------

class TestProvenanceContract:
    def test_provider_confirmed_plus_registry_sourced_equals_confirmed(self, catalogue, snapshot_oracle):
        models, _ = catalogue
        r = compute_data_quality_report(models, snapshot_oracle)
        assert r.provider_confirmed + r.registry_sourced == r.confirmed_models
        per_token_confirmed = [m for m in models if m.pricing_model == "per_token" and m.price_confirmed]
        assert r.provider_confirmed == sum(1 for m in per_token_confirmed if m.price_source_kind == "provider")
        assert r.registry_sourced == sum(1 for m in per_token_confirmed if m.price_source_kind == "registry")
        assert r.registry_sourced >= 350
        d = r.as_dict()
        assert d["provider_confirmed"] == r.provider_confirmed and d["registry_sourced"] == r.registry_sourced

    def test_data_quality_not_regressed_against_pre_change_baseline(self, catalogue, snapshot_oracle):
        """AC13. Baseline measured against the same snapshot before this change:
        confirmed_pct 95.5, withheld_for_drift 2, never_priced 5, registry_coverage 54.1."""
        models, _ = catalogue
        r = compute_data_quality_report(models, snapshot_oracle)
        assert r.confirmed_pct >= 95.5
        assert r.withheld_for_drift <= 2
        assert r.never_priced <= 5
        assert r.registry_coverage_pct >= 54.1

    def test_registry_sourced_models_reach_the_default_output_with_nonzero_cost(self, default_output):
        reg = [m for m in default_output if m.price_source_kind == "registry" and m.provider in NEW_PROVIDERS]
        assert len(reg) >= 350
        for m in reg:
            assert m.price_confirmed is True
            assert m.cost_at_1m_tokens.total_cost > 0

    @pytest.mark.asyncio
    async def test_estimate_cost_tool_prices_a_registry_sourced_model(self, default_output):
        from mcp.tools.estimate_cost import EstimateCostTool
        target = next(m for m in default_output if m.provider == "Nebius" and m.price_source_kind == "registry")
        tool = EstimateCostTool()
        tool.service = AsyncMock()
        tool.service.find_model_pricing = AsyncMock(return_value=target)
        r = await tool.execute({"model_name": target.model_name, "input_tokens": 1_000_000, "output_tokens": 1_000_000})
        assert r["success"] is True
        assert r["total_cost"] > 0
        assert r["provider"] == "Nebius"

    @pytest.mark.asyncio
    async def test_compare_costs_tool_ranks_registry_sourced_models(self, default_output):
        from mcp.tools.compare_costs import CompareCostsTool
        picks = [m for m in default_output if m.provider == "DeepInfra"][:3]
        tool = CompareCostsTool()
        tool.service = AsyncMock()
        tool.service.get_all_pricing_async = AsyncMock(return_value=(default_output, []))
        r = await tool.execute({"model_names": [m.model_name for m in picks],
                                "input_tokens": 1000, "output_tokens": 1000})
        assert r["success"] is True
        assert all(row.get("is_available", True) for row in r["models"])
        assert all(row["total_cost"] > 0 for row in r["models"])

    def test_get_all_pricing_serialises_provenance(self, default_output):
        from mcp.tools.get_all_pricing import GetAllPricingTool
        m = next(x for x in default_output if x.provider == "Novita" and x.price_source_kind == "registry")
        d = GetAllPricingTool._serialize_pricing(m)
        for key in ("source", "price_as_of", "price_is_stale", "price_confirmed", "price_source_kind", "registry_key"):
            assert key in d
        assert d["price_source_kind"] == "registry" and d["registry_key"].startswith("novita/")


# ---------------------------------------------------------------------------
# AC8 — naming and oracle matching
# ---------------------------------------------------------------------------

class TestNaming:
    def test_no_new_provider_model_starts_with_its_registry_prefix(self, catalogue):
        models, _ = catalogue
        bad = []
        for m in models:
            if m.provider not in NEW_PROVIDERS:
                continue
            prefixes = tuple(a + "/" for a in _aliases_for(m.provider))
            if m.model_name.lower().startswith(prefixes):
                bad.append(f"{m.provider}/{m.model_name}")
        assert not bad, bad

    def test_oracle_finds_a_same_provider_record_for_every_registry_sourced_entry(self, catalogue, snapshot_oracle):
        models, _ = catalogue
        unresolved, mismatched = [], []
        for m in models:
            if m.price_source_kind != "registry":
                continue
            hit = snapshot_oracle.lookup(m.model_name, m.provider, require_provider_match=True)
            if hit is None:
                unresolved.append(f"{m.provider}/{m.model_name}")
            elif m.registry_key and hit.model_key.lower() != m.registry_key.lower():
                mismatched.append(f"{m.provider}/{m.model_name}: {hit.model_key} != {m.registry_key}")
        assert not unresolved, unresolved[:10]
        assert not mismatched, mismatched[:10]


# ---------------------------------------------------------------------------
# AC15 — provider count in docs (what's-new excluded: it is a historical record)
# ---------------------------------------------------------------------------

PROVIDER_COUNT_LOCATIONS = [
    "README.md",
    "docs/ARCHITECTURE.md",
    "server.json",
    "npm/llm-pricing-mcp/package.json",
    "npm/llm-pricing-mcp/README.md",
    "static/mcp-setup/index.html",
    "static/landing/index.html",
    "package.json",
]
OLD_COUNT_RE = re.compile(r"\b26\s+(?:major\s+)?(?:LLM\s+)?providers?\b", re.IGNORECASE)
OLD_IMPLS_RE = re.compile(r"\b26 provider impls\b")


class TestDocsProviderCount:
    @pytest.mark.parametrize("rel", PROVIDER_COUNT_LOCATIONS + ["src/main.py"])
    def test_zero_26_providers_matches(self, rel):
        text = (project_root / rel).read_text(encoding="utf-8")
        assert not OLD_COUNT_RE.search(text) and not OLD_IMPLS_RE.search(text), rel

    @pytest.mark.parametrize("rel", PROVIDER_COUNT_LOCATIONS)
    def test_each_location_reads_the_current_count(self, rel):
        text = (project_root / rel).read_text(encoding="utf-8")
        current = re.compile(rf"\b{PROVIDER_COUNT}\b[^\n]{{0,40}}\bprovider", re.IGNORECASE)
        assert current.search(text), f"{rel} does not state {PROVIDER_COUNT} providers"

    def test_architecture_states_the_count_in_all_three_places(self):
        text = (project_root / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
        assert f"from {PROVIDER_COUNT} providers" in text
        assert f"{PROVIDER_COUNT} provider impls" in text
        assert f"Each of {PROVIDER_COUNT} LLM providers" in text

    def test_openapi_description_derives_from_provider_count(self):
        text = (project_root / "src/main.py").read_text(encoding="utf-8")
        assert "{PROVIDER_COUNT} providers" in text

    def test_whats_new_top_entry_is_this_release_and_history_is_untouched(self):
        from src import __version__
        text = (project_root / "static/whats-new/index.html").read_text(encoding="utf-8")
        latest_idx = text.index('class="release latest"')
        assert text.count('class="release latest"') == 1
        assert f"v{__version__}" in text[latest_idx:latest_idx + 400]
        # the previous top entry keeps its historical text
        assert "Pricing integrity: 55 retired models removed, and the catalogue now polices itself" in text
        assert "(53% today)" in text
