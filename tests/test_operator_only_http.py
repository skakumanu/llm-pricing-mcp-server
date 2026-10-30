"""v1.79.2 — HTTP list/delete of price alerts and conversations is operator-only.

GET /pricing/alerts and DELETE /pricing/alerts/{id} used to be fully bypassed by the
"/pricing" prefix; GET /agent/conversations sat in _unauthenticated_paths and
DELETE /agent/conversations/{id} accepted any customer key (which public signup hands
out). The security middleware now checks a small method-aware table
(_is_operator_only_request) before the full bypass, and those requests need the
global MCP_API_KEY in x-api-key. Creating stays public: POST /pricing/alerts is
anonymous but now rate-limited and body-capped; /agent/chat still works keyless.

Keys are assembled at runtime so no secret-shaped literal lands in a commit.
"""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402
from src.services.billing_service import CustomerRecord  # noqa: E402
from src.services.pricing_alerts import PricingAlertService  # noqa: E402
from agent.conversation import ConversationStore  # noqa: E402

client = TestClient(app)

OPERATOR_KEY = "-".join(["op", "http", "global", "a11c"])
CUSTOMER_KEY = "-".join(["svc", "http", "cust", "b22d"])
WRONG_KEY = "-".join(["svc", "http", "wrong", "c33e"])
NON_ASCII_KEY = "clé-opérateur".encode("utf-8")  # raw non-ASCII header bytes (Starlette decodes as latin-1)
SENTINEL_URL = "https://" + "-".join(["http", "subscriber", "42"]) + ".example/hook"
SENTINEL_PREVIEW = " ".join(["http", "private", "question", "k7"])
CONV_ID = "-".join(["http", "conv", "6060"])

# TestClient reports its host as "testclient"; anonymous callers use the "<ip>:" bucket.
ANON_BUCKET = "testclient:"


def _customer():
    return CustomerRecord(
        id="cust-http", email="http@example.com", stripe_customer_id=None, stripe_subscription_id=None,
        api_key=CUSTOMER_KEY, tier="free", org_id="org-http", created_at=time.time(), updated_at=time.time(),
    )


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    main_module._rate_limit_store.clear()
    yield
    main_module._rate_limit_store.clear()


