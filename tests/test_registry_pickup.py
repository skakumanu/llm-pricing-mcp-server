"""Tests for scripts/pickup_registry_models.py — the weekly registry pickup (v1.75.0).

A tiny fake registry carrying one provider ("acme") with every shape the
exclusion rules must handle is run through the script's functions against a
stub provider, writing into tmp_path. Nothing here touches the real
data/registry_models/ files or any src/services/*_pricing.py.
"""
import copy
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "scripts"))

import pickup_registry_models as pickup  # noqa: E402
from src.services.base_provider import BasePricingProvider  # noqa: E402
from src.services.price_oracle import PriceOracle, reset_price_oracle  # noqa: E402

SERVICES_DIR = project_root / "src" / "services"


def _rec(inp=1e-06, out=2e-06, **extra):
    d = {"mode": "chat", "litellm_provider": "acme",
         "input_cost_per_token": inp, "output_cost_per_token": out,
         "max_input_tokens": 32000, "supports_function_calling": True}
    d.update(extra)
    return d


# One plain chat key that must be added, one more so "only that entry changed"
# is meaningful, and one of every exclusion FR7 names.
FAKE_REGISTRY = {
    "acme/plain-chat": _rec(),
    "acme/second-chat": _rec(inp=3e-06, out=4e-06, supports_vision=True),
    "acme/deprecated-model": _rec(deprecation_date="2026-01-01"),
    "acme/text-embedding-thing": _rec(mode="embedding", out=0.0),
    "acme/whisper-large": _rec(),                        # chat mode, non-chat name
    "acme/plain-chat/community": _rec(inp=5e-07),        # deeper: strict path prefix
    "acme/eu/plain-chat": _rec(inp=1.1e-06),             # deeper: same tail, more slashes
    "acme/curated-model": _rec(),                        # already in the stub's STATIC_PRICING
    "acme/too-cheap": _rec(inp=1e-10, out=1e-10),        # below the plausibility band
    "acme/no-output-price": {"mode": "chat", "litellm_provider": "acme", "input_cost_per_token": 1e-06},
    "acme/ft:base-model:org::abc": _rec(),               # fine-tune template
    "acme/plain-chat-2024-05-13": _rec(),                # dated snapshot beside undated key
    "acme/plain-chat-latest": _rec(),                    # -latest alias of an existing key
    "acme/acme-4.1b-8b": _rec(),                         # size-bucket pseudo-model
    "acme/acme-up-to-4b": _rec(),                        # size-bucket pseudo-model
    "other/plain-chat": _rec(litellm_provider="other"),  # another host's price: not acme's business
}

EXPECTED_SKIPS = {
    "acme/deprecated-model": "deprecated",
    "acme/text-embedding-thing": "mode is 'embedding'",
    "acme/whisper-large": "non-chat artefact",
    "acme/plain-chat/community": "deeper region/tier variant of acme/plain-chat",
    "acme/eu/plain-chat": "deeper region/tier variant of acme/plain-chat",
    "acme/curated-model": "already in catalogue",
    "acme/too-cheap": "outside plausibility band",
    "acme/no-output-price": "missing input or output price",
    "acme/ft:base-model:org::abc": "fine-tune template",
    "acme/plain-chat-2024-05-13": "dated snapshot of acme/plain-chat",
    "acme/plain-chat-latest": "'-latest' alias of acme/plain-chat",
    "acme/acme-4.1b-8b": "size-bucket",
    "acme/acme-up-to-4b": "size-bucket",
}


class _StubProvider(BasePricingProvider):
    PRICE_AS_OF = "2026-10-01"
    STATIC_PRICING = {"curated-model": {"input": 0.001, "output": 0.002}}

    def __init__(self):
        super().__init__("Acme")

    async def fetch_pricing_data(self):
        return [self.build_metrics(n, i, source="Acme (Static)") for n, i in self.STATIC_PRICING.items()]


