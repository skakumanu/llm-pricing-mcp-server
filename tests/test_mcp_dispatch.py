"""v1.80.0 — the MCP `dispatch` tool: a real gateway call over POST /mcp with the caller's own
stored provider keys, through the same pre-dispatch pipeline as HTTP dispatch.

The billing service is patched to resolve two customer keys (two orgs); org A has one stored
OpenAI key in a real ProviderKeyStore over tmp_path; the dispatch service is a real
DispatchService over tmp_path ledgers with a scripted executor, patched into BOTH src.main (HTTP)
and src.services.dispatch_entry (MCP), so HTTP and MCP runs can be compared against the same mock.
Nothing touches the network. Test names follow the Spec's acceptance criteria.
"""
import asyncio
import base64
import json
import logging
import os
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
from src.models.dispatch import DispatchPolicy, FailoverPolicy, ShadowPolicy  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.billing_service import CustomerRecord  # noqa: E402
from src.services.budget_alerts import BudgetAlertService  # noqa: E402
from src.services.dispatch_executors import ChatResult, ExecutorError  # noqa: E402
from src.services.dispatch_receipts import DispatchReceiptStore  # noqa: E402
from src.services.dispatch_service import DispatchService  # noqa: E402
from src.services.provider_key_store import ProviderKeyStore  # noqa: E402
from src.services.router import ModelRouter  # noqa: E402
from src.services.savings_tracker import SavingsTrackerService  # noqa: E402
from src.services.usage_tracker import UsageTrackerService  # noqa: E402
from mcp.caller import ANONYMOUS, CUSTOMER_ONLY, McpCaller  # noqa: E402
from mcp.server import MCPServer  # noqa: E402
from mcp.tools.dispatch import DispatchTool  # noqa: E402
from mcp.tools.tool_manager import TRANSPORT_STDIO, ToolManager  # noqa: E402
from agent.tools import build_agent_tools  # noqa: E402

client = TestClient(app)

# Assembled at runtime so secret scanners do not flag the fixtures; none is a credential.
STORED_KEY = "-".join(["sk", "test", "STORED", "SENTINEL"])
SVC_KEY_A = "-".join(["svc", "mcpd", "orgA", "8c1e"])
SVC_KEY_B = "-".join(["svc", "mcpd", "orgB", "2b7f"])
OPERATOR_KEY = "-".join(["op", "mcpd", "global", "e04d"])
ORG_A, ORG_B = "org-mcpd-a", "org-mcpd-b"
PROMPT = "PROMPT-SENTINEL-MCPD tell me something"
COMPLETION = "COMPLETION-SENTINEL-MCPD"
_STATUS = {"timeout": None, "server_error": 503, "rate_limited": 429, "auth_error": 401}


def _pm(name, provider, quality, blended_per_1m):
    per_token = blended_per_1m / 1_000_000
    m = PricingMetrics(model_name=name, provider=provider, cost_per_input_token=per_token,
                       cost_per_output_token=per_token, context_window=128_000, price_confirmed=True,
                       price_as_of="2026-10-01")
    m.quality_score = quality
    return m


def _catalogue():
    return [
        _pm("model-a", "OpenAI", 85, 10.0),
        _pm("model-e", "OpenAI", 80, 5.0),
        _pm("model-b", "Anthropic", 72, 1.0),
        _pm("model-c", "Google", 60, 0.20),
    ]


class FakeExecutor:
    """Scripted executor: ``script[model]`` is 'ok' or a failure outcome raised before any result."""

    def __init__(self):
        self.script = {}
        self.calls = []

    async def execute(self, req, api_key):
        self.calls.append((req.model, api_key))
        outcome = self.script.get(req.model, "ok")
        if outcome == "ok":
            return ChatResult(COMPLETION, "stop", 100, 50, 9.0, 200, req.model)
        raise ExecutorError(outcome, _STATUS.get(outcome))

    async def stream(self, req, api_key):  # pragma: no cover — MCP never streams
        raise AssertionError("MCP dispatch must never stream")
        yield  # noqa


