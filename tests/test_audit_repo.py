"""Tests for scripts/audit_repo.py: the static LLM-call-site auditor."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "scripts"))

import audit_repo  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402


def _model(name, provider, cost_in, cost_out, quality=None, price_confirmed=True):
    return PricingMetrics(
        model_name=name,
        provider=provider,
        cost_per_input_token=cost_in,
        cost_per_output_token=cost_out,
        quality_score=quality,
        price_confirmed=price_confirmed,
    )


# ---------------------------------------------------------------------------
# AST extraction
# ---------------------------------------------------------------------------

def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return path


def test_extracts_openai_chat_completion_call(tmp_path):
    _write(tmp_path, "app.py", '''
import openai
client = openai.OpenAI()
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "hello there"}],
)
''')
    sites = audit_repo.extract_call_sites(tmp_path)
    assert len(sites) == 1
    assert sites[0].sdk_hint == "openai"
    assert sites[0].model_literal == "gpt-4o"
    assert sites[0].prompt_text == "hello there"
    assert sites[0].prompt_dynamic is False


def test_extracts_anthropic_messages_call(tmp_path):
    _write(tmp_path, "app.py", '''
resp = client.messages.create(
    model="claude-3-haiku",
    system="You are terse.",
    messages=[{"role": "user", "content": "hi"}],
)
''')
    sites = audit_repo.extract_call_sites(tmp_path)
    assert len(sites) == 1
    assert sites[0].sdk_hint == "anthropic"
    # Both system= and messages= are combined, not just the first-matching kwarg.
    assert "You are terse." in sites[0].prompt_text
    assert "hi" in sites[0].prompt_text


def test_resolves_module_level_string_constant(tmp_path):
    _write(tmp_path, "app.py", '''
SYSTEM_PROMPT = "You are a helpful assistant with detailed instructions."

def run():
    return client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "go"},
        ],
    )
''')
    sites = audit_repo.extract_call_sites(tmp_path)
    assert len(sites) == 1
    assert "helpful assistant" in sites[0].prompt_text
    assert "go" in sites[0].prompt_text


def test_dynamic_model_and_prompt_marked_dynamic(tmp_path):
    _write(tmp_path, "app.py", '''
def run(user_model, user_prompt):
    return client.chat.completions.create(
        model=user_model,
        messages=[{"role": "user", "content": user_prompt}],
    )
''')
    sites = audit_repo.extract_call_sites(tmp_path)
    assert len(sites) == 1
    assert sites[0].model_literal is None
    assert sites[0].prompt_dynamic is True


def test_detects_cache_control_kwarg(tmp_path):
    _write(tmp_path, "app.py", '''
resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "hi"}],
    cache_control={"type": "ephemeral"},
)
''')
    sites = audit_repo.extract_call_sites(tmp_path)
    assert sites[0].cache_kwarg_present is True


def test_unrelated_calls_are_ignored(tmp_path):
    _write(tmp_path, "app.py", '''
import requests
requests.get("https://example.com")
os.path.join("a", "b")
''')
    sites = audit_repo.extract_call_sites(tmp_path)
    assert sites == []


def test_google_generate_content_detected(tmp_path):
    _write(tmp_path, "app.py", '''
resp = model.generate_content("Describe this image")
''')
    sites = audit_repo.extract_call_sites(tmp_path)
    assert len(sites) == 1
    assert sites[0].sdk_hint == "google"


def test_walks_nested_directories_and_skips_excluded(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / ".venv").mkdir()
    call = 'client.chat.completions.create(model="gpt-4o", messages=[{"role":"user","content":"hi"}])'
    _write(tmp_path / "src", "a.py", call)
    _write(tmp_path / ".venv", "b.py", call)
    sites = audit_repo.extract_call_sites(tmp_path)
    assert len(sites) == 1
    assert sites[0].file == str(Path("src") / "a.py")


def test_skips_files_with_syntax_errors(tmp_path):
    _write(tmp_path, "broken.py", "def broken(:\n    pass")
    sites = audit_repo.extract_call_sites(tmp_path)
    assert sites == []


# ---------------------------------------------------------------------------
# analyze_call_site
# ---------------------------------------------------------------------------

FLAGSHIP = _model("gpt-4o", "OpenAI", cost_in=0.0025, cost_out=0.01, quality=95)
CHEAP = _model("gpt-4o-mini", "OpenAI", cost_in=0.00015, cost_out=0.0006, quality=90)
UNCONFIRMED = _model("brand-new-model", "OpenAI", cost_in=0.001, cost_out=0.001, price_confirmed=False)
ALL_PRICING = [FLAGSHIP, CHEAP, UNCONFIRMED]


def _site(model="gpt-4o", prompt="Please summarize this long document for me in detail.", cache=False):
    return audit_repo.CallSite(
        file="app.py", line=1, sdk_hint="openai",
        model_literal=model, prompt_text=prompt, prompt_dynamic=False,
        cache_kwarg_present=cache,
    )


def test_analyze_dynamic_site_returns_none():
    site = audit_repo.CallSite(
        file="app.py", line=1, sdk_hint="openai",
        model_literal=None, prompt_text=None, prompt_dynamic=True, cache_kwarg_present=False,
    )
    assert audit_repo.analyze_call_site(site, ALL_PRICING, calls_per_month=1000) is None


def test_analyze_known_model_computes_cost():
    finding = audit_repo.analyze_call_site(_site(), ALL_PRICING, calls_per_month=1000)
    assert finding is not None
    assert finding.model_in_catalogue is True
    assert finding.cost_per_call_usd > 0
    assert finding.prompt_tokens > 0


def test_analyze_unknown_model_falls_back_to_median():
    finding = audit_repo.analyze_call_site(_site(model="totally-made-up-model"), ALL_PRICING, calls_per_month=1000)
    assert finding is not None
    assert finding.model_in_catalogue is False


def test_recommends_downgrade_when_cheaper_model_available():
    finding = audit_repo.analyze_call_site(_site(model="gpt-4o"), ALL_PRICING, calls_per_month=1000)
    downgrade = next((r for r in finding.recommendations if r["type"] == "model_downgrade"), None)
    assert downgrade is not None
    assert downgrade["suggested_model"] == "gpt-4o-mini"
    assert downgrade["estimated_savings_per_call_usd"] > 0


def test_no_downgrade_when_already_cheapest():
    finding = audit_repo.analyze_call_site(_site(model="gpt-4o-mini"), ALL_PRICING, calls_per_month=1000)
    downgrade = next((r for r in finding.recommendations if r["type"] == "model_downgrade"), None)
    assert downgrade is None


def test_no_downgrade_when_cheaper_model_is_much_lower_quality():
    low_quality_cheap = _model("bargain-model", "OpenAI", cost_in=0.0000001, cost_out=0.0000001, quality=10)
    pricing = [FLAGSHIP, low_quality_cheap]
    finding = audit_repo.analyze_call_site(_site(model="gpt-4o"), pricing, calls_per_month=1000)
    downgrade = next((r for r in finding.recommendations if r["type"] == "model_downgrade"), None)
    assert downgrade is None


def test_never_recommends_unconfirmed_price_model():
    finding = audit_repo.analyze_call_site(_site(model="gpt-4o"), ALL_PRICING, calls_per_month=1000)
    suggested = [r["suggested_model"] for r in finding.recommendations if r["type"] == "model_downgrade"]
    assert "brand-new-model" not in suggested


def test_recommends_caching_when_absent_and_provider_supports_it():
    finding = audit_repo.analyze_call_site(_site(model="gpt-4o", cache=False), ALL_PRICING, calls_per_month=1000)
    cache_rec = next((r for r in finding.recommendations if r["type"] == "enable_prompt_caching"), None)
    assert cache_rec is not None


def test_no_caching_recommendation_when_already_present():
    finding = audit_repo.analyze_call_site(_site(model="gpt-4o", cache=True), ALL_PRICING, calls_per_month=1000)
    cache_rec = next((r for r in finding.recommendations if r["type"] == "enable_prompt_caching"), None)
    assert cache_rec is None


def test_no_caching_recommendation_for_non_caching_provider():
    non_caching_model = _model("some-model", "Groq", cost_in=0.0001, cost_out=0.0001, quality=80)
    finding = audit_repo.analyze_call_site(_site(model="some-model"), [non_caching_model], calls_per_month=1000)
    cache_rec = next((r for r in finding.recommendations if r["type"] == "enable_prompt_caching"), None)
    assert cache_rec is None


def test_classification_downgrade_message_mentions_task_type():
    finding = audit_repo.analyze_call_site(
        _site(model="gpt-4o", prompt="Classify this ticket as billing, technical, or account."),
        ALL_PRICING, calls_per_month=1000,
    )
    downgrade = next((r for r in finding.recommendations if r["type"] == "model_downgrade"), None)
    assert downgrade is not None
    assert "classification" in downgrade["message"]


# ---------------------------------------------------------------------------
# build_report / format_markdown
# ---------------------------------------------------------------------------

def test_build_report_aggregates_totals():
    sites = [_site(model="gpt-4o")]
    findings = [audit_repo.analyze_call_site(sites[0], ALL_PRICING, calls_per_month=1000)]
    report = audit_repo.build_report(Path("/repo"), sites, findings, calls_per_month=1000)
    assert report["call_sites_found"] == 1
    assert report["call_sites_analyzed"] == 1
    assert report["summary"]["total_estimated_monthly_cost_usd"] > 0
    assert report["summary"]["total_potential_savings_monthly_usd"] > 0


def test_build_report_lists_skipped_dynamic_sites():
    dynamic_site = audit_repo.CallSite(
        file="app.py", line=5, sdk_hint="openai",
        model_literal=None, prompt_text=None, prompt_dynamic=True, cache_kwarg_present=False,
    )
    report = audit_repo.build_report(Path("/repo"), [dynamic_site], [], calls_per_month=1000)
    assert report["call_sites_skipped"] == 1
    assert report["skipped"][0]["file"] == "app.py"


def test_format_markdown_contains_key_sections():
    sites = [_site(model="gpt-4o")]
    findings = [audit_repo.analyze_call_site(sites[0], ALL_PRICING, calls_per_month=1000)]
    report = audit_repo.build_report(Path("/repo"), sites, findings, calls_per_month=1000)
    md = audit_repo.format_markdown(report)
    assert "# LLM Usage Audit" in md
    assert "app.py:1" in md
    assert "Findings" in md


def test_format_markdown_empty_report_does_not_crash():
    report = audit_repo.build_report(Path("/repo"), [], [], calls_per_month=1000)
    md = audit_repo.format_markdown(report)
    assert "# LLM Usage Audit" in md


# ---------------------------------------------------------------------------
# run_audit (mocked pricing fetch — no network)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_audit_end_to_end(tmp_path):
    _write(tmp_path, "app.py", '''
client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Write a detailed product description for a new pair of running shoes."}],
)
''')
    mock_aggregator = AsyncMock()
    mock_aggregator.get_all_pricing_async = AsyncMock(return_value=(ALL_PRICING, []))
    with patch("audit_repo.PricingAggregatorService", return_value=mock_aggregator):
        report = await audit_repo.run_audit(tmp_path, calls_per_month=500)

    assert report["call_sites_found"] == 1
    assert report["call_sites_analyzed"] == 1
    assert report["findings"][0]["model"] == "gpt-4o"
