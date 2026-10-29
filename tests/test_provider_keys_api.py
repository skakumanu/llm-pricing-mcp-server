"""HTTP-level tests for stored per-org provider keys (v1.79.0): the /dispatch/keys endpoints and
their use by dispatch on POST /v1/chat/completions.

The billing service is patched to resolve two customer keys (two orgs); provider keys are stored
in a real ProviderKeyStore over a tmp_path billing.db with a per-test random server secret; the
dispatch service is a real DispatchService over tmp_path ledgers with a scripted executor (or a
real executor over httpx.MockTransport where the wire header matters). Nothing touches the
network. Test numbering follows the Spec's acceptance criteria (AC1..AC20).
"""
import asyncio
import base64
import csv
import io
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.billing_service import CustomerRecord  # noqa: E402
from src.services.budget_alerts import BudgetAlertService  # noqa: E402
from src.services.dispatch_executors import (  # noqa: E402
    EVENT_DELTA, EVENT_FINISH, EVENT_USAGE, ChatResult, ExecutorError, StreamEvent, get_executor,
)
from src.services.dispatch_receipts import DispatchReceiptStore  # noqa: E402
from src.services.dispatch_service import DispatchService  # noqa: E402
from src.services.provider_key_store import ProviderKeyStore  # noqa: E402
from src.services.router import ModelRouter  # noqa: E402
from src.services.savings_tracker import SavingsTrackerService  # noqa: E402
from src.services.usage_tracker import UsageTrackerService  # noqa: E402

client = TestClient(app)


def _fake_key(*parts: str) -> str:
    """Assembled at runtime so secret scanners do not flag the fixtures; none is a credential."""
    return "-".join(["sk", "test", *parts])


STORED = _fake_key("STOREDALPHA", "Zq9X")
STORED_NEW = _fake_key("STOREDBRAVO", "Wk3P")
HEADER = _fake_key("HEADERCHARLIE", "Jm4V")
ALL_SECRETS = (STORED, STORED_NEW, HEADER)
SVC_KEY_1 = "svc-key-org-1"  # nosec B105
SVC_KEY_2 = "svc-key-org-2"  # nosec B105
GLOBAL_KEY = "global-operator-key"  # nosec B105
_STATUS = {"server_error": 503, "rate_limited": 429, "auth_error": 401, "timeout": None}


def _pm(name, provider, quality, blended_per_1m, confirmed=True):
    per_token = blended_per_1m / 1_000_000
    m = PricingMetrics(model_name=name, provider=provider, cost_per_input_token=per_token,
                       cost_per_output_token=per_token, context_window=128_000, price_confirmed=confirmed,
                       price_as_of="2026-10-01")
    m.quality_score = quality
    return m


def _catalogue():
    return [
        _pm("model-a", "OpenAI", 85, 10.0),
        _pm("model-b", "Anthropic", 72, 1.0),
        _pm("model-c", "Google", 60, 0.20),
        _pm("model-g", "Groq", 80, 0.05),
        _pm("model-x", "Cohere", 90, 0.01),   # recommend-only
    ]


class FakeExecutor:
    """Scripted executor recording (model, api_key) per call. ``script[model]`` is 'ok' or a failure
    outcome raised before any result / before the first streamed event."""

    def __init__(self):
        self.script = {}
        self.calls = []

    async def execute(self, req, api_key):
        self.calls.append((req.model, api_key))
        outcome = self.script.get(req.model, "ok")
        if outcome == "ok":
            return ChatResult("hello", "stop", 100, 50, 9.0, 200, req.model)
        raise ExecutorError(outcome, _STATUS.get(outcome))

    async def stream(self, req, api_key):
        self.calls.append((req.model, api_key))
        outcome = self.script.get(req.model, "ok")
        if outcome != "ok":
            raise ExecutorError(outcome, _STATUS.get(outcome))
        yield StreamEvent(EVENT_DELTA, text="hello")
        yield StreamEvent(EVENT_FINISH, finish_reason="stop")
        yield StreamEvent(EVENT_USAGE, input_tokens=100, output_tokens=50)


def _customer(n):
    return CustomerRecord(id=f"cust-{n}", email=f"c{n}@example.com", stripe_customer_id=None,
                          stripe_subscription_id=None, api_key=f"svc-key-org-{n}", tier="free",
                          org_id=f"org-{n}", created_at=time.time(), updated_at=time.time())


