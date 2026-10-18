"""Guards for .github/workflows/npm-publish.yml, the npm Trusted Publishing workflow.

Two of npm's Trusted Publishing rules fail silently if broken — the publish
step just 403s with an unhelpful message, long after the PR that broke it
merged:

- The workflow's *filename* is part of the trust configuration on npmjs.com.
  Renaming the file breaks publishing until npmjs.com is updated to match.
- npm rejects OIDC tokens minted from `issue_comment` and `pull_request_target`
  runs, and GitHub issues no OIDC token at all without `id-token: write`.

These tests pin those invariants so a well-meaning refactor can't undo them.
The structural check uses PyYAML only when importable (it is not a declared
dependency); the text-level checks always run.
"""
import re
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
WORKFLOW = project_root / ".github" / "workflows" / "npm-publish.yml"
PACKAGE_DIR = "npm/llm-pricing-mcp"


@pytest.fixture(scope="module")
def text():
    assert WORKFLOW.exists(), (
        f"{WORKFLOW.name} is missing — its filename is part of the npmjs.com trust config"
    )
    return WORKFLOW.read_text(encoding="utf-8")


class TestNpmPublishWorkflow:
    def test_architecture_doc_names_this_file(self):
        arch = (project_root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        assert WORKFLOW.name in arch, (
            "docs/ARCHITECTURE.md must name the workflow file the npmjs.com trust config points at"
        )

    def test_requests_oidc_token(self, text):
        assert re.search(r"id-token:\s*write", text), (
            "Trusted Publishing needs `id-token: write`; without it npm falls back to a token that doesn't exist"
        )

    def test_never_uses_triggers_npm_rejects(self, text):
        # Match the name used as a YAML key (a trigger), not merely mentioned in
        # a comment — the workflow's own header explains why these are banned.
        for trigger in ("issue_comment", "pull_request_target"):
            assert not re.search(rf"^\s*{trigger}\s*:", text, re.MULTILINE), (
                f"npm rejects Trusted Publishing tokens from `{trigger}` runs"
            )

    def test_publishes_from_the_package_directory(self, text):
        assert f"working-directory: {PACKAGE_DIR}" in text
        assert "npm publish" in text

    def test_structure(self, text):
        yaml = pytest.importorskip("yaml")
        wf = yaml.safe_load(text)
        # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
        triggers = wf.get("on", wf.get(True))
        assert triggers["push"]["branches"] == ["master"]
        assert "workflow_dispatch" in triggers
        job = wf["jobs"]["publish"]
        assert job["permissions"]["id-token"] == "write"
        assert job["runs-on"] == "ubuntu-latest", "Trusted Publishing only works on GitHub-hosted runners"
