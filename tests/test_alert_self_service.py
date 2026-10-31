"""v1.81.0 — self-service price alerts via a one-time alert_token.

Registration (HTTP, MCP, chat agent) returns an ``alert_token`` shown once; only its
SHA-256 hash is stored. The holder can view or delete exactly that alert, with no API
key, via POST /pricing/alerts/self/view|delete (token in the JSON body) or the public
``manage_price_alert`` MCP tool. Every non-matching token gets one identical refusal.

Organised by the Spec's acceptance criteria. Tokens are never literals: real ones come
from registration responses, never-issued ones are minted at runtime. Webhook URLs are
example.org hosts (the autouse webhook_dns stub resolves them offline).
"""
import asyncio
import hashlib
import json
import logging
import re
import secrets
import sqlite3
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
from src.models.pricing import PricingAlertTokenRequest  # noqa: E402
from src.services import pricing_alerts as pa  # noqa: E402
from src.services.billing_service import CustomerRecord  # noqa: E402
from src.services.pricing_alerts import (  # noqa: E402
    ALERT_TOKEN_PREFIX, ALERT_TOKEN_RE, ALERT_TOKEN_REDACTED, ALERT_TOKEN_REFUSAL_MESSAGE,
    PricingAlertService, redact_alert_tokens,
)
from src.services.telemetry import get_telemetry_service  # noqa: E402
from mcp.caller import PUBLIC  # noqa: E402
from mcp.tools.tool_manager import ToolManager  # noqa: E402

client = TestClient(app)

OPERATOR_KEY = "-".join(["op", "selfsvc", "global", "71aa"])
CUSTOMER_KEY = "-".join(["svc", "selfsvc", "cust", "82bb"])
OP = {"x-api-key": OPERATOR_KEY}

URL_A = "https://hooks.example.org/alerts/a"
URL_B = "https://hooks.example.org/alerts/b"
URL_LEGACY = "https://hooks.example.org/alerts/legacy"

PUBLIC_FIELDS = {"id", "url", "threshold_pct", "provider", "model_name", "created_at"}
HTTP_REFUSAL = {"error": "invalid_alert_token", "message": ALERT_TOKEN_REFUSAL_MESSAGE}
MCP_REFUSAL = {"success": False, "error_code": "invalid_alert_token", "error": ALERT_TOKEN_REFUSAL_MESSAGE}

ANON_BUCKET = "testclient:"
SELF_ROUTES = ("/pricing/alerts/self/view", "/pricing/alerts/self/delete")

TREND = {
    "model_name": "gpt-4o", "provider": "openai", "input_change_pct": 50.0, "output_change_pct": -2.5,
    "direction": "up", "first_seen": "2026-10-30T00:00:00", "last_seen": "2026-10-30T06:00:00",
}