def _write_registry(tmp_path: Path, raw: dict, name="registry.json") -> str:
    p = tmp_path / name
    p.write_text(json.dumps(raw), encoding="utf-8")
    return str(p)


def _run(tmp_path: Path, raw: dict, date="2026-10-25", apply=True, stub=None):
    digest = hashlib.md5(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:8]  # nosec B324 - file naming only
    reg = _write_registry(tmp_path, raw, name=f"registry-{digest}.json")
    return pickup.run(registry=reg, providers=["Acme"], out_dir=str(tmp_path / "out"),
                      date=date, apply=apply, services=[stub or _StubProvider()])


def _services_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(SERVICES_DIR.glob("*_pricing.py")):
        h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture(autouse=True)
def _isolate_oracle():
    reset_price_oracle()
    yield
    reset_price_oracle()


# ---------------------------------------------------------------------------
# Additions (AC9) and naming / oracle round-trip (AC4, AC8)
# ---------------------------------------------------------------------------

class TestAdditions:
    def test_adds_exactly_the_plain_chat_models(self, tmp_path):
        report = _run(tmp_path, FAKE_REGISTRY)
        plan = report["providers"][0]
        assert {a["model"] for a in plan["added"]} == {"plain-chat", "second-chat"}

    def test_name_is_key_minus_provider_prefix(self, tmp_path):
        _run(tmp_path, FAKE_REGISTRY)
        doc = json.loads((tmp_path / "out" / "acme.json").read_text())
        assert set(doc["models"]) == {"plain-chat", "second-chat"}
        assert doc["models"]["plain-chat"]["registry_key"] == "acme/plain-chat"
        assert not any(n.startswith("acme/") for n in doc["models"])

    def test_entry_carries_machine_readable_provenance_and_nonzero_prices(self, tmp_path):
        _run(tmp_path, FAKE_REGISTRY)
        entry = json.loads((tmp_path / "out" / "acme.json").read_text())["models"]["second-chat"]
        assert entry["price_source_kind"] == "registry"
        assert entry["registry_key"] == "acme/second-chat"
        assert entry["price_as_of"] == "2026-10-25" and entry["added_on"] == "2026-10-25"
        assert entry["input"] == pytest.approx(0.003) and entry["output"] == pytest.approx(0.004)  # per 1k
        assert entry["context_window"] == 32000
        assert entry["supports_vision"] is True and entry["supports_function_calling"] is True

    def test_oracle_round_trips_each_added_name_to_its_key(self, tmp_path):
        _run(tmp_path, FAKE_REGISTRY)
        oracle = PriceOracle()
        oracle.load_from_dict(FAKE_REGISTRY, "test")
        doc = json.loads((tmp_path / "out" / "acme.json").read_text())
        for name, entry in doc["models"].items():
            hit = oracle.lookup(name, "Acme", require_provider_match=True)
            assert hit is not None and hit.model_key == entry["registry_key"]

    def test_merged_into_the_provider_catalogue_as_registry_sourced(self, tmp_path):
        import asyncio
        stub = _StubProvider()
        _run(tmp_path, FAKE_REGISTRY, stub=stub)
        models, status = asyncio.run(stub.get_pricing_with_status())
        by = {m.model_name: m for m in models}
        assert status.is_available
        assert set(by) == {"curated-model", "plain-chat", "second-chat"}
        assert by["curated-model"].price_source_kind == "provider"
        assert by["plain-chat"].price_source_kind == "registry"
        assert by["plain-chat"].registry_key == "acme/plain-chat"
        assert by["plain-chat"].price_confirmed is True
        assert by["plain-chat"].cost_per_input_token == pytest.approx(1e-06)
        assert by["plain-chat"].source == "LiteLLM price registry (acme/plain-chat)"

    def test_report_only_mode_writes_nothing(self, tmp_path):
        report = _run(tmp_path, FAKE_REGISTRY, apply=False)
        assert report["providers"][0]["added"]
        assert not (tmp_path / "out" / "acme.json").exists()


# ---------------------------------------------------------------------------
# Exclusions (AC10)
# ---------------------------------------------------------------------------

