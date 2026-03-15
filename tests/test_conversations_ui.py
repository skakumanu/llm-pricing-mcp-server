"""Tests for the /conversations static page mount and related API behaviour."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)
HEADERS = {"x-api-key": "test-key"}


def _mock_store(conversations=None, delete_ret=True):
    store = AsyncMock()
    store.list_conversations = AsyncMock(return_value=conversations or [])
    store.delete = AsyncMock(return_value=delete_ret)
    return store


# ---------------------------------------------------------------------------
# Static page
# ---------------------------------------------------------------------------

class TestConversationsPage:
    def test_page_returns_200(self):
        resp = client.get("/conversations/")
        assert resp.status_code == 200

    def test_page_content_type_html(self):
        resp = client.get("/conversations/")
        assert "text/html" in resp.headers["content-type"]

    def test_page_no_auth_required(self):
        resp = client.get("/conversations/", headers={})
        assert resp.status_code == 200

    def test_page_contains_api_call(self):
        resp = client.get("/conversations/")
        assert "/agent/conversations" in resp.text

    def test_page_contains_resume_link(self):
        resp = client.get("/conversations/")
        assert "/chat" in resp.text

    def test_page_contains_delete_button(self):
        resp = client.get("/conversations/")
        assert "Delete" in resp.text

    def test_page_contains_chat_history_heading(self):
        resp = client.get("/conversations/")
        assert "Chat History" in resp.text


class TestConversationsPageSecurityBypass:
    def test_conversations_subpath_bypasses_auth(self):
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            resp = client.get("/conversations/")
            assert resp.status_code == 200
        finally:
            main_module.settings.mcp_api_key = original

    def test_agent_conversations_api_is_public(self):
        """GET /agent/conversations is in the public bypass list — no auth required."""
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            store = _mock_store()
            with patch("src.main.get_conversation_store", return_value=store):
                resp = client.get("/agent/conversations")  # no key needed
            assert resp.status_code == 200
        finally:
            main_module.settings.mcp_api_key = original


# ---------------------------------------------------------------------------
# API integration (via the /agent/conversations endpoints)
# ---------------------------------------------------------------------------

class TestConversationsApiFromPage:
    def test_list_returns_200_with_empty_list(self):
        store = _mock_store([])
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.get("/agent/conversations", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_list_returns_conversations_with_preview(self):
        convs = [
            {"id": "abc", "updated_at": 1700000000.0, "turn_count": 4, "preview": "Hello world"},
        ]
        store = _mock_store(convs)
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.get("/agent/conversations", headers=HEADERS)
        data = resp.json()
        assert data["total"] == 1
        assert data["conversations"][0]["preview"] == "Hello world"

    def test_delete_conversation_returns_204(self):
        store = _mock_store(delete_ret=True)
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.delete("/agent/conversations/abc", headers=HEADERS)
        assert resp.status_code == 204

    def test_delete_nonexistent_returns_404(self):
        store = _mock_store(delete_ret=False)
        with patch("src.main.get_conversation_store", return_value=store):
            resp = client.delete("/agent/conversations/ghost-id", headers=HEADERS)
        assert resp.status_code == 404