CUSTOMERS = {SVC_KEY_1: _customer(1), SVC_KEY_2: _customer(2)}


async def _lookup(key):
    return CUSTOMERS.get(key)


def _secret():
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


@pytest.fixture
def env(tmp_path):
    ledgers = str(tmp_path / "ledgers.db")
    billing_db = str(tmp_path / "billing.db")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    usage, savings = UsageTrackerService(ledgers), SavingsTrackerService(ledgers)
    budget, receipts = BudgetAlertService(ledgers), DispatchReceiptStore(ledgers)
    for s in (usage, savings, budget, receipts):
        loop.run_until_complete(s.initialize())
    store = ProviderKeyStore(billing_db, _secret())
    loop.run_until_complete(store.initialize())
    executor = FakeExecutor()
    holder = {"store": store, "factory": lambda spec, t: executor}
    aggregator = MagicMock()
    aggregator.get_all_pricing_async = AsyncMock(return_value=(_catalogue(), []))
    svc = DispatchService(
        aggregator=aggregator, router=ModelRouter(aggregator), usage_tracker=usage, savings_tracker=savings,
        budget_alerts=budget, receipts=receipts,
        executor_factory=lambda spec, t: holder["factory"](spec, t),
        default_max_tokens=100, attempt_timeout_ms=2000, max_attempts=3,
    )
    billing = MagicMock()
    billing.get_customer_by_api_key = AsyncMock(side_effect=_lookup)

    def _get_store():
        if holder["store"] is None:
            raise RuntimeError("ProviderKeyStore has not been initialized")
        return holder["store"]

    main_module._rate_limit_store.clear()
    with patch("src.main.get_billing_service", return_value=billing), \
         patch("src.main.get_dispatch_service", return_value=svc), \
         patch("src.main.get_dispatch_receipt_store", return_value=receipts), \
         patch("src.main.get_provider_key_store", side_effect=_get_store), \
         patch.object(main_module.settings, "rate_limit_per_minute", 0):
        yield {"ledgers": ledgers, "billing_db": billing_db, "store": store, "holder": holder,
               "executor": executor, "svc": svc, "loop": loop, "receipts": receipts}
    main_module._rate_limit_store.clear()
    loop.close()


def _run(env, coro):
    return env["loop"].run_until_complete(coro)


def _register(provider="OpenAI", api_key=STORED, svc_key=SVC_KEY_1):
    return client.post("/dispatch/keys", json={"provider": provider, "api_key": api_key},
                       headers={"x-api-key": svc_key} if svc_key else {})


def _list(svc_key=SVC_KEY_1):
    return client.get("/dispatch/keys", headers={"x-api-key": svc_key} if svc_key else {})


def _rotate(provider="openai", api_key=STORED_NEW, svc_key=SVC_KEY_1):
    return client.put(f"/dispatch/keys/{provider}", json={"api_key": api_key},
                      headers={"x-api-key": svc_key} if svc_key else {})


def _delete(provider="openai", svc_key=SVC_KEY_1):
    return client.delete(f"/dispatch/keys/{provider}", headers={"x-api-key": svc_key} if svc_key else {})


def _dispatch(policy=None, headers=None, svc_key=SVC_KEY_1, stream=False):
    body = {"model": "gpt-4", "messages": [{"role": "user", "content": "hi there"}],
            "dispatch": {"objective": "cheapest", **(policy or {})}}
    if stream:
        body["stream"] = True
    h = {"x-api-key": svc_key}
    h.update(headers or {})
    return client.post("/v1/chat/completions", json=body, headers=h)


def _parse_sse(text):
    out = []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            data = block[len("data: "):]
            out.append(data if data == "[DONE]" else json.loads(data))
    return out


def _raw_bytes(db_path):
    out = b""
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(db_path + suffix)
        if p.exists():
            out += p.read_bytes()
    return out


def _db_rows(db_path, table):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(f"SELECT * FROM {table}")]  # nosec B608 — test-only
    con.close()
    return rows


# ---------------------------------------------------------------------------
# AC1 / AC2 — register and list
# ---------------------------------------------------------------------------