class TestExclusions:
    @pytest.mark.parametrize("key,reason_fragment", sorted(EXPECTED_SKIPS.items()))
    def test_each_exclusion_is_reported_with_its_reason(self, tmp_path, key, reason_fragment):
        report = _run(tmp_path, FAKE_REGISTRY)
        skipped = {s["registry_key"]: s["reason"] for s in report["providers"][0]["skipped"]}
        assert key in skipped, f"{key} was not skipped; skipped={skipped}"
        assert reason_fragment in skipped[key]

    def test_other_providers_records_are_not_considered(self, tmp_path):
        report = _run(tmp_path, FAKE_REGISTRY)
        plan = report["providers"][0]
        touched = {a["registry_key"] for a in plan["added"]} | {s["registry_key"] for s in plan["skipped"]}
        assert "other/plain-chat" not in touched

    def test_nothing_excluded_is_written(self, tmp_path):
        _run(tmp_path, FAKE_REGISTRY)
        doc = json.loads((tmp_path / "out" / "acme.json").read_text())
        written_keys = {e["registry_key"] for e in doc["models"].values()}
        assert not (written_keys & set(EXPECTED_SKIPS))

    def test_org_segment_is_not_mistaken_for_a_non_chat_token(self):
        """'NousResearch' contains 'search'; the token check applies to the model id only."""
        assert pickup._tail("acme/NousResearch/Hermes-3-Llama-3.1-405B") == "Hermes-3-Llama-3.1-405B"
        tail = pickup._tail("acme/NousResearch/Hermes-3-Llama-3.1-405B").lower()
        assert not any(t in tail for t in BasePricingProvider._DISCOVERY_EXCLUDE)

    @pytest.mark.parametrize("key", [
        "p/together-ai-4.1b-8b", "p/together-ai-up-to-4b", "p/together-ai-41.1b-80b",
    ])
    def test_size_bucket_shapes(self, key):
        assert pickup.is_size_bucket(key)

    @pytest.mark.parametrize("key", [
        "p/Qwen/Qwen3-235B-A22B", "p/meta-llama/llama-3.2-11b", "p/baidu/ernie-4.5-21B-a3b",
        "p/Qwen/Qwen3.8-2.4T-A95B", "p/minimaxai/minimax-m1-80k",
    ])
    def test_real_model_ids_are_not_size_buckets(self, key):
        assert not pickup.is_size_bucket(key)

    def test_dated_snapshot_requires_the_undated_base_to_exist(self):
        keys = {"acme/qwen3-235b-a22b"}
        assert pickup.dated_snapshot_of("acme/qwen3-235b-a22b-instruct-2507", keys) is None
        assert pickup.dated_snapshot_of("acme/qwen3-235b-a22b-2507", keys) == "acme/qwen3-235b-a22b"
        assert pickup.dated_snapshot_of("acme/qwen3-235b-a22b-20250701", keys) == "acme/qwen3-235b-a22b"

    def test_chat_latest_alias_resolves_to_unsuffixed_key(self):
        keys = {"acme/foo"}
        assert pickup.latest_alias_of("acme/foo-chat-latest", keys) == "acme/foo"
        assert pickup.latest_alias_of("acme/bar-latest", keys) is None


# ---------------------------------------------------------------------------
# Idempotency, price tracking, ageing (AC9, AC11, AC17)
# ---------------------------------------------------------------------------

