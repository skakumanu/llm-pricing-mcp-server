"""/agent/chat is anonymous but rate-limited — and that must stay true in both directions.

Before v1.73.3, /agent/chat and /agent/chat/stream sat in the security
middleware's *full* bypass list, which skips auth, the per-IP rate limiter, and
the body-size cap all at once. Every call runs a ReAct loop on the operator's
LLM key, so an unauthenticated, unthrottled endpoint was an unbounded cost
exposure — made relevant the moment the server became discoverable (npm
package, MCP registry listing). They now live in `_anonymous_rate_limited_paths`
instead (a tier that did not previously exist — `_unauthenticated_paths` members
are *fully* bypassed, limiter included): no API key needed (the chat UI must keep working for anonymous
visitors), but the limiter and body cap apply.

The 429 tests pre-fill the limiter's bucket rather than looping real requests,
so they never reach the agent and need no LLM mock to be meaningful — they
prove the middleware stops the request *before* the handler, which is the
property that bounds spend.
"""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)

# TestClient reports its host as "testclient"; an anonymous caller with no
# X-Api-Key-Tier header lands in the "<ip>:" bucket (empty tier suffix).
ANON_BUCKET = "testclient:"


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    main_module._rate_limit_store.clear()
    yield
    main_module._rate_limit_store.clear()


def _exhaust_anonymous_bucket():
    limit = main_module.settings.rate_limit_per_minute
    assert limit > 0, "rate limiting is disabled; these tests assume it is on"
    now = time.time()
    main_module._rate_limit_store[ANON_BUCKET].extend([now] * limit)


def _agent_that_must_not_run():
    agent = MagicMock()
    agent.chat = AsyncMock(side_effect=AssertionError("agent was invoked past the rate limit"))
    agent.chat_stream = MagicMock(side_effect=AssertionError("agent was invoked past the rate limit"))

    async def _get():
        return agent

    return patch("src.main.get_pricing_agent", _get), agent


class TestAgentChatIsThrottled:
    def test_agent_chat_returns_429_when_bucket_exhausted(self):
        _exhaust_anonymous_bucket()
        ctx, agent = _agent_that_must_not_run()
        with ctx:
            response = client.post("/agent/chat", json={"message": "hello"})
        assert response.status_code == 429
        agent.chat.assert_not_called()

    def test_agent_chat_stream_returns_429_when_bucket_exhausted(self):
        _exhaust_anonymous_bucket()
        ctx, agent = _agent_that_must_not_run()
        with ctx:
            response = client.post("/agent/chat/stream", json={"message": "hello"})
        assert response.status_code == 429
        agent.chat_stream.assert_not_called()

    def test_each_agent_chat_call_consumes_rate_limit_budget(self):
        resp = MagicMock(reply="ok", conversation_id="c1", tool_calls=[], sources=[])
        agent = MagicMock()
        agent.chat = AsyncMock(return_value=resp)

        async def _get():
            return agent

        with patch("src.main.get_pricing_agent", _get):
            response = client.post("/agent/chat", json={"message": "hello"})
        assert response.status_code == 200
        assert len(main_module._rate_limit_store[ANON_BUCKET]) == 1


class TestAgentChatStaysAnonymous:
    def test_no_api_key_required_even_when_global_key_is_configured(self):
        resp = MagicMock(reply="ok", conversation_id="c1", tool_calls=[], sources=[])
        agent = MagicMock()
        agent.chat = AsyncMock(return_value=resp)

        async def _get():
            return agent

        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "test-secret-key"  # nosec B105 — test fixture, not a credential
            with patch("src.main.get_pricing_agent", _get):
                response = client.post("/agent/chat", json={"message": "hello"})
        finally:
            main_module.settings.mcp_api_key = original
        assert response.status_code == 200


class TestChatUiPageIsUnaffected:
    def test_static_chat_page_still_bypasses_limiter(self):
        # The /chat HTML page costs nothing to serve and must keep loading for
        # a throttled visitor so they can see the UI (and any error it shows).
        _exhaust_anonymous_bucket()
        response = client.get("/chat")
        assert response.status_code != 429
