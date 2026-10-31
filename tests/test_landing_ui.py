"""v1.79.2 (AC15) — the landing page no longer advertises operator-only surfaces.

list_price_alerts, delete_price_alert, list_conversations and delete_conversation
became operator-only, and /conversations became an operator management page, so the
public landing page drops their chips and the "Chat history" tile. The total tool
count line and the rest of the chips stay.
"""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)

OPERATOR_ONLY_TOOLS = ["list_price_alerts", "delete_price_alert", "list_conversations", "delete_conversation"]


@pytest.fixture(scope="module")
def landing():
    resp = client.get("/")
    assert resp.status_code == 200
    return resp.text


def test_no_conversations_link(landing):
    assert 'href="/conversations"' not in landing


def test_no_chat_history_tile(landing):
    assert "Chat history" not in landing


@pytest.mark.parametrize("tool", OPERATOR_ONLY_TOOLS)
def test_no_chip_for_operator_only_tool(landing, tool):
    assert f'<span class="mcp-tool-chip">{tool}</span>' not in landing
    assert tool not in landing


def test_public_chips_and_tool_count_remain(landing):
    assert "32 tools available" in landing  # v1.84.0: + support_chat
    for tool in ("get_all_pricing", "estimate_cost", "register_price_alert", "manage_price_alert", "ask_agent"):
        assert f'<span class="mcp-tool-chip">{tool}</span>' in landing


def test_chat_link_remains(landing):
    assert "/chat" in landing
