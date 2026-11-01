#!/usr/bin/env python3
"""Repo-side checks for the weekly compliance audit (v1.88.0).

The production image has no .github/, scripts/ or tests/, so the checks that need them run here, in
the GitHub job of .github/workflows/compliance-audit.yml, and are posted to the orchestrator as a
bounded ``{check_id: "pass"|"fail"}`` dict plus the repository version. The service shows an absent
check as n/a "repo-side check not supplied", never as a pass.

    python scripts/compliance_repo_checks.py --json     # print {"repo_checks": {...}, "repo_version": "x.y.z"}

Read-only: it runs ``git ls-files``, reads files and runs the repository's own check scripts and
version tests. It writes nothing and needs no credentials.
"""
from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - fixed argv lists only, never a shell
import sys
from pathlib import Path
from typing import Callable, Dict

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TRACKED = re.compile(r"\.(db|env|pem|key|p12|pfx|secret)$")


def _run(argv: list) -> bool:
    try:
        proc = subprocess.run(argv, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=300)  # nosec B603
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def check_dependabot() -> bool:
    path = PROJECT_ROOT / ".github" / "dependabot.yml"
    if not path.exists():
        return False
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    updates = data.get("updates") or []
    ecosystems = {u.get("package-ecosystem") for u in updates}
    return {"pip", "github-actions"} <= ecosystems and all(u.get("target-branch") == "develop" for u in updates)


def check_codeql() -> bool:
    path = PROJECT_ROOT / ".github" / "workflows" / "codeql.yml"
    return path.exists() and "python" in path.read_text(encoding="utf-8").lower()


def check_forbidden_tracked_files() -> bool:
    try:
        proc = subprocess.run(["git", "ls-files"], cwd=PROJECT_ROOT, capture_output=True, text=True,  # nosec B603 B607
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    return not any(FORBIDDEN_TRACKED.search(line) for line in proc.stdout.splitlines())


def check_replication() -> bool:
    return _run([sys.executable, "scripts/check_replication.py", "--check"])


def check_launch_checklist() -> bool:
    return _run([sys.executable, "scripts/launch_checklist.py", "--check"])


def check_version_tests() -> bool:
    return _run([sys.executable, "-m", "pytest", "tests/test_version_consistency.py", "-q"])


CHECKS: Dict[str, Callable[[], bool]] = {
    "repo.dependabot": check_dependabot,
    "repo.codeql": check_codeql,
    "repo.forbidden_tracked_files": check_forbidden_tracked_files,
    "repo.replication_check": check_replication,
    "repo.launch_checklist_valid": check_launch_checklist,
    "repo.version_tests": check_version_tests,
}


def repo_version() -> str:
    match = re.search(r'__version__\s*=\s*"([^"]+)"', (PROJECT_ROOT / "src" / "__init__.py").read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def run_all() -> Dict[str, object]:
    results: Dict[str, str] = {}
    for check_id, fn in CHECKS.items():
        try:
            results[check_id] = "pass" if fn() else "fail"
        except Exception:  # a broken check is a failure to report, not a crash of the job
            results[check_id] = "fail"
    return {"repo_checks": results, "repo_version": repo_version()}


def main(argv: list) -> int:
    payload = run_all()
    if "--json" in argv:
        print(json.dumps(payload, sort_keys=True))
    else:
        for check_id, result in payload["repo_checks"].items():  # type: ignore[union-attr]
            print(f"{check_id}: {result}")
        print(f"repo_version: {payload['repo_version']}")
    return 0  # the audit service decides what a failure means; this job only reports


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
