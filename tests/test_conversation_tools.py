"""Tests for list_conversations and delete_conversation MCP tools + agent wiring."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp.tools.list_conversations import ListConversationsTool    # noqa: E402
from mcp.tools.delete_conversation import DeleteConversationTool  # noqa: E402
from mcp.tools.tool_manager import ToolManager                    # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_store(conversations=None, delete_ret=True):
    store = AsyncMock()
    store.list_conversations = AsyncMock(return_value=conversations or [])
    store.delete = AsyncMock(return_value=delete_ret)
    return store


_SAMPLE_CONV = {
    "id": "abc-123",
    "updated_at": 1700000000.0,
    "turn_count": 4,
    "preview": "What is the cheapest GPT-4 alternative?",
}


# ---------------------------------------------------------------------------
# ListConversationsTool
# ---------------------------------------------------------------------------

class TestListConversationsTool:
    @pytest.mark.asyncio
    async def test_returns_empty_list(self):
        store = _mock_store(conversations=[])
        with patch("mcp.tools.list_conversations.get_conversation_store", return_value=store):
            result = await ListConversationsTool().execute({})
        assert result["success"] is True
        assert result["conversations"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_returns_conversations(self):
        store = _mock_store(conversations=[_SAMPLE_CONV])
        with patch("mcp.tools.list_conversations.get_conversation_store", return_value=store):
            result = await ListConversationsTool().execute({})
        assert result["success"] is True
        assert result["total"] == 1
        assert result["conversations"][0]["id"] == "abc-123"

    @pytest.mark.asyncio
    async def test_default_limit_20(self):
        convs = [{"id": str(i), "updated_at": 0.0, "turn_count": 2, "preview": "x"} for i in range(30)]
        store = _mock_store(conversations=convs)
        with patch("mcp.tools.list_conversations.get_conversation_store", return_value=store):
            result = await ListConversationsTool().execute({})
        assert result["total"] == 20

    @pytest.mark.asyncio
    async def test_custom_limit(self):
        convs = [{"id": str(i), "updated_at": 0.0, "turn_count": 2, "preview": "x"} for i in range(30)]
        store = _mock_store(conversations=convs)
        with patch("mcp.tools.list_conversations.get_conversation_store", return_value=store):
            result = await ListConversationsTool().execute({"limit": 5})
        assert result["total"] == 5

    @pytest.mark.asyncio
    async def test_limit_clamped_to_max_100(self):
        convs = [{"id": str(i), "updated_at": 0.0, "turn_count": 2, "preview": "x"} for i in range(150)]
        store = _mock_store(conversations=convs)
        with patch("mcp.tools.list_conversations.get_conversation_store", return_value=store):
            result = await ListConversationsTool().execute({"limit": 9999})
        assert result["total"] == 100

    @pytest.mark.asyncio
    async def test_store_not_initialized_returns_error(self):
        with patch("mcp.tools.list_conversations.get_conversation_store",
                   side_effect=RuntimeError("not initialized")):
            result = await ListConversationsTool().execute({})
        assert result["success"] is False
        assert "not initialized" in result["error"]

    @pytest.mark.asyncio
    async def test_store_exception_returns_error(self):
        store = AsyncMock()
        store.list_conversations = AsyncMock(side_effect=Exception("DB error"))
        with patch("mcp.tools.list_conversations.get_conversation_store", return_value=store):
            result = await ListConversationsTool().execute({})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_result_includes_preview(self):
        store = _mock_store(conversations=[_SAMPLE_CONV])
        with patch("mcp.tools.list_conversations.get_conversation_store", return_value=store):
            result = await ListConversationsTool().execute({})
        assert result["conversations"][0]["preview"] == _SAMPLE_CONV["preview"]


# ---------------------------------------------------------------------------
# DeleteConversationTool
# ---------------------------------------------------------------------------

class TestDeleteConversationTool:
    @pytest.mark.asyncio
    async def test_delete_existing_returns_success(self):
        store = _mock_store(delete_ret=True)
        with patch("mcp.tools.delete_conversation.get_conversation_store", return_value=store):
            result = await DeleteConversationTool().execute({"conversation_id": "abc-123"})
        assert result["success"] is True
        assert result["deleted"] is True
        assert result["conversation_id"] == "abc-123"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_not_found(self):
        store = _mock_store(delete_ret=False)
        with patch("mcp.tools.delete_conversation.get_conversation_store", return_value=store):
            result = await DeleteConversationTool().execute({"conversation_id": "ghost-id"})
        assert result["success"] is False
        assert result["deleted"] is False
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_conversation_id_returns_error(self):
        result = await DeleteConversationTool().execute({})
        assert result["success"] is False
        assert "conversation_id" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_conversation_id_returns_error(self):
        result = await DeleteConversationTool().execute({"conversation_id": "  "})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_store_not_initialized_returns_error(self):
        with patch("mcp.tools.delete_conversation.get_conversation_store",
                   side_effect=RuntimeError("not initialized")):
            result = await DeleteConversationTool().execute({"conversation_id": "abc"})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_delete_calls_store_with_correct_id(self):
        store = _mock_store(delete_ret=True)
        with patch("mcp.tools.delete_conversation.get_conversation_store", return_value=store):
            await DeleteConversationTool().execute({"conversation_id": "my-conv-id"})
        store.delete.assert_called_once_with("my-conv-id")

    @pytest.mark.asyncio
    async def test_store_exception_returns_error(self):
        store = AsyncMock()
        store.delete = AsyncMock(side_effect=Exception("DB error"))
        with patch("mcp.tools.delete_conversation.get_conversation_store", return_value=store):
            result = await DeleteConversationTool().execute({"conversation_id": "abc"})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# ToolManager registration
# ---------------------------------------------------------------------------

class TestToolManagerRegistration:
    def test_both_tools_registered(self):
        tm = ToolManager()
        for name in ("list_conversations", "delete_conversation"):
            assert tm.get_tool(name) is not None, f"{name} not registered"

    def test_delete_schema_requires_conversation_id(self):
        tm = ToolManager()
        schema = tm.get_tool("delete_conversation")["input_schema"]
        assert "conversation_id" in schema["required"]

    def test_list_schema_has_limit_property(self):
        tm = ToolManager()
        schema = tm.get_tool("list_conversations")["input_schema"]
        assert "limit" in schema["properties"]

    def test_both_tools_in_list_tools(self):
        tm = ToolManager()
        names = [t["name"] for t in tm.list_tools()]
        for name in ("list_conversations", "delete_conversation"):
            assert name in names


# ---------------------------------------------------------------------------
# Agent tool wiring
# ---------------------------------------------------------------------------

class TestAgentToolWiring:
    def test_both_tools_in_build_agent_tools(self):
        from agent.tools import build_agent_tools
        tm = ToolManager()
        tools = {t.name: t for t in build_agent_tools(tm, MagicMock())}
        for name in ("list_conversations", "delete_conversation"):
            assert name in tools, f"{name} missing from agent tools"

    def test_each_tool_has_valid_llm_schema(self):
        from agent.tools import build_agent_tools
        tm = ToolManager()
        tools = {t.name: t for t in build_agent_tools(tm, MagicMock())}
        for name in ("list_conversations", "delete_conversation"):
            schema = tools[name].to_llm_schema()
            assert schema["name"] == name
            assert "input_schema" in schema