def _customer(key, org, n):
    return CustomerRecord(id=f"cust-mcpd-{n}", email=f"m{n}@example.com", stripe_customer_id=None,
                          stripe_subscription_id=None, api_key=key, tier="free", org_id=org,
                          created_at=time.time(), updated_at=time.time())


CUSTOMERS = {SVC_KEY_A: _customer(SVC_KEY_A, ORG_A, 1), SVC_KEY_B: _customer(SVC_KEY_B, ORG_B, 2)}
CUST_A_ID = CUSTOMERS[SVC_KEY_A].id


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
    loop.run_until_complete(store.register(ORG_A, "OpenAI", STORED_KEY))
    executor = FakeExecutor()
    aggregator = MagicMock()
    aggregator.get_all_pricing_async = AsyncMock(return_value=(_catalogue(), []))
    svc = DispatchService(
        aggregator=aggregator, router=ModelRouter(aggregator), usage_tracker=usage, savings_tracker=savings,
        budget_alerts=budget, receipts=receipts, executor_factory=lambda spec, t: executor,
        default_max_tokens=100, attempt_timeout_ms=2000, max_attempts=3,
    )
    billing = MagicMock()
    billing.get_customer_by_api_key = AsyncMock(side_effect=_lookup)
    telemetry = MagicMock()
    holder = {"svc": svc}

    def _get_svc():
        if holder["svc"] is None:
            raise RuntimeError("DispatchService has not been initialized")
        return holder["svc"]

    main_module._rate_limit_store.clear()
    patches = [
        patch("src.main.get_billing_service", return_value=billing),
        patch("src.main.get_dispatch_service", side_effect=_get_svc),
        patch("src.services.dispatch_entry.get_dispatch_service", side_effect=_get_svc),
        patch("src.main.get_dispatch_receipt_store", return_value=receipts),
        patch("src.main.get_provider_key_store", return_value=store),
        patch("src.services.dispatch_entry.get_provider_key_store", return_value=store),
        patch("src.services.dispatch_entry.get_telemetry_service", return_value=telemetry),
        patch("src.services.dispatch_service.count_tokens", lambda text: max(1, len(text.split()))),
        patch.object(main_module.settings, "mcp_api_key", OPERATOR_KEY),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(ledgers=ledgers, billing_db=billing_db, usage=usage, savings=savings, budget=budget,
                              receipts=receipts, store=store, executor=executor, svc=svc, holder=holder,
                              loop=loop, billing=billing, telemetry=telemetry)
    finally:
        for p in reversed(patches):
            p.stop()
        main_module._rate_limit_store.clear()
        loop.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _run(env, coro):
    return env.loop.run_until_complete(coro)


def _rpc(method, params=None, key=None, bearer=False, raw=None):
    headers = {}
    if key:
        headers = {"Authorization": f"Bearer {key}"} if bearer else {"x-api-key": key}
    if raw is not None:
        return client.post("/mcp", content=raw, headers={**headers, "content-type": "application/json"})
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def _call(arguments, key=SVC_KEY_A, bearer=False):
    resp = _rpc("tools/call", {"name": "dispatch", "arguments": arguments}, key=key, bearer=bearer)
    assert resp.status_code == 200, resp.text
    return json.loads(resp.json()["result"]["content"][0]["text"])


def _args(policy=None, **extra):
    args = {"prompt": PROMPT, "dispatch": {"objective": "cheapest", **(policy or {})}}
    args.update(extra)
    return args


def _http(policy=None, key=SVC_KEY_A, extra=None, headers=None):
    body = {"model": "auto", "messages": [{"role": "user", "content": PROMPT}],
            "dispatch": {"objective": "cheapest", **(policy or {})}}
    body.update(extra or {})
    h = {"x-api-key": key} if key else {}
    h.update(headers or {})
    return client.post("/v1/chat/completions", json=body, headers=h)


def _db_rows(db_path, table):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(f"SELECT * FROM {table}").fetchall()  # nosec B608 — test-only
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _ledger_counts(env):
    return {t: len(_db_rows(env.ledgers, t))
            for t in ("dispatch_receipts", "usage_events", "routing_feedback", "routing_savings")}


def _assert_nothing_written(env):
    assert all(n == 0 for n in _ledger_counts(env).values()), _ledger_counts(env)


def _dump_all(db_path):
    out = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = Path(db_path + suffix)
        if p.exists():
            out.append(p.read_bytes().decode("latin-1"))
    con = sqlite3.connect(db_path)
    for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for row in con.execute(f"SELECT * FROM {name}"):  # nosec B608 — test-only table dump
            out.append(f"{name}:{row!r}")
    con.close()
    return "\n".join(out)


_VOLATILE = ("dispatch_id", "id", "created", "created_at", "routing_id")


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items()
                if k not in _VOLATILE and "latency" not in k and not k.endswith("_ms")}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def _mcp_error_payload(result):
    """The MCP result minus the two transport fields, for comparison with the HTTP body."""
    return {k: v for k, v in result.items() if k not in ("success", "status_code")}


