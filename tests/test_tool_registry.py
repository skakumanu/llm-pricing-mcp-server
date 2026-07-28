"""Guards against tool-registry drift between code, agent bindings, and docs.

These tests exist because the documented MCP tool count silently fell out of sync
with the registry more than once. If you add or remove a tool, update EXPECTED_TOOLS
and the docs references listed in test_docs_report_correct_tool_count.
"""
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp.tools.tool_manager import ToolManager  # noqa: E402
from agent.tools import build_agent_tools  # noqa: E402


# The authoritative list. Update deliberately when adding/removing a tool.
EXPECTED_TOOLS = {
    "get_all_pricing",
    "estimate_cost",
    "compare_costs",
    "predict_cost",
    "get_performance_metrics",
    "get_use_cases",
    "get_ide_pricing",
    "get_telemetry",
    "get_pricing_history",
    "get_pricing_trends",
    "register_price_alert",
    "list_price_alerts",
    "delete_price_alert",
    "get_pricing_export_url",
    "list_conversations",
    "delete_conversation",
    "ask_agent",
}

# Tools intentionally NOT exposed to the ReAct agent.
#   ask_agent    — would let the agent recurse into itself
#   get_telemetry — server operations, not a pricing question
AGENT_EXCLUDED = {"ask_agent", "get_telemetry"}


@pytest.fixture(scope="module")
def registry():
    return ToolManager()


class TestToolRegistry:
    def test_registry_matches_expected_set(self, registry):
        assert set(registry.tools.keys()) == EXPECTED_TOOLS

    def test_registry_count(self, registry):
        assert len(registry.tools) == 17

    def test_every_tool_has_instance_and_schema(self, registry):
        for name, meta in registry.tools.items():
            assert meta.get("instance") is not None, f"{name} missing instance"
            assert meta.get("description"), f"{name} missing description"
            assert meta.get("input_schema", {}).get("type") == "object", f"{name} bad schema"

    def test_list_tools_matches_registry(self, registry):
        listed = {t["name"] for t in registry.list_tools()}
        assert listed == EXPECTED_TOOLS

    def test_predict_cost_registered(self, registry):
        meta = registry.get_tool("predict_cost")
        assert meta is not None
        assert "prompt" in meta["input_schema"]["required"]


class TestAgentBindings:
    def test_agent_exposes_all_non_excluded_tools(self, registry):
        agent_tools = build_agent_tools(registry, rag_pipeline=None)
        bound = {t.name for t in agent_tools}
        expected = EXPECTED_TOOLS - AGENT_EXCLUDED
        missing = expected - bound
        assert not missing, f"MCP tools not reachable from the ReAct agent: {sorted(missing)}"

    def test_agent_can_reach_predict_cost(self, registry):
        agent_tools = build_agent_tools(registry, rag_pipeline=None)
        assert "predict_cost" in {t.name for t in agent_tools}

    def test_agent_does_not_recurse_into_ask_agent(self, registry):
        agent_tools = build_agent_tools(registry, rag_pipeline=None)
        assert "ask_agent" not in {t.name for t in agent_tools}


class TestDocsStayInSync:
    """Docs quote the tool count in prose; keep those numbers honest.

    static/whats-new/ is deliberately excluded — it is a historical changelog and
    its past entries must keep their original counts.
    """

    DOC_FILES = [
        "README.md",
        "docs/ARCHITECTURE.md",
        "docs/PERPLEXITY_INTEGRATION.md",
        "docs/MCP_QUICK_START.md",
        "static/mcp-setup/index.html",
        "static/api-docs/index.html",
        "static/landing/index.html",
    ]

    # Matches "15 tools", "15 MCP tools", "15 pricing tools" — any count that is
    # talking about the tool total.
    COUNT_RE = re.compile(r"\b(\d{1,3})\s+(?:MCP\s+|pricing\s+)?tools?\b", re.IGNORECASE)

    # Counts that legitimately are not the registry total.
    ALLOWED_OTHER_COUNTS = {15}  # agent binds 15 of 17 (ask_agent + get_telemetry excluded)

    def test_docs_report_correct_tool_count(self, registry):
        total = len(registry.tools)
        problems = []
        for rel in self.DOC_FILES:
            path = project_root / rel
            if not path.exists():
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in self.COUNT_RE.finditer(line):
                    count = int(match.group(1))
                    if count != total and count not in self.ALLOWED_OTHER_COUNTS:
                        problems.append(f"{rel}:{lineno} says '{match.group(0)}' (registry has {total})")
        assert not problems, "Stale tool counts in docs:\n" + "\n".join(problems)

    def test_readme_lists_every_tool_by_name(self, registry):
        readme = (project_root / "README.md").read_text(encoding="utf-8")
        missing = [name for name in registry.tools if f"`{name}`" not in readme]
        assert not missing, f"Tools missing from README tool list: {missing}"
