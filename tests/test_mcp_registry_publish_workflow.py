"""Guards for server.json and .github/workflows/mcp-registry-publish.yml.

Publishing to the official MCP registry (registry.modelcontextprotocol.io)
has a few rules that fail silently — a rejected publish deep in a CI log,
long after the PR that broke it merged. These tests pin them:

- The registry caps `description` at 100 characters; longer ones fail
  validation at publish time, not at commit time.
- `server.json`'s `name` must start with `io.github.skakumanu/` to match the
  GitHub OIDC auth this workflow uses (`mcp-publisher login github-oidc`) —
  a mismatched namespace is rejected with "you do not have permission".
- `id-token: write` must be present or GitHub issues no OIDC token at all.
- This is a remote-only entry (`remotes`, no `packages`): server.json must
  stay schema-valid as such, and must not silently grow a `packages` array
  without also adding the package-ownership-verification marker that
  requires (npm's `mcpName` / PyPI-NuGet's `mcp-name` README comment) —
  that's a distinct, larger change this test deliberately does not green-light.
"""
import json
import re
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
SERVER_JSON = project_root / "server.json"
WORKFLOW = project_root / ".github" / "workflows" / "mcp-registry-publish.yml"
EXPECTED_NAME = "io.github.skakumanu/llm-pricing-mcp-server"


@pytest.fixture(scope="module")
def server_json():
    assert SERVER_JSON.exists()
    return json.loads(SERVER_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workflow_text():
    assert WORKFLOW.exists(), (
        f"{WORKFLOW.name} is missing — server.json has no publishing path without it"
    )
    return WORKFLOW.read_text(encoding="utf-8")


class TestServerJson:
    def test_name_matches_github_oidc_namespace(self, server_json):
        assert server_json["name"] == EXPECTED_NAME, (
            "name must be io.github.skakumanu/... to match the github-oidc auth "
            "this repo's workflow uses, or publishing is rejected with "
            "'you do not have permission to publish this server'"
        )

    def test_description_under_registry_limit(self, server_json):
        desc = server_json["description"]
        assert len(desc) <= 100, (
            f"description is {len(desc)} chars; the registry caps it at 100 "
            "and rejects anything longer at publish time"
        )

    def test_remote_only_has_no_packages(self, server_json):
        assert "remotes" in server_json
        assert "packages" not in server_json, (
            "adding a `packages` entry needs its own ownership-verification "
            "marker (npm's mcpName / PyPI-NuGet's mcp-name README comment) — "
            "this test exists so that requirement isn't skipped by accident"
        )

    def test_remote_url_matches_hosted_mcp_endpoint(self, server_json):
        urls = {r["url"] for r in server_json["remotes"]}
        assert "https://llm-pricing-api.fly.dev/mcp" in urls

    def test_repository_points_at_this_repo(self, server_json):
        assert server_json["repository"]["url"] == "https://github.com/skakumanu/llm-pricing-mcp-server"
        assert server_json["repository"]["source"] == "github"

    def test_schema_url_is_versioned(self, server_json):
        pattern = r"^https://static\.modelcontextprotocol\.io/schemas/\d{4}-\d{2}-\d{2}/server\.schema\.json$"
        assert re.match(pattern, server_json["$schema"])


class TestMcpRegistryPublishWorkflow:
    def test_architecture_doc_names_this_file(self):
        arch = (project_root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
        assert WORKFLOW.name in arch

    def test_requests_oidc_token(self, workflow_text):
        assert re.search(r"id-token:\s*write", workflow_text)

    def test_uses_github_oidc_auth(self, workflow_text):
        assert "login github-oidc" in workflow_text, (
            "this repo has no MCP_GITHUB_TOKEN/MCP_PRIVATE_KEY secret configured — "
            "PAT or DNS auth would fail with no credential to authenticate against"
        )

    def test_watches_server_json_for_changes(self, workflow_text):
        assert "server.json" in workflow_text