# ---------------------------------------------------------------------------
# AC: tools/list includes dispatch; 31 tools (v1.81.0: + manage_price_alert)
# ---------------------------------------------------------------------------

def test_tools_list_includes_dispatch_and_has_31(env):
    tools = _rpc("tools/list").json()["result"]["tools"]
    assert len(tools) == 32  # v1.84.0: + support_chat
    by_name = {t["name"]: t for t in tools}
    desc = by_name["dispatch"]["description"]
    assert "REAL model call" in desc and "your own provider keys" in desc
    assert "service API key" in desc
    assert "dispatch.failover.timeout_ms" in desc and "dispatch.failover.max_attempts" in desc
    assert "LLM_PRICING_MCP_TIMEOUT_MS=120000" in desc
    env.billing.get_customer_by_api_key.assert_not_awaited()


def test_dispatch_schema_has_no_identity_or_key_arguments(env):
    props = ToolManager().get_tool("dispatch")["input_schema"]["properties"]
    assert set(props) == {"prompt", "system", "messages", "model", "max_tokens", "temperature", "top_p",
                          "stop", "dispatch"}
    for forbidden in ("org_id", "api_key", "customer_id", "stream", "provider_keys"):
        assert forbidden not in props
    assert ToolManager().get_tool("dispatch")["input_schema"]["required"] == []


def test_dispatch_policy_schema_mirrors_the_pydantic_models(env):
    """Drift guard: the hand-written inline schema has exactly the models' fields."""
    schema = ToolManager().get_tool("dispatch")["input_schema"]["properties"]["dispatch"]
    assert "$ref" not in json.dumps(schema)
    assert set(schema["properties"]) == set(DispatchPolicy.model_fields)
    assert set(schema["properties"]["failover"]["properties"]) == set(FailoverPolicy.model_fields)
    assert set(schema["properties"]["shadow"]["properties"]) == set(ShadowPolicy.model_fields)
    assert set(schema["properties"]["objective"]["enum"]) == {
        "balanced", "cheapest", "best_quality", "lowest_latency", "pinned"}
    assert "pinned" not in schema["properties"]["shadow"]["properties"]["objective"]["enum"]


# ---------------------------------------------------------------------------
# AC: agent exclusion
# ---------------------------------------------------------------------------

def test_agent_does_not_bind_dispatch_and_still_binds_27(env):
    tm = ToolManager()
    bound = {t.name for t in build_agent_tools(tm, rag_pipeline=None)}
    assert "dispatch" not in bound
    assert len(bound & set(tm.tools)) == 27  # v1.81.0: + manage_price_alert


def test_react_agent_run_cannot_invoke_dispatch(env):
    from agent.llm_backend import LLMResponse
    from agent.react_loop import ReActLoop

    class _Backend:
        def __init__(self):
            self.n = 0

        async def complete(self, messages, tools, system):
            self.n += 1
            assert "dispatch" not in {t["name"] for t in tools}
            if self.n == 1:
                return LLMResponse(content="", tool_calls=[{"tool": "dispatch", "id": "t1",
                                                            "args": {"prompt": PROMPT}}])
            return LLMResponse(content="done", tool_calls=[])

        def append_assistant_turn(self, messages, response):
            messages.append({"role": "assistant", "content": response.content})

        def append_tool_results(self, messages, results):
            messages.append({"role": "user", "content": json.dumps([r["result"] for r in results])})

    loop = ReActLoop(_Backend(), build_agent_tools(ToolManager(), rag_pipeline=None))
    result = _run(env, loop.run([{"role": "user", "content": "dispatch something"}]))
    assert result.tool_calls[0]["result"] == {"success": False, "error": "Unknown tool: dispatch"}
    assert env.executor.calls == []
    _assert_nothing_written(env)