def test_ac1_register_returns_metadata_only(env):
    before = time.time()
    resp = _register()
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["provider"] == "OpenAI" and body["slug"] == "openai"
    assert body["fingerprint"] == STORED[-4:]
    assert body["created_at"].endswith("Z") and body["rotated_at"] is None and body["last_used_at"] is None
    assert body["status"] == "active"
    assert STORED not in resp.text
    created = _run(env, env["store"].list("org-1"))[0].created_at
    assert created >= before - 1


def _assert_no_key_substring(text, key, longer_than=4):
    for i in range(len(key) - longer_than):
        piece = key[i:i + longer_than + 1]
        assert piece not in text, f"substring {piece!r} of the key leaked"


def test_ac2_list_after_register(env):
    _register()
    resp = _list()
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1 and len(body["keys"]) == 1
    entry = body["keys"][0]
    assert entry["provider"] == "OpenAI" and entry["fingerprint"] == STORED[-4:]
    assert entry["created_at"] and entry["last_used_at"] is None
    _assert_no_key_substring(resp.text, STORED)


def test_register_accepts_slug_and_strips_whitespace(env):
    resp = _register(provider="together", api_key="  " + STORED + "  ")
    assert resp.status_code == 201 and resp.json()["provider"] == "Together AI"
    assert _run(env, env["store"].load_for_dispatch("org-1"))["Together AI"].value == STORED


def test_register_twice_is_409_and_keeps_first(env):
    _register()
    resp = _register(api_key=STORED_NEW)
    assert resp.status_code == 409 and resp.json()["error"] == "provider_key_exists"
    assert STORED_NEW not in resp.text
    assert _run(env, env["store"].load_for_dispatch("org-1"))["OpenAI"].value == STORED


# ---------------------------------------------------------------------------
# AC3 / AC4 — validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["Cohere", "unknown-provider", "", None, 7])
def test_ac3_unsupported_provider_rejected(env, provider):
    resp = _register(provider=provider)
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "unsupported_provider"
    names = {a["provider"] for a in body["accepted_providers"]}
    assert names == {"OpenAI", "Anthropic", "Google", "Groq", "Together AI", "Fireworks AI", "DeepSeek",
                     "Mistral AI", "xAI"}
    assert "OpenAI" in body["message"] and STORED not in resp.text
    assert _db_rows(env["billing_db"], "provider_keys") == []


@pytest.mark.parametrize("value", ["", "   ", "\t\n", None, 12345, "x" * 513, "two words-here-Zq9X"])
def test_ac4_bad_key_values_rejected(env, value):
    resp = _register(api_key=value)
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_provider_key"
    if isinstance(value, str) and value.strip():
        assert value.strip() not in resp.text
    assert _db_rows(env["billing_db"], "provider_keys") == []


def test_missing_fields_and_bad_json(env):
    r1 = client.post("/dispatch/keys", json={"provider": "OpenAI"}, headers={"x-api-key": SVC_KEY_1})
    assert r1.status_code == 422 and r1.json()["error"] == "invalid_provider_key"
    r2 = client.post("/dispatch/keys", json={"api_key": STORED}, headers={"x-api-key": SVC_KEY_1})
    assert r2.status_code == 422 and r2.json()["error"] == "unsupported_provider" and STORED not in r2.text
    r3 = client.post("/dispatch/keys", content=b"{not json " + STORED.encode(),
                     headers={"x-api-key": SVC_KEY_1, "content-type": "application/json"})
    assert r3.status_code == 400 and r3.json()["error"] == "invalid_json" and STORED not in r3.text
    r4 = client.post("/dispatch/keys", json=["OpenAI", STORED], headers={"x-api-key": SVC_KEY_1})
    assert r4.status_code == 400 and STORED not in r4.text
    r5 = client.put("/dispatch/keys/openai", json={}, headers={"x-api-key": SVC_KEY_1})
    assert r5.status_code == 422
    assert _db_rows(env["billing_db"], "provider_keys") == []


# ---------------------------------------------------------------------------
# AC5 / AC6 / AC7 — rotate, delete, missing
# ---------------------------------------------------------------------------

def test_ac5_rotate_replaces_value_used_by_dispatch(env):
    _register()
    resp = _rotate()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["fingerprint"] == STORED_NEW[-4:] and body["rotated_at"] and STORED_NEW not in resp.text
    keys = _list().json()["keys"]
    assert [(k["provider"], k["fingerprint"]) for k in keys] == [("OpenAI", STORED_NEW[-4:])]
    d = _dispatch({"allow": ["provider:openai"]})
    assert d.status_code == 200, d.text
    sent = [k for _, k in env["executor"].calls]
    assert sent == [STORED_NEW] and STORED not in sent