class TestIdempotencyAndTracking:
    def test_second_run_adds_nothing_and_leaves_identical_bytes(self, tmp_path):
        _run(tmp_path, FAKE_REGISTRY)
        before = (tmp_path / "out" / "acme.json").read_bytes()
        report = _run(tmp_path, FAKE_REGISTRY)
        plan = report["providers"][0]
        assert plan["added"] == [] and plan["changes"] == []
        assert plan["changed"] is False
        assert (tmp_path / "out" / "acme.json").read_bytes() == before

    def test_price_change_updates_only_that_entry(self, tmp_path):
        _run(tmp_path, FAKE_REGISTRY)
        before = json.loads((tmp_path / "out" / "acme.json").read_text())["models"]
        moved = copy.deepcopy(FAKE_REGISTRY)
        moved["acme/plain-chat"]["input_cost_per_token"] = 2e-06
        report = _run(tmp_path, moved, date="2026-11-01")
        plan = report["providers"][0]
        assert [c["model"] for c in plan["changes"]] == ["plain-chat"]
        assert plan["changes"][0]["old_input_per_1k"] == pytest.approx(0.001)
        assert plan["changes"][0]["new_input_per_1k"] == pytest.approx(0.002)
        after = json.loads((tmp_path / "out" / "acme.json").read_text())["models"]
        assert after["plain-chat"]["input"] == pytest.approx(0.002)
        assert after["plain-chat"]["price_as_of"] == "2026-11-01"
        # the untouched entry keeps its price, is re-dated, and keeps added_on
        assert after["second-chat"]["input"] == before["second-chat"]["input"]
        assert after["second-chat"]["price_as_of"] == "2026-11-01"
        assert after["second-chat"]["added_on"] == "2026-10-25"
        assert "second-chat" in plan["redated"]

    def test_curated_files_are_never_touched(self, tmp_path):
        digest = _services_digest()
        _run(tmp_path, FAKE_REGISTRY)
        moved = copy.deepcopy(FAKE_REGISTRY)
        moved["acme/plain-chat"]["input_cost_per_token"] = 9e-06
        _run(tmp_path, moved, date="2026-11-01")
        assert _services_digest() == digest
        # and the curated entry itself is not in the JSON under any name
        doc = json.loads((tmp_path / "out" / "acme.json").read_text())
        assert "curated-model" not in doc["models"]

    def test_disappeared_record_is_left_untouched_and_ages_stale(self, tmp_path):
        import asyncio
        stub = _StubProvider()
        old = (datetime.now(UTC).date() - timedelta(days=120)).isoformat()
        _run(tmp_path, FAKE_REGISTRY, date=old, stub=stub)
        gone = copy.deepcopy(FAKE_REGISTRY)
        del gone["acme/second-chat"]
        today = datetime.now(UTC).date().isoformat()
        report = _run(tmp_path, gone, date=today, stub=stub)
        plan = report["providers"][0]
        assert [u["model"] for u in plan["unbacked"]] == ["second-chat"]
        doc = json.loads((tmp_path / "out" / "acme.json").read_text())["models"]
        assert doc["second-chat"]["price_as_of"] == old        # not re-dated, not removed
        assert doc["plain-chat"]["price_as_of"] == today       # still backed -> re-dated
        models, _ = asyncio.run(stub.get_pricing_with_status())
        by = {m.model_name: m for m in models}
        assert by["second-chat"].price_is_stale is True
        assert by["plain-chat"].price_is_stale is False

    def test_deprecated_record_is_left_to_age_not_resynced(self, tmp_path):
        _run(tmp_path, FAKE_REGISTRY, date="2026-01-01")
        dep = copy.deepcopy(FAKE_REGISTRY)
        dep["acme/plain-chat"]["deprecation_date"] = "2026-06-01"
        report = _run(tmp_path, dep, date="2026-10-25")
        plan = report["providers"][0]
        assert [u["model"] for u in plan["unbacked"]] == ["plain-chat"]
        assert "deprecated on 2026-06-01" in plan["unbacked"][0]["reason"]
        doc = json.loads((tmp_path / "out" / "acme.json").read_text())["models"]
        assert doc["plain-chat"]["price_as_of"] == "2026-01-01"

    def test_runtime_registry_sourced_price_the_registry_moved_is_withheld_until_resync(self, tmp_path):
        """OWNER DECISION (FR10): between weekly runs, drift >5% on a stored
        registry-sourced entry is withheld from cost math like any other drift."""
        import asyncio
        stub = _StubProvider()
        _run(tmp_path, FAKE_REGISTRY, stub=stub)
        moved = copy.deepcopy(FAKE_REGISTRY)
        moved["acme/plain-chat"]["input_cost_per_token"] = 2e-06  # registry moved 2x since pickup
        live = PriceOracle()
        live.load_from_dict(moved, "live")
        models, _ = asyncio.run(stub.get_pricing_with_status())
        live.demote_drifted(models)
        by = {m.model_name: m for m in models}
        assert by["plain-chat"].price_confirmed is False
        assert by["second-chat"].price_confirmed is True


