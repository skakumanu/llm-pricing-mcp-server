"""X-Api-Key-Tier only raises a caller's rate limit when they hold the global MCP_API_KEY.

The header is self-declared. Before v1.73.5 the limiter read it for every
non-billing caller, so an anonymous client could send
``X-Api-Key-Tier: enterprise`` and get 600/min instead of the 60/min default —
a 10x escape hatch on exactly the endpoints that cost money to serve.

These tests go through /agent/chat because it is anonymous *and* reaches the
limiter (it sits in ``_anonymous_rate_limited_paths``). The pre-existing
tier tests in test_tiers.py hit /rate-limits/tiers, which is in
``_unauthenticated_paths`` and therefore bypasses the limiter entirely — they
never exercised this code path. Bucket keys are asserted directly: TestClient's
host is "testclient", so an anonymous caller lands in "testclient:" and a
tier-granted caller in "testclient:<tier>".
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
GLOBAL_KEY = "test-secret-key"  # nosec B105 — test fixture, not a credential


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    main_module._rate_limit_store.clear()
    yield
    main_module._rate_limit_store.clear()


@pytest.fixture
def mocked_agent():
    resp = MagicMock(reply="ok", conversation_id="c1", tool_calls=[], sources=[])
    agent = MagicMock()
    agent.chat = AsyncMock(return_value=resp)

    async def _get():
        return agent

    with patch("src.main.get_pricing_agent", _get):
        yield agent


@pytest.fixture
def global_key_configured():
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = GLOBAL_KEY
    try:
        yield
    finally:
        main_module.settings.mcp_api_key = original


def _post(headers=None):
    return client.post("/agent/chat", json={"message": "hello"}, headers=headers or {})


def _bucket_keys():
    return set(main_module._rate_limit_store.keys())


class TestAnonymousCallers:
    def test_tier_header_is_ignored_without_a_key(self, mocked_agent, global_key_configured):
        response = _post({"X-Api-Key-Tier": "enterprise"})
        assert response.status_code == 200
        assert _bucket_keys() == {"testclient:"}

    def test_tier_header_cannot_escape_the_default_limit(self, global_key_configured):
        limit = main_module.settings.rate_limit_per_minute
        main_module._rate_limit_store["testclient:"].extend([time.time()] * limit)
        agent = MagicMock()
        agent.chat = AsyncMock(side_effect=AssertionError("agent ran past the limit"))

        async def _get():
            return agent

        with patch("src.main.get_pricing_agent", _get):
            response = _post({"X-Api-Key-Tier": "enterprise"})
        assert response.status_code == 429
        agent.chat.assert_not_called()

    def test_tier_header_is_ignored_when_auth_is_not_configured(self, mocked_agent):
        assert main_module.settings.mcp_api_key in (None, "")
        response = _post({"X-Api-Key-Tier": "pro"})
        assert response.status_code == 200
        assert _bucket_keys() == {"testclient:"}

    def test_wrong_key_does_not_grant_a_tier(self, mocked_agent, global_key_configured):
        response = _post({"x-api-key": "not-the-key", "X-Api-Key-Tier": "enterprise"})
        assert response.status_code == 200  # /agent/chat itself needs no key
        assert _bucket_keys() == {"testclient:"}


class TestGlobalKeyHolders:
    def test_tier_header_is_honored_with_the_valid_global_key(self, mocked_agent, global_key_configured):
        response = _post({"x-api-key": GLOBAL_KEY, "X-Api-Key-Tier": "enterprise"})
        assert response.status_code == 200
        assert _bucket_keys() == {"testclient:enterprise"}

    def test_unknown_tier_still_lands_in_a_tier_bucket_at_default_limit(self, mocked_agent, global_key_configured):
        # Same semantics as before for key holders: an unknown tier name is
        # its own bucket but falls back to the default limit.
        response = _post({"x-api-key": GLOBAL_KEY, "X-Api-Key-Tier": "platinum"})
        assert response.status_code == 200
        assert _bucket_keys() == {"testclient:platinum"}
