"""The repo's gitleaks allowlist must keep exempting registry model identifiers.

v1.75.0's first CI run failed its secret_scan gate on
``"registry_key": "deepinfra/tencent/Hy3"`` in data/registry_models/deepinfra.json:
gitleaks' generic-api-key rule fires on the word "key" beside a high-entropy
value. The weekly pickup PR adds hundreds of such lines, so the exemption in
.gitleaks.toml is load-bearing for the whole automatic-pickup loop. This test
fails if the file disappears or the exemption is narrowed away.
"""
import re
import tomllib
from pathlib import Path

project_root = Path(__file__).parent.parent
CONFIG = project_root / ".gitleaks.toml"
SAMPLE_LINE = '   "registry_key": "deepinfra/tencent/Hy3",'
SAMPLE_PATH = "data/registry_models/deepinfra.json"


def _config():
    assert CONFIG.exists(), ".gitleaks.toml is missing — the secret_scan gate will fail on every pickup PR"
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def test_default_rules_are_kept():
    assert _config()["extend"]["useDefault"] is True, "the allowlist must extend, not replace, the default ruleset"


def test_registry_key_lines_are_allowlisted_by_regex():
    regexes = _config()["allowlist"]["regexes"]
    assert any(re.search(rx, SAMPLE_LINE) for rx in regexes), (
        "no allowlist regex matches a registry_key line; gitleaks will flag it as generic-api-key"
    )


def test_generated_registry_files_are_allowlisted_by_path():
    paths = _config()["allowlist"]["paths"]
    assert any(re.search(p, SAMPLE_PATH) for p in paths), (
        "data/registry_models/*.json must be path-allowlisted; every weekly pickup PR adds such lines"
    )


def test_allowlist_does_not_blanket_source_or_config_files():
    paths = _config()["allowlist"]["paths"]
    for sample in ("src/main.py", "src/config/settings.py", ".env", "fly.toml"):
        assert not any(re.search(p, sample) for p in paths), f"allowlist path exempts {sample}"
