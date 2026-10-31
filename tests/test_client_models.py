"""Tests for the curated client table (src/services/client_models.py, v1.85.0)."""
import asyncio
import re
import sys
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.pricing import PricingMetrics  # noqa: E402
from src.services import pricing_aggregator as pa  # noqa: E402
from src.services.base_provider import BasePricingProvider  # noqa: E402
from src.services.client_models import (  # noqa: E402
    CLIENTS,
    eligible_models,
    is_runnable,
    normalize_client_name,
    recognised_client_ids,
    resolve_client,
    resolve_handshake_client,
    restriction_label,
)
from src.services.price_oracle import PriceOracle  # noqa: E402

LEGACY = {
    "copilot": "github_copilot", "cursor": "cursor", "windsurf": "windsurf",
    "claude_code": "claude_code", "jetbrains": "jetbrains_ai", "amazon_q": "amazon_q",
}
CLAUDE_5X = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]


@pytest.fixture(scope="module")
def catalogue():
    """Offline catalogue: curated + registry-sourced entries, no live sync."""
    with patch.object(BasePricingProvider, "_fetch_live_model_ids", new=AsyncMock(return_value=None)):
        oracle = PriceOracle()
        with patch.object(PriceOracle, "_fetch_remote", new=AsyncMock(return_value=None)):
            asyncio.run(oracle.load())
        agg = pa.PricingAggregatorService()
        with patch.object(pa, "get_price_oracle", return_value=oracle):
            models, _ = asyncio.run(agg.get_all_pricing_async(include_unconfirmed=True))
    return models


def _pm(name, provider, pricing_model="per_token"):
    return PricingMetrics(
        model_name=name, provider=provider, cost_per_input_token=1e-6,
        cost_per_output_token=1e-6, pricing_model=pricing_model,
    )


@pytest.mark.parametrize("legacy,expected", sorted(LEGACY.items()))
def test_legacy_ide_context_values_resolve(legacy, expected):
    assert resolve_client(legacy).id == expected


@pytest.mark.parametrize("raw", ["Claude Code", "claude-code", "claude_code", "CLAUDE CODE"])
def test_claude_code_spellings_resolve_identically(raw):
    assert resolve_client(raw).id == "claude_code"


def test_unknown_and_empty_names_do_not_resolve():
    assert resolve_client("emacs-copilot-9000") is None
    assert resolve_client("") is None
    assert resolve_client(None) is None
    assert resolve_client("---") is None
    assert resolve_client(123) is None


def test_table_covers_every_required_client():
    assert set(recognised_client_ids()) == {
        "claude_code", "claude_desktop", "cursor", "github_copilot", "windsurf", "cline",
        "continue", "zed", "jetbrains_ai", "gemini_cli", "codex_cli", "aider", "amazon_q",
        "chatgpt_desktop",
    }


def test_ids_and_all_lookup_names_are_unambiguous():
    seen = {}
    for e in CLIENTS:
        for n in (e.id, e.display_name, *e.aliases, *e.handshake_names):
            key = normalize_client_name(n)
            assert seen.setdefault(key, e.id) == e.id, f"{n!r} maps to two clients"


def test_every_entry_cites_a_source_and_an_iso_as_of_not_in_the_future():
    for e in CLIENTS:
        assert e.source.startswith("https://"), e.id
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", e.as_of), e.id
        assert date.fromisoformat(e.as_of) <= date.today(), e.id
        assert e.menu in ("fixed", "open")
        if e.menu == "fixed":
            assert e.providers or e.models, f"{e.id}: fixed menu with nothing in it"


def test_handshake_resolution_is_exact_and_rejects_generic_hosts():
    assert resolve_handshake_client("claude-code").id == "claude_code"
    assert resolve_handshake_client("Cursor").id == "cursor"
    assert resolve_handshake_client("Visual Studio Code") is None
    assert resolve_handshake_client("my-claude-code-wrapper") is None
    assert resolve_handshake_client(None) is None
    assert resolve_handshake_client(5) is None


