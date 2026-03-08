"""Tests for the POST /agent/chat endpoint: validation, auth, response shape, and security."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_agent_response(reply="Test reply", conv_id="conv-abc-123"):
    """Return a minimal AgentResponse-like object without importing the agent."""
    resp = MagicMock()
    resp.reply = reply
    resp.conversation_id = conv_id
    resp.tool_calls = []
    resp.sources = []
    return resp


def _patch_agent(agent_response=None, side_effect=None):
    """Context manager: patches get_pricing_agent to return a mock agent."""
    mock_agent = MagicMock()
    if side_effect:
        mock_agent.chat = AsyncMock(side_effect=side_effect)
        mock_agent.run_task = AsyncMock(side_effect=side_effect)
    else:
        mock_agent.chat = AsyncMock(return_value=agent_response or _fake_agent_response())
        mock_agent.run_task = AsyncMock(return_value=agent_response or _fake_agent_response())

    async def _fake_get_pricing_agent():
        return mock_agent

    return patch("src.main.get_pricing_agent", _fake_get_pricing_agent), mock_agent


# ---------------------------------------------------------------------------
# Input validation (422)
# ---------------------------------------------------------------------------

class TestAgentChatValidation:
    def test_empty_message_rejected(self):
        response = client.post("/agent/chat", json={"message": ""})
        assert response.status_code == 422

    def test_missing_message_rejected(self):
        response = client.post("/agent/chat", json={})
        assert response.status_code == 422

    def test_message_too_long_rejected(self):
        response = client.post("/agent/chat", json={"message": "x" * 10_001})
        assert response.status_code == 422

    def test_message_at_max_length_accepted(self):
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post("/agent/chat", json={"message": "x" * 10_000})
        assert response.status_code == 200

    def test_message_one_char_accepted(self):
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post("/agent/chat", json={"message": "?"})
        assert response.status_code == 200

    def test_conversation_id_optional(self):
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post(
                "/agent/chat", json={"message": "hello"}
            )
        assert response.status_code == 200

    def test_autonomous_defaults_to_false(self):
        ctx, mock_agent = _patch_agent()
        with ctx:
            client.post("/agent/chat", json={"message": "hello"})
        mock_agent.chat.assert_called_once()
        mock_agent.run_task.assert_not_called()

    def test_non_json_body_rejected(self):
        response = client.post(
            "/agent/chat",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Agent initialization errors (503)
# ---------------------------------------------------------------------------

class TestAgentInitErrors:
    def test_missing_api_key_returns_503(self):
        with patch(
            "src.main.get_pricing_agent",
            side_effect=ValueError("API key for provider 'anthropic' is not configured"),
        ):
            response = client.post("/agent/chat", json={"message": "hello"})
        assert response.status_code == 503
        assert "API key" in response.json()["detail"]

    def test_graceful_shutdown_returns_503(self):
        with patch(
            "src.main.get_pricing_agent",
            side_effect=RuntimeError("Service is shutting down; agent requests are not accepted."),
        ):
            response = client.post("/agent/chat", json={"message": "hello"})
        assert response.status_code == 503
        assert "shutting down" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Internal errors (500) — must not leak sensitive info
# ---------------------------------------------------------------------------

class TestAgentInternalErrors:
    def test_agent_exception_returns_500(self):
        ctx, _ = _patch_agent(side_effect=RuntimeError("LLM upstream failure"))
        with ctx:
            response = client.post("/agent/chat", json={"message": "hello"})
        assert response.status_code == 500

    def test_500_detail_is_generic_not_internal(self):
        ctx, _ = _patch_agent(side_effect=RuntimeError("LLM upstream failure"))
        with ctx:
            response = client.post("/agent/chat", json={"message": "hello"})
        detail = response.json().get("detail", "")
        assert "LLM upstream failure" not in detail

    def test_500_does_not_expose_api_keys(self):
        ctx, _ = _patch_agent(side_effect=Exception("sk-ant-secret-key-12345"))
        with ctx:
            response = client.post("/agent/chat", json={"message": "hello"})
        assert response.status_code == 500
        detail = response.json().get("detail", "")
        assert "sk-ant" not in detail
        assert "secret-key" not in detail

    def test_500_detail_contains_helpful_message(self):
        ctx, _ = _patch_agent(side_effect=RuntimeError("anything"))
        with ctx:
            response = client.post("/agent/chat", json={"message": "hello"})
        detail = response.json().get("detail", "")
        assert len(detail) > 0  # not empty


# ---------------------------------------------------------------------------
# Successful response structure
# ---------------------------------------------------------------------------

class TestAgentChatResponseStructure:
    def test_response_has_required_fields(self):
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post("/agent/chat", json={"message": "What is the cheapest model?"})
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert "conversation_id" in data
        assert "tool_calls" in data
        assert "sources" in data

    def test_reply_is_string(self):
        ctx, _ = _patch_agent(_fake_agent_response(reply="Claude Haiku is cheapest."))
        with ctx:
            response = client.post("/agent/chat", json={"message": "cheapest?"})
        assert isinstance(response.json()["reply"], str)

    def test_conversation_id_returned(self):
        ctx, _ = _patch_agent(_fake_agent_response(conv_id="session-xyz"))
        with ctx:
            response = client.post("/agent/chat", json={"message": "hello"})
        assert response.json()["conversation_id"] == "session-xyz"

    def test_tool_calls_is_list(self):
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post("/agent/chat", json={"message": "hello"})
        assert isinstance(response.json()["tool_calls"], list)

    def test_sources_is_list(self):
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post("/agent/chat", json={"message": "hello"})
        assert isinstance(response.json()["sources"], list)


# ---------------------------------------------------------------------------
# Conversation routing
# ---------------------------------------------------------------------------

class TestAgentChatRouting:
    def test_conversation_id_forwarded_to_agent(self):
        ctx, mock_agent = _patch_agent()
        with ctx:
            client.post(
                "/agent/chat",
                json={"message": "Follow-up", "conversation_id": "my-session-id"},
            )
        mock_agent.chat.assert_called_once_with("Follow-up", "my-session-id")

    def test_no_conversation_id_passes_none(self):
        ctx, mock_agent = _patch_agent()
        with ctx:
            client.post("/agent/chat", json={"message": "Fresh start"})
        mock_agent.chat.assert_called_once_with("Fresh start", None)

    def test_autonomous_true_calls_run_task(self):
        ctx, mock_agent = _patch_agent()
        with ctx:
            response = client.post(
                "/agent/chat",
                json={"message": "Autonomous task", "autonomous": True},
            )
        assert response.status_code == 200
        mock_agent.run_task.assert_called_once_with("Autonomous task")
        mock_agent.chat.assert_not_called()

    def test_autonomous_false_calls_chat(self):
        ctx, mock_agent = _patch_agent()
        with ctx:
            client.post(
                "/agent/chat",
                json={"message": "Conversational", "autonomous": False},
            )
        mock_agent.chat.assert_called_once()
        mock_agent.run_task.assert_not_called()

    def test_message_content_forwarded_exactly(self):
        ctx, mock_agent = _patch_agent()
        with ctx:
            client.post("/agent/chat", json={"message": "exact message content here"})
        call_args = mock_agent.chat.call_args
        assert call_args[0][0] == "exact message content here"


# ---------------------------------------------------------------------------
# AskAgentTool (MCP)
# ---------------------------------------------------------------------------

class TestAskAgentTool:
    def test_missing_message_returns_error(self):
        from mcp.tools.ask_agent import AskAgentTool
        import asyncio

        tool = AskAgentTool(pricing_agent=MagicMock())
        result = asyncio.run(tool.execute({}))
        assert result["success"] is False
        assert "message" in result["error"]

    def test_message_too_long_returns_error(self):
        from mcp.tools.ask_agent import AskAgentTool
        import asyncio

        tool = AskAgentTool(pricing_agent=MagicMock())
        result = asyncio.run(
            tool.execute({"message": "x" * 10_001})
        )
        assert result["success"] is False
        assert "10000" in result["error"] or "maximum" in result["error"].lower()

    def test_no_agent_returns_error(self):
        from mcp.tools.ask_agent import AskAgentTool
        import asyncio

        tool = AskAgentTool()
        result = asyncio.run(
            tool.execute({"message": "hello"})
        )
        assert result["success"] is False
        assert "not initialized" in result["error"].lower()

    def test_successful_execution(self):
        from mcp.tools.ask_agent import AskAgentTool
        import asyncio

        mock_agent = MagicMock()
        mock_response = _fake_agent_response(reply="Haiku is cheapest", conv_id="c1")
        mock_agent.chat = AsyncMock(return_value=mock_response)

        tool = AskAgentTool(pricing_agent=mock_agent)
        result = asyncio.run(
            tool.execute({"message": "What is cheapest?"})
        )
        assert result["success"] is True
        assert result["reply"] == "Haiku is cheapest"
        assert result["conversation_id"] == "c1"

    def test_autonomous_mode_calls_run_task(self):
        from mcp.tools.ask_agent import AskAgentTool
        import asyncio

        mock_agent = MagicMock()
        mock_agent.run_task = AsyncMock(return_value=_fake_agent_response())
        mock_agent.chat = AsyncMock(return_value=_fake_agent_response())

        tool = AskAgentTool(pricing_agent=mock_agent)
        asyncio.run(
            tool.execute({"message": "task", "autonomous": True})
        )
        mock_agent.run_task.assert_called_once_with("task")
        mock_agent.chat.assert_not_called()

    def test_conversation_id_truncated_at_128(self):
        from mcp.tools.ask_agent import AskAgentTool
        import asyncio

        mock_agent = MagicMock()
        mock_agent.chat = AsyncMock(return_value=_fake_agent_response())

        tool = AskAgentTool(pricing_agent=mock_agent)
        long_id = "a" * 300
        asyncio.run(
            tool.execute({"message": "hi", "conversation_id": long_id})
        )
        call_args = mock_agent.chat.call_args[0]
        passed_id = call_args[1]
        assert len(passed_id) <= 128

    def test_set_agent_binds_late(self):
        from mcp.tools.ask_agent import AskAgentTool
        import asyncio

        tool = AskAgentTool()
        mock_agent = MagicMock()
        mock_agent.chat = AsyncMock(return_value=_fake_agent_response())
        tool.set_agent(mock_agent)

        result = asyncio.run(
            tool.execute({"message": "hello after late bind"})
        )
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Timeout behavior (LLM04)
# ---------------------------------------------------------------------------

class TestAgentTimeout:
    def test_timeout_returns_504(self):
        """asyncio.TimeoutError from the agent call must yield HTTP 504, not 500."""
        import asyncio as _asyncio

        # Must enter ctx so get_pricing_agent() is mocked (otherwise it raises 503).
        ctx, _ = _patch_agent()
        with ctx, patch("src.main.asyncio.wait_for", side_effect=_asyncio.TimeoutError()):
            response = client.post("/agent/chat", json={"message": "slow query"})
        assert response.status_code == 504

    def test_timeout_detail_is_user_friendly(self):
        """504 detail should tell users to retry, not expose internal info."""
        import asyncio as _asyncio

        ctx, _ = _patch_agent()
        with ctx, patch("src.main.asyncio.wait_for", side_effect=_asyncio.TimeoutError()):
            response = client.post("/agent/chat", json={"message": "slow query"})
        detail = response.json().get("detail", "")
        # Must not be empty and must not contain stack traces / internal names
        assert len(detail) > 0
        assert "TimeoutError" not in detail
        assert "asyncio" not in detail


# ---------------------------------------------------------------------------
# OWASP / AI Security: endpoint-level input hardening
# ---------------------------------------------------------------------------

class TestEndpointInputHardening:
    def test_message_with_null_bytes_accepted_at_http_level(self):
        """Null-byte payloads must not crash the server (sanitization happens in agent)."""
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post("/agent/chat", json={"message": "hello\u0000world"})
        # FastAPI / pydantic accepts the message; sanitization is the agent's job
        assert response.status_code == 200

    def test_whitespace_only_message_accepted(self):
        """Whitespace-only message satisfies min_length=1 and reaches the agent."""
        # Pydantic min_length=1 counts characters, not semantic content.
        # A 3-space string passes validation; the agent sanitizes it and processes it.
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post("/agent/chat", json={"message": "   "})
        assert response.status_code == 200

    def test_unicode_homoglyphs_in_message_accepted(self):
        """Full-width Unicode characters are valid input; sanitization NFKC-normalizes them."""
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post("/agent/chat", json={"message": "\uff37\uff48\uff41\uff54"})
        assert response.status_code == 200

    def test_very_long_conversation_id_rejected(self):
        """conversation_id longer than 128 chars must be rejected at the HTTP layer."""
        ctx, _ = _patch_agent()
        with ctx:
            response = client.post(
                "/agent/chat",
                json={"message": "hello", "conversation_id": "a" * 129},
            )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# SSE streaming endpoint /agent/chat/stream
# ---------------------------------------------------------------------------

def _parse_sse(text: str) -> list:
    """Parse SSE response body into a list of event dicts."""
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


async def _fake_chat_stream(*events):
    """Async generator that yields the given event dicts."""
    for event in events:
        yield event


class TestAgentChatStream:
    def test_stream_returns_event_stream_content_type(self):
        mock_agent = MagicMock()
        mock_agent.chat_stream = MagicMock(return_value=_fake_chat_stream(
            {"type": "thinking", "iteration": 1},
            {"type": "answer", "text": "hi", "tool_calls": [], "conversation_id": "c1", "sources": []},
        ))

        async def _fake_get():
            return mock_agent

        with patch("src.main.get_pricing_agent", _fake_get):
            response = client.post("/agent/chat/stream", json={"message": "hello"})

        assert "text/event-stream" in response.headers["content-type"]

    def test_stream_emits_thinking_and_answer_events(self):
        mock_agent = MagicMock()
        mock_agent.chat_stream = MagicMock(return_value=_fake_chat_stream(
            {"type": "thinking", "iteration": 1},
            {"type": "answer", "text": "The answer", "tool_calls": [], "conversation_id": "c1", "sources": []},
        ))

        async def _fake_get():
            return mock_agent

        with patch("src.main.get_pricing_agent", _fake_get):
            response = client.post("/agent/chat/stream", json={"message": "hello"})

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        assert "thinking" in types
        assert "answer" in types
        assert "done" in types
        answer = next(e for e in events if e["type"] == "answer")
        assert answer["text"] == "The answer"

    def test_stream_returns_422_on_invalid_input(self):
        response = client.post("/agent/chat/stream", json={"message": ""})
        assert response.status_code == 422

    def test_stream_returns_401_on_missing_api_key(self):
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "test-secret-key"
            response = client.post(
                "/agent/chat/stream",
                json={"message": "hello"},
                headers={"x-api-key": "wrong-key"},
            )
        finally:
            main_module.settings.mcp_api_key = original
        assert response.status_code == 401

    def test_stream_emits_error_event_on_503(self):
        with patch(
            "src.main.get_pricing_agent",
            side_effect=ValueError("API key for provider 'anthropic' is not configured"),
        ):
            response = client.post("/agent/chat/stream", json={"message": "hello"})

        # The response itself is 200 (SSE), but carries an error event
        assert response.status_code == 200
        events = _parse_sse(response.text)
        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert "API key" in error_events[0]["detail"]