def test_rotate_accepts_display_name_path(env):
    _register(provider="Fireworks AI")
    resp = client.put("/dispatch/keys/Fireworks%20AI", json={"api_key": STORED_NEW}, headers={"x-api-key": SVC_KEY_1})
    assert resp.status_code == 200 and resp.json()["slug"] == "fireworks"


def test_ac6_delete_stops_dispatch_use(env):
    _register()
    resp = _delete()
    assert resp.status_code == 204 and resp.content == b""
    assert _list().json() == {"keys": [], "count": 0}
    d = _dispatch({"allow": ["provider:openai"]})
    assert d.status_code == 400
    body = d.json()
    assert body["error"] == "no_eligible_candidate" and body["eliminating_constraint"] == "provider_key"
    assert {e["reason"] for e in body["excluded"] if e["provider"] == "OpenAI"} == {"no_provider_key"}
    assert env["executor"].calls == []


def test_ac7_rotate_or_delete_unknown_is_404(env):
    for resp in (_rotate(), _delete()):
        assert resp.status_code == 404 and resp.json()["error"] == "provider_key_not_found"
    _register(provider="Groq")
    assert _rotate(provider="openai").status_code == 404
    assert _delete(provider="anthropic").status_code == 404


def test_unknown_provider_in_path_is_422(env):
    for resp in (_rotate(provider="cohere"), _delete(provider="cohere")):
        assert resp.status_code == 422 and resp.json()["error"] == "unsupported_provider"


# ---------------------------------------------------------------------------
# AC8 — authentication
# ---------------------------------------------------------------------------

def _all_management(svc_key):
    return [_register(svc_key=svc_key), _list(svc_key=svc_key), _rotate(svc_key=svc_key),
            _delete(svc_key=svc_key)]


def test_ac8_refused_without_customer_key(env):
    _register()  # org-1's key must survive every refused call untouched
    before = _db_rows(env["billing_db"], "provider_keys")
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = GLOBAL_KEY
    try:
        for resp in _all_management(None) + _all_management("not-a-real-key"):
            assert resp.status_code == 401
        for resp in _all_management(GLOBAL_KEY):
            assert resp.status_code == 403 and resp.json()["error"] == "customer_key_required"
    finally:
        main_module.settings.mcp_api_key = original
    main_module.settings.mcp_api_key = None
    try:
        for resp in _all_management(None):  # anonymous when no global key is configured
            assert resp.status_code == 403
    finally:
        main_module.settings.mcp_api_key = original
    rows = _db_rows(env["billing_db"], "provider_keys")
    assert rows == before and [(r["org_id"], r["provider"]) for r in rows] == [("org-1", "OpenAI")]
    assert _run(env, env["store"].load_for_dispatch("org-1"))["OpenAI"].value == STORED


def test_bearer_header_is_not_a_management_credential(env):
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = GLOBAL_KEY
    try:
        resp = client.get("/dispatch/keys", headers={"Authorization": f"Bearer {SVC_KEY_1}"})
        assert resp.status_code == 401
    finally:
        main_module.settings.mcp_api_key = original


# ---------------------------------------------------------------------------
# AC9 — org isolation
# ---------------------------------------------------------------------------

def test_ac9_org_isolation(env):
    assert _register(provider="Anthropic").status_code == 201
    assert all(k["provider"] != "Anthropic" for k in _list(svc_key=SVC_KEY_2).json()["keys"])
    assert _delete(provider="anthropic", svc_key=SVC_KEY_2).status_code == 404
    assert _rotate(provider="anthropic", svc_key=SVC_KEY_2).status_code == 404
    org1 = _list().json()["keys"]
    assert [(k["provider"], k["fingerprint"], k["rotated_at"]) for k in org1] == [("Anthropic", STORED[-4:], None)]
    assert _run(env, env["store"].load_for_dispatch("org-1"))["Anthropic"].value == STORED

    d = _dispatch({"min_quality_score": 70}, headers={"X-Provider-Key-OpenAI": HEADER}, svc_key=SVC_KEY_2)
    assert d.status_code == 200, d.text
    excluded = {e["model"]: e["reason"] for e in d.json()["dispatch_receipt"]["excluded"]}
    assert excluded["model-b"] == "no_provider_key"
    assert all(k != STORED for _, k in env["executor"].calls)


