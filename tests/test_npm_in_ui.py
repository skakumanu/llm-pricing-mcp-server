"""The npm package is advertised in the UI, and what the UI says matches the package.

v1.76.1 put `npx -y llm-pricing-mcp` on the MCP setup page (an npx tab and a
package card) and on the landing page's MCP section, replacing the
clone-and-run-Python instructions. These tests pin the version, the Node
floor, the env-var names and the registry name shown there to
npm/llm-pricing-mcp (package.json, README, bin), so a republish that bumps
the package cannot leave the pages describing the old one.
"""
import json
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

NPM_DIR = project_root / "npm" / "llm-pricing-mcp"
MCP_PAGE = project_root / "static" / "mcp-setup" / "index.html"
LANDING = project_root / "static" / "landing" / "index.html"


@pytest.fixture(scope="module")
def manifest():
    return json.loads((NPM_DIR / "package.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mcp_page():
    return MCP_PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def landing():
    return LANDING.read_text(encoding="utf-8")


def test_both_pages_show_the_npx_command(manifest, mcp_page, landing):
    cmd = f"npx -y {manifest['name']}"
    assert cmd in mcp_page and cmd in landing


def test_mcp_page_shows_the_published_version(manifest, mcp_page):
    assert f"v{manifest['version']}" in mcp_page, "npm version on the MCP page differs from package.json"
    assert f"v{manifest['version']}" in LANDING.read_text(encoding="utf-8")


def test_mcp_page_states_the_node_floor_from_the_manifest(manifest, mcp_page):
    floor = manifest["engines"]["node"].lstrip(">=").strip()
    assert f"Node {floor}+" in mcp_page, f"MCP page must state the Node floor {floor} from package.json engines"


def test_env_vars_shown_match_the_bridge(mcp_page):
    bridge = (NPM_DIR / "bin" / "llm-pricing-mcp.js").read_text(encoding="utf-8")
    used = set(re.findall(r"process\.env\.(LLM_PRICING_MCP_[A-Z_]+)", bridge))
    assert used == {"LLM_PRICING_MCP_URL", "LLM_PRICING_MCP_TIMEOUT_MS"}
    for name in used:
        assert name in mcp_page, f"{name} is read by the bridge but not documented on the MCP page"


def test_registry_name_and_npm_link_are_present(manifest, mcp_page, landing):
    assert manifest["mcpName"] in mcp_page and manifest["mcpName"] in landing
    link = f"https://www.npmjs.com/package/{manifest['name']}"
    assert link in mcp_page and link in landing


def test_mcp_page_has_an_npx_tab_with_a_copyable_config(mcp_page):
    assert "showTab('npx')" in mcp_page and 'id="tab-npx"' in mcp_page
    assert "copyCode('npx', this)" in mcp_page
    m = re.search(r"npx: '(.*?)',\n", mcp_page)
    assert m, "the copy map must carry an npx entry"
    config = json.loads(m.group(1).encode().decode("unicode_escape"))
    assert config["mcpServers"]["llm-pricing"] == {"command": "npx", "args": ["-y", "llm-pricing-mcp"]}


def test_landing_configs_and_copy_button_use_npx_not_python(landing):
    mcp_section = landing[landing.index('id="mcp"'):]
    assert 'class="str">"npx"' in mcp_section
    assert '"python"' not in mcp_section and "mcp/server.py" not in mcp_section
    m = re.search(r"var _mcpJson = '(.*?)';", landing)
    assert m
    config = json.loads(m.group(1).encode().decode("unicode_escape"))
    assert config["mcpServers"]["llm-pricing"] == {"command": "npx", "args": ["-y", "llm-pricing-mcp"]}
    assert "git clone" not in mcp_section, "the landing no longer tells visitors to clone the repo"