def test_anonymous_in_process_execute_tool_is_refused(env):
    """Even if something called it through the ToolManager with the default (anonymous) caller."""
    out = _run(env, ToolManager().execute_tool("dispatch", {"prompt": PROMPT}))
    assert out["error"] == "dispatch_requires_api_key"
    assert env.executor.calls == []


# ---------------------------------------------------------------------------
# AC: no credential / operator key only / unknown key
# ---------------------------------------------------------------------------

def _http_401_payload():
    resp = _http(key=None)
    assert resp.status_code == 401
    return resp.json()


@pytest.mark.parametrize("key,bearer", [(None, False), (OPERATOR_KEY, False), (OPERATOR_KEY, True),
                                        ("-".join(["svc", "unknown", "0000"]), False)])
def test_no_customer_credential_is_refused_like_http_401(env, key, bearer):
    expected = _http_401_payload()
    out = _call(_args(), key=key, bearer=bearer)
    assert out["success"] is False and out["status_code"] == 401
    assert out["error"] == "dispatch_requires_api_key"
    assert _mcp_error_payload(out) == expected
    assert out["signup"] == "/billing/signup"
    assert env.executor.calls == []
    _assert_nothing_written(env)
    assert _run(env, env.store.list(ORG_A))[0].last_used_at is None


def test_anonymous_gets_api_key_refusal_before_disabled_or_unsupported(env):
    """Owner decision: anonymous/operator callers see dispatch_requires_api_key first."""
    with patch.object(main_module.settings, "dispatch_enabled", False):
        assert _call(_args(), key=None)["error"] == "dispatch_requires_api_key"
        assert _call(_args(), key=OPERATOR_KEY)["error"] == "dispatch_requires_api_key"
    assert _call(_args(tools=[{"type": "function"}]), key=None)["error"] == "dispatch_requires_api_key"


# ---------------------------------------------------------------------------
# AC: identity in arguments is never trusted
# ---------------------------------------------------------------------------

def test_org_b_in_arguments_is_rejected_and_org_b_keys_never_loaded(env):
    with patch.object(env.store, "load_for_dispatch", wraps=env.store.load_for_dispatch) as spy:
        out = _call({**_args(), "org_id": ORG_B})
        assert out["success"] is False and out["error_code"] == "org_mismatch"
        assert spy.await_count == 0
        # identity-like arguments that are not org_id are simply ignored: the run is org A's
        out = _call({**_args(), "customer_id": "cust-mcpd-2", "api_key": SVC_KEY_B})
        assert out["success"] is True
        assert [c.args[0] for c in spy.await_args_list] == [ORG_A]
    assert SVC_KEY_B not in json.dumps(out)
    rows = client.get("/dispatch/receipts?format=json", headers={"x-api-key": SVC_KEY_B}).json()
    assert rows["count"] == 0


def test_own_org_id_in_arguments_is_allowed(env):
    assert _call({**_args(), "org_id": ORG_A})["success"] is True


# ---------------------------------------------------------------------------
# AC: happy path with exactly one ledger row each
# ---------------------------------------------------------------------------