# ---------------------------------------------------------------------------
# AC10 / AC11 — stored key used / header wins (real executor over a mocked transport)
# ---------------------------------------------------------------------------

def _mock_transport(env, seen):
    def handler(request: httpx.Request):
        seen.append({"host": request.url.host, "auth": request.headers.get("authorization"),
                     "x-api-key": request.headers.get("x-api-key")})
        if "anthropic" in request.url.host:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}], "stop_reason": "end_turn",
                                             "usage": {"input_tokens": 5, "output_tokens": 2}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    transport = httpx.MockTransport(handler)
    env["holder"]["factory"] = lambda spec, t: get_executor(spec, t, transport=transport)


def test_ac10_stored_key_reaches_provider_with_only_service_key(env):
    seen = []
    _mock_transport(env, seen)
    _register(provider="Groq")
    resp = _dispatch()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dispatch_receipt"]["chosen"]["provider"] == "Groq"
    assert seen == [{"host": "api.groq.com", "auth": f"Bearer {STORED}", "x-api-key": None}]
    assert body["dispatch_receipt"]["attempts"][0]["key_source"] == "stored"
    assert STORED not in resp.text and STORED[-4:] not in json.dumps(body["dispatch_receipt"])


def test_ac11_header_wins_over_stored(env):
    seen = []
    _mock_transport(env, seen)
    _register(provider="OpenAI")
    resp = _dispatch({"allow": ["provider:openai"]}, headers={"X-Provider-Key-OpenAI": HEADER})
    assert resp.status_code == 200, resp.text
    assert [s["auth"] for s in seen] == [f"Bearer {HEADER}"]
    assert resp.json()["dispatch_receipt"]["attempts"][0]["key_source"] == "header"


def test_header_wins_and_auth_error_is_not_retried_with_stored_key(env):
    _register(provider="OpenAI")
    env["executor"].script["model-a"] = "auth_error"
    resp = _dispatch({"allow": ["provider:openai"]}, headers={"X-Provider-Key-OpenAI": HEADER})
    assert resp.status_code == 400 and resp.json()["error"] == "provider_auth_failed"
    assert env["executor"].calls == [("model-a", HEADER)]
    body = resp.json()
    assert body["key_source"] == "header" and "X-Provider-Key-OpenAI" in body["message"]
    assert _list().json()["keys"][0]["last_used_at"] is None   # the stored key was never used


def test_stored_key_auth_error_message_points_at_rotation(env):
    _register(provider="OpenAI")
    env["executor"].script["model-a"] = "auth_error"
    resp = _dispatch({"allow": ["provider:openai"]})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "provider_auth_failed" and body["key_source"] == "stored"
    assert "stored provider key" in body["message"] and "PUT /dispatch/keys/openai" in body["message"]
    assert body["attempts"][0]["key_source"] == "stored"
    assert STORED not in resp.text and STORED[-4:] not in resp.text
    assert env["executor"].calls == [("model-a", STORED)]


# ---------------------------------------------------------------------------
# AC12 / AC13 — key source per attempt, everywhere
# ---------------------------------------------------------------------------

def test_ac12_mixed_sources_across_failover(env):
    _register(provider="OpenAI")
    env["executor"].script["model-b"] = "server_error"
    resp = _dispatch({"allow": ["provider:anthropic", "provider:openai"]},
                     headers={"X-Provider-Key-Anthropic": HEADER})
    assert resp.status_code == 200, resp.text
    attempts = resp.json()["dispatch_receipt"]["attempts"]
    assert [(a["provider"], a["outcome"], a["key_source"]) for a in attempts] == [
        ("Anthropic", "server_error", "header"), ("OpenAI", "ok", "stored"),
    ]
    assert env["executor"].calls == [("model-b", HEADER), ("model-a", STORED)]


