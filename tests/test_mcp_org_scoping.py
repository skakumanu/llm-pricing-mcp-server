"""v1.79.1 — org-scoped MCP tools act only on the caller's own org.

POST /mcp is a full-bypass path in the security middleware (no auth), and before
v1.79.1 the seven per-org usage/budget tools trusted an org_id typed into the tool
arguments: anyone could list every org's budget alerts, delete any org's alert
(including a hard_stop one), write usage under any org, or read any org's spend.

These tests drive the real POST /mcp handler with real temp-file
BudgetAlertService / UsageTrackerService instances and a fake billing service that
maps keyA -> org-a and keyB -> org-b, and check the rule from every direction:
anonymous (no key, unknown key, STDIO, the anonymous agent), customer (x-api-key and
Authorization: Bearer), and operator (the global MCP_API_KEY). The 19 public tools
must behave exactly as before, with or without a credential (v1.79.2 moved four
alert/conversation tools to OPERATOR_ONLY; see tests/test_mcp_operator_only.py;
v1.81.0 added the public manage_price_alert, 18 -> 19).

Keys are assembled at runtime so no secret-shaped literal lands in a commit.
"""
import asyncio
import io
import json
import re
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
from src.services.budget_alerts import BudgetAlertService  # noqa: E402
from src.services.usage_tracker import UsageTrackerService  # noqa: E402
from mcp.caller import (  # noqa: E402
    ANONYMOUS, CUSTOMER_ONLY, OPERATOR_ONLY, ORG_SCOPED, PUBLIC, McpCaller, authorize, effective_org_id,
)
from mcp.tools.tool_manager import ToolManager  # noqa: E402

client = TestClient(app)

KEY_A = "-".join(["svc", "test", "orgA", "7f3c"])
KEY_B = "-".join(["svc", "test", "orgB", "91d2"])
OPERATOR_KEY = "-".join(["op", "test", "global", "55aa"])
UNKNOWN_KEY = "-".join(["svc", "test", "nobody", "0000"])
ORG_A = "org-a-1111"
ORG_B = "org-b-2222"

ORG_SCOPED_TOOLS = {
    "record_usage", "import_session_usage", "get_usage_summary", "analyze_session_usage",
    "register_budget_alert", "list_budget_alerts", "delete_budget_alert",
}
ORG_TOOL_CLASSES = {
    "RecordUsageTool", "ImportSessionUsageTool", "GetUsageSummaryTool", "AnalyzeSessionUsageTool",
    "RegisterBudgetAlertTool", "ListBudgetAlertsTool", "DeleteBudgetAlertTool",
}
# v1.79.2: operator-only tools — excluded from the public set, and guarded like the above.
OPERATOR_ONLY_TOOLS = {"list_price_alerts", "delete_price_alert", "list_conversations", "delete_conversation"}
OPERATOR_TOOL_CLASSES = {
    "ListPriceAlertsTool", "DeletePriceAlertTool", "ListConversationsTool", "DeleteConversationTool",
}
# v1.80.0: customer-only tools (dispatch) — excluded from the public set, and guarded like the above.
CUSTOMER_ONLY_TOOLS = {"dispatch"}
CUSTOMER_ONLY_TOOL_CLASSES = {"DispatchTool"}
RESTRICTED_TOOLS = ORG_SCOPED_TOOLS | OPERATOR_ONLY_TOOLS | CUSTOMER_ONLY_TOOLS
_TOOL_MODULES = {
    "record_usage": ("get_usage_tracker", "get_budget_alert_service"),
    "import_session_usage": ("get_usage_tracker", "get_budget_alert_service"),
    "get_usage_summary": ("get_usage_tracker",),
    "analyze_session_usage": ("get_usage_tracker",),
    "register_budget_alert": ("get_budget_alert_service",),
    "list_budget_alerts": ("get_budget_alert_service",),
    "delete_budget_alert": ("get_budget_alert_service",),
}


def _customer(key, org, n):
    return CustomerRecord(
        id=f"cust-{n}", email=f"c{n}@example.com", stripe_customer_id=None, stripe_subscription_id=None,
        api_key=key, tier="free", org_id=org, created_at=time.time(), updated_at=time.time(),
    )


