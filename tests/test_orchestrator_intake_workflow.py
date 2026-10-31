"""Pins .github/workflows/orchestrator-intake.yml and the price-audit.yml report-run job (v1.83.0).

The orchestrator's GitHub executor must stay least-privilege: read-only everywhere except
issue comments on the executing job, no PAT, same-repo PR guard, jq-picked fields (never
`${{ github.event.* }}` text in a script), and a clean exit when the intake secret is absent.
PyYAML is used only when importable (not a declared dependency); text checks always run.
"""
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.orchestrator_workflows import GITHUB_BOT_LOGIN  # noqa: E402

INTAKE = project_root / ".github" / "workflows" / "orchestrator-intake.yml"
AUDIT = project_root / ".github" / "workflows" / "price-audit.yml"


@pytest.fixture(scope="module")
def intake():
    assert INTAKE.exists()
    return INTAKE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def audit():
    return AUDIT.read_text(encoding="utf-8")


def _scripts(text):
    """The `run:` script bodies only (block scalars), for interpolation checks."""
    return "\n".join(m.group(1) for m in re.finditer(r"run: \|\n((?:[ \t]+.*\n?)+)", text))


class TestIntakeWorkflowText:
    def test_architecture_names_the_file(self):
        arch = (project_root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        assert INTAKE.name in arch

    def test_never_contents_write_and_no_pat(self, intake):
        assert not re.search(r"^\s*contents:\s*write", intake, re.MULTILINE)
        assert not re.search(r"^\s*pull-requests:\s*write", intake, re.MULTILINE)
        assert re.search(r"secrets\.GITHUB_TOKEN", intake)
        assert not re.search(r"secrets\.[A-Z_]*(PAT|PERSONAL)", intake)
        secrets_used = set(re.findall(r"secrets\.([A-Z_]+)", intake))
        assert secrets_used == {"ORCHESTRATOR_INTAKE_KEY", "GITHUB_TOKEN"}

    def test_secret_absent_exits_zero_with_summary(self, intake):
        assert 'if [ -z "$ORCHESTRATOR_INTAKE_KEY" ]' in intake
        assert "GITHUB_STEP_SUMMARY" in intake
        assert re.search(r'-z "\$ORCHESTRATOR_INTAKE_KEY" \]; then\n(?:.*\n){1,3}?\s+exit 0', intake)

    def test_same_repo_pr_guard(self, intake):
        assert "github.event.pull_request.head.repo.full_name == github.repository" in intake

    def test_scripts_never_interpolate_event_text(self, intake):
        scripts = _scripts(intake)
        assert "${{ github.event." not in scripts
        assert "$GITHUB_EVENT_PATH" in scripts
        assert "jq" in scripts

    def test_posts_only_picked_fields_not_the_payload(self, intake):
        assert "--data-binary @event.json" in intake
        assert "--data-binary @$GITHUB_EVENT_PATH" not in intake
        assert "/orchestrator/events" in intake
        assert "/actions?executor=github" in intake
        assert "/result" in intake

    def test_marker_lookup_filters_by_bot_author(self, intake):
        # The YAML literal and the Python constant are the same login; changing one without the
        # other would silently start creating duplicate comments.
        assert f'.user.login == "{GITHUB_BOT_LOGIN}"' in intake
        assert GITHUB_BOT_LOGIN == "github-actions[bot]"
        assert "contains($m)" in intake
        # update before create: the PATCH branch exists and the create branch is the fallback.
        assert "-X PATCH" in intake and "-X POST" in intake
        assert intake.index("-X PATCH") < intake.index('repos/$REPO/issues/$ISSUE/comments" -F body')

    def test_result_echoes_hash_and_provenance(self, intake):
        assert "artifact_hash: $hash" in intake
        assert "provenance_echo: {run_id: $run, action_id: $action}" in intake

    def test_owner_asserted_from_sender_vs_repository_owner(self, intake):
        assert "actor_is_owner: ($actor == $owner)" in intake
        assert "github.repository_owner" in intake

    def test_pr_marker_parsed_from_body(self, intake):
        assert "Orchestrator-Run: orc_[a-z0-9]{12}" in intake

    def test_run_id_validated_before_execution(self, intake):
        assert "grep -qE '^orc_[a-z0-9]{12}$'" in intake


class TestIntakeWorkflowStructure:
    def test_structure(self, intake):
        yaml = pytest.importorskip("yaml")
        wf = yaml.safe_load(intake)
        triggers = wf.get("on", wf.get(True))
        assert triggers["issues"]["types"] == ["labeled"]
        assert triggers["pull_request"]["types"] == ["opened"]
        assert triggers["pull_request"]["branches"] == ["develop"]
        assert triggers["push"]["branches"] == ["master"]
        assert triggers["workflow_dispatch"]["inputs"]["run_id"]["required"] is True
        assert "issue_comment" not in triggers and "pull_request_target" not in triggers
        assert wf["permissions"] == {"contents": "read"}
        jobs = wf["jobs"]
        assert set(jobs) == {"intake", "execute"}
        assert jobs["intake"]["permissions"] == {"contents": "read", "pull-requests": "read"}
        assert jobs["execute"]["permissions"] == {"contents": "read", "issues": "write"}
        assert jobs["execute"]["needs"] == "intake"
        for job in jobs.values():
            assert job["runs-on"] == "ubuntu-latest"
            assert "write" not in str(job["permissions"].get("contents"))


class TestPriceAuditReportRun:
    def test_report_run_job_text(self, audit):
        assert "report-run:" in audit
        assert "schedule:price-audit:" in audit
        assert "price-audit.completed" in audit
        assert 'if [ -z "$ORCHESTRATOR_INTAKE_KEY" ]' in audit
        assert "date -u +%G-W%V" in audit

    def test_report_run_structure(self, audit):
        yaml = pytest.importorskip("yaml")
        wf = yaml.safe_load(audit)
        jobs = wf["jobs"]
        report = jobs["report-run"]
        others = sorted(j for j in jobs if j != "report-run")
        assert sorted(report["needs"]) == others
        assert report["if"] == "always()"
        assert report["permissions"] == {"contents": "read"}
        # Every other job's result is forwarded.
        text = str(report["steps"])
        for job in others:
            assert f"needs.{job}.result" in text
        # The existing jobs are untouched in shape: still six, still human-merged PRs.
        assert len(others) == 6
