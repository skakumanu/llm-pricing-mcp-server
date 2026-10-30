"""Tests for the /conversations static page mount and related API behaviour."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)
# v1.79.2: the API behind the page is operator-only; fake key built at runtime.
OP_KEY = "-".join(["op", "ui", "test", "key"])
HEADERS = {"x-api-key": OP_KEY}


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

    def test_agent_conversations_api_is_operator_only(self):
        """v1.79.2: GET /agent/conversations left the public bypass — no key is 401."""
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = OP_KEY
            store = _mock_store()
            with patch("src.main.get_conversation_store", return_value=store):
                resp = client.get("/agent/conversations")
            assert resp.status_code == 401
            store.list_conversations.assert_not_called()
        finally:
            main_module.settings.mcp_api_key = original


# ---------------------------------------------------------------------------
# API integration (via the /agent/conversations endpoints)
# ---------------------------------------------------------------------------

class TestConversationsApiFromPage:
    @pytest.fixture(autouse=True)
    def _operator_key_configured(self):
        with patch("src.main.settings.mcp_api_key", OP_KEY):
            yield

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


# ---------------------------------------------------------------------------
# v1.79.2 (AC15): the page is an operator-only management page
# ---------------------------------------------------------------------------

PAGE_PATH = project_root / "static" / "conversations" / "index.html"


class TestConversationsPageOperatorGate:
    def _html(self):
        return PAGE_PATH.read_text(encoding="utf-8")

    def test_operator_key_required_state_present(self):
        html = self._html()
        assert 'id="operator-key-required"' in html or "\"operator-key-required\"" in html
        assert "Operator key required" in html
        assert "restricted to the server operator" in html

    def test_key_bar_uses_shared_admin_storage_key(self):
        html = self._html()
        assert 'id="api-key-input"' in html
        assert 'type="password"' in html
        assert "llm_admin_api_key" in html
        assert "Connect" in html and "Forget" in html

    def test_loading_is_gated_on_a_stored_key(self):
        html = self._html()
        start = html.index("async function loadConversations()")
        body = html[start:html.index("// Stats", start)]
        # The no-key branch returns before any fetch.
        guard = body.index("if (!_apiKey)")
        assert guard < body.index("fetch(")
        assert "showOperatorKeyRequired()" in body[guard:body.index("fetch(")]

    def test_auth_failures_render_the_same_state_not_raw_errors(self):
        html = self._html()
        assert "status === 401 || status === 403 || status === 503" in html
        # No raw "HTTP <status>" error text is rendered into the list anymore.
        assert "throw new Error(`HTTP ${res.status}`)" not in html

    def test_initial_markup_has_no_rows_and_no_loading_fetch(self):
        html = self._html()
        assert '<div id="list-container"></div>' in html
        assert 'id="btn-delete-all" style="display:none"' in html

    def test_delete_buttons_do_not_inline_preview_into_onclick(self):
        html = self._html()
        assert "onclick=\"confirmDelete(" not in html
        assert 'data-preview="${escapeHtml(c.preview || "")}"' in html

    def test_page_served_without_key_contains_gate(self):
        resp = client.get("/conversations/")
        assert resp.status_code == 200
        assert "operator-key-required" in resp.text

    def test_admin_banner_names_the_operator_key(self):
        html = (project_root / "static" / "admin" / "index.html").read_text(encoding="utf-8")
        assert "Operator key (MCP_API_KEY)" in html
        # /admin still loads both panels' data with the operator key.
        assert 'fetch("/pricing/alerts", { headers: headers() })' in html
        assert 'fetch("/agent/conversations", { headers: headers() })' in html
