"""Pins .github/workflows/compliance-audit.yml and scripts/compliance_repo_checks.py (v1.88.0, AC14).

Like the price-audit report-run job: read-only contents, no PAT, jq-built payload with picked fields
only, exit 0 without the intake key, and the event type is the literal orchestrator_workflows routes.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.orchestrator_workflows import COMPLIANCE_AUDIT_EVENT, route  # noqa: E402

WORKFLOW = project_root / ".github" / "workflows" / "compliance-audit.yml"


@pytest.fixture(scope="module")
def text():
    assert WORKFLOW.exists()
    return WORKFLOW.read_text(encoding="utf-8")


def _scripts(t):
    return "\n".join(m.group(1) for m in re.finditer(r"run: \|\n((?:[ \t]+.*\n?)+)", t))


class TestWorkflowText:
    def test_least_privilege_and_no_pat(self, text):
        assert not re.search(r"^\s*contents:\s*write", text, re.MULTILINE)
        assert not re.search(r"^\s*(pull-requests|issues|actions|checks):\s*write", text, re.MULTILINE)
        assert not re.search(r"secrets\.[A-Z_]*(PAT|PERSONAL)", text)
        assert set(re.findall(r"secrets\.([A-Z_]+)", text)) == {"ORCHESTRATOR_INTAKE_KEY"}

    def test_exits_zero_with_a_summary_line_without_the_key(self, text):
        assert 'if [ -z "$ORCHESTRATOR_INTAKE_KEY" ]' in text
        assert re.search(r'-z "\$ORCHESTRATOR_INTAKE_KEY" \]; then\n(?:.*\n){1,3}?\s+exit 0', text)
        assert "GITHUB_STEP_SUMMARY" in text

    def test_event_type_is_the_literal_the_service_routes(self, text):
        assert COMPLIANCE_AUDIT_EVENT == "compliance-audit.requested"
        assert f'event_type: "{COMPLIANCE_AUDIT_EVENT}"' in text
        assert route("schedule", COMPLIANCE_AUDIT_EVENT, {}) == ("compliance_audit", "routed")

    def test_dedup_key_is_one_per_utc_day_and_valid(self, text):
        from src.services.orchestrator_workflows import DEDUP_KEY_RE
        assert 'dedup_key: ("schedule:compliance-audit:" + $day)' in text
        assert "date -u +%F" in text
        assert DEDUP_KEY_RE.match("schedule:compliance-audit:2026-11-02")

    def test_posts_only_picked_fields(self, text):
        assert "--data-binary @event.json" in text
        assert "data: {repo_checks: $checks, repo_version: $version, sha: $sha}" in text
        assert "actor_is_owner: false" in text and 'trigger_class: "schedule"' in text
        assert "/orchestrator/events" in text and "x-api-key: $ORCHESTRATOR_INTAKE_KEY" in text

    def test_scripts_never_interpolate_expressions(self, text):
        assert "${{" not in _scripts(text)

    def test_architecture_and_housekeeping_name_the_file(self):
        for doc in ("ARCHITECTURE.md", "HOUSEKEEPING.md"):
            assert WORKFLOW.name in (project_root / "docs" / doc).read_text(encoding="utf-8"), doc


class TestWorkflowStructure:
    def test_structure(self, text):
        yaml = pytest.importorskip("yaml")
        wf = yaml.safe_load(text)
        triggers = wf.get("on", wf.get(True))
        assert triggers["schedule"] == [{"cron": "0 7 * * 1"}] and "workflow_dispatch" in triggers
        assert "push" not in triggers and "pull_request" not in triggers and "pull_request_target" not in triggers
        assert wf["permissions"] == {"contents": "read"}
        jobs = wf["jobs"]
        assert set(jobs) == {"repo-checks", "report-run"}
        assert jobs["report-run"]["needs"] == ["repo-checks"] and jobs["report-run"]["if"] == "always()"
        for job in jobs.values():
            assert job["permissions"] == {"contents": "read"} and job["runs-on"] == "ubuntu-latest"

    def test_runs_after_the_price_audit(self, text):
        yaml = pytest.importorskip("yaml")
        price = yaml.safe_load((project_root / ".github" / "workflows" / "price-audit.yml").read_text(encoding="utf-8"))
        cron = lambda wf: (wf.get("on", wf.get(True)))["schedule"][0]["cron"].split()  # noqa: E731
        ours, theirs = cron(yaml.safe_load(text)), cron(price)
        assert ours[4] == theirs[4] and int(ours[1]) > int(theirs[1])  # same weekday, later hour


class TestRepoChecksScript:
    def test_json_output_is_bounded_and_matches_the_service_ids(self):
        from src.services.audit_checks import CHECK_IDS
        proc = subprocess.run([sys.executable, "scripts/compliance_repo_checks.py", "--json"], cwd=project_root,
                              capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert set(payload) == {"repo_checks", "repo_version"}
        assert set(payload["repo_checks"]) <= set(CHECK_IDS) and len(payload["repo_checks"]) == 6
        assert set(payload["repo_checks"].values()) <= {"pass", "fail"}
        import src
        assert payload["repo_version"] == src.__version__

    def test_individual_checks(self, tmp_path, monkeypatch):
        sys.path.insert(0, str(project_root / "scripts"))
        import compliance_repo_checks as crc
        assert crc.check_dependabot() and crc.check_codeql() and crc.check_forbidden_tracked_files()
        monkeypatch.setattr(crc, "PROJECT_ROOT", tmp_path)
        assert crc.check_dependabot() is False and crc.check_codeql() is False
        (tmp_path / ".github").mkdir()
        (tmp_path / ".github" / "dependabot.yml").write_text(
            "version: 2\nupdates:\n  - package-ecosystem: pip\n    target-branch: master\n"
            "  - package-ecosystem: github-actions\n    target-branch: develop\n", encoding="utf-8")
        assert crc.check_dependabot() is False  # wrong target branch

    def test_forbidden_tracked_file_is_detected(self, tmp_path, monkeypatch):
        sys.path.insert(0, str(project_root / "scripts"))
        import compliance_repo_checks as crc
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "test.env").write_text("X=1", encoding="utf-8")
        subprocess.run(["git", "add", "-f", "test.env"], cwd=tmp_path, check=True)
        monkeypatch.setattr(crc, "PROJECT_ROOT", tmp_path)
        assert crc.check_forbidden_tracked_files() is False
