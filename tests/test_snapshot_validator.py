"""Tests for the price-snapshot validator.

This validator is the only verification the weekly snapshot-refresh PR gets:
GitHub does not trigger workflow runs for PRs created with GITHUB_TOKEN, so
that PR arrives with zero checks. The job runs this before opening it. If the
validator is wrong, bad pricing data reaches a PR that looks clean — so it is
worth testing properly rather than trusting it.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

SCRIPT = project_root / "scripts" / "validate_price_snapshot.py"
COMMITTED = project_root / "data" / "model_prices_snapshot.json"


def run(snapshot: Path, baseline: Path = None):
    cmd = [sys.executable, str(SCRIPT), str(snapshot)]
    if baseline:
        cmd += ["--baseline", str(baseline)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


@pytest.fixture(scope="module")
def committed():
    with COMMITTED.open(encoding="utf-8") as fh:
        return json.load(fh)


def write(tmp_path: Path, name: str, data) -> Path:
    p = tmp_path / name
    with p.open("w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return p


class TestAcceptsGoodData:
    def test_committed_snapshot_is_valid(self):
        """The file actually in the repo must pass its own validator."""
        code, out = run(COMMITTED)
        assert code == 0, out

    def test_unchanged_snapshot_against_itself(self):
        code, out = run(COMMITTED, COMMITTED)
        assert code == 0, out
        assert "Added | 0" in out and "Removed | 0" in out

    def test_routine_change_is_accepted(self, tmp_path, committed):
        new = dict(committed)
        key = next(iter(new))
        new[key] = {**new[key], "input_cost_per_token": 9.9e-07}
        new["brand/new-model"] = {
            "input_cost_per_token": 1e-06,
            "output_cost_per_token": 2e-06,
            "mode": "chat",
        }
        code, out = run(write(tmp_path, "new.json", new), COMMITTED)
        assert code == 0, out
        assert "Added | 1" in out


class TestRejectsBadData:
    def test_truncated_fetch(self, tmp_path, committed):
        small = dict(list(committed.items())[:100])
        code, out = run(write(tmp_path, "small.json", small), COMMITTED)
        assert code == 1
        assert "truncated" in out

    def test_mass_removal(self, tmp_path, committed):
        gutted = dict(list(committed.items())[: len(committed) // 2])
        code, out = run(write(tmp_path, "gutted.json", gutted), COMMITTED)
        assert code == 1
        assert "removed" in out.lower()

    def test_mass_reprice(self, tmp_path, committed):
        churned = {
            k: {**v, "input_cost_per_token": (v.get("input_cost_per_token") or 0) * 2 + 1e-09}
            for k, v in committed.items()
        }
        code, out = run(write(tmp_path, "churn.json", churned), COMMITTED)
        assert code == 1
        assert "repriced" in out.lower()

    def test_non_numeric_price(self, tmp_path, committed):
        bad = dict(committed)
        key = next(iter(bad))
        bad[key] = {**bad[key], "input_cost_per_token": "free"}
        code, out = run(write(tmp_path, "bad.json", bad), COMMITTED)
        assert code == 1
        assert "malformed" in out

    def test_negative_price(self, tmp_path, committed):
        neg = dict(committed)
        key = next(iter(neg))
        neg[key] = {**neg[key], "output_cost_per_token": -1}
        code, out = run(write(tmp_path, "neg.json", neg), COMMITTED)
        assert code == 1
        assert "negative" in out

    def test_missing_required_field(self, tmp_path, committed):
        miss = dict(committed)
        key = next(iter(miss))
        miss[key] = {k: v for k, v in miss[key].items() if k != "output_cost_per_token"}
        code, out = run(write(tmp_path, "miss.json", miss), COMMITTED)
        assert code == 1
        assert "malformed" in out

    def test_corrupt_json(self, tmp_path):
        p = tmp_path / "corrupt.json"
        p.write_text("{not json", encoding="utf-8")
        code, out = run(p)
        assert code == 1
        assert "cannot read" in out

    def test_json_array_instead_of_object(self, tmp_path):
        code, out = run(write(tmp_path, "arr.json", [1, 2, 3]))
        assert code == 1
        assert "cannot read" in out

    def test_missing_file(self, tmp_path):
        code, out = run(tmp_path / "nope.json")
        assert code == 1
        assert "cannot read" in out


class TestSummaryOutput:
    def test_reports_repriced_models_with_before_and_after(self, tmp_path, committed):
        new = dict(committed)
        key = next(iter(new))
        new[key] = {**new[key], "input_cost_per_token": 4.2e-06}
        code, out = run(write(tmp_path, "n.json", new), COMMITTED)
        assert code == 0
        assert "Repriced" in out
        assert key in out
        assert "$4.200" in out  # per-1M rendering of 4.2e-06

    def test_no_baseline_skips_the_diff_section(self):
        code, out = run(COMMITTED)
        assert code == 0
        assert "Repriced" not in out


class TestWholesaleProviderRemoval:
    """Oct 2026: upstream dropped every `openrouter/*` entry (486 keys, 15% of the file)
    and the 10% removal guard rejected three refreshes in a row, leaving the vendored
    snapshot — and the pickup job that reads it — frozen. A provider namespace that
    vanishes entirely is an editorial change, discounted from the limit and noted in
    the summary; a protected provider vanishing, or a removal above the hard ceiling,
    is still a bad fetch."""

    # The vanishing namespace is synthesised rather than picked from the committed
    # snapshot: the largest real provider changes with every refresh (openrouter one
    # week, fireworks_ai the next) and may well be a protected one, which turns an
    # "editorial change" fixture into a "bad fetch" fixture overnight.
    SYNTHETIC_PROVIDER = "zz_aggregator"

    @staticmethod
    def _by_provider(committed):
        out = {}
        for k, v in committed.items():
            out.setdefault(v.get("litellm_provider") or "", []).append(k)
        return out

    @classmethod
    def _baseline_with_synthetic_provider(cls, committed, fraction=0.15):
        """committed + a whole non-protected provider namespace worth `fraction`
        of the resulting file, so dropping it exceeds the plain 10% limit."""
        from scripts.validate_price_snapshot import PROTECTED_PROVIDERS
        assert cls.SYNTHETIC_PROVIDER not in PROTECTED_PROVIDERS
        assert not any(v.get("litellm_provider") == cls.SYNTHETIC_PROVIDER for v in committed.values())
        n = int(len(committed) * fraction / (1 - fraction)) + 1
        baseline = dict(committed)
        for i in range(n):
            baseline[f"{cls.SYNTHETIC_PROVIDER}/model-{i}"] = {
                "input_cost_per_token": 1e-06,
                "output_cost_per_token": 2e-06,
                "litellm_provider": cls.SYNTHETIC_PROVIDER,
                "mode": "chat",
            }
        assert n / len(baseline) > 0.10, "fixture must exceed the plain 10% limit to be meaningful"
        return baseline

    def test_a_vanished_provider_namespace_is_discounted_and_noted(self, tmp_path, committed):
        baseline = self._baseline_with_synthetic_provider(committed)
        old = write(tmp_path, "baseline.json", baseline)
        code, out = run(write(tmp_path, "editorial.json", committed), old)
        assert code == 0, out
        assert "removed upstream entirely" in out and f"`{self.SYNTHETIC_PROVIDER}`" in out

    def test_residual_removals_beyond_the_limit_still_reject(self, tmp_path, committed):
        baseline = self._baseline_with_synthetic_provider(committed)
        old = write(tmp_path, "baseline.json", baseline)
        # the whole synthetic namespace, plus 12% scattered across the real providers,
        # each of them only partly removed
        new = {k: v for i, (k, v) in enumerate(committed.items()) if i % 8}
        code, out = run(write(tmp_path, "scatter.json", new), old)
        assert code == 1
        assert "removed" in out.lower()

    def test_a_protected_provider_vanishing_is_a_bad_fetch(self, tmp_path, committed):
        by_prov = self._by_provider(committed)
        assert by_prov.get("openai"), "fixture expects openai keys in the snapshot"
        new = {k: v for k, v in committed.items() if k not in set(by_prov["openai"])}
        code, out = run(write(tmp_path, "no-openai.json", new), COMMITTED)
        assert code == 1
        assert "vanished entirely" in out and "openai" in out

    def test_removing_most_of_the_file_is_rejected_even_as_whole_providers(self, tmp_path, committed):
        by_prov = self._by_provider(committed)
        ranked = sorted(by_prov.items(), key=lambda pk: -len(pk[1]))
        gone, i = set(), 0
        while len(gone) / len(committed) <= 0.40 and i < len(ranked):
            if ranked[i][0] not in ("openai", "anthropic"):
                gone |= set(ranked[i][1])
            i += 1
        new = {k: v for k, v in committed.items() if k not in gone}
        code, out = run(write(tmp_path, "gutted.json", new), COMMITTED)
        assert code == 1
        assert "hard limit" in out
