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
- The entry has a `remotes` entry (hosted /mcp) and an npm `packages` entry
  (`llm-pricing-mcp`, the STDIO bridge). The registry verifies npm ownership
  by fetching the exact version named in server.json from registry.npmjs.org
  and requiring its package.json `mcpName` to equal server.json's `name`. So
  three things must move together or the publish is rejected: the npm
  package's `mcpName`, its `version`, and the `version` server.json points at.
"""
import json
import re
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
SERVER_JSON = project_root / "server.json"
PACKAGE_JSON = project_root / "npm" / "llm-pricing-mcp" / "package.json"
WORKFLOW = project_root / ".github" / "workflows" / "mcp-registry-publish.yml"
EXPECTED_NAME = "io.github.skakumanu/llm-pricing-mcp-server"


@pytest.fixture(scope="module")
def server_json():
    assert SERVER_JSON.exists()
    return json.loads(SERVER_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def package_json():
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def npm_package(server_json):
    pkgs = [p for p in server_json.get("packages", []) if p.get("registryType") == "npm"]
    assert len(pkgs) == 1, "server.json should list exactly one npm package (the STDIO bridge)"
    return pkgs[0]


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

    def test_has_remote_and_npm_package(self, server_json, npm_package):
        assert "remotes" in server_json
        assert npm_package["identifier"] == "llm-pricing-mcp"
        assert npm_package["registryBaseUrl"] == "https://registry.npmjs.org", (
            "the registry only supports the public npm registry for ownership checks"
        )
        assert npm_package["transport"]["type"] == "stdio"

    def test_npm_package_version_matches_package_json(self, npm_package, package_json):
        # The registry fetches exactly this version from npm to verify ownership,
        # so server.json must name a version npm-publish.yml actually publishes.
        assert npm_package["version"] == package_json["version"], (
            f"server.json points at llm-pricing-mcp@{npm_package['version']} but "
            f"npm/llm-pricing-mcp/package.json is {package_json['version']} — bump both together"
        )

    def test_package_json_carries_matching_mcp_name(self, package_json):
        assert package_json.get("mcpName") == EXPECTED_NAME, (
            "npm ownership verification requires package.json.mcpName to equal "
            "server.json.name; without it the registry rejects the publish"
        )

    def test_env_vars_documented_in_package_readme(self, npm_package):
        readme = (project_root / "npm" / "llm-pricing-mcp" / "README.md").read_text(encoding="utf-8")
        for var in npm_package.get("environmentVariables", []):
            assert var["name"] in readme, f"{var['name']} is advertised to the registry but not in the npm README"

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

    def test_waits_for_npm_version_before_publishing(self, workflow_text):
        # A master push that bumps package.json and server.json together starts
        # npm-publish.yml and this workflow concurrently; the registry's ownership
        # check 404s until npm has processed the new version.
        assert "Wait for npm packages" in workflow_text
        assert workflow_text.index("Wait for npm packages") < workflow_text.index("mcp-publisher publish")