CUSTOMERS = {KEY_A: _customer(KEY_A, ORG_A, 1), KEY_B: _customer(KEY_B, ORG_B, 2)}


def _fake_pricing(model_name):
    return SimpleNamespace(
        provider="openai", model_name="gpt-4o",
        cost_per_input_token=0.0000025, cost_per_output_token=0.00001,
    )


@pytest.fixture
def env(tmp_path):
    """Real temp-file usage + budget services, fake billing, operator key configured."""
    db = str(tmp_path / "org_scoping.db")
    loop = asyncio.new_event_loop()
    usage = UsageTrackerService(db)
    budget = BudgetAlertService(db)
    loop.run_until_complete(usage.initialize())
    loop.run_until_complete(budget.initialize())

    # Seed: one alert + one usage event per org (thresholds high so nothing fires).
    alert_a = loop.run_until_complete(budget.register("https://a.example/hook", 9999.0, org_id=ORG_A))
    alert_b = loop.run_until_complete(
        budget.register("https://b.example/hook", 9999.0, org_id=ORG_B, hard_stop=True)
    )
    for org, sess in ((ORG_A, "sess-a"), (ORG_B, "sess-b")):
        loop.run_until_complete(usage.record_event(
            provider="openai", model_name="gpt-4o", input_tokens=100, output_tokens=50,
            cost_usd=0.75, org_id=org, request_id=f"seed-{org}", session_id=sess,
        ))

    billing = MagicMock()
    billing.get_customer_by_api_key = AsyncMock(side_effect=lambda k: CUSTOMERS.get(k))

    tm = loop.run_until_complete(main_module._get_http_mcp_tool_manager())
    patches = [
        patch("src.main.get_billing_service", return_value=billing),
        patch("src.main.settings.mcp_api_key", OPERATOR_KEY),
        patch.object(tm.aggregator, "find_model_pricing", AsyncMock(side_effect=_fake_pricing)),
    ]
    for tool, getters in _TOOL_MODULES.items():
        for getter in getters:
            target = usage if getter == "get_usage_tracker" else budget
            patches.append(patch(f"mcp.tools.{tool}.{getter}", return_value=target))
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(
            usage=usage, budget=budget, billing=billing, loop=loop, tm=tm,
            alert_a=alert_a["id"], alert_b=alert_b["id"],
        )
    finally:
        for p in reversed(patches):
            p.stop()
        loop.close()


def _headers(style, key):
    if style == "x-api-key":
        return {"x-api-key": key}
    if style == "bearer":
        return {"Authorization": f"Bearer {key}"}
    return {}


