"""Guards for .github/workflows/npm-bootstrap-publish.yml, the one-time token publish.

npm's Trusted Publisher setting can only be attached to a package that already
exists on the registry, so the first publish of npm/llm-pricing-mcp cannot use
OIDC. The bootstrap workflow does that first publish with a short-lived
Granular Access Token held in the NPM_TOKEN repository secret. It is meant to
be deleted afterwards; while it exists these tests pin the properties that keep
it from being a standing credential path:

- it runs only on manual ``workflow_dispatch``, never on push/schedule/PR;
- it refuses to run off ``master``;
- the token is read from ``secrets.NPM_TOKEN`` and never appears inline;
- it shares the ``npm-publish`` concurrency group with the OIDC workflow so the
  two can never publish at the same time;
- the triggers npm rejects for OIDC are absent (it requests ``id-token`` for
  provenance, so the same rule applies).
"""
import re
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
WORKFLOW = project_root / ".github" / "workflows" / "npm-bootstrap-publish.yml"
PACKAGE_DIR = "npm/llm-pricing-mcp"


@pytest.fixture(scope="module")
def text():
    if not WORKFLOW.exists():
        pytest.skip(f"{WORKFLOW.name} has been removed (bootstrap complete)")
    return WORKFLOW.read_text(encoding="utf-8")


class TestNpmBootstrapPublishWorkflow:
    def test_marked_temporary(self, text):
        assert "TEMPORARY" in text, "the bootstrap workflow must say it is meant to be deleted"

    def test_token_comes_from_secrets_only(self, text):
        assert "secrets.NPM_TOKEN" in text
        assert not re.search(r"npm_[A-Za-z0-9]{20,}", text), "an npm token is inlined in the workflow"
        assert "_authToken" not in text, "write the token via NODE_AUTH_TOKEN, not an .npmrc line"

    def test_never_uses_triggers_npm_rejects(self, text):
        for trigger in ("issue_comment", "pull_request_target"):
            assert not re.search(rf"^\s*{trigger}\s*:", text, re.MULTILINE)

    def test_publishes_from_the_package_directory(self, text):
        assert f"working-directory: {PACKAGE_DIR}" in text
        assert "npm publish" in text

    def test_structure(self, text):
        yaml = pytest.importorskip("yaml")
        wf = yaml.safe_load(text)
        triggers = wf.get("on", wf.get(True))
        assert set(triggers) == {"workflow_dispatch"}, "bootstrap publish must be manual-only"
        assert wf["concurrency"]["group"] == "npm-publish", (
            "must share the OIDC workflow's concurrency group so the two never publish concurrently"
        )
        job = wf["jobs"]["bootstrap-publish"]
        assert job["if"] == "github.ref == 'refs/heads/master'"
        assert job["runs-on"] == "ubuntu-latest"
        assert job["permissions"]["id-token"] == "write", "needed for --provenance"
        assert job["permissions"]["contents"] == "read"