def test_ac13_key_source_in_stream_and_exports(env):
    _register(provider="Groq")
    plain = _dispatch()
    assert plain.json()["dispatch_receipt"]["attempts"][0]["key_source"] == "stored"

    streamed = _dispatch(stream=True)
    assert streamed.status_code == 200
    events = _parse_sse(streamed.text)
    trailing = [e for e in events if isinstance(e, dict) and e.get("dispatch_receipt")]
    assert len(trailing) == 1 and events[-1] == "[DONE]"
    assert trailing[0]["dispatch_receipt"]["attempts"][0]["key_source"] == "stored"
    assert STORED not in streamed.text

    j = client.get("/dispatch/receipts", headers={"x-api-key": SVC_KEY_1})
    assert j.status_code == 200
    rows = j.json()["receipts"]
    assert len(rows) == 2
    for row in rows:
        assert [a["key_source"] for a in json.loads(row["attempts_json"])] == ["stored"]
    c = client.get("/dispatch/receipts?format=csv", headers={"x-api-key": SVC_KEY_1})
    csv_rows = list(csv.DictReader(io.StringIO(c.text)))
    assert len(csv_rows) == 2
    assert all(json.loads(r["attempts_json"])[0]["key_source"] == "stored" for r in csv_rows)
    for text in (j.text, c.text):
        assert STORED not in text and STORED[-4:] not in text


def test_shadow_report_carries_key_source(env):
    _register(provider="Groq")
    resp = _dispatch({"min_quality_score": 70, "shadow": {"model": "model-b"}},
                     headers={"X-Provider-Key-Anthropic": HEADER})
    assert resp.status_code == 200, resp.text
    r = resp.json()["dispatch_receipt"]
    assert r["attempts"][0]["key_source"] == "stored"
    assert r["shadow"]["model"] == "model-b" and r["shadow"]["key_source"] == "header"


def test_stream_failover_records_both_sources(env):
    _register(provider="OpenAI")
    env["executor"].script["model-b"] = "rate_limited"
    resp = _dispatch({"allow": ["provider:anthropic", "provider:openai"]},
                     headers={"X-Provider-Key-Anthropic": HEADER}, stream=True)
    assert resp.status_code == 200
    trailing = [e for e in _parse_sse(resp.text) if isinstance(e, dict) and e.get("dispatch_receipt")][0]
    assert [a["key_source"] for a in trailing["dispatch_receipt"]["attempts"]] == ["header", "stored"]


# ---------------------------------------------------------------------------
# AC14 — last_used
# ---------------------------------------------------------------------------

def _last_used(provider):
    return {k["provider"]: k["last_used_at"] for k in _list().json()["keys"]}[provider]


def _ts(iso):
    from datetime import datetime
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def test_ac14_last_used_on_success_and_failure_only_for_used_key(env):
    _register(provider="Groq")
    _register(provider="OpenAI")
    t0 = time.time() - 0.01
    assert _dispatch().status_code == 200                      # cheapest = Groq, stored
    assert _ts(_last_used("Groq")) >= t0
    assert _last_used("OpenAI") is None                        # unused stored key untouched

    first = _ts(_last_used("Groq"))
    env["executor"].script["model-a"] = "auth_error"
    t1 = time.time() - 0.01
    resp = _dispatch({"allow": ["provider:openai"]})           # failed attempt with the stored key
    assert resp.status_code == 400
    assert _ts(_last_used("OpenAI")) >= t1
    assert _ts(_last_used("Groq")) == pytest.approx(first, abs=0.002)


def test_ac14_streaming_updates_last_used(env):
    _register(provider="Groq")
    t0 = time.time() - 0.01
    assert _dispatch(stream=True).status_code == 200
    assert _ts(_last_used("Groq")) >= t0


def test_last_used_store_failure_never_fails_dispatch(env, monkeypatch):
    _register(provider="Groq")

    async def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(env["store"], "touch_last_used", boom)
    assert _dispatch().status_code == 200


def test_store_load_failure_falls_back_to_header_only(env, monkeypatch):
    _register(provider="Groq")

    async def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(env["store"], "load_for_dispatch", boom)
    resp = _dispatch({"min_quality_score": 70}, headers={"X-Provider-Key-OpenAI": HEADER})
    assert resp.status_code == 200
    assert resp.json()["dispatch_receipt"]["chosen"]["provider"] == "OpenAI"


# ---------------------------------------------------------------------------
# AC15 / AC16 — nothing in plaintext, nothing in logs/receipts
# ---------------------------------------------------------------------------