# ---------------------------------------------------------------------------
# Report body (AC12) and provider selection
# ---------------------------------------------------------------------------

class TestReport:
    def test_markdown_has_every_section(self, tmp_path):
        report = _run(tmp_path, FAKE_REGISTRY)
        md = pickup.markdown(report)
        for header in ("## Added (2)", "## Price changes (0)", "## Re-dated (0)",
                       "## No longer backed (0)", "## Skipped (", "## Guards run"):
            assert header in md, f"missing {header!r}"
        assert "### Acme (2)" in md
        assert "| `plain-chat` | `acme/plain-chat` | 1.0000 | 2.0000 |" in md
        for test_file in pickup.GUARD_TESTS:
            assert test_file in md
        assert "tests/test_registry_pickup.py" in md

    def test_markdown_lists_skips_grouped_by_provider_with_reason(self, tmp_path):
        md = pickup.markdown(_run(tmp_path, FAKE_REGISTRY))
        assert f"### Acme ({len(EXPECTED_SKIPS)})" in md
        assert "| `acme/too-cheap` | outside plausibility band" in md

    def test_markdown_price_change_row(self, tmp_path):
        _run(tmp_path, FAKE_REGISTRY)
        moved = copy.deepcopy(FAKE_REGISTRY)
        moved["acme/plain-chat"]["input_cost_per_token"] = 2e-06
        md = pickup.markdown(_run(tmp_path, moved, date="2026-11-01"))
        assert "## Price changes (1)" in md
        assert "| Acme | `plain-chat` | `acme/plain-chat` | 1.0000 → 2.0000 | 2.0000 → 2.0000 |" in md
        assert "## Re-dated (1)" in md

    def test_unknown_provider_fails_loudly(self, tmp_path):
        reg = _write_registry(tmp_path, FAKE_REGISTRY)
        with pytest.raises(SystemExit):
            pickup.run(registry=reg, providers=["Nope"], out_dir=str(tmp_path / "out"),
                       date="2026-10-25", apply=False, services=[_StubProvider()])

    def test_provider_without_registry_records_is_not_registry_backed(self, tmp_path):
        class Lonely(_StubProvider):
            def __init__(self):
                BasePricingProvider.__init__(self, "Lonely")
        assert pickup.registry_backed_services(FAKE_REGISTRY, services=[Lonely()]) == []
        assert pickup.registry_backed_services(FAKE_REGISTRY, services=[_StubProvider(), Lonely()])[0].provider_name == "Acme"

    def test_resellers_are_excluded_even_when_named(self):
        for name in ("Hugging Face", "NVIDIA NIM", "PromptQL", "Salesforce"):
            assert name in pickup.PICKUP_EXCLUDED_PROVIDERS

    def test_naming_an_excluded_provider_fails_loudly(self):
        """A widened --providers that names a reseller must not exit 0 having picked up nothing."""
        class Reseller(_StubProvider):
            def __init__(self):
                BasePricingProvider.__init__(self, "Hugging Face")
        svcs = [_StubProvider(), Reseller()]
        # not named: silently left out, as before
        assert [s.provider_name for s in pickup.registry_backed_services(FAKE_REGISTRY, services=svcs)] == ["Acme"]
        with pytest.raises(SystemExit, match="PICKUP_EXCLUDED_PROVIDERS"):
            pickup.registry_backed_services(FAKE_REGISTRY, only=["Acme", "Hugging Face"], services=svcs)

    def test_naming_a_provider_without_registry_records_fails_loudly(self):
        class Lonely(_StubProvider):
            def __init__(self):
                BasePricingProvider.__init__(self, "Lonely")
        with pytest.raises(SystemExit, match="no same-provider record"):
            pickup.registry_backed_services(FAKE_REGISTRY, only=["Lonely"], services=[_StubProvider(), Lonely()])

    def test_markdown_neutralises_registry_strings(self, tmp_path):
        """Keys, modes and dates come from a third-party file and land in a PR body
        a maintainer merges; they must render as text, not as table breaks, code-span
        escapes or links."""
        raw = copy.deepcopy(FAKE_REGISTRY)
        raw["acme/evil | [Approve](https://evil.example) `x`"] = _rec(
            mode="chat' | [Approve rate change](https://evil.example) |")
        md = pickup.markdown(_run(tmp_path, raw))
        assert "[Approve](https://evil.example)" not in md
        assert "[Approve rate change](https://evil.example)" not in md
        assert "\\[Approve\\](https://evil.example)" in md
        # a pipe inside a registry string never starts a new table cell
        for line in md.splitlines():
            if "evil.example" in line:
                assert line.count("|") - line.count("\\|") == 3, line
        # a backtick inside a key cannot close the code span
        assert "`acme/evil \\| \\[Approve\\](https://evil.example) \\`x\\``" in md

    def test_workflow_runs_the_pickup_against_the_vendored_snapshot(self):
        """The pickup job's guard tests price the catalogue against the vendored
        snapshot (tests/test_pricing_integrity.py patches _fetch_remote to None),
        so the pickup must read the same registry: run live, every genuinely new
        key would be added and then fail the oracle round-trip guard, and the
        job could only ever succeed when it had nothing to add."""
        text = (project_root / ".github" / "workflows" / "price-audit.yml").read_text(encoding="utf-8")
        step = text.split("pickup:", 1)[1]
        cmd = next(line for line in step.splitlines() if "scripts/pickup_registry_models.py" in line)
        assert "--snapshot-only" in cmd, cmd
        assert "--apply" in cmd and "--markdown" in cmd