def _never_issued() -> str:
    """Well-formed, never-registered token of the same format."""
    return ALERT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def _customer():
    return CustomerRecord(
        id="cust-selfsvc", email="selfsvc@example.org", stripe_customer_id=None, stripe_subscription_id=None,
        api_key=CUSTOMER_KEY, tier="free", org_id="org-selfsvc", created_at=time.time(), updated_at=time.time(),
    )


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    main_module._rate_limit_store.clear()
    yield
    main_module._rate_limit_store.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A real temp-file PricingAlertService installed as the singleton every surface uses."""
    loop = asyncio.new_event_loop()
    db = str(tmp_path / "selfsvc_alerts.db")
    svc = PricingAlertService(db)
    loop.run_until_complete(svc.initialize())
    monkeypatch.setattr(pa, "_alert_service", svc)

    billing = MagicMock()
    customer = _customer()
    billing.get_customer_by_api_key = AsyncMock(side_effect=lambda k: customer if k == CUSTOMER_KEY else None)
    patches = [
        patch("src.main.settings.mcp_api_key", OPERATOR_KEY),
        patch("src.main.get_billing_service", return_value=billing),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(loop=loop, svc=svc, db=db, tm=ToolManager())
    finally:
        for p in reversed(patches):
            p.stop()
        loop.close()


def _run(env, coro):
    return env.loop.run_until_complete(coro)


def _register_http(url=URL_A, **extra):
    resp = client.post("/pricing/alerts", json={"url": url, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _rpc(method, params=None, headers=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post("/mcp", json=body, headers=headers or {})


def _mcp(name, arguments, headers=None):
    resp = _rpc("tools/call", {"name": name, "arguments": arguments}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" not in data, data
    return json.loads(data["result"]["content"][0]["text"])


def _view(token, **extra):
    return client.post("/pricing/alerts/self/view", json={"alert_token": token, **extra})


def _delete(token, **extra):
    return client.post("/pricing/alerts/self/delete", json={"alert_token": token, **extra})


def _alert_ids(env):
    return [a["id"] for a in _run(env, env.svc.list_alerts())]


def _mock_client():
    mock = MagicMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    resp = MagicMock()
    resp.status_code = 200
    mock.post = AsyncMock(return_value=resp)
    return mock


async def _insert_legacy(db, url):
    import aiosqlite
    async with aiosqlite.connect(db) as conn:
        cur = await conn.execute(
            "INSERT INTO pricing_alerts (url, threshold_pct, provider, model_name, created_at) "
            "VALUES (?, 1.0, NULL, NULL, ?)", (url, time.time()),
        )
        await conn.commit()
        return cur.lastrowid


def _db_bytes(path):
    data = b""
    for suffix in ("", "-journal", "-wal"):
        p = Path(path + suffix)
        if p.exists():
            data += p.read_bytes()
    return data


# ---------------------------------------------------------------------------
# AC1: HTTP registration returns a once-only alert_token
# ---------------------------------------------------------------------------

class TestAC1HttpRegistration:
    def test_201_with_existing_fields_plus_token_and_notice(self, env):
        data = _register_http(URL_A, threshold_pct=7.5, provider="openai")
        assert PUBLIC_FIELDS <= set(data)
        assert set(data) == PUBLIC_FIELDS | {"alert_token", "alert_token_notice"}
        assert data["url"] == URL_A and data["threshold_pct"] == 7.5 and data["provider"] == "openai"
        token = data["alert_token"]
        assert ALERT_TOKEN_RE.fullmatch(token)
        # >= 128 bits: 43 url-safe base64 chars after the prefix = 256 bits.
        assert len(token) - len(ALERT_TOKEN_PREFIX) >= 22
        notice = data["alert_token_notice"]
        assert "only once" in notice and "cannot be recovered" in notice
        assert "/pricing/alerts/self/view" in notice and "manage_price_alert" in notice

    def test_rejected_url_returns_no_token(self, env):
        resp = client.post("/pricing/alerts", json={"url": "http://127.0.0.1/x"})
        assert resp.status_code == 422
        assert "alert_token" not in resp.text
        assert _alert_ids(env) == []


# ---------------------------------------------------------------------------
# AC2: MCP registration returns the same token; rejected URL stores nothing
# ---------------------------------------------------------------------------

class TestAC2McpRegistration:
    def test_success_with_token_and_notice(self, env):
        result = _mcp("register_price_alert", {"url": URL_A})
        assert result["success"] is True
        assert set(result) == PUBLIC_FIELDS | {"success", "alert_token", "alert_token_notice"}
        assert ALERT_TOKEN_RE.fullmatch(result["alert_token"])
        assert "only once" in result["alert_token_notice"]

    def test_rejected_url(self, env):
        result = _mcp("register_price_alert", {"url": "http://127.0.0.1/x"})
        assert result["success"] is False
        assert result["error_code"] == "invalid_webhook_url"
        assert "alert_token" not in result and "alert_token_notice" not in result
        assert _alert_ids(env) == []


# ---------------------------------------------------------------------------
# AC3: two registrations, two tokens; a token reaches only its own alert
# ---------------------------------------------------------------------------

class TestAC3Isolation:
    def test_tokens_differ(self, env):
        a = _register_http(URL_A)
        b = _register_http(URL_B)
        assert a["alert_token"] != b["alert_token"]
        assert a["id"] != b["id"]

    def test_http_token_a_only_reaches_a(self, env):
        a = _register_http(URL_A)
        b = _register_http(URL_B)
        before_b = _run(env, env.svc.get_by_token(b["alert_token"]))
        view = _view(a["alert_token"])
        assert view.status_code == 200 and view.json()["id"] == a["id"]
        deleted = _delete(a["alert_token"])
        assert deleted.status_code == 200 and deleted.json() == {"deleted": True, "id": a["id"]}
        assert _alert_ids(env) == [b["id"]]
        assert _run(env, env.svc.get_by_token(b["alert_token"])) == before_b

    def test_mcp_token_a_only_reaches_a(self, env):
        a = _mcp("register_price_alert", {"url": URL_A})
        b = _mcp("register_price_alert", {"url": URL_B})
        before_b = _run(env, env.svc.get_by_token(b["alert_token"]))
        view = _mcp("manage_price_alert", {"action": "view", "alert_token": a["alert_token"]})
        assert view["success"] is True and view["alert"]["id"] == a["id"]
        deleted = _mcp("manage_price_alert", {"action": "delete", "alert_token": a["alert_token"]})
        assert deleted == {"success": True, "action": "delete", "deleted": True, "alert_id": a["id"]}
        assert _alert_ids(env) == [b["id"]]
        assert _run(env, env.svc.get_by_token(b["alert_token"])) == before_b


# ---------------------------------------------------------------------------
# AC4: view returns exactly the six fields of that one alert
# ---------------------------------------------------------------------------

class TestAC4View:
    def test_http_view_exact_fields(self, env):
        a = _register_http(URL_A, threshold_pct=3.0, model_name="gpt-4o")
        _register_http(URL_B)
        resp = _view(a["alert_token"])
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == PUBLIC_FIELDS
        assert body == {k: a[k] for k in PUBLIC_FIELDS}
        assert a["alert_token"] not in resp.text
        assert hashlib.sha256(a["alert_token"].encode()).hexdigest() not in resp.text
        assert URL_B not in resp.text

    def test_mcp_view_exact_fields(self, env):
        a = _mcp("register_price_alert", {"url": URL_A})
        _mcp("register_price_alert", {"url": URL_B})
        result = _mcp("manage_price_alert", {"action": "view", "alert_token": a["alert_token"]})
        assert set(result) == {"success", "action", "alert"}
        assert set(result["alert"]) == PUBLIC_FIELDS
        assert result["alert"] == {k: a[k] for k in PUBLIC_FIELDS}
        dumped = json.dumps(result)
        assert a["alert_token"] not in dumped and URL_B not in dumped

    def test_view_never_changes_the_alert(self, env):
        a = _register_http(URL_A)
        for _ in range(3):
            _view(a["alert_token"])
        listed = client.get("/pricing/alerts", headers=OP).json()["alerts"]
        assert [x["url"] for x in listed] == [URL_A]


# ---------------------------------------------------------------------------
# AC5: delete removes the alert from the listing and from delivery
# ---------------------------------------------------------------------------

class TestAC5Delete:
    def test_http_delete_stops_listing_and_firing(self, env):
        a = _register_http(URL_A, threshold_pct=1.0)
        b = _register_http(URL_B, threshold_pct=1.0)
        resp = _delete(a["alert_token"])
        assert resp.status_code == 200 and resp.json() == {"deleted": True, "id": a["id"]}
        listed = client.get("/pricing/alerts", headers=OP).json()
        assert [x["id"] for x in listed["alerts"]] == [b["id"]]
        mock = _mock_client()
        with patch("src.services.pricing_alerts.httpx.AsyncClient", return_value=mock):
            fired = _run(env, env.svc.check_and_fire([TREND]))
        assert fired == 1
        assert [c.args[0] for c in mock.post.await_args_list] == [URL_B]

    def test_mcp_delete_stops_firing(self, env):
        a = _mcp("register_price_alert", {"url": URL_A, "threshold_pct": 1.0})
        assert _mcp("manage_price_alert", {"action": "delete", "alert_token": a["alert_token"]})["deleted"]
        mock = _mock_client()
        with patch("src.services.pricing_alerts.httpx.AsyncClient", return_value=mock):
            assert _run(env, env.svc.check_and_fire([TREND])) == 0
        mock.post.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC6: one identical refusal for deleted, never-issued, malformed and missing tokens
# ---------------------------------------------------------------------------

class TestAC6IdenticalRefusal:
    def _bad_bodies(self, env):
        a = _register_http(URL_A)
        _register_http(URL_B)  # an alert exists, so refusals can't depend on an empty table
        assert _delete(a["alert_token"]).status_code == 200
        return [
            {"alert_token": a["alert_token"]},  # deleted
            {"alert_token": _never_issued()},
            {"alert_token": ""},
            {"alert_token": "abc"},
            {"alert_token": None},
            {"alert_token": a["alert_token"] + "x"},
            {"alert_token": a["alert_token"].upper()},
            {"alert_token": "lpa_" + "é" * 43},
            {},
        ]

    def test_http_refusals_byte_identical_on_both_routes(self, env):
        bodies = self._bad_bodies(env)
        seen = set()
        for route in SELF_ROUTES:
            for body in bodies:
                resp = client.post(route, json=body)
                assert resp.status_code == 404, (route, body)
                assert resp.json() == HTTP_REFUSAL
                seen.add((resp.status_code, resp.content, resp.headers.get("content-type")))
        assert len(seen) == 1

    def test_non_string_tokens_and_non_object_bodies_get_the_same_refusal(self, env):
        """No 422 for odd shapes: a non-string token or a non-object body is just a bad token."""
        a = _register_http(URL_A)
        token = a["alert_token"]
        expected = client.post(SELF_ROUTES[0], json={"alert_token": _never_issued()})
        json_bodies = [
            {"alert_token": 12345},
            {"alert_token": [token]},
            {"alert_token": {"alert_token": token}},
            {"alert_token": True},
            [token],
            token,
            42,
            None,
        ]
        raw_bodies = [b"not json", b'{"alert_token": ', b""]
        for route in SELF_ROUTES:
            for body in json_bodies:
                resp = client.post(route, json=body)
                assert (resp.status_code, resp.content) == (expected.status_code, expected.content), (route, body)
                assert token not in resp.text
            for raw in raw_bodies:
                resp = client.post(route, content=raw, headers={"Content-Type": "application/json"})
                assert (resp.status_code, resp.content) == (expected.status_code, expected.content), (route, raw)
        assert _alert_ids(env) == [a["id"]]

    def test_openapi_still_documents_the_body(self):
        spec = client.get("/openapi.json").json()
        for route in SELF_ROUTES:
            schema = spec["paths"][route]["post"]["requestBody"]["content"]["application/json"]["schema"]
            assert set(schema["properties"]) == {"alert_token"}

    def test_refusal_never_echoes_token(self, env):
        token = _never_issued()
        for route in SELF_ROUTES:
            assert token not in client.post(route, json={"alert_token": token}).text

    def test_mcp_refusals_identical_for_both_actions(self, env):
        a = _mcp("register_price_alert", {"url": URL_A})
        _mcp("register_price_alert", {"url": URL_B})
        _mcp("manage_price_alert", {"action": "delete", "alert_token": a["alert_token"]})
        tokens = [a["alert_token"], _never_issued(), "", "abc", None]
        for action in ("view", "delete"):
            for token in tokens:
                assert _mcp("manage_price_alert", {"action": action, "alert_token": token}) == MCP_REFUSAL
            assert _mcp("manage_price_alert", {"action": action}) == MCP_REFUSAL

    def test_mcp_invalid_action(self, env):
        a = _mcp("register_price_alert", {"url": URL_A})
        for action in ("edit", "", None, "VIEW"):
            result = _mcp("manage_price_alert", {"action": action, "alert_token": a["alert_token"]})
            assert result["success"] is False and result["error_code"] == "invalid_arguments"
            assert a["alert_token"] not in json.dumps(result)
        assert _alert_ids(env) == [a["id"]]


# ---------------------------------------------------------------------------
# AC7: no interface carries an alert id; a stray id changes nothing
# ---------------------------------------------------------------------------

class TestAC7NoAlertId:
    def test_body_model_and_mcp_schema_have_no_id(self, env):
        assert set(PricingAlertTokenRequest.model_fields) == {"alert_token"}
        schema = env.tm.get_tool("manage_price_alert")["input_schema"]
        assert set(schema["properties"]) == {"action", "alert_token"}
        assert schema["required"] == ["action", "alert_token"]

    def test_stray_id_ignored_on_http(self, env):
        a = _register_http(URL_A)
        b = _register_http(URL_B)
        resp = _view(a["alert_token"], id=b["id"], alert_id=b["id"])
        assert resp.status_code == 200 and resp.json()["id"] == a["id"]
        r1 = _delete(_never_issued(), alert_id=b["id"])
        r2 = _delete(_never_issued(), alert_id=987654)
        assert (r1.status_code, r1.content) == (r2.status_code, r2.content)
        assert r1.json() == HTTP_REFUSAL
        assert sorted(_alert_ids(env)) == sorted([a["id"], b["id"]])

    def test_stray_id_ignored_on_mcp(self, env):
        a = _mcp("register_price_alert", {"url": URL_A})
        b = _mcp("register_price_alert", {"url": URL_B})
        r1 = _mcp("manage_price_alert", {"action": "delete", "alert_token": _never_issued(), "alert_id": b["id"]})
        r2 = _mcp("manage_price_alert", {"action": "delete", "alert_token": _never_issued(), "alert_id": 987654})
        assert r1 == r2 == MCP_REFUSAL
        assert sorted(_alert_ids(env)) == sorted([a["id"], b["id"]])


# ---------------------------------------------------------------------------
# AC8 / AC10: operator-only list/delete unchanged for everyone without the key
# ---------------------------------------------------------------------------

class TestAC8AC10OperatorOnlyUnchanged:
    @pytest.mark.parametrize("headers", [{}, {"x-api-key": CUSTOMER_KEY}])
    def test_http_list_and_delete_still_401(self, env, headers):
        a = _register_http(URL_A)
        assert client.get("/pricing/alerts", headers=headers).status_code == 401
        assert client.delete(f"/pricing/alerts/{a['id']}", headers=headers).status_code == 401
        assert _alert_ids(env) == [a["id"]]

    def test_http_503_when_operator_key_unset(self, env):
        a = _register_http(URL_A)
        with patch("src.main.settings.mcp_api_key", None):
            assert client.get("/pricing/alerts").status_code == 503
            assert client.delete(f"/pricing/alerts/{a['id']}").status_code == 503
        assert _alert_ids(env) == [a["id"]]

    @pytest.mark.parametrize("headers", [{}, {"x-api-key": CUSTOMER_KEY}])
    def test_mcp_list_and_delete_by_id_still_refused(self, env, headers):
        a = _mcp("register_price_alert", {"url": URL_A})
        listed = _mcp("list_price_alerts", {}, headers=headers)
        assert listed["error_code"] == "operator_key_required"
        deleted = _mcp("delete_price_alert", {"alert_id": a["id"]}, headers=headers)
        assert deleted["error_code"] == "operator_key_required"
        assert _alert_ids(env) == [a["id"]]

    def test_self_routes_are_not_operator_only(self):
        for route in SELF_ROUTES:
            assert main_module._is_operator_only_request("POST", route) is False
        assert main_module._is_operator_only_request("DELETE", "/pricing/alerts/self") is True
        assert main_module._is_operator_only_request("DELETE", "/pricing/alerts/self/delete") is True

    def test_manage_price_alert_is_public(self, env):
        assert env.tm.caller_access("manage_price_alert") == PUBLIC
        assert getattr(env.tm.get_tool("manage_price_alert")["instance"], "redact_errors", False) is True


# ---------------------------------------------------------------------------
# AC9: the operator listing never shows a token or its hash; delete by id works
# ---------------------------------------------------------------------------

class TestAC9OperatorListing:
    def test_listing_has_no_token_or_hash(self, env):
        a = _register_http(URL_A)
        b = _mcp("register_price_alert", {"url": URL_B})
        resp = client.get("/pricing/alerts", headers=OP)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        for record in body["alerts"]:
            assert set(record) == PUBLIC_FIELDS
        for token in (a["alert_token"], b["alert_token"]):
            assert token not in resp.text
            assert hashlib.sha256(token.encode()).hexdigest() not in resp.text
        assert all("token_hash" not in r for r in _run(env, env.svc.list_alerts()))

    def test_operator_delete_by_id_then_token_refused(self, env):
        a = _register_http(URL_A)
        assert client.delete(f"/pricing/alerts/{a['id']}", headers=OP).status_code == 204
        assert _view(a["alert_token"]).json() == HTTP_REFUSAL


# ---------------------------------------------------------------------------
# AC11: the raw token never reaches logs, telemetry or the alert DB
# ---------------------------------------------------------------------------

class TestAC11NoTokenAnywhere:
    def test_http_and_mcp_flows(self, env, caplog):
        caplog.set_level(logging.DEBUG)
        tokens = []
        a = _register_http(URL_A)
        tokens.append(a["alert_token"])
        _view(a["alert_token"])
        _delete(a["alert_token"])
        b = _mcp("register_price_alert", {"url": URL_B})
        tokens.append(b["alert_token"])
        _mcp("manage_price_alert", {"action": "view", "alert_token": b["alert_token"]})
        c = _register_http(URL_A)
        tokens.append(c["alert_token"])
        _mcp("manage_price_alert", {"action": "view", "alert_token": c["alert_token"]})
        _mcp("manage_price_alert", {"action": "delete", "alert_token": b["alert_token"]})
        bad = _never_issued()
        _view(bad)
        tokens.append(bad)

        telemetry = get_telemetry_service()
        telemetry_dump = json.dumps(vars(telemetry), default=str) + json.dumps(
            [telemetry.get_endpoint_stats(), telemetry.get_feature_usage(), telemetry.get_overall_stats()],
            default=str,
        )
        db = _db_bytes(env.db)
        for token in tokens:
            assert token not in caplog.text
            for record in caplog.records:
                assert token not in record.getMessage() and token not in str(record.args)
            assert token not in telemetry_dump
            assert token.encode() not in db
        # The surviving alert is stored by hash only.
        assert hashlib.sha256(c["alert_token"].encode()).hexdigest().encode() in db

    def test_hash_is_sha256_of_token(self, env):
        a = _register_http(URL_A)
        with sqlite3.connect(env.db) as conn:
            (stored,) = conn.execute("SELECT token_hash FROM pricing_alerts WHERE id = ?", (a["id"],)).fetchone()
        assert stored == hashlib.sha256(a["alert_token"].encode("utf-8")).hexdigest()
        assert stored == pa._hash_token(a["alert_token"])


# ---------------------------------------------------------------------------
# AC12: self routes share the anonymous per-IP limiter and the body cap
# ---------------------------------------------------------------------------

class TestAC12Throttling:
    def test_self_routes_listed_as_anonymous_rate_limited(self):
        for route in SELF_ROUTES:
            assert route in main_module._anonymous_rate_limited_paths

    @pytest.mark.parametrize("route", SELF_ROUTES)
    def test_consumes_anonymous_bucket(self, env, route):
        client.post(route, json={"alert_token": _never_issued()})
        assert len(main_module._rate_limit_store[ANON_BUCKET]) == 1

    @pytest.mark.parametrize("route", SELF_ROUTES)
    def test_429_when_bucket_full(self, env, route):
        a = _register_http(URL_A)
        main_module._rate_limit_store.clear()
        limit = main_module.settings.rate_limit_per_minute
        assert limit > 0
        main_module._rate_limit_store[ANON_BUCKET].extend([time.time()] * limit)
        resp = client.post(route, json={"alert_token": a["alert_token"]})
        assert resp.status_code == 429
        assert resp.json() == {"detail": "Rate limit exceeded"}
        assert _alert_ids(env) == [a["id"]]

    @pytest.mark.parametrize("route", SELF_ROUTES)
    def test_413_oversized(self, env, route):
        a = _register_http(URL_A)
        resp = client.post(
            route, content=b"{}",
            headers={"Content-Type": "application/json",
                     "Content-Length": str(main_module.settings.max_body_bytes + 1)},
        )
        assert resp.status_code == 413
        big = b'{"alert_token": "' + b"a" * (main_module.settings.max_body_bytes + 10) + b'"}'
        resp = client.post(route, content=big, headers={"Content-Type": "application/json"})
        assert resp.status_code == 413
        assert _alert_ids(env) == [a["id"]]


# ---------------------------------------------------------------------------
# AC13: pre-release (tokenless) alerts stay operator-managed and unreachable by token
# ---------------------------------------------------------------------------

class TestAC13LegacyRows:
    def test_legacy_row_fires_listed_deleted_by_operator_never_by_token(self, env):
        legacy_id = _run(env, _insert_legacy(env.db, URL_LEGACY))
        for token in ("", None, "None", "null", _never_issued()):
            assert _view(token).json() == HTTP_REFUSAL
            assert _delete(token).json() == HTTP_REFUSAL
            for action in ("view", "delete"):
                assert _mcp("manage_price_alert", {"action": action, "alert_token": token}) == MCP_REFUSAL
        assert _alert_ids(env) == [legacy_id]

        mock = _mock_client()
        with patch("src.services.pricing_alerts.httpx.AsyncClient", return_value=mock):
            assert _run(env, env.svc.check_and_fire([TREND])) == 1
        assert mock.post.await_args.args[0] == URL_LEGACY

        listed = client.get("/pricing/alerts", headers=OP).json()["alerts"]
        assert [x["id"] for x in listed] == [legacy_id]
        assert client.delete(f"/pricing/alerts/{legacy_id}", headers=OP).status_code == 204
        assert _alert_ids(env) == []

    def test_lookup_never_queries_for_malformed_tokens(self, env):
        """None/'' never reach SQL, so a NULL token_hash can never be bound and matched."""
        _run(env, _insert_legacy(env.db, URL_LEGACY))
        for token in (None, "", "abc", 123, b"lpa_x"):
            assert _run(env, env.svc.get_by_token(token)) is None
            assert _run(env, env.svc.delete_by_token(token)) is None
        assert len(_alert_ids(env)) == 1


# ---------------------------------------------------------------------------
# AC14: the chat agent shows the token live and redacts it from stored turns
# ---------------------------------------------------------------------------

class _ScriptedBackend:
    """LLM backend stand-in: a list of callables mapping (messages, last tool results) -> LLMResponse."""

    def __init__(self, steps):
        self._steps = list(steps)
        self.last_results = []
        self.seen_messages = []

    def _next(self, messages):
        self.seen_messages.append([dict(m) for m in messages])
        return self._steps.pop(0)(messages, self.last_results)

    async def complete(self, messages, tools, system=None):
        return self._next(messages)

    async def complete_stream(self, messages, tools, system=None):
        yield {"type": "response", "response": self._next(messages)}

    def append_assistant_turn(self, messages, response):
        messages.append({"role": "assistant", "content": response.content})

    def append_tool_results(self, messages, results):
        self.last_results = results
        messages.append({"role": "user", "content": "tool results"})


def _token_in(text):
    m = re.search(r"lpa_[A-Za-z0-9_-]{43}", text)
    return m.group(0) if m else None


@pytest.fixture
def agent_env(env, tmp_path):
    from agent.conversation import SQLiteConversationStore
    from agent.pricing_agent import PricingAgent
    from agent.tools import build_agent_tools

    conv_db = str(tmp_path / "conversations.db")
    store = SQLiteConversationStore(conv_db)
    _run(env, store.initialize())

    def make(steps):
        agent = PricingAgent(tool_manager=env.tm)
        agent._conversation_store = store
        agent._tools = build_agent_tools(env.tm, rag_pipeline=MagicMock())
        agent._llm_backend = _ScriptedBackend(steps)
        agent._initialized = True
        return agent

    return SimpleNamespace(make=make, store=store, conv_db=conv_db)


def _register_steps():
    from agent.llm_backend import LLMResponse
    return [
        lambda msgs, res: LLMResponse(
            content="", tool_calls=[{"id": "t1", "tool": "register_price_alert", "args": {"url": URL_A}}],
        ),
        lambda msgs, res: LLMResponse(
            content=f"Done. Save your alert_token now: {res[0]['result']['alert_token']} (shown only once).",
        ),
    ]


def _manage_steps(action="delete"):
    from agent.llm_backend import LLMResponse

    def _call(msgs, res):
        token = _token_in(msgs[-1]["content"]) or ""
        return LLMResponse(content="", tool_calls=[
            {"id": "t2", "tool": "manage_price_alert", "args": {"action": action, "alert_token": token}},
        ])

    def _answer(msgs, res):
        result = res[0]["result"]
        if result.get("success"):
            return LLMResponse(content="Your alert was deleted.")
        return LLMResponse(content=f"I could not do that: {result['error']}")

    return [_call, _answer]


class TestAC14ChatAgent:
    def test_tool_bound_with_self_service_descriptions(self, env):
        from agent.tools import build_agent_tools
        tools = {t.name: t for t in build_agent_tools(env.tm, rag_pipeline=MagicMock())}
        assert "manage_price_alert" in tools
        assert "alert_token" in tools["register_price_alert"].description
        assert "only once" in tools["register_price_alert"].description
        desc = tools["delete_price_alert"].description
        assert "manage_price_alert" in desc and "contact the operator" not in desc
        assert len(set(tools) & set(env.tm.tools)) == 27

    def test_register_live_token_redacted_in_store(self, env, agent_env):
        agent = agent_env.make(_register_steps())
        resp = _run(env, agent.chat(f"Set up a price alert to {URL_A}"))
        token = _token_in(resp.reply)
        assert token and ALERT_TOKEN_RE.fullmatch(token)
        assert _run(env, env.svc.get_by_token(token))["url"] == URL_A
        _, history = _run(env, agent_env.store.get_or_create(resp.conversation_id))
        stored = history.to_messages()
        assert ALERT_TOKEN_REDACTED in stored[-1]["content"]
        assert all(token not in m["content"] for m in stored)
        assert token.encode() not in _db_bytes(agent_env.conv_db)

    def test_delete_with_user_token_redacts_user_turn(self, env, agent_env):
        a = _register_http(URL_A)
        token = a["alert_token"]
        agent = agent_env.make(_manage_steps("delete"))
        resp = _run(env, agent.chat(f"delete my alert, token {token}"))
        assert resp.reply == "Your alert was deleted."
        assert _alert_ids(env) == []
        # The model saw the real token for this turn...
        assert token in agent._llm_backend.seen_messages[0][-1]["content"]
        # ...but the stored user turn has it redacted.
        _, history = _run(env, agent_env.store.get_or_create(resp.conversation_id))
        user_turn = history.to_messages()[0]
        assert user_turn["role"] == "user" and ALERT_TOKEN_REDACTED in user_turn["content"]
        assert token.encode() not in _db_bytes(agent_env.conv_db)

    def test_invalid_token_reports_failure_without_ids(self, env, agent_env):
        _register_http(URL_A)
        _register_http(URL_B)
        bad = _never_issued()
        agent = agent_env.make(_manage_steps("delete"))
        resp = _run(env, agent.chat(f"delete my alert, token {bad}"))
        assert resp.tool_calls[0]["result"] == MCP_REFUSAL
        assert ALERT_TOKEN_REFUSAL_MESSAGE in resp.reply
        assert not any(ch.isdigit() for ch in resp.reply)
        assert len(_alert_ids(env)) == 2

    def test_stream_live_token_redacted_in_store(self, env, agent_env):
        agent = agent_env.make(_register_steps())

        async def _collect():
            return [ev async for ev in agent.chat_stream(f"alert me at {URL_A}")]

        events = _run(env, _collect())
        answer = [e for e in events if e["type"] == "answer"][0]
        token = _token_in(answer["text"])
        assert token
        _, history = _run(env, agent_env.store.get_or_create(answer["conversation_id"]))
        assert all(token not in m["content"] for m in history.to_messages())
        assert token.encode() not in _db_bytes(agent_env.conv_db)

    def test_tool_exception_log_is_redacted(self, env, caplog):
        from agent.llm_backend import LLMResponse
        from agent.react_loop import ReActLoop
        from agent.tools import AgentTool

        token = _never_issued()

        async def _boom(args):
            raise RuntimeError(f"failed for {args['alert_token']}")

        tool = AgentTool(name="manage_price_alert", description="d", input_schema={"type": "object"}, execute=_boom)
        backend = _ScriptedBackend([
            lambda m, r: LLMResponse(content="", tool_calls=[
                {"id": "x", "tool": "manage_price_alert", "args": {"action": "view", "alert_token": token}}]),
            lambda m, r: LLMResponse(content="ok"),
        ])
        caplog.set_level(logging.DEBUG)
        _run(env, ReActLoop(llm_backend=backend, tools=[tool]).run([{"role": "user", "content": "hi"}]))
        assert "raised" in caplog.text
        assert token not in caplog.text and ALERT_TOKEN_REDACTED in caplog.text

    @pytest.mark.parametrize("path", ["/agent/chat", "/agent/chat/stream"])
    def test_agent_endpoint_error_logs_are_redacted(self, env, caplog, path):
        """Both chat endpoints log an agent exception without the token or a traceback."""
        token = _never_issued()
        fake_agent = MagicMock()
        boom = RuntimeError(f"provider rejected input: delete my alert, token {token}")
        fake_agent.chat = AsyncMock(side_effect=boom)

        async def _stream(*_a, **_k):
            raise boom
            yield  # pragma: no cover

        fake_agent.chat_stream = _stream
        caplog.set_level(logging.DEBUG)
        with patch.object(main_module, "get_pricing_agent", AsyncMock(return_value=fake_agent)):
            resp = client.post(path, json={"message": f"delete my alert, token {token}"})
        if path == "/agent/chat":
            assert resp.status_code == 500
        assert token not in resp.text
        assert ALERT_TOKEN_REDACTED in caplog.text
        assert token not in caplog.text
        for record in caplog.records:
            assert record.exc_info is None or token not in str(record.exc_info[1])


# ---------------------------------------------------------------------------
# AC16 / AC17: registry, descriptions and docs
# ---------------------------------------------------------------------------

class TestAC16AC17DocsAndWording:
    def test_registry_has_31_with_19_public(self, env):
        assert len(env.tm.tools) == 31
        assert len([n for n in env.tm.tools if env.tm.caller_access(n) == PUBLIC]) == 19

    def test_mcp_descriptions(self, env):
        tools = {t["name"]: t for t in _rpc("tools/list").json()["result"]["tools"]}
        assert "manage_price_alert" in tools
        assert "alert_token" in tools["register_price_alert"]["description"]
        desc = tools["delete_price_alert"]["description"]
        assert "manage_price_alert" in desc and "contact the operator" not in desc

    @pytest.mark.parametrize("rel", ["static/mcp-setup/index.html", "static/api-docs/index.html", "README.md"])
    def test_docs_self_service_wording(self, rel):
        text = (project_root / rel).read_text(encoding="utf-8")
        assert "contact the operator" not in text
        assert "alert_token" in text

    def test_api_docs_list_self_routes(self):
        text = (project_root / "static/api-docs/index.html").read_text(encoding="utf-8")
        for route in SELF_ROUTES:
            assert route in text

    def test_whats_new_entry(self):
        # Locate the v1.81.0 entry rather than the top one: later releases push it down.
        text = (project_root / "static/whats-new/index.html").read_text(encoding="utf-8")
        start = text.index("<!-- v1.81.0 -->")
        end = text.index("<!-- v", start + 1)
        assert "alert_token" in text[start:end]


# ---------------------------------------------------------------------------
# Units: hashing, redaction, migration, unique index
# ---------------------------------------------------------------------------

class TestUnits:
    def test_new_token_format_and_uniqueness(self):
        tokens = {pa._new_token() for _ in range(200)}
        assert len(tokens) == 200
        assert all(ALERT_TOKEN_RE.fullmatch(t) for t in tokens)

    def test_redact(self):
        token = _never_issued()
        assert redact_alert_tokens(f"here: {token}.") == f"here: {ALERT_TOKEN_REDACTED}."
        assert redact_alert_tokens(f"{token}\n{token}").count(ALERT_TOKEN_REDACTED) == 2
        assert redact_alert_tokens("lpa_short") == "lpa_short"
        assert redact_alert_tokens("xlpa_" + "a" * 43) == "xlpa_" + "a" * 43
        assert redact_alert_tokens("no tokens here") == "no tokens here"
        assert redact_alert_tokens(None) is None
        assert redact_alert_tokens(42) == 42

    def test_migration_adds_column_to_old_schema(self, tmp_path):
        db = str(tmp_path / "old.db")
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE pricing_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL, "
                "threshold_pct REAL NOT NULL DEFAULT 5.0, provider TEXT, model_name TEXT, created_at REAL NOT NULL)"
            )
            conn.execute(
                "INSERT INTO pricing_alerts (url, threshold_pct, created_at) VALUES (?, 5.0, ?)",
                (URL_LEGACY, time.time()),
            )
        svc = PricingAlertService(db)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(svc.initialize())
            loop.run_until_complete(svc.initialize())  # idempotent
            rec = loop.run_until_complete(svc.register(URL_A, 5.0))
            assert loop.run_until_complete(svc.get_by_token(rec["alert_token"]))["id"] == rec["id"]
            assert len(loop.run_until_complete(svc.list_alerts())) == 2
        finally:
            loop.close()
        with sqlite3.connect(db) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(pricing_alerts)")]
            assert "token_hash" in cols
            legacy = conn.execute("SELECT token_hash FROM pricing_alerts WHERE url = ?", (URL_LEGACY,)).fetchone()
            assert legacy == (None,)

    def test_unique_index_on_token_hash(self, tmp_path):
        db = str(tmp_path / "idx.db")
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(PricingAlertService(db).initialize())
        finally:
            loop.close()
        with sqlite3.connect(db) as conn:
            idx = {r[1]: r[2] for r in conn.execute("PRAGMA index_list(pricing_alerts)")}
            assert idx.get("idx_pricing_alerts_token_hash") == 1
            conn.execute("INSERT INTO pricing_alerts (url, created_at, token_hash) VALUES ('u', 1, 'h')")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO pricing_alerts (url, created_at, token_hash) VALUES ('v', 1, 'h')")
            # Multiple NULLs (legacy rows) are allowed.
            conn.execute("INSERT INTO pricing_alerts (url, created_at) VALUES ('w', 1)")
            conn.execute("INSERT INTO pricing_alerts (url, created_at) VALUES ('x', 1)")