def test_ac15_raw_db_bytes_hold_no_plaintext(env):
    _register(provider="OpenAI")
    _register(provider="Groq", api_key=STORED_NEW)
    _dispatch()
    raw = _raw_bytes(env["billing_db"]) + _raw_bytes(env["ledgers"])
    for key in (STORED, STORED_NEW):
        assert key.encode() not in raw and key.encode("utf-16-le") not in raw
    _rotate(provider="openai", api_key=HEADER)
    raw = _raw_bytes(env["billing_db"])
    assert HEADER.encode() not in raw


def test_ac16_no_key_material_in_logs_receipts_or_exports(env, caplog):
    caplog.set_level(logging.DEBUG)
    responses = [_register(provider="OpenAI"), _register(provider="Groq", api_key=STORED_NEW)]
    responses.append(_dispatch())                                         # success on stored Groq
    env["executor"].script["model-g"] = "server_error"
    responses.append(_dispatch({"min_quality_score": 70}))                # failover Groq -> OpenAI
    env["executor"].script["model-a"] = "auth_error"
    responses.append(_dispatch({"allow": ["provider:openai"]}))           # auth_error on stored OpenAI
    responses.append(_dispatch(stream=True))
    responses.append(_rotate(provider="openai", api_key=HEADER))
    responses.append(_list())
    responses.append(_delete(provider="groq"))
    exports = [client.get("/dispatch/receipts", headers={"x-api-key": SVC_KEY_1}),
               client.get("/dispatch/receipts?format=csv", headers={"x-api-key": SVC_KEY_1})]

    logs = "\n".join(r.getMessage() for r in caplog.records) + "\n".join(str(r.args) for r in caplog.records)
    for key in ALL_SECRETS:
        assert key not in logs
        for resp in responses + exports:
            assert key not in resp.text
    for resp in exports:
        for fp in (STORED[-4:], STORED_NEW[-4:], HEADER[-4:]):
            assert fp not in resp.text
    receipts = _db_rows(env["ledgers"], "dispatch_receipts")
    assert len(receipts) == 4
    dump = json.dumps(receipts, default=str)
    for key in ALL_SECRETS:
        assert key not in dump
    for fp in (STORED[-4:], STORED_NEW[-4:]):
        assert fp not in dump


# ---------------------------------------------------------------------------
# AC17 — header-only orgs unchanged
# ---------------------------------------------------------------------------

def _shape(body):
    r = dict(body["dispatch_receipt"])
    return {
        "top": sorted(body), "receipt": sorted(r), "chosen": r["chosen"]["model"],
        "attempts": [{k: v for k, v in a.items() if k != "latency_ms"} for a in r["attempts"]],
        "excluded": r["excluded"],
    }


def test_ac17_header_only_org_sees_only_the_added_field(env):
    headers = {"X-Provider-Key-OpenAI": HEADER, "X-Provider-Key-Anthropic": HEADER}
    with_store = _dispatch({"min_quality_score": 70}, headers=headers, svc_key=SVC_KEY_2)
    env["holder"]["store"] = None   # as before this feature: no store at all
    without_store = _dispatch({"min_quality_score": 70}, headers=headers, svc_key=SVC_KEY_2)
    assert with_store.status_code == without_store.status_code == 200
    assert _shape(with_store.json()) == _shape(without_store.json())
    attempts = with_store.json()["dispatch_receipt"]["attempts"]
    assert set(attempts[0]) == {"n", "model", "provider", "outcome", "http_status", "latency_ms", "key_source"}
    assert attempts[0]["key_source"] == "header"


def test_other_orgs_stored_keys_do_not_change_header_only_dispatch(env):
    _register(provider="Groq")   # org-1's key
    resp = _dispatch({"min_quality_score": 70}, headers={"X-Provider-Key-OpenAI": HEADER}, svc_key=SVC_KEY_2)
    assert resp.json()["dispatch_receipt"]["chosen"]["provider"] == "OpenAI"


def test_plain_proxy_ignores_stored_keys(env):
    _register(provider="Groq")
    resp = client.post("/v1/chat/completions", json={"model": "gpt-4", "messages": [{"role": "user", "content": "x"}]},
                       headers={"x-api-key": SVC_KEY_1})
    assert resp.status_code == 200
    assert resp.json()["routing_metadata"]["actual_forwarding"] is False
    assert env["executor"].calls == []