@pytest.fixture
def env(tmp_path):
    loop = asyncio.new_event_loop()
    alerts = PricingAlertService(str(tmp_path / "http_alerts.db"))
    loop.run_until_complete(alerts.initialize())
    alert = loop.run_until_complete(alerts.register(url=SENTINEL_URL, threshold_pct=5.0))
    store = ConversationStore()
    _, history = loop.run_until_complete(store.get_or_create(CONV_ID))
    history.add("user", SENTINEL_PREVIEW)

    billing = MagicMock()
    customer = _customer()
    billing.get_customer_by_api_key = AsyncMock(side_effect=lambda k: customer if k == CUSTOMER_KEY else None)

    patches = [
        patch("src.main.settings.mcp_api_key", OPERATOR_KEY),
        patch("src.main.get_billing_service", return_value=billing),
        patch("src.main.get_pricing_alert_service", return_value=alerts),
        patch("src.main.get_conversation_store", return_value=store),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(loop=loop, alerts=alerts, store=store, alert_id=alert["id"])
    finally:
        for p in reversed(patches):
            p.stop()
        loop.close()


def _alert_ids(env):
    return [a["id"] for a in env.loop.run_until_complete(env.alerts.list_alerts())]


def _conv_ids(env):
    return [c["id"] for c in env.loop.run_until_complete(env.store.list_conversations())]


REFUSED_HEADERS = [
    pytest.param({}, id="no-key"),
    pytest.param({"x-api-key": CUSTOMER_KEY}, id="customer-key"),
    pytest.param({"x-api-key": WRONG_KEY}, id="wrong-key"),
    pytest.param({"Authorization": f"Bearer {OPERATOR_KEY}"}, id="operator-as-bearer"),
    pytest.param({"x-api-key": NON_ASCII_KEY}, id="non-ascii-key"),
]
OP = {"x-api-key": OPERATOR_KEY}


def _assert_401_no_data(resp):
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Unauthorized"}
    for leak in (SENTINEL_URL, SENTINEL_PREVIEW, CONV_ID):
        assert leak not in resp.text


# ---------------------------------------------------------------------------
# AC9-AC10: /pricing/alerts
# ---------------------------------------------------------------------------

class TestPricingAlertsHttp:
    @pytest.mark.parametrize("headers", REFUSED_HEADERS)
    def test_ac9_list_refused(self, env, headers):
        _assert_401_no_data(client.get("/pricing/alerts", headers=headers))

    @pytest.mark.parametrize("headers", REFUSED_HEADERS)
    def test_ac9_list_with_trailing_slash_or_head_refused(self, env, headers):
        assert client.get("/pricing/alerts/", headers=headers).status_code in (401, 404)
        assert client.head("/pricing/alerts", headers=headers).status_code == 401

    def test_ac9_operator_lists(self, env):
        resp = client.get("/pricing/alerts", headers=OP)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1 and resp.json()["alerts"][0]["url"] == SENTINEL_URL

    @pytest.mark.parametrize("headers", REFUSED_HEADERS)
    def test_ac10_delete_refused_and_alert_remains(self, env, headers):
        _assert_401_no_data(client.delete(f"/pricing/alerts/{env.alert_id}", headers=headers))
        assert _alert_ids(env) == [env.alert_id]

    @pytest.mark.parametrize("headers", REFUSED_HEADERS)
    def test_refused_delete_identical_for_unknown_id(self, env, headers):
        known = client.delete(f"/pricing/alerts/{env.alert_id}", headers=headers)
        unknown = client.delete("/pricing/alerts/987654", headers=headers)
        assert (known.status_code, known.text) == (unknown.status_code, unknown.text)

    def test_ac10_operator_deletes_204(self, env):
        resp = client.delete(f"/pricing/alerts/{env.alert_id}", headers=OP)
        assert resp.status_code == 204
        assert _alert_ids(env) == []

    def test_ac10_operator_unknown_id_404(self, env):
        assert client.delete("/pricing/alerts/987654", headers=OP).status_code == 404

    def test_unset_operator_key_is_503(self, env):
        with patch("src.main.settings.mcp_api_key", None):
            assert client.get("/pricing/alerts", headers=OP).status_code == 503
            assert client.delete(f"/pricing/alerts/{env.alert_id}", headers=OP).status_code == 503
        assert _alert_ids(env) == [env.alert_id]

    def test_signing_info_stays_public(self, env):
        assert client.get("/pricing/alerts/signing-info").status_code == 200

    def test_options_preflight_not_gated(self, env):
        resp = client.options("/pricing/alerts")
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# AC11-AC12: /agent/conversations
# ---------------------------------------------------------------------------

class TestConversationsHttp:
    @pytest.mark.parametrize("headers", REFUSED_HEADERS)
    def test_ac11_list_refused(self, env, headers):
        _assert_401_no_data(client.get("/agent/conversations", headers=headers))

    def test_ac11_head_refused(self, env):
        assert client.head("/agent/conversations").status_code == 401

    def test_ac11_operator_lists(self, env):
        resp = client.get("/agent/conversations", headers=OP)
        assert resp.status_code == 200
        assert resp.json()["conversations"][0]["id"] == CONV_ID

    @pytest.mark.parametrize("headers", REFUSED_HEADERS)
    def test_ac12_delete_refused_and_conversation_remains(self, env, headers):
        _assert_401_no_data(client.delete(f"/agent/conversations/{CONV_ID}", headers=headers))
        assert _conv_ids(env) == [CONV_ID]

    def test_ac12_operator_deletes_204(self, env):
        assert client.delete(f"/agent/conversations/{CONV_ID}", headers=OP).status_code == 204
        assert _conv_ids(env) == []

    def test_ac12_operator_unknown_id_404(self, env):
        assert client.delete("/agent/conversations/no-such-conv", headers=OP).status_code == 404

    def test_unset_operator_key_is_503(self, env):
        with patch("src.main.settings.mcp_api_key", None):
            assert client.get("/agent/conversations", headers=OP).status_code == 503


# ---------------------------------------------------------------------------
# AC13: creating a price alert stays public, but is throttled and body-capped
# ---------------------------------------------------------------------------

class TestCreateAlertThrottled:
    def test_ac13_keyless_post_is_201(self, env):
        resp = client.post("/pricing/alerts", json={"url": "https://new.example/hook"})
        assert resp.status_code == 201
        assert len(_alert_ids(env)) == 2
        # It went through the limiter (one token consumed from the anonymous bucket).
        assert len(main_module._rate_limit_store[ANON_BUCKET]) == 1

    def test_ac13_keyless_post_is_429_when_anonymous_bucket_full(self, env):
        limit = main_module.settings.rate_limit_per_minute
        assert limit > 0
        main_module._rate_limit_store[ANON_BUCKET].extend([time.time()] * limit)
        resp = client.post("/pricing/alerts", json={"url": "https://new.example/hook"})
        assert resp.status_code == 429
        assert _alert_ids(env) == [env.alert_id]

    def test_ac13_public_reads_unaffected_by_full_bucket(self, env):
        main_module._rate_limit_store[ANON_BUCKET].extend(
            [time.time()] * main_module.settings.rate_limit_per_minute
        )
        assert client.get("/pricing/alerts/signing-info").status_code == 200
        with patch("src.main.get_pricing_alert_service", return_value=env.alerts):
            pricing = client.get("/pricing")
        assert pricing.status_code != 429

    def test_ac13_oversized_post_is_413(self, env):
        resp = client.post(
            "/pricing/alerts",
            content=b"{}",
            headers={"Content-Type": "application/json",
                     "Content-Length": str(main_module.settings.max_body_bytes + 1)},
        )
        assert resp.status_code == 413
        assert _alert_ids(env) == [env.alert_id]

    def test_ac13_oversized_actual_body_is_413(self, env):
        big = b'{"url": "https://x.example/' + b"a" * (main_module.settings.max_body_bytes + 10) + b'"}'
        resp = client.post("/pricing/alerts", content=big, headers={"Content-Type": "application/json"})
        assert resp.status_code == 413
        assert _alert_ids(env) == [env.alert_id]


# ---------------------------------------------------------------------------
# AC14: /agent/chat keyless still works and continues a conversation
# ---------------------------------------------------------------------------

class TestAgentChatStillPublic:
    def test_ac14_chat_and_follow_up(self, env):
        store = ConversationStore()

        class _Agent:
            async def chat(self, message, conversation_id=None):
                cid, history = await store.get_or_create(conversation_id)
                history.add("user", message)
                history.add("assistant", f"turns={len(history)}")
                return SimpleNamespace(reply=f"turns={len(history)}", conversation_id=cid,
                                       tool_calls=[], sources=[])

        async def _get():
            return _Agent()

        with patch("src.main.get_pricing_agent", _get):
            first = client.post("/agent/chat", json={"message": "hello"})
            assert first.status_code == 200
            cid = first.json()["conversation_id"]
            assert first.json()["reply"] == "turns=2"
            second = client.post("/agent/chat", json={"message": "again", "conversation_id": cid})
        assert second.status_code == 200
        assert second.json()["conversation_id"] == cid
        assert second.json()["reply"] == "turns=4"


# ---------------------------------------------------------------------------
# AC20 (HTTP part): non-ASCII keys are 401 (not 500) and the key is never logged
# ---------------------------------------------------------------------------

class TestKeyHandling:
    @pytest.mark.parametrize("method,path", [
        ("GET", "/pricing/alerts"),
        ("DELETE", "/pricing/alerts/1"),
        ("GET", "/agent/conversations"),
        ("DELETE", "/agent/conversations/x"),
        ("GET", "/admin/customers"),
        ("GET", "/deployment/shutdown/status"),
    ])
    def test_non_ascii_key_is_401_not_500(self, env, method, path):
        resp = client.request(method, path, headers={"x-api-key": NON_ASCII_KEY})
        assert resp.status_code == 401

    def test_non_ascii_key_on_general_auth_route_is_401_not_500(self, env):
        # General (non-sensitive) auth branch: billing finds no customer, then the
        # global-key compare must not raise TypeError on a non-ASCII header.
        resp = client.get("/usage/session/abc", headers={"x-api-key": NON_ASCII_KEY})
        assert resp.status_code == 401

    def test_non_ascii_key_on_anonymous_post_is_not_500(self, env):
        resp = client.post("/pricing/alerts", json={"url": "https://new.example/hook"},
                           headers={"x-api-key": NON_ASCII_KEY})
        assert resp.status_code == 201

    @pytest.mark.parametrize("key", [CUSTOMER_KEY, WRONG_KEY, OPERATOR_KEY])
    def test_key_never_logged(self, env, caplog, key):
        caplog.set_level("DEBUG")
        for method, path in (("GET", "/pricing/alerts"), ("DELETE", "/pricing/alerts/987654"),
                             ("GET", "/agent/conversations"), ("DELETE", "/agent/conversations/zz")):
            resp = client.request(method, path, headers={"x-api-key": key})
            assert key not in resp.text
        for record in caplog.records:
            assert key not in record.getMessage()
            assert key not in str(record.args)


# ---------------------------------------------------------------------------
# Middleware table and route guard
# ---------------------------------------------------------------------------

class TestOperatorOnlyTable:
    @pytest.mark.parametrize("method,path,expected", [
        ("GET", "/pricing/alerts", True),
        ("HEAD", "/pricing/alerts", True),
        ("GET", "/pricing/alerts/", True),
        ("get", "/pricing/alerts", True),
        ("POST", "/pricing/alerts", False),
        ("OPTIONS", "/pricing/alerts", False),
        ("GET", "/pricing/alerts/signing-info", False),
        ("DELETE", "/pricing/alerts/7", True),
        ("DELETE", "/pricing/alerts/", True),
        ("GET", "/agent/conversations", True),
        ("HEAD", "/agent/conversations", True),
        ("DELETE", "/agent/conversations/abc", True),
        ("OPTIONS", "/agent/conversations/abc", False),
        ("POST", "/agent/chat", False),
        ("GET", "/pricing", False),
        ("GET", "/conversations", False),
        ("DELETE", "/usage/alerts/1", False),
    ])
    def test_is_operator_only_request(self, method, path, expected):
        assert main_module._is_operator_only_request(method, path) is expected

    def test_every_alert_and_conversation_route_is_covered(self):
        """Guard: a new GET/DELETE route under these paths must not slip back into the bypass."""
        from fastapi.routing import APIRoute
        seen = []
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            if not (route.path.startswith("/pricing/alerts") or route.path.startswith("/agent/conversations")):
                continue
            for method in route.methods:
                concrete = route.path.replace("{alert_id}", "1").replace("{conversation_id}", "abc")
                seen.append((method, route.path))
                if route.path == "/pricing/alerts/signing-info":
                    assert not main_module._is_operator_only_request(method, concrete)
                elif method == "POST":
                    assert not main_module._is_operator_only_request(method, concrete)
                else:
                    assert main_module._is_operator_only_request(method, concrete), (method, route.path)
        assert ("GET", "/pricing/alerts") in seen
        assert ("DELETE", "/agent/conversations/{conversation_id}") in seen