class TestCommandLine:
    """The entry point as the workflow invokes it, against a real catalogue provider."""

    def test_cli_applies_and_prints_markdown_for_a_real_provider(self, tmp_path):
        raw = {"databricks/databricks-test-model": {
            "mode": "chat", "litellm_provider": "databricks",
            "input_cost_per_token": 1e-06, "output_cost_per_token": 3e-06, "max_input_tokens": 8000,
        }}
        reg = _write_registry(tmp_path, raw)
        out = tmp_path / "out"
        proc = subprocess.run(
            [sys.executable, str(project_root / "scripts" / "pickup_registry_models.py"),
             "--apply", "--markdown", "--registry", reg, "--out-dir", str(out),
             "--providers", "Databricks", "--date", "2026-10-25"],
            capture_output=True, text=True, cwd=str(project_root), timeout=300,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert "## Added (1)" in proc.stdout and "### Databricks (1)" in proc.stdout
        doc = json.loads((out / "databricks.json").read_text())
        assert doc["provider"] == "Databricks"
        assert doc["models"]["databricks-test-model"]["registry_key"] == "databricks/databricks-test-model"
        # the real data file is not what we wrote to
        assert not (out / "databricks.json").samefile(project_root / "data" / "registry_models" / "databricks.json")

    def test_cli_rejects_unknown_provider(self, tmp_path):
        reg = _write_registry(tmp_path, FAKE_REGISTRY)
        proc = subprocess.run(
            [sys.executable, str(project_root / "scripts" / "pickup_registry_models.py"),
             "--registry", reg, "--out-dir", str(tmp_path / "out"), "--providers", "Nope"],
            capture_output=True, text=True, cwd=str(project_root), timeout=300,
        )
        assert proc.returncode != 0
        assert "unknown provider" in (proc.stderr + proc.stdout)