def test_happy_path_completion_usage_receipt_and_ledgers(env):
    before = time.time()
    out = _call(_args())
    assert out["success"] is True, out
    assert out["completion"] == COMPLETION and out["finish_reason"] == "stop"
    assert out["model"] == "model-e" and out["id"].startswith("chatcmpl-")
    assert out["usage"] == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    r = out["dispatch_receipt"]
    assert r["chosen"]["model"] == "model-e" and r["reason"]
    assert r["cost"]["actual_usd"] > 0 and "vs_most_expensive_eligible" in r["savings"]
    assert r["streamed"] is False
    assert [(a["model"], a["key_source"]) for a in r["attempts"]] == [("model-e", "stored")]
    assert out["routing_metadata"]["dispatch_id"] == r["dispatch_id"]
    assert env.executor.calls == [("model-e", STORED_KEY)]

    listed = client.get("/dispatch/receipts?format=json", headers={"x-api-key": SVC_KEY_A}).json()
    assert listed["count"] == 1 and listed["receipts"][0]["dispatch_id"] == r["dispatch_id"]
    assert listed["receipts"][0]["org_id"] == ORG_A
    summary = _run(env, env.usage.get_summary(org_id=ORG_A))
    assert summary["total_requests"] == 1
    feedback = _db_rows(env.ledgers, "routing_feedback")
    assert len(feedback) == 1
    con = sqlite3.connect(env.ledgers)
    row = con.execute("SELECT routing_id, was_used, actual_model_used FROM routing_feedback").fetchone()
    con.close()
    assert row == (r["dispatch_id"], 1, "model-e")
    last_used = _run(env, env.store.list(ORG_A))[0].last_used_at
    assert last_used is not None and last_used >= before - 1
    env.telemetry.track_feature_usage.assert_called_once_with("dispatch")


def test_messages_form_and_system_prompt(env):
    out = _call({"messages": [{"role": "system", "content": "be brief"}, {"role": "user", "content": PROMPT}],
                 "dispatch": {"objective": "cheapest"}, "max_tokens": 20, "temperature": 0.2, "top_p": 0.5,
                 "stop": "END"})
    assert out["success"] is True
    out = _call({"prompt": PROMPT, "system": "be brief"})  # dispatch omitted -> balanced default
    assert out["success"] is True and out["dispatch_receipt"]["objective"] == "balanced"


def test_bearer_connection_credential_works(env):
    assert _call(_args(), bearer=True)["success"] is True


# ---------------------------------------------------------------------------
# AC: parity with HTTP
# ---------------------------------------------------------------------------

def test_receipt_parity_with_http_stored_keys_no_headers(env):
    policy = {"min_quality_score": 70, "max_request_cost_usd": 5}
    http = _http(policy)
    assert http.status_code == 200, http.text
    out = _call(_args(policy))
    assert out["success"] is True
    h = http.json()
    assert _strip_volatile(out["dispatch_receipt"]) == _strip_volatile(h["dispatch_receipt"])
    assert _strip_volatile(out["routing_metadata"]) == _strip_volatile(h["routing_metadata"])
    assert out["usage"] == h["usage"]
    assert out["completion"] == h["choices"][0]["message"]["content"]
    assert env.executor.calls == [("model-e", STORED_KEY), ("model-e", STORED_KEY)]


def test_org_without_stored_keys_gets_http_no_eligible_payload(env):
    http = _http(key=SVC_KEY_B)
    out = _call(_args(), key=SVC_KEY_B)
    assert out["success"] is False and out["status_code"] == http.status_code
    assert _strip_volatile(_mcp_error_payload(out)) == _strip_volatile(http.json())
    assert env.executor.calls == []


def test_budget_hard_stop_payload_identical_to_http_402(env):
    _run(env, env.budget.register("https://example.com/hook", threshold_usd=0.0001, org_id=ORG_A, hard_stop=True))
    _run(env, env.usage.record_event("OpenAI", "model-a", 10, 10, 0.5, org_id=ORG_A))
    http = _http()
    assert http.status_code == 402
    out = _call(_args())
    assert out["success"] is False and out["status_code"] == 402 and out["error"] == "budget_hard_stop"
    assert _strip_volatile(_mcp_error_payload(out)) == _strip_volatile(http.json())
    assert env.executor.calls == []


def test_cost_cap_below_cheapest_refused_like_http(env):
    http = _http({"max_request_cost_usd": 1e-9})
    out = _call(_args({"max_request_cost_usd": 1e-9}))
    assert out["success"] is False and out["status_code"] == http.status_code
    assert _strip_volatile(_mcp_error_payload(out)) == _strip_volatile(http.json())
    assert env.executor.calls == []


