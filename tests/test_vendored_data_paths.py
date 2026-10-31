"""v1.84.1: the Fly volume mount at /app/data hides the image's data/ tree.

Production served 273 models at 62.6% confirmed while develop had 624 at 98.1%,
because every registry-sourced model file and the offline registry snapshot were
invisible at runtime. These tests pin the override settings, the Dockerfile copy,
the fly.toml wiring and the startup inventory that makes the condition visible.
"""
import json
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import base_provider as bp  # noqa: E402
from src.services import price_oracle as po  # noqa: E402


class TestSettingsOverrides:
    def test_defaults_are_the_repo_paths(self, monkeypatch):
        from src.config.settings import settings
        monkeypatch.setattr(settings, "price_snapshot_path", None)
        monkeypatch.setattr(settings, "registry_models_dir", None)
        assert po.snapshot_path() == po.SNAPSHOT_PATH
        assert bp.registry_models_dir() == bp.REGISTRY_MODELS_DIR

    def test_overrides_are_honoured(self, monkeypatch, tmp_path):
        from src.config.settings import settings
        monkeypatch.setattr(settings, "price_snapshot_path", str(tmp_path / "snap.json"))
        monkeypatch.setattr(settings, "registry_models_dir", str(tmp_path / "reg"))
        assert po.snapshot_path() == tmp_path / "snap.json"
        assert bp.registry_models_dir() == tmp_path / "reg"
        assert bp.registry_models_path("Anthropic").parent == tmp_path / "reg"

    def test_oracle_reads_the_overridden_snapshot(self, monkeypatch, tmp_path):
        from src.config.settings import settings
        snap = tmp_path / "snap.json"
        snap.write_text(json.dumps({"x/y": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6,
                                            "litellm_provider": "x", "mode": "chat"}}), encoding="utf-8")
        monkeypatch.setattr(settings, "price_snapshot_path", str(snap))
        assert po.PriceOracle()._read_snapshot() == json.loads(snap.read_text())

    def test_provider_honours_override_unless_explicitly_redirected(self, monkeypatch, tmp_path):
        from src.config.settings import settings
        monkeypatch.setattr(settings, "registry_models_dir", str(tmp_path / "reg"))

        class P(bp.BasePricingProvider):
            provider_name = "Anthropic"

            async def fetch_pricing_data(self):  # pragma: no cover - interface only
                return []

        p = P.__new__(P)
        assert p._registry_models_path().parent == tmp_path / "reg"
        p.REGISTRY_MODELS_DIR = tmp_path / "explicit"   # the pickup script / tests redirect this way
        assert p._registry_models_path().parent == tmp_path / "explicit"


class TestInventory:
    def test_repo_inventory_counts_every_registry_model(self):
        inv = bp.registry_models_inventory(bp.REGISTRY_MODELS_DIR)
        files = sorted(bp.REGISTRY_MODELS_DIR.glob("*.json"))
        expected = sum(len(json.loads(f.read_text(encoding="utf-8")).get("models", {})) for f in files)
        assert inv["exists"] and inv["files"] == len(files) and inv["models"] == expected and expected > 0

    def test_missing_dir_reports_zero(self, tmp_path):
        inv = bp.registry_models_inventory(tmp_path / "nope")
        assert inv == {"dir": str(tmp_path / "nope"), "exists": False, "files": 0, "models": 0}


class TestDeploymentWiring:
    def test_dockerfile_keeps_a_copy_of_data_outside_the_mount(self):
        text = (project_root / "Dockerfile").read_text(encoding="utf-8")
        assert re.search(r"^COPY data/ \./vendored/data/\s*$", text, flags=re.M), "Dockerfile must copy data/ to /app/vendored/data"

    def test_fly_toml_points_both_paths_at_the_vendored_copy(self):
        text = (project_root / "fly.toml").read_text(encoding="utf-8")
        assert 'PRICE_SNAPSHOT_PATH = "/app/vendored/data/model_prices_snapshot.json"' in text
        assert 'REGISTRY_MODELS_DIR = "/app/vendored/data/registry_models"' in text

    def test_vendored_paths_match_the_repo_layout(self):
        # The fly.toml paths are /app/vendored/data/<relative repo path>; those relative paths must exist.
        assert (project_root / "data" / "model_prices_snapshot.json").is_file()
        assert (project_root / "data" / "registry_models").is_dir()

    def test_settings_are_documented(self):
        env_example = (project_root / ".env.example").read_text(encoding="utf-8")
        assert "PRICE_SNAPSHOT_PATH" in env_example and "REGISTRY_MODELS_DIR" in env_example