def _rpc(method, params=None, headers=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    return client.post("/mcp", json=body, headers=headers or {})


def _call(name, arguments=None, headers=None):
    resp = _rpc("tools/call", {"name": name, "arguments": arguments or {}}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" not in data, data
    return json.loads(data["result"]["content"][0]["text"]), resp.text


def _alerts(env):
    return env.loop.run_until_complete(env.budget.list_alerts())


def _summary(env, org=None):
    return env.loop.run_until_complete(env.usage.get_summary(org_id=org, days=365))


def _assert_customer_key_required(result):
    assert result["success"] is False
    assert result["error_code"] == "customer_key_required"
    assert result["signup"] == "/billing/signup"
    assert result["auth"]["header"] == "x-api-key"
    assert result["auth"]["npx_env"] == "LLM_PRICING_API_KEY"
    assert result["auth"]["docs"] == "/mcp-setup"


def _assert_no_org_data(text):
    for leak in (ORG_A, ORG_B, "a.example", "b.example", "sess-a", "sess-b"):
        assert leak not in text, f"anonymous response leaked {leak!r}"


ORG_TOOL_ARGS = {
    "record_usage": {"model_name": "gpt-4o", "input_tokens": 10, "output_tokens": 5, "org_id": ORG_A},
    "import_session_usage": {
        "session_id": "evil-sess", "org_id": ORG_A,
        "entries": [{"model_name": "gpt-4o", "input_tokens": 10, "output_tokens": 5}],
    },
    "get_usage_summary": {"org_id": ORG_A},
    "analyze_session_usage": {"session_id": "sess-a", "org_id": ORG_A},
    "register_budget_alert": {"url": "https://evil.example/hook", "threshold_usd": 1, "org_id": ORG_A},
    "list_budget_alerts": {},
    "delete_budget_alert": {"alert_id": 1},
}


# ---------------------------------------------------------------------------
# AC1-AC4: anonymous callers can never read, write or delete org-scoped data
# ---------------------------------------------------------------------------

class TestAnonymous:
    def test_ac1_list_budget_alerts_refused_without_data(self, env):
        result, text = _call("list_budget_alerts")
        _assert_customer_key_required(result)
        assert "alerts" not in result
        _assert_no_org_data(text)

    def test_ac2_delete_other_orgs_alert_refused_and_row_remains(self, env):
        result, _ = _call("delete_budget_alert", {"alert_id": env.alert_b})
        _assert_customer_key_required(result)
        assert {a["id"] for a in _alerts(env)} == {env.alert_a, env.alert_b}

    def test_ac3_record_usage_naming_a_real_org_writes_nothing(self, env):
        before = _summary(env)["total_requests"]
        result, _ = _call("record_usage", ORG_TOOL_ARGS["record_usage"])
        _assert_customer_key_required(result)
        assert _summary(env)["total_requests"] == before
        assert _summary(env, ORG_A)["total_requests"] == 1

    def test_ac3_import_session_usage_naming_a_real_org_writes_nothing(self, env):
        result, _ = _call("import_session_usage", ORG_TOOL_ARGS["import_session_usage"])
        _assert_customer_key_required(result)
        assert _summary(env)["total_requests"] == 2
        session = env.loop.run_until_complete(env.usage.get_session_usage("evil-sess"))
        assert session["total_requests"] == 0

    def test_register_budget_alert_refused_and_nothing_registered(self, env):
        result, _ = _call("register_budget_alert", ORG_TOOL_ARGS["register_budget_alert"])
        _assert_customer_key_required(result)
        assert len(_alerts(env)) == 2

    @pytest.mark.parametrize("args", [{}, {"org_id": ORG_A}, {"org_id": ORG_B}])
    def test_ac4_get_usage_summary_returns_no_org_data(self, env, args):
        result, text = _call("get_usage_summary", args)
        _assert_customer_key_required(result)
        assert "total_cost_usd" not in result
        _assert_no_org_data(text)

    @pytest.mark.parametrize("args", [{"session_id": "sess-a"}, {"session_id": "sess-b", "org_id": ORG_B}])
    def test_ac4_analyze_session_usage_returns_no_org_data(self, env, args):
        result, text = _call("analyze_session_usage", args)
        _assert_customer_key_required(result)
        assert "total_cost_usd" not in result
        _assert_no_org_data(text)

    @pytest.mark.parametrize("tool", sorted(ORG_SCOPED_TOOLS))
    def test_every_org_tool_refuses_anonymous(self, env, tool):
        result, _ = _call(tool, ORG_TOOL_ARGS[tool])
        _assert_customer_key_required(result)

    def test_refusal_is_a_tool_result_not_a_jsonrpc_error(self, env):
        resp = _rpc("tools/call", {"name": "list_budget_alerts", "arguments": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert "error" not in data and data["id"] == 1

    def test_billing_not_initialised_means_anonymous(self, env):
        with patch("src.main.get_billing_service", side_effect=RuntimeError("not init")):
            result, text = _call("list_budget_alerts", headers={"x-api-key": KEY_A})
        _assert_customer_key_required(result)
        assert KEY_A not in text


# ---------------------------------------------------------------------------
# AC5 / AC6: a customer key acts on, and only on, its own org
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("style", ["x-api-key", "bearer"])
class TestCustomerKey:
    def test_list_returns_only_own_orgs_alerts(self, env, style):
        result, text = _call("list_budget_alerts", headers=_headers(style, KEY_A))
        assert result["success"] is True
        assert [a["id"] for a in result["alerts"]] == [env.alert_a]
        assert result["total"] == 1
        assert ORG_B not in text and "b.example" not in text

    def test_delete_other_orgs_alert_is_not_found_and_row_stays(self, env, style):
        result, _ = _call("delete_budget_alert", {"alert_id": env.alert_b}, headers=_headers(style, KEY_A))
        assert result["success"] is False and result["deleted"] is False
        assert result["error"] == f"Alert {env.alert_b} not found"
        assert env.alert_b in {a["id"] for a in _alerts(env)}

    def test_delete_own_alert_succeeds(self, env, style):
        result, _ = _call("delete_budget_alert", {"alert_id": env.alert_a}, headers=_headers(style, KEY_A))
        assert result == {"success": True, "deleted": True, "alert_id": env.alert_a}
        assert {a["id"] for a in _alerts(env)} == {env.alert_b}

    def test_record_usage_for_other_org_refused_org_mismatch(self, env, style):
        args = {**ORG_TOOL_ARGS["record_usage"], "org_id": ORG_B}
        result, text = _call("record_usage", args, headers=_headers(style, KEY_A))
        assert result == {
            "success": False,
            "error": "A customer key can only act on its own org; omit org_id or pass your own.",
            "error_code": "org_mismatch",
        }
        assert ORG_A not in text and KEY_A not in text
        assert _summary(env, ORG_B)["total_requests"] == 1
        assert _summary(env)["total_requests"] == 2

    def test_get_usage_summary_for_other_org_refused_org_mismatch(self, env, style):
        result, text = _call("get_usage_summary", {"org_id": ORG_B}, headers=_headers(style, KEY_A))
        assert result["error_code"] == "org_mismatch"
        assert "total_cost_usd" not in result and "b.example" not in text

    @pytest.mark.parametrize("org_arg", [None, ORG_A, ""])
    def test_record_usage_own_or_omitted_org_writes_to_own_org(self, env, style, org_arg):
        args = {"model_name": "gpt-4o", "input_tokens": 10, "output_tokens": 5}
        if org_arg is not None:
            args["org_id"] = org_arg
        result, _ = _call("record_usage", args, headers=_headers(style, KEY_A))
        assert result["success"] is True and result["org_id"] == ORG_A
        assert _summary(env, ORG_A)["total_requests"] == 2
        assert _summary(env, ORG_B)["total_requests"] == 1

    @pytest.mark.parametrize("org_arg", [None, ORG_A])
    def test_get_usage_summary_own_or_omitted_org_is_own_org_only(self, env, style, org_arg):
        args = {} if org_arg is None else {"org_id": org_arg}
        result, _ = _call("get_usage_summary", args, headers=_headers(style, KEY_A))
        assert result["success"] is True
        assert result["org_id"] == ORG_A
        assert result["total_requests"] == 1  # never the 2-event all-org aggregate

    def test_import_session_usage_lands_in_own_org(self, env, style):
        args = {"session_id": "imp-1", "entries": [{"model_name": "gpt-4o", "input_tokens": 3, "output_tokens": 2}]}
        result, _ = _call("import_session_usage", args, headers=_headers(style, KEY_A))
        assert result["success"] is True and result["summary"]["recorded_exact"] == 1
        own = env.loop.run_until_complete(env.usage.get_session_usage("imp-1", org_id=ORG_A))
        assert own["total_requests"] == 1

    def test_import_session_usage_for_other_org_refused(self, env, style):
        args = {**ORG_TOOL_ARGS["import_session_usage"], "org_id": ORG_B}
        result, _ = _call("import_session_usage", args, headers=_headers(style, KEY_A))
        assert result["error_code"] == "org_mismatch"
        assert _summary(env, ORG_B)["total_requests"] == 1

    def test_analyze_other_orgs_session_reads_as_no_usage(self, env, style):
        result, text = _call("analyze_session_usage", {"session_id": "sess-b"}, headers=_headers(style, KEY_A))
        assert result["success"] is False and result["has_data"] is False
        assert "No usage recorded" in result["error"]
        assert ORG_B not in text

    def test_analyze_own_session(self, env, style):
        result, _ = _call("analyze_session_usage", {"session_id": "sess-a"}, headers=_headers(style, KEY_A))
        assert result["success"] is True and result["total_requests"] == 1

    def test_register_binds_to_own_org_and_never_sets_hard_stop(self, env, style):
        args = {"url": "https://a.example/new", "threshold_usd": 5, "hard_stop": True}
        result, _ = _call("register_budget_alert", args, headers=_headers(style, KEY_A))
        assert result["success"] is True
        assert result["org_id"] == ORG_A and result["hard_stop"] is False
        stored = [a for a in _alerts(env) if a["id"] == result["id"]][0]
        assert stored["org_id"] == ORG_A and stored["hard_stop"] is False

    def test_register_for_other_org_refused(self, env, style):
        args = {"url": "https://a.example/new", "threshold_usd": 5, "org_id": ORG_B}
        result, _ = _call("register_budget_alert", args, headers=_headers(style, KEY_A))
        assert result["error_code"] == "org_mismatch"
        assert len(_alerts(env)) == 2

    def test_key_b_sees_only_org_b(self, env, style):
        result, _ = _call("list_budget_alerts", headers=_headers(style, KEY_B))
        assert [a["id"] for a in result["alerts"]] == [env.alert_b]


# ---------------------------------------------------------------------------
# AC7: the global MCP_API_KEY is the operator and keeps all-org visibility
# ---------------------------------------------------------------------------

class TestOperator:
    @pytest.mark.parametrize("style", ["x-api-key", "bearer"])
    def test_ac7_operator_lists_all_orgs_alerts(self, env, style):
        result, _ = _call("list_budget_alerts", headers=_headers(style, OPERATOR_KEY))
        assert result["success"] is True
        assert {a["id"] for a in result["alerts"]} == {env.alert_a, env.alert_b}
        env.billing.get_customer_by_api_key.assert_not_awaited()

    def test_operator_keeps_todays_org_id_behaviour(self, env):
        headers = _headers("x-api-key", OPERATOR_KEY)
        result, _ = _call("get_usage_summary", {}, headers=headers)
        assert result["total_requests"] == 2  # all-org aggregate, as before
        result, _ = _call("get_usage_summary", {"org_id": ORG_B}, headers=headers)
        assert result["org_id"] == ORG_B and result["total_requests"] == 1
        result, _ = _call("delete_budget_alert", {"alert_id": env.alert_b}, headers=headers)
        assert result["deleted"] is True

    def test_no_operator_key_configured_means_nothing_matches_it(self, env):
        with patch("src.main.settings.mcp_api_key", None):
            result, _ = _call("list_budget_alerts", headers=_headers("x-api-key", OPERATOR_KEY))
        _assert_customer_key_required(result)


# ---------------------------------------------------------------------------
# AC8: an unknown key is anonymous and is never echoed
# ---------------------------------------------------------------------------

class TestUnknownKey:
    @pytest.mark.parametrize("style", ["x-api-key", "bearer"])
    def test_ac8_unknown_key_is_anonymous(self, env, style):
        headers = _headers(style, UNKNOWN_KEY)
        public, public_text = _call("get_ide_pricing", {"provider": "Cursor"}, headers=headers)
        assert public["success"] is True
        assert UNKNOWN_KEY not in public_text
        for tool in sorted(ORG_SCOPED_TOOLS):
            result, text = _call(tool, ORG_TOOL_ARGS[tool], headers=headers)
            _assert_customer_key_required(result)
            assert UNKNOWN_KEY not in text
        assert len(_alerts(env)) == 2 and _summary(env)["total_requests"] == 2

    def test_empty_bearer_is_anonymous(self, env):
        result, _ = _call("list_budget_alerts", headers={"Authorization": "Bearer   "})
        _assert_customer_key_required(result)

    def test_non_ascii_key_is_anonymous_not_500(self, env):
        """A latin-1 header byte must not crash the operator-key comparison (str compare_digest TypeError)."""
        result, _ = _call(
            "list_budget_alerts", headers={"x-api-key": "clé".encode("latin-1")}
        )
        _assert_customer_key_required(result)


# ---------------------------------------------------------------------------
# AC9: the 19 public tools are unchanged, with or without a credential
# ---------------------------------------------------------------------------

def _strip_timestamps(obj):
    if isinstance(obj, dict):
        return {
            k: _strip_timestamps(v) for k, v in obj.items()
            if not re.search(r"(timestamp|_at$|^at$|time|date|generated|updated|cached)", k, re.I)
        }
    if isinstance(obj, list):
        return [_strip_timestamps(v) for v in obj]
    return obj


class TestPublicToolParity:
    def test_ac9_tools_list_has_31_entries_and_public_entries_identical(self, env):
        anon = _rpc("tools/list").json()["result"]["tools"]
        keyed = _rpc("tools/list", headers=_headers("x-api-key", KEY_A)).json()["result"]["tools"]
        op = _rpc("tools/list", headers=_headers("bearer", OPERATOR_KEY)).json()["result"]["tools"]
        assert len(anon) == 32  # v1.84.0: + support_chat (identity-aware, listed for every caller)
        assert anon == keyed == op
        # v1.84.0: support_chat is identity-aware (neither public nor restricted) and listed for everyone.
        public = [t for t in anon if t["name"] not in RESTRICTED_TOOLS and t["name"] != "support_chat"]
        assert len(public) == 19
        env.billing.get_customer_by_api_key.assert_not_awaited()

    def test_ac9_every_public_tool_gets_identical_input_and_no_caller(self, env):
        """Each public tool's execute() sees exactly the arguments, never a caller, with or without a key."""
        tm = env.tm
        public = [n for n in tm.tools if tm.caller_access(n) == PUBLIC]
        assert len(public) == 19 and not set(public) & RESTRICTED_TOOLS
        seen = {}

        def _recorder(name):
            async def _execute(*args, **kwargs):
                seen.setdefault(name, []).append((args, kwargs))
                return {"success": True, "tool": name, "echo": args[0] if args else None}
            return _execute

        originals = {n: tm.tools[n]["instance"].execute for n in public}
        try:
            for n in public:
                tm.tools[n]["instance"].execute = _recorder(n)
            for n in public:
                args = {"probe": n}
                anon, _ = _call(n, args)
                keyed, _ = _call(n, args, headers=_headers("x-api-key", KEY_A))
                bearer, _ = _call(n, args, headers=_headers("bearer", KEY_B))
                op, _ = _call(n, args, headers=_headers("x-api-key", OPERATOR_KEY))
                assert anon == keyed == bearer == op
        finally:
            for n, fn in originals.items():
                tm.tools[n]["instance"].execute = fn
        for n in public:
            assert len(seen[n]) == 4
            for args, kwargs in seen[n]:
                assert args == ({"probe": n},) and kwargs == {}
        # Lazy resolution: a public tool never triggers a billing lookup.
        env.billing.get_customer_by_api_key.assert_not_awaited()

    @pytest.mark.parametrize("name,args", [
        ("get_ide_pricing", {"provider": "Cursor"}),
        ("get_ide_pricing", {"max_monthly": 20}),
        ("get_use_cases", {}),
        ("estimate_cost", {"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}),
    ])
    def test_ac9_real_public_results_identical_with_and_without_key(self, env, name, args):
        anon, _ = _call(name, args)
        keyed, _ = _call(name, args, headers=_headers("x-api-key", KEY_A))
        op, _ = _call(name, args, headers=_headers("bearer", OPERATOR_KEY))
        assert _strip_timestamps(anon) == _strip_timestamps(keyed) == _strip_timestamps(op)

    def test_initialize_never_resolves_a_caller(self, env):
        resp = _rpc("initialize", headers=_headers("x-api-key", KEY_A))
        assert resp.status_code == 200
        env.billing.get_customer_by_api_key.assert_not_awaited()

    def test_org_tool_property_sets_unchanged(self, env):
        """FR13: only description strings changed on the 7 tools; names and property sets are pinned."""
        expected = {
            "record_usage": {"model_name", "input_tokens", "output_tokens", "org_id", "occurred_at",
                             "request_id", "session_id"},
            "get_usage_summary": {"org_id", "days"},
            "analyze_session_usage": {"session_id", "org_id"},
            "import_session_usage": {"session_id", "org_id", "entries"},
            "register_budget_alert": {"url", "threshold_usd", "org_id", "period_days"},
            "list_budget_alerts": set(),
            "delete_budget_alert": {"alert_id"},
        }
        for name, props in expected.items():
            schema = env.tm.tools[name]["input_schema"]
            assert set(schema["properties"]) == props, name
            assert "service key" in env.tm.tools[name]["description"].lower() or \
                "service api key" in env.tm.tools[name]["description"].lower(), name


# ---------------------------------------------------------------------------
# AC10: STDIO is anonymous
# ---------------------------------------------------------------------------

class TestStdio:
    @pytest.mark.parametrize("tool", sorted(ORG_SCOPED_TOOLS))
    def test_ac10_stdio_org_tools_refuse(self, env, tool):
        from mcp.server import MCPServer
        server = MCPServer()
        req = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
               "params": {"name": tool, "arguments": ORG_TOOL_ARGS[tool]}}
        resp = env.loop.run_until_complete(server._handle_request(req))
        result = json.loads(resp["result"]["content"][0]["text"])
        _assert_customer_key_required(result)
        assert len(_alerts(env)) == 2 and _summary(env)["total_requests"] == 2

    def test_stdio_debug_logs_method_and_id_only(self, env, caplog, capsys):
        from mcp.server import MCPServer
        sentinel = "-".join(["BODY", "SENTINEL", "x9"])
        req = {"jsonrpc": "2.0", "id": 42, "method": "tools/call",
               "params": {"name": "get_ide_pricing", "arguments": {"provider": sentinel}}}
        server = MCPServer()
        caplog.set_level("DEBUG", logger="mcp.server")
        with patch("sys.stdin", io.StringIO(json.dumps(req) + "\n")):
            env.loop.run_until_complete(server.run())
        out = capsys.readouterr().out
        assert '"id": 42' in out
        messages = [r.getMessage() for r in caplog.records]
        assert any("method=tools/call" in m and "id=42" in m for m in messages)
        assert any("Sending response: id=42" in m for m in messages)
        assert not any(sentinel in m for m in messages)


# ---------------------------------------------------------------------------
# AC11: no DEBUG record on the /mcp path contains the key
# ---------------------------------------------------------------------------

class TestNoKeyInLogs:
    @pytest.mark.parametrize("key", [KEY_A, OPERATOR_KEY, UNKNOWN_KEY])
    @pytest.mark.parametrize("style", ["x-api-key", "bearer"])
    def test_ac11_key_never_logged_or_echoed(self, env, caplog, key, style):
        caplog.set_level("DEBUG")
        texts = []
        for tool in sorted(ORG_SCOPED_TOOLS):
            _, text = _call(tool, ORG_TOOL_ARGS[tool], headers=_headers(style, key))
            texts.append(text)
        _, text = _call("get_ide_pricing", {}, headers=_headers(style, key))
        texts.append(text)
        texts.append(_rpc("tools/list", headers=_headers(style, key)).text)
        for record in caplog.records:
            assert key not in record.getMessage()
            assert key not in str(record.args)
        for t in texts:
            assert key not in t


# ---------------------------------------------------------------------------
# AC14: the anonymous agent path is refused; the agent binds 27 tools (v1.81.0)
# ---------------------------------------------------------------------------

class TestAgentPath:
    def test_ac14_agent_bound_count_and_runtime_refusal(self, env):
        from agent.tools import build_agent_tools
        tm = ToolManager()
        agent_tools = {t.name: t for t in build_agent_tools(tm, rag_pipeline=None)}
        mcp_bound = set(agent_tools) & set(tm.tools)
        assert len(mcp_bound) == 27
        assert ORG_SCOPED_TOOLS <= mcp_bound
        for tool in sorted(ORG_SCOPED_TOOLS):
            result = env.loop.run_until_complete(agent_tools[tool].execute(ORG_TOOL_ARGS[tool]))
            _assert_customer_key_required(result)
        assert len(_alerts(env)) == 2 and _summary(env)["total_requests"] == 2

    @pytest.mark.parametrize("tool", sorted(ORG_SCOPED_TOOLS))
    def test_tool_manager_default_and_explicit_none_caller_are_anonymous(self, env, tool):
        tm = ToolManager()
        for kwargs in ({}, {"caller": None}, {"caller": ANONYMOUS}):
            result = env.loop.run_until_complete(tm.execute_tool(tool, ORG_TOOL_ARGS[tool], **kwargs))
            _assert_customer_key_required(result)

    def test_nothing_outside_mcp_tools_imports_org_scoped_tool_classes(self):
        """Only ToolManager (which defaults to ANONYMOUS) may construct the 7 org-scoped
        tools, (v1.79.2) the four operator-only tools, or (v1.80.0) the dispatch tool."""
        offenders = []
        paths = list((project_root / "src").rglob("*.py")) + list((project_root / "agent").rglob("*.py"))
        paths.append(project_root / "mcp" / "server.py")
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for cls in ORG_TOOL_CLASSES | OPERATOR_TOOL_CLASSES | CUSTOMER_ONLY_TOOL_CLASSES:
                if re.search(rf"\b{cls}\b", text):
                    offenders.append(f"{path.relative_to(project_root)}: {cls}")
        assert not offenders, offenders


# ---------------------------------------------------------------------------
# mcp.caller unit edge cases
# ---------------------------------------------------------------------------

class TestCallerUnit:
    def test_from_customer_copies_no_credential(self):
        caller = McpCaller.from_customer(CUSTOMERS[KEY_A])
        assert caller.is_customer and caller.org_id == ORG_A and caller.customer_id == "cust-1"
        assert KEY_A not in repr(caller) and "example.com" not in repr(caller)
        assert not hasattr(caller, "api_key") and not hasattr(caller, "email")

    def test_kinds(self):
        assert ANONYMOUS.is_anonymous and McpCaller.anonymous() is ANONYMOUS
        assert McpCaller.operator().is_operator

    def test_authorize_matrix(self):
        cust = McpCaller.from_customer(CUSTOMERS[KEY_A])
        op = McpCaller.operator()
        assert authorize(ANONYMOUS, PUBLIC, {}) is None
        assert authorize(ANONYMOUS, ORG_SCOPED, {})["error_code"] == "customer_key_required"
        assert authorize(None, ORG_SCOPED, {})["error_code"] == "customer_key_required"
        assert authorize(op, ORG_SCOPED, {"org_id": ORG_B}) is None
        assert authorize(op, CUSTOMER_ONLY, {})["error_code"] == "customer_key_required"
        assert authorize(cust, ORG_SCOPED, {}) is None
        assert authorize(cust, ORG_SCOPED, {"org_id": ORG_A}) is None
        assert authorize(cust, ORG_SCOPED, {"org_id": ""}) is None
        assert authorize(cust, ORG_SCOPED, {"org_id": ORG_B})["error_code"] == "org_mismatch"
        assert authorize(cust, CUSTOMER_ONLY, {}) is None
        # v1.79.2: OPERATOR_ONLY admits only the operator, with one constant refusal.
        assert authorize(op, OPERATOR_ONLY, {}) is None
        refusals = [
            authorize(ANONYMOUS, OPERATOR_ONLY, {}),
            authorize(None, OPERATOR_ONLY, {"alert_id": 1}),
            authorize(cust, OPERATOR_ONLY, {}),
            authorize(cust, OPERATOR_ONLY, {"org_id": ORG_A}),
            authorize(McpCaller(kind="customer", customer_id="c", org_id=None), OPERATOR_ONLY, {}),
        ]
        assert all(r == refusals[0] for r in refusals)
        assert refusals[0]["error_code"] == "operator_key_required"
        assert set(refusals[0]) == {"success", "error", "error_code"}

    def test_customer_without_org_is_refused(self):
        orgless = McpCaller(kind="customer", customer_id="c", org_id=None)
        assert authorize(orgless, ORG_SCOPED, {})["error_code"] == "customer_key_required"

    def test_effective_org_id(self):
        cust = McpCaller.from_customer(CUSTOMERS[KEY_A])
        assert effective_org_id(cust, {}) == ORG_A
        assert effective_org_id(cust, {"org_id": ""}) == ORG_A
        assert effective_org_id(McpCaller.operator(), {"org_id": ORG_B}) == ORG_B
        assert effective_org_id(None, {"org_id": ORG_B}) == ORG_B

    def test_refusals_echo_neither_org_nor_key(self):
        cust = McpCaller.from_customer(CUSTOMERS[KEY_A])
        text = json.dumps(authorize(cust, ORG_SCOPED, {"org_id": ORG_B}))
        assert ORG_A not in text and ORG_B not in text and KEY_A not in text

    def test_tool_with_anonymous_caller_refuses_even_when_called_directly(self, env):
        """Defence in depth: the tool itself refuses a non-None anonymous caller."""
        from mcp.tools.list_budget_alerts import ListBudgetAlertsTool
        result = env.loop.run_until_complete(ListBudgetAlertsTool().execute({}, caller=ANONYMOUS))
        _assert_customer_key_required(result)
        unrestricted = env.loop.run_until_complete(ListBudgetAlertsTool().execute({}))
        assert unrestricted["total"] == 2
