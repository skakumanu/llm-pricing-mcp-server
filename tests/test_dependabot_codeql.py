"""Dependabot and CodeQL configuration (v1.86.0, AC22)."""
import re
import sys
from pathlib import Path

import yaml

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

DEPENDABOT = yaml.safe_load((project_root / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
CODEQL_TEXT = (project_root / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
CODEQL = yaml.safe_load(CODEQL_TEXT)
CI = yaml.safe_load((project_root / ".github" / "workflows" / "ci-cd.yml").read_text(encoding="utf-8"))


def _on(workflow):
    # PyYAML parses the bare key `on` as boolean True
    return workflow.get("on", workflow.get(True))


class TestDependabot:
    def test_exactly_pip_and_github_actions(self):
        assert DEPENDABOT["version"] == 2
        assert sorted(u["package-ecosystem"] for u in DEPENDABOT["updates"]) == ["github-actions", "pip"]

    def test_weekly_and_minor_patch_grouped(self):
        for update in DEPENDABOT["updates"]:
            assert update["directory"] == "/"
            assert update["schedule"]["interval"] == "weekly"
            groups = update["groups"]
            assert len(groups) == 1
            assert sorted(next(iter(groups.values()))["update-types"]) == ["minor", "patch"]

    def test_no_auto_merge_configured(self):
        text = (project_root / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        assert "auto-merge" not in text.lower()


class TestCodeQL:
    def test_python_on_pull_request_and_weekly_schedule(self):
        triggers = _on(CODEQL)
        assert "pull_request" in triggers
        assert len(triggers["schedule"]) == 1
        cron = triggers["schedule"][0]["cron"].split()
        assert len(cron) == 5 and cron[2] == "*" and cron[3] == "*" and cron[4].isdigit()  # one weekday = weekly
        steps = CODEQL["jobs"]["analyze"]["steps"]
        init = next(s for s in steps if s["uses"].startswith("github/codeql-action/init"))
        assert init["with"]["languages"] == "python"
        assert any(s["uses"].startswith("github/codeql-action/analyze") for s in steps)

    def test_permissions_are_minimal(self):
        assert CODEQL["permissions"] == {"contents": "read", "actions": "read", "security-events": "write"}


class TestDeployGatesUnchanged:
    def test_deploy_needs_exactly_the_five_gates(self):
        needs = CI["jobs"]["deploy_fly"]["needs"]
        assert sorted(needs) == ["lint", "osv_scan", "secret_scan", "security", "test"]

    def test_codeql_is_not_a_deploy_gate(self):
        assert "codeql" not in CI["jobs"]
        assert "codeql" not in str(CI["jobs"]["deploy_fly"]["needs"]).lower()


class TestDocsMentionThem:
    def _doc(self, *parts):
        return project_root.joinpath(*parts).read_text(encoding="utf-8")

    def test_architecture_housekeeping_deployment(self):
        for doc in (self._doc("docs", "ARCHITECTURE.md"), self._doc("docs", "HOUSEKEEPING.md"),
                    self._doc("docs", "DEPLOYMENT.md")):
            assert "codeql" in doc.lower() and "dependabot" in doc.lower()

    def test_push_protection_is_recorded_as_manual(self):
        assert re.search(r"push protection", self._doc("docs", "LAUNCH_CHECKLIST.md"), re.I)
