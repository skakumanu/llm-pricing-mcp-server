"""v1.82.1: strict .env validation, with retired keys tolerated.

v1.81.2 switched Settings to extra="ignore" so a stale retired-provider key in
.env could not crash startup, which also meant a typo in .env (MCP_APIKEY for
MCP_API_KEY) was silently ignored and the server ran without the setting.
Settings is back on extra="forbid"; RETIRED_ENV_KEYS are filtered out of the
.env source before validation.
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.config.settings import RETIRED_ENV_KEYS, Settings  # noqa: E402


def _write(tmp_path, text):
    env_file = tmp_path / ".env"
    env_file.write_text(text, encoding="utf-8")
    return str(env_file)


def test_model_config_forbids_extra_keys():
    assert Settings.model_config["extra"] == "forbid"


def test_typo_in_dotenv_fails_loudly(tmp_path):
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=_write(tmp_path, "MCP_APIKEY=value\n"))
    assert "mcp_apikey" in str(exc.value).lower()


def test_known_keys_in_dotenv_still_load(tmp_path, monkeypatch):
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.delenv("RATE_LIMIT_FREE", raising=False)
    s = Settings(_env_file=_write(tmp_path, "SERVER_PORT=8123\nRATE_LIMIT_FREE=7\n"))
    assert s.server_port == 8123
    assert s.rate_limit_free == 7


@pytest.mark.parametrize("key", sorted(RETIRED_ENV_KEYS))
def test_every_retired_key_is_tolerated_in_any_case(tmp_path, monkeypatch, key):
    monkeypatch.delenv("SERVER_PORT", raising=False)
    text = f"{key.upper()}=one\n{key}=two\nSERVER_PORT=8124\n"
    assert Settings(_env_file=_write(tmp_path, text)).server_port == 8124


def test_retired_keys_are_not_settings_fields():
    assert not RETIRED_ENV_KEYS & set(Settings.model_fields)


def test_unrelated_process_env_vars_are_ignored(monkeypatch):
    monkeypatch.setenv("SOME_UNRELATED_VARIABLE", "1")
    monkeypatch.setenv("MCP_APIKEY", "1")
    Settings(_env_file=None)


def test_env_example_only_uses_known_keys():
    """Copying .env.example to .env must not trip the strict check."""
    import re
    fields = {f.lower() for f in Settings.model_fields}
    keys = []
    for line in (project_root / ".env.example").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", line)
        if m:
            keys.append(m.group(1).lower())
    assert keys, ".env.example should list settings"
    assert [k for k in keys if k not in fields and k not in RETIRED_ENV_KEYS] == []