def test_fixed_menu_references_resolve_against_the_catalogue(catalogue):
    """FR12: a provider or model named in a fixed entry must exist."""
    providers = {m.provider.lower() for m in catalogue}
    names = {m.model_name.lower() for m in catalogue}
    for e in CLIENTS:
        if e.menu != "fixed":
            continue
        for p in e.providers:
            assert p.lower() in providers, f"{e.id}: dangling provider {p!r}"
        for n in e.models:
            assert n.lower() in names, f"{e.id}: dangling model {n!r}"


def test_native_providers_exist_for_clients_with_own_plans(catalogue):
    providers = {m.provider.lower() for m in catalogue}
    for e in CLIENTS:
        for np in e.native_providers:
            if np == "codeium":  # legacy alias of Windsurf
                continue
            assert any(np in p for p in providers), f"{e.id}: native provider {np!r} not in catalogue"


def test_claude_code_set_is_anthropic_only_and_includes_the_four_claude_5x(catalogue):
    entry = resolve_client("Claude Code")
    eligible = eligible_models(entry, catalogue)
    assert {m.provider for m in eligible} == {"Anthropic"}
    names = {m.model_name for m in eligible}
    for n in CLAUDE_5X:
        assert n in names
    assert not any(re.search(r"codestral|mixtral|mistral|llama", m.model_name, re.I) for m in eligible)


def test_open_menu_clients_exclude_every_subscription_plan(catalogue):
    plans = [m for m in catalogue if m.pricing_model == "subscription"]
    assert plans, "catalogue should contain IDE subscription plans"
    for cid in ("aider", "cline", "continue", "zed"):
        eligible = eligible_models(resolve_client(cid), catalogue)
        assert eligible
        assert all(m.pricing_model == "per_token" for m in eligible)
    # and they keep per-token models from every vendor
    aider = eligible_models(resolve_client("aider"), catalogue)
    assert {"Anthropic", "OpenAI", "Google"} <= {m.provider for m in aider}


def test_single_vendor_clients_never_return_other_tools_plans(catalogue):
    assert {m.provider for m in eligible_models(resolve_client("gemini cli"), catalogue)} == {"Google"}
    assert {m.provider for m in eligible_models(resolve_client("codex cli"), catalogue)} == {"OpenAI"}
    assert {m.provider for m in eligible_models(resolve_client("chatgpt desktop"), catalogue)} == {"OpenAI"}
    assert {m.provider for m in eligible_models(resolve_client("claude desktop"), catalogue)} == {"Anthropic"}


def test_cursor_and_copilot_never_see_each_others_plans(catalogue):
    cursor = eligible_models(resolve_client("cursor"), catalogue)
    copilot = eligible_models(resolve_client("github copilot"), catalogue)
    assert not any(m.provider == "GitHub Copilot" for m in cursor)
    assert not any(m.provider == "Cursor" for m in copilot)
    assert any(m.provider == "Cursor" for m in cursor)
    assert any(m.provider == "GitHub Copilot" for m in copilot)


def test_claude_code_keeps_its_own_plans_but_claude_desktop_does_not(catalogue):
    plans = [m for m in catalogue if m.provider == "Anthropic" and m.pricing_model == "subscription"]
    assert plans
    code = resolve_client("claude_code")
    desktop = resolve_client("claude_desktop")
    assert all(is_runnable(code, m) for m in plans)
    assert not any(is_runnable(desktop, m) for m in plans)


def test_is_runnable_matches_provider_case_insensitively_and_explicit_models():
    from dataclasses import replace
    code = resolve_client("claude_code")
    assert is_runnable(code, _pm("x", "anthropic"))
    assert not is_runnable(code, _pm("claude-look-alike", "Together AI"))
    with_extra = replace(code, models=("claude-look-alike",))
    assert is_runnable(with_extra, _pm("Claude-Look-Alike", "Together AI"))


def test_restriction_label():
    assert restriction_label(resolve_client("claude_code")) == "Anthropic models"
    assert "no subscription plans" in restriction_label(resolve_client("aider"))