def test_failover_5xx_then_ok_records_both_attempts_in_order(env):
    env.executor.script = {"model-e": "server_error"}
    out = _call(_args())
    assert out["success"] is True and out["model"] == "model-a"
    attempts = out["dispatch_receipt"]["attempts"]
    assert [a["model"] for a in attempts] == ["model-e", "model-a"]
    assert attempts[0]["outcome"] == "server_error"
    assert env.executor.calls == [("model-e", STORED_KEY), ("model-a", STORED_KEY)]


def test_provider_401_is_terminal_like_http(env):
    env.executor.script = {"model-e": "auth_error"}
    http = _http()
    env.executor.calls.clear()
    out = _call(_args())
    assert out["success"] is False and out["status_code"] == http.status_code
    assert out["error"] == "provider_auth_failed" and out["key_source"] == "stored"
    assert _strip_volatile(_mcp_error_payload(out)) == _strip_volatile(http.json())
    assert env.executor.calls == [("model-e", STORED_KEY)]


@pytest.mark.parametrize("extra,feature", [
    ({"tools": [{"type": "function", "function": {"name": "f"}}]}, "tools"),
    ({"functions": [{"name": "f"}]}, "functions"),
    ({"tool_choice": "auto"}, "tool_choice"),
    ({"n": 2}, "n"),
    ({"response_format": {"type": "json_object"}}, "response_format"),
])
def test_unsupported_features_match_http(env, extra, feature):
    http = _http(extra=extra)
    out = _call(_args(**extra))
    assert out["success"] is False and out["status_code"] == 400 == http.status_code
    assert out["error"] == "unsupported_feature" and out["feature"] == feature
    assert _mcp_error_payload(out) == http.json()
    assert env.executor.calls == []


def test_multimodal_content_matches_http(env):
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    http = client.post("/v1/chat/completions", json={"model": "auto", "messages": msgs, "dispatch": {}},
                       headers={"x-api-key": SVC_KEY_A})
    out = _call({"messages": msgs})
    assert out["feature"] == "multimodal_content" and _mcp_error_payload(out) == http.json()


def test_default_valued_n_and_text_response_format_are_accepted(env):
    assert _call(_args(n=1, response_format={"type": "text"}))["success"] is True


def test_dispatch_disabled_with_customer_key(env):
    with patch.object(main_module.settings, "dispatch_enabled", False):
        http = _http()
        out = _call(_args())
    assert out["success"] is False and out["status_code"] == 503 and out["error"] == "dispatch_disabled"
    assert _mcp_error_payload(out) == http.json()
    assert env.executor.calls == []


def test_unsupported_currency_matches_http(env):
    http = _http({"currency": "XYZ"})
    out = _call(_args({"currency": "XYZ"}))
    assert out["error"] == "unsupported_currency" and _mcp_error_payload(out) == http.json()


def test_dispatch_service_uninitialised_matches_http(env):
    env.holder["svc"] = None
    http = _http()
    out = _call(_args())
    assert out["error"] == "dispatch_unavailable" and out["status_code"] == 503
    assert _mcp_error_payload(out) == http.json()


# ---------------------------------------------------------------------------
# AC: rate limit shared both ways
# ---------------------------------------------------------------------------

def test_rate_limit_bucket_shared_both_ways(env):
    with patch.object(main_module.settings, "rate_limit_free", 1):
        assert _http().status_code == 200                       # HTTP consumes the bucket...
        out = _call(_args())                                     # ...so MCP is refused
        assert out["success"] is False and out["status_code"] == 429
        assert out["error"] == "rate_limit_exceeded" and out["detail"] == "Rate limit exceeded"
        assert len(main_module._rate_limit_store[CUST_A_ID]) == 1

        main_module._rate_limit_store.clear()
        assert _call(_args())["success"] is True                # MCP consumes the bucket...
        resp = _http()                                           # ...so HTTP is refused
        assert resp.status_code == 429 and resp.json() == {"detail": "Rate limit exceeded"}
        assert len(main_module._rate_limit_store[CUST_A_ID]) == 1
    assert len(env.executor.calls) == 2


def test_prefilled_bucket_refuses_mcp(env):
    limit = main_module.settings.rate_limit_free
    main_module._rate_limit_store[CUST_A_ID].extend([time.time()] * limit)
    out = _call(_args())
    assert out["status_code"] == 429 and env.executor.calls == []
    _assert_nothing_written(env)


