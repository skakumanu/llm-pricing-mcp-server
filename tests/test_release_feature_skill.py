"""The /release-feature skill and CLAUDE.md know about the orchestrator wrap (v1.83.0).

The orchestrator's sdlc_run checkpoint hands the owner an exact command; the skill must
stamp `Orchestrator-Run: orc_...` into the PR footer so the intake workflow can complete
the checkpoint from the pull_request.opened event. Outside the orchestrator the skill is
unchanged, which these tests also pin (the marker is conditional on $ARGUMENTS).
"""
import re
from pathlib import Path

project_root = Path(__file__).parent.parent
SKILL = project_root / ".claude" / "skills" / "release-feature" / "SKILL.md"
CLAUDE_MD = project_root / "CLAUDE.md"
INTAKE = project_root / ".github" / "workflows" / "orchestrator-intake.yml"


def test_skill_mentions_orchestrator_run_marker_and_pattern():
    text = SKILL.read_text(encoding="utf-8")
    assert "Orchestrator-Run: orc_" in text
    assert "orc_[a-z0-9]{12}" in text
    assert "Refs #N" in text
    assert "prFooter" in text


def test_skill_marker_is_conditional_on_arguments():
    text = SKILL.read_text(encoding="utf-8")
    assert "$ARGUMENTS" in text
    assert re.search(r"omit both lines", text)
    # The pipeline still ends at a PR the owner merges.
    assert "does not merge the PR" in text


def test_skill_command_format_matches_the_service_template():
    from src.services.orchestrator_workflows import release_feature_command
    text = SKILL.read_text(encoding="utf-8")
    assert "/release-feature Fix issue #N (orchestrator run orc_xxxxxxxxxxxx)" in text
    assert release_feature_command(7, "orc_abcdefghijkl") == \
        "/release-feature Fix issue #7 (orchestrator run orc_abcdefghijkl)"


def test_intake_workflow_parses_the_same_marker_line():
    text = INTAKE.read_text(encoding="utf-8")
    assert "Orchestrator-Run: orc_[a-z0-9]{12}" in text


def test_claude_md_pipeline_section_names_the_orchestrator():
    text = CLAUDE_MD.read_text(encoding="utf-8")
    start = text.index("## AI-Native SDLC Pipeline")
    section = text[start:]
    assert "Orchestrator core" in section
    assert "orchestrator-intake.yml" in section
    assert "owner checkpoint" in section
    assert "never merges" in section