# ---------------------------------------------------------------------------
# AC18 / AC19 — messages and the public provider list
# ---------------------------------------------------------------------------

def test_ac18_no_key_from_either_source(env):
    resp = _dispatch()
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "no_eligible_candidate" and body["eliminating_constraint"] == "provider_key"
    assert "X-Provider-Key-<Provider>" in body["message"] and "POST /dispatch/keys" in body["message"]
    assert "keys present for" not in body["message"]


def test_ac19_providers_public_documents_both_ways_and_hides_org_state(env):
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = GLOBAL_KEY
    try:
        before = client.get("/dispatch/providers")
        _register(provider="OpenAI")
        after_anon = client.get("/dispatch/providers")
        after_customer = client.get("/dispatch/providers", headers={"x-api-key": SVC_KEY_1})
    finally:
        main_module.settings.mcp_api_key = original
    assert before.status_code == after_anon.status_code == after_customer.status_code == 200
    assert before.json() == after_anon.json() == after_customer.json()
    body = before.json()
    rows = {r["provider"]: r for r in body["executable_providers"]}
    assert rows["Together AI"]["slug"] == "together"
    assert rows["Together AI"]["stored_key_path"] == "/dispatch/keys/together"
    assert rows["OpenAI"]["key_header"] == "X-Provider-Key-OpenAI"
    assert "POST /dispatch/keys" in body["note"] and "key_header" in body["note"]


# ---------------------------------------------------------------------------
# AC20 — no secret / different secret
# ---------------------------------------------------------------------------

def test_ac20_no_secret_management_503_dispatch_header_only(env):
    env["holder"]["store"] = ProviderKeyStore(env["billing_db"], None)
    for resp in _all_management(SVC_KEY_1):
        assert resp.status_code == 503 and resp.json()["error"] == "stored_keys_unavailable"
        assert "PROVIDER_KEY_ENCRYPTION_KEY" in resp.json()["message"]
    d = _dispatch({"min_quality_score": 70}, headers={"X-Provider-Key-OpenAI": HEADER})
    assert d.status_code == 200 and d.json()["dispatch_receipt"]["attempts"][0]["key_source"] == "header"


def test_uninitialized_store_is_503_and_dispatch_header_only(env):
    env["holder"]["store"] = None
    assert _list().status_code == 503
    d = _dispatch({"min_quality_score": 70}, headers={"X-Provider-Key-OpenAI": HEADER})
    assert d.status_code == 200


def test_auth_checked_before_availability(env):
    env["holder"]["store"] = ProviderKeyStore(env["billing_db"], None)
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = GLOBAL_KEY
    try:
        assert _list(svc_key=GLOBAL_KEY).status_code == 403
    finally:
        main_module.settings.mcp_api_key = original


def test_ac20_changed_secret_needs_reregistration_and_never_sends(env):
    _register(provider="OpenAI")
    env["holder"]["store"] = ProviderKeyStore(env["billing_db"], _secret())
    keys = _list().json()["keys"]
    assert [(k["provider"], k["status"]) for k in keys] == [("OpenAI", "needs_reregistration")]
    d = _dispatch({"allow": ["provider:openai"]})
    assert d.status_code == 400 and d.json()["eliminating_constraint"] == "provider_key"
    assert env["executor"].calls == []
    # Re-registration = rotate under the new secret.
    assert _rotate(provider="openai", api_key=STORED_NEW).status_code == 200
    assert _list().json()["keys"][0]["status"] == "active"
    assert _dispatch({"allow": ["provider:openai"]}).status_code == 200
    assert env["executor"].calls == [("model-a", STORED_NEW)]


def test_ac20_tampered_row_is_never_sent(env):
    _register(provider="OpenAI")
    con = sqlite3.connect(env["billing_db"])
    blob = con.execute("SELECT ciphertext FROM provider_keys").fetchone()[0]
    tampered = blob[:-1] + bytes([blob[-1] ^ 0x01])
    con.execute("UPDATE provider_keys SET ciphertext = ?", (tampered,))
    con.commit()
    con.close()
    assert _list().json()["keys"][0]["status"] == "needs_reregistration"
    d = _dispatch({"allow": ["provider:openai"]})
    assert d.status_code == 400 and env["executor"].calls == []
