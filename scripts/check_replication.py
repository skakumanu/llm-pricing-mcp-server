#!/usr/bin/env python3
"""Replication completeness guard (v1.86.0).

Every database the application opens in production must be on the Fly volume (set in
fly.toml [env] under /app/data) and listed in litestream.yml. A database added later
through a new ``*_db_path`` setting would otherwise sit unreplicated and unnoticed until
the volume is lost. Shared by tests/test_litestream_config.py and CI.

A setting whose default is ``None`` is opt-in (the store is in-memory unless the operator
sets a path) and is exempt until fly.toml sets it, except the stores in REQUIRED_IN_PRODUCTION
(conversation_db_path, v1.87.0), which fly.toml must set.

    python scripts/check_replication.py [--check]    # exit 1 and print problems if incomplete
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Dict, List

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

FLY_TOML = PROJECT_ROOT / "fly.toml"
LITESTREAM_YML = PROJECT_ROOT / "litestream.yml"
VOLUME = "/app/data"


# Opt-in stores (default None) that production must nevertheless set: conversations are
# persisted per organisation since v1.87.0 (privacy policy retention, export and deletion).
REQUIRED_IN_PRODUCTION = ("conversation_db_path",)


def db_path_fields() -> List[str]:
    """Settings field names ending in ``_db_path`` that default to a real path, plus the opt-in
    stores production is required to set (REQUIRED_IN_PRODUCTION)."""
    from src.config.settings import Settings
    return sorted(
        name for name, field in Settings.model_fields.items()
        if name.endswith("_db_path") and (field.default is not None or name in REQUIRED_IN_PRODUCTION)
    )


def all_db_path_fields() -> List[str]:
    from src.config.settings import Settings
    return sorted(name for name in Settings.model_fields if name.endswith("_db_path"))


def fly_env(path: Path = FLY_TOML) -> Dict[str, str]:
    return dict(tomllib.loads(Path(path).read_text(encoding="utf-8")).get("env", {}))


def litestream_paths(path: Path = LITESTREAM_YML) -> List[str]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [entry["path"] for entry in data.get("dbs", [])]


def check(fields: List[str], env: Dict[str, str], ls_paths: List[str]) -> List[str]:
    """Problems for the given inputs; empty means every database is on the volume and replicated."""
    out: List[str] = []
    for name in fields:
        key = name.upper()
        value = env.get(key)
        if not value:
            out.append(f"{name}: {key} is not set in fly.toml [env] (the database would sit off the volume)")
            continue
        if not value.startswith(VOLUME + "/"):
            out.append(f"{name}: {key}={value} is not under {VOLUME}")
        if value not in ls_paths:
            out.append(f"{name}: {value} is not listed in litestream.yml")
    # Opt-in settings that fly.toml does set must be covered as well.
    for name in all_db_path_fields():
        if name not in fields and env.get(name.upper()) and env[name.upper()] not in ls_paths:
            out.append(f"{name}: {env[name.upper()]} is set in fly.toml but not listed in litestream.yml")
    return out


def main(argv: List[str]) -> int:
    found = check(db_path_fields(), fly_env(), litestream_paths())
    for p in found:
        print(p)
    print("replication complete" if not found else f"{len(found)} problem(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