# ---------------------------------------------------------------------------
# AC: body cap
# ---------------------------------------------------------------------------

def test_over_cap_mcp_body_is_413_before_dispatch(env):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "dispatch", "arguments": _args()}})
    with patch.object(main_module.settings, "max_body_bytes", len(body) - 1):
        resp = _rpc(None, key=SVC_KEY_A, raw=body)
    assert resp.status_code == 413
    assert env.executor.calls == []
    env.billing.get_customer_by_api_key.assert_not_awaited()
    _assert_nothing_written(env)


# ---------------------------------------------------------------------------
# AC: validation errors (owner decision: same type/loc as HTTP, never input/ctx)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("policy", [
    {"objective": "pinned"},
    {"pinned_model": "model-a"},
    {"min_quality_score": 150},
    {"failover": {"timeout_ms": 5}},
    {"shadow": {}},
    {"allow": ["x" * 201]},
])
def test_validation_errors_match_http_type_and_loc_without_input(env, policy):
    http = _http(policy)
    assert http.status_code == 422
    out = _call(_args(policy))
    assert out["success"] is False and out["status_code"] == 422 and out["error"] == "validation_error"
    assert [(d["type"], d["loc"]) for d in out["detail"]] == [(d["type"], d["loc"]) for d in http.json()["detail"]]
    for d in out["detail"]:
        assert set(d) == {"type", "loc", "msg"}
    text = json.dumps(out)
    assert PROMPT not in text and "x" * 201 not in text
    assert env.executor.calls == []


@pytest.mark.parametrize("args,loc", [
    ({"prompt": PROMPT, "messages": [{"role": "user", "content": "hi"}]}, "prompt"),
    ({}, "prompt"),
    ({"messages": [{"role": "user", "content": PROMPT}], "system": "s"}, "system"),
])
def test_prompt_messages_exclusivity(env, args, loc):
    out = _call(args)
    assert out["status_code"] == 422 and out["detail"][0]["loc"] == ["body", loc]
    assert PROMPT not in json.dumps(out)
    assert env.executor.calls == []


def test_non_string_prompt_and_bad_messages_are_validation_errors(env):
    assert _call({"prompt": 5})["error"] == "validation_error"
    out = _call({"messages": "not-a-list"})
    assert out["error"] == "validation_error" and out["detail"][0]["loc"][:2] == ["body", "messages"]
    assert "not-a-list" not in json.dumps(out)


# ---------------------------------------------------------------------------
# AC: privacy sweep
# ---------------------------------------------------------------------------

def test_privacy_sentinels_never_in_logs_dbs_errors_or_telemetry(env, caplog):
    caplog.set_level(logging.DEBUG)
    results = [_call(_args())]                                   # success
    env.executor.script = {"model-e": "server_error", "model-a": "server_error"}
    results.append(_call(_args()))                               # all attempts failed
    env.executor.script = {"model-e": "auth_error"}
    results.append(_call(_args()))                               # terminal 401
    env.executor.script = {}
    results.append(_call(_args({"objective": "pinned"})))        # validation error
    results.append(_call(_args(), key=None))                     # anonymous
    results.append(_call(_args({"max_request_cost_usd": 1e-9})))  # cost cap
    with patch("mcp.tools.dispatch.prepare_and_dispatch",
               AsyncMock(side_effect=RuntimeError(f"boom {PROMPT} {STORED_KEY}"))):
        results.append(_call(_args()))                           # unexpected exception
    stdio = MCPServer()
    stdio_resp = _run(env, stdio._handle_request({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                                  "params": {"name": "dispatch", "arguments": _args()}}))

    assert results[0]["success"] is True and results[0]["completion"] == COMPLETION
    assert results[-1] == {"success": False, "error": "dispatch_internal_error", "error_type": "RuntimeError"}

    logs = "\n".join(r.getMessage() for r in caplog.records) + "\n".join(str(r.args) for r in caplog.records)
    errors = json.dumps(results[1:]) + json.dumps(stdio_resp)
    dbs = _dump_all(env.ledgers) + _dump_all(env.billing_db)
    telemetry = repr(env.telemetry.mock_calls)
    for sentinel in (PROMPT, "PROMPT-SENTINEL", COMPLETION, STORED_KEY, "STORED-SENTINEL"):
        assert sentinel not in logs, sentinel
        assert sentinel not in errors, sentinel
        assert sentinel not in dbs, sentinel
        assert sentinel not in telemetry, sentinel
    assert STORED_KEY not in json.dumps(results[0]) and "STORED-SENTINEL" not in json.dumps(results[0])
    assert all(c.args == ("dispatch",) for c in env.telemetry.track_feature_usage.call_args_list)


