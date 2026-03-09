"""Tests for GET /agent/conversations and DELETE /agent/conversations/{id}."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.main import app  # noqa: E402

client = TestClient(app)

API_KEY = "test-key"
HEADERS = {"x-api-key": API_KEY}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_store(conversations=None, delete_ret=True):
    """Build a mock conversation store."""
    store = AsyncMock()
    store.list_conversations = AsyncMock(return_value=conversations or [])
    store.delete = AsyncMock(return_value=delete_ret)
    return store


def _sample_conv(conv_id="abc", updated_at=1700000000.0, turn_count=4, preview="Hello?"):
    return {
        "id": conv_id,
        "updated_at": updated_at,
        "turn_count": turn_count,
        "preview": preview,
    }


# ---------------------------------------------------------------------------
# GET /agent/conversations
# ---------------------------------------------------------------------------

class TestListConversations:
    def test_returns_200_with_empty_list(self):
        store = _mock_store(conversations=[])
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.get("/agent/conversations", headers=HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversations"] == []
        assert body["total"] == 0

    def test_returns_all_conversations(self):
        convs = [_sample_conv("id1"), _sample_conv("id2", preview="How much?")]
        store = _mock_store(conversations=convs)
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.get("/agent/conversations", headers=HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["conversations"][0]["id"] == "id1"
        assert body["conversations"][1]["preview"] == "How much?"

    def test_conversation_fields_present(self):
        store = _mock_store(conversations=[_sample_conv()])
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.get("/agent/conversations", headers=HEADERS)
        conv = resp.json()["conversations"][0]
        assert "id" in conv
        assert "updated_at" in conv
        assert "turn_count" in conv
        assert "preview" in conv

    def test_returns_401_without_api_key(self):
        """Endpoint requires auth when MCP_API_KEY is set."""
        with patch("src.main.settings") as mock_settings:
            mock_settings.mcp_api_key = "secret"
            mock_settings.mcp_api_key_header = "x-api-key"
            mock_settings.rate_limit_per_minute = 0
            mock_settings.max_body_bytes = 1_000_000
            resp = client.get("/agent/conversations")  # no key
        assert resp.status_code == 401

    def test_null_preview_allowed(self):
        """Conversations with no user messages have preview=None."""
        store = _mock_store(conversations=[_sample_conv(preview=None)])
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.get("/agent/conversations", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["conversations"][0]["preview"] is None

    def test_turn_count_returned_correctly(self):
        store = _mock_store(conversations=[_sample_conv(turn_count=6)])
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.get("/agent/conversations", headers=HEADERS)
        assert resp.json()["conversations"][0]["turn_count"] == 6


# ---------------------------------------------------------------------------
# DELETE /agent/conversations/{conversation_id}
# ---------------------------------------------------------------------------

class TestDeleteConversation:
    def test_delete_existing_returns_204(self):
        store = _mock_store(delete_ret=True)
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.delete("/agent/conversations/abc-123", headers=HEADERS)
        assert resp.status_code == 204
        assert resp.content == b""

    def test_delete_nonexistent_returns_404(self):
        store = _mock_store(delete_ret=False)
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.delete("/agent/conversations/no-such-id", headers=HEADERS)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_delete_calls_store_with_correct_id(self):
        store = _mock_store(delete_ret=True)
        with patch("src.main.get_conversation_store", return_value=store):
            client.delete("/agent/conversations/my-conv-id", headers=HEADERS)
        store.delete.assert_called_once_with("my-conv-id")

    def test_delete_returns_401_without_api_key(self):
        with patch("src.main.settings") as mock_settings:
            mock_settings.mcp_api_key = "secret"
            mock_settings.mcp_api_key_header = "x-api-key"
            mock_settings.rate_limit_per_minute = 0
            mock_settings.max_body_bytes = 1_000_000
            resp = client.delete("/agent/conversations/abc")
        assert resp.status_code == 401

    def test_delete_uuid_style_id(self):
        store = _mock_store(delete_ret=True)
        conv_id = "550e8400-e29b-41d4-a716-446655440000"
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.delete(f"/agent/conversations/{conv_id}", headers=HEADERS)
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# ConversationStore (in-memory) unit tests
# ---------------------------------------------------------------------------

class TestInMemoryConversationStore:
    @pytest.mark.asyncio
    async def test_list_returns_empty_initially(self):
        from agent.conversation import ConversationStore
        store = ConversationStore()
        result = await store.list_conversations()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_after_get_or_create(self):
        from agent.conversation import ConversationStore
        store = ConversationStore()
        conv_id, history = await store.get_or_create()
        history.add("user", "Hi there!")
        result = await store.list_conversations()
        assert len(result) == 1
        assert result[0]["id"] == conv_id
        assert result[0]["turn_count"] == 1
        assert result[0]["preview"] == "Hi there!"

    @pytest.mark.asyncio
    async def test_delete_existing_returns_true(self):
        from agent.conversation import ConversationStore
        store = ConversationStore()
        conv_id, _ = await store.get_or_create()
        deleted = await store.delete(conv_id)
        assert deleted is True

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self):
        from agent.conversation import ConversationStore
        store = ConversationStore()
        deleted = await store.delete("no-such-id")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_preview_truncated_at_120_chars(self):
        from agent.conversation import ConversationStore
        store = ConversationStore()
        conv_id, history = await store.get_or_create()
        history.add("user", "x" * 200)
        result = await store.list_conversations()
        assert len(result[0]["preview"]) == 121  # 120 chars + "…"
        assert result[0]["preview"].endswith("…")


# ---------------------------------------------------------------------------
# Singleton init / get tests
# ---------------------------------------------------------------------------

class TestConversationStoreSingleton:
    @pytest.mark.asyncio
    async def test_init_creates_in_memory_store_when_no_path(self):
        import agent.conversation as conv_mod
        original = conv_mod._conversation_store
        try:
            conv_mod._conversation_store = None
            await conv_mod.init_conversation_store(db_path=None)
            store = conv_mod.get_conversation_store()
            assert isinstance(store, conv_mod.ConversationStore)
        finally:
            conv_mod._conversation_store = original

    @pytest.mark.asyncio
    async def test_get_raises_before_init(self):
        import agent.conversation as conv_mod
        original = conv_mod._conversation_store
        try:
            conv_mod._conversation_store = None
            with pytest.raises(RuntimeError, match="not been initialized"):
                conv_mod.get_conversation_store()
        finally:
            conv_mod._conversation_store = original