def test_tool_manager_redacts_unexpected_exception_text(env):
    tm = ToolManager()
    with patch.object(tm.tools["dispatch"]["instance"], "execute", AsyncMock(side_effect=ValueError(PROMPT))):
        out = _run(env, tm.execute_tool("dispatch", {}, caller=McpCaller.from_customer(CUSTOMERS[SVC_KEY_A])))
    assert out["success"] is False and out["error_type"] == "ValueError"
    assert PROMPT not in json.dumps(out)


# ---------------------------------------------------------------------------
# AC: STDIO refusal
# ---------------------------------------------------------------------------

def test_stdio_returns_structured_hosted_only_refusal(env):
    server = MCPServer()
    assert server.tool_manager.transport == TRANSPORT_STDIO
    resp = _run(env, server._handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                             "params": {"name": "dispatch", "arguments": _args()}}))
    assert "error" not in resp
    out = json.loads(resp["result"]["content"][0]["text"])
    assert out["success"] is False and out["error"] == "dispatch_requires_hosted_mcp"
    assert "/mcp" in out["message"] and "LLM_PRICING_API_KEY" in out["message"]
    assert out["hosted_endpoint"] == "/mcp" and out["npx_env"] == "LLM_PRICING_API_KEY"
    assert env.executor.calls == []
    _assert_nothing_written(env)


def test_stdio_refusal_even_with_a_customer_caller(env):
    tm = ToolManager(transport=TRANSPORT_STDIO)
    out = _run(env, tm.execute_tool("dispatch", _args(), caller=McpCaller.from_customer(CUSTOMERS[SVC_KEY_A])))
    assert out["error"] == "dispatch_requires_hosted_mcp" and env.executor.calls == []


def test_stdio_other_tools_unchanged(env):
    tm = ToolManager(transport=TRANSPORT_STDIO)
    assert _run(env, tm.execute_tool("list_budget_alerts", {}))["error_code"] == "customer_key_required"
    assert _run(env, tm.execute_tool("list_price_alerts", {}))["error_code"] == "operator_key_required"


# ---------------------------------------------------------------------------
# Direct construction and access declarations
# ---------------------------------------------------------------------------

def test_tool_declares_customer_only_and_redaction():
    assert DispatchTool.caller_access == CUSTOMER_ONLY and DispatchTool.redact_errors is True


@pytest.mark.parametrize("caller", [None, ANONYMOUS, McpCaller.operator(),
                                    McpCaller(kind="customer", customer_id="c", org_id=None, tier="free")])
def test_direct_construction_is_never_unrestricted(env, caller):
    out = _run(env, DispatchTool().execute(_args(), caller=caller))
    assert out["error"] == "dispatch_requires_api_key" and out["status_code"] == 401
    assert env.executor.calls == []


# ---------------------------------------------------------------------------
# Existing behaviour unchanged
# ---------------------------------------------------------------------------

def test_public_tools_still_anonymous(env):
    resp = _rpc("tools/call", {"name": "get_cache_stats", "arguments": {}})
    out = json.loads(resp.json()["result"]["content"][0]["text"])
    assert out.get("success", True) is not False
    env.billing.get_customer_by_api_key.assert_not_awaited()


def test_http_proxy_class_is_the_shared_model():
    from src.models.dispatch import ChatCompletionsRequest
    assert issubclass(main_module._ProxyRequest, ChatCompletionsRequest)
    assert set(main_module._ProxyRequest.model_fields) == set(ChatCompletionsRequest.model_fields)
