"""v1.79.2 — list/delete of price alerts and conversations are operator-only over MCP.

Before v1.79.2 list_price_alerts, delete_price_alert, list_conversations and
delete_conversation were PUBLIC tools: anonymous POST /mcp callers (and the public
PricingAgent) could read every subscriber's webhook URL and every visitor's chat
preview, and delete any of them. They now declare ``caller_access = OPERATOR_ONLY``:
only the global MCP_API_KEY (x-api-key or Bearer on the connection) may run them;
everyone else gets one constant ``operator_key_required`` refusal.

These tests seed a real temp-file PricingAlertService and an in-memory conversation
store with sentinel values, and drive the real POST /mcp handler, the agent's bound
tools and the STDIO server. Keys are assembled at runtime so no secret-shaped
literal lands in a commit.
"""
import asyncio
import json
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
from mcp.caller import (  # noqa: E402
    OPERATOR_KEY_REQUIRED_ERROR, OPERATOR_ONLY, McpCaller, operator_key_required,
)
from mcp.tools.tool_manager import ToolManager  # noqa: E402

client = TestClient(app)

CUSTOMER_KEY = "-".join(["svc", "opo", "cust", "4b1e"])
OPERATOR_KEY = "-".join(["op", "opo", "global", "c0de"])
UNKNOWN_KEY = "-".join(["svc", "opo", "nobody", "9999"])
ORG = "org-opo-1"

SENTINEL_URL = "https://" + "-".join(["subscriber", "sentinel", "77"]) + ".example/hook"
SENTINEL_PREVIEW = " ".join(["private", "visitor", "question", "zq81"])
CONV_ID = "-".join(["conv", "sentinel", "5150"])

OPERATOR_ONLY_TOOLS = ("list_price_alerts", "delete_price_alert", "list_conversations", "delete_conversation")

EXPECTED_REFUSAL = {
    "success": False,
    "error": OPERATOR_KEY_REQUIRED_ERROR,
    "error_code": "operator_key_required",
}


def _customer():
    return CustomerRecord(
        id="cust-opo", email="opo@example.com", stripe_customer_id=None, stripe_subscription_id=None,
        api_key=CUSTOMER_KEY, tier="free", org_id=ORG, created_at=time.time(), updated_at=time.time(),
    )


@pytest.fixture
def env(tmp_path):
    """Real alert DB + conversation store with sentinels, fake billing, operator key configured."""
    loop = asyncio.new_event_loop()
    alerts = PricingAlertService(str(tmp_path / "opo_alerts.db"))
    loop.run_until_complete(alerts.initialize())
    alert = loop.run_until_complete(alerts.register(url=SENTINEL_URL, threshold_pct=5.0))

    store = ConversationStore()
    _, history = loop.run_until_complete(store.get_or_create(CONV_ID))
    history.add("user", SENTINEL_PREVIEW)
    history.add("assistant", "ok")

    billing = MagicMock()
    customer = _customer()
    billing.get_customer_by_api_key = AsyncMock(side_effect=lambda k: customer if k == CUSTOMER_KEY else None)

    patches = [
        patch("src.main.get_billing_service", return_value=billing),
        patch("src.main.settings.mcp_api_key", OPERATOR_KEY),
        patch("src.main.get_pricing_alert_service", return_value=alerts),
        patch("src.main.get_conversation_store", return_value=store),
        patch("mcp.tools.list_price_alerts.get_pricing_alert_service", return_value=alerts),
        patch("mcp.tools.delete_price_alert.get_pricing_alert_service", return_value=alerts),
        patch("mcp.tools.register_price_alert.get_pricing_alert_service", return_value=alerts),
        patch("mcp.tools.list_conversations.get_conversation_store", return_value=store),
        patch("mcp.tools.delete_conversation.get_conversation_store", return_value=store),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(loop=loop, alerts=alerts, store=store, alert_id=alert["id"], billing=billing)
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


def _alert_ids(env):
    return [a["id"] for a in env.loop.run_until_complete(env.alerts.list_alerts())]


def _conv_ids(env):
    return [c["id"] for c in env.loop.run_until_complete(env.store.list_conversations())]


def _assert_refused(result, text=""):
    assert result == EXPECTED_REFUSAL
    for leak in (SENTINEL_URL, SENTINEL_PREVIEW, CONV_ID, "alerts", "total", "conversations"):
        assert leak not in json.dumps(result)
    for leak in (SENTINEL_URL, SENTINEL_PREVIEW, CONV_ID):
        assert leak not in text


NON_OPERATOR = [
    ("none", None),
    ("x-api-key", CUSTOMER_KEY),
    ("bearer", CUSTOMER_KEY),
    ("x-api-key", UNKNOWN_KEY),
    ("bearer", UNKNOWN_KEY),
]


# ---------------------------------------------------------------------------
# AC1-AC3: list_price_alerts
# ---------------------------------------------------------------------------

class TestListPriceAlerts:
    def test_ac1_no_key_is_refused_without_data(self, env):
        result, text = _call("list_price_alerts")
        _assert_refused(result, text)

    @pytest.mark.parametrize("style", ["x-api-key", "bearer"])
    def test_ac2_customer_key_is_refused(self, env, style):
        result, text = _call("list_price_alerts", headers=_headers(style, CUSTOMER_KEY))
        _assert_refused(result, text)

    @pytest.mark.parametrize("style", ["x-api-key", "bearer"])
    def test_unknown_key_is_refused(self, env, style):
        result, text = _call("list_price_alerts", headers=_headers(style, UNKNOWN_KEY))
        _assert_refused(result, text)

    @pytest.mark.parametrize("style", ["x-api-key", "bearer"])
    def test_ac3_operator_key_lists_seeded_alerts(self, env, style):
        result, _ = _call("list_price_alerts", headers=_headers(style, OPERATOR_KEY))
        assert result["success"] is True
        assert result["total"] == 1
        assert result["alerts"][0]["url"] == SENTINEL_URL

    def test_operator_key_unset_means_nothing_matches_it(self, env):
        with patch("src.main.settings.mcp_api_key", None):
            result, _ = _call("list_price_alerts", headers=_headers("x-api-key", OPERATOR_KEY))
        _assert_refused(result)


# ---------------------------------------------------------------------------
# AC4-AC5: delete_price_alert
# ---------------------------------------------------------------------------

class TestDeletePriceAlert:
    @pytest.mark.parametrize("style,key", NON_OPERATOR)
    def test_ac4_non_operator_refused_and_alert_remains(self, env, style, key):
        result, text = _call("delete_price_alert", {"alert_id": env.alert_id}, headers=_headers(style, key))
        _assert_refused(result, text)
        assert _alert_ids(env) == [env.alert_id]

    @pytest.mark.parametrize("style", ["x-api-key", "bearer"])
    def test_ac4_operator_deletes(self, env, style):
        result, _ = _call("delete_price_alert", {"alert_id": env.alert_id}, headers=_headers(style, OPERATOR_KEY))
        assert result["success"] is True and result["deleted"] is True
        assert _alert_ids(env) == []

    def test_operator_unknown_id_is_not_found(self, env):
        result, _ = _call("delete_price_alert", {"alert_id": 987654}, headers=_headers("x-api-key", OPERATOR_KEY))
        assert result["success"] is False and result["deleted"] is False
        assert _alert_ids(env) == [env.alert_id]

    @pytest.mark.parametrize("style,key", NON_OPERATOR)
    def test_ac5_refusal_identical_for_existing_unknown_invalid_and_missing_ids(self, env, style, key):
        headers = _headers(style, key)
        texts = []
        for args in ({"alert_id": env.alert_id}, {"alert_id": 987654}, {"alert_id": "not-an-int"}, {}):
            result, text = _call("delete_price_alert", args, headers=headers)
            assert result == EXPECTED_REFUSAL
            texts.append(result)
        assert all(t == texts[0] for t in texts)
        assert _alert_ids(env) == [env.alert_id]


# ---------------------------------------------------------------------------
# AC6: the same for list_conversations / delete_conversation
# ---------------------------------------------------------------------------

class TestConversations:
    @pytest.mark.parametrize("style,key", NON_OPERATOR)
    def test_list_refused_without_id_or_preview(self, env, style, key):
        result, text = _call("list_conversations", {"limit": 5}, headers=_headers(style, key))
        _assert_refused(result, text)

    @pytest.mark.parametrize("style", ["x-api-key", "bearer"])
    def test_operator_lists(self, env, style):
        result, _ = _call("list_conversations", {}, headers=_headers(style, OPERATOR_KEY))
        assert result["success"] is True
        assert result["conversations"][0]["id"] == CONV_ID
        assert result["conversations"][0]["preview"] == SENTINEL_PREVIEW

    @pytest.mark.parametrize("style,key", NON_OPERATOR)
    def test_delete_refused_and_conversation_remains(self, env, style, key):
        result, text = _call("delete_conversation", {"conversation_id": CONV_ID}, headers=_headers(style, key))
        _assert_refused(result, text)
        assert _conv_ids(env) == [CONV_ID]

    @pytest.mark.parametrize("style,key", NON_OPERATOR)
    def test_delete_refusal_identical_for_existing_unknown_and_missing_ids(self, env, style, key):
        headers = _headers(style, key)
        results = [
            _call("delete_conversation", args, headers=headers)[0]
            for args in ({"conversation_id": CONV_ID}, {"conversation_id": "no-such-conv"}, {})
        ]
        assert all(r == EXPECTED_REFUSAL for r in results)
        assert _conv_ids(env) == [CONV_ID]

    def test_operator_deletes(self, env):
        result, _ = _call("delete_conversation", {"conversation_id": CONV_ID},
                          headers=_headers("bearer", OPERATOR_KEY))
        assert result["success"] is True and result["deleted"] is True
        assert _conv_ids(env) == []

    def test_operator_unknown_conversation_is_not_found(self, env):
        result, _ = _call("delete_conversation", {"conversation_id": "no-such-conv"},
                          headers=_headers("x-api-key", OPERATOR_KEY))
        assert result["success"] is False and result["deleted"] is False


# ---------------------------------------------------------------------------
# AC7: the PricingAgent's bound tools are anonymous, so all four refuse
# ---------------------------------------------------------------------------

class TestAgentPath:
    def test_ac7_agent_bound_operator_tools_refuse(self, env):
        from agent.tools import build_agent_tools
        tm = ToolManager()
        agent_tools = {t.name: t for t in build_agent_tools(tm, rag_pipeline=None)}
        assert len(set(agent_tools) & set(tm.tools)) == 26
        args = {
            "list_price_alerts": {},
            "delete_price_alert": {"alert_id": env.alert_id},
            "list_conversations": {},
            "delete_conversation": {"conversation_id": CONV_ID},
        }
        for tool in OPERATOR_ONLY_TOOLS:
            assert tool in agent_tools, tool
            result = env.loop.run_until_complete(agent_tools[tool].execute(args[tool]))
            _assert_refused(result, json.dumps(result))
        assert _alert_ids(env) == [env.alert_id]
        assert _conv_ids(env) == [CONV_ID]

    @pytest.mark.parametrize("tool", OPERATOR_ONLY_TOOLS)
    def test_tool_manager_default_caller_is_refused(self, env, tool):
        tm = ToolManager()
        for kwargs in ({}, {"caller": None}):
            result = env.loop.run_until_complete(tm.execute_tool(tool, {}, **kwargs))
            assert result == EXPECTED_REFUSAL

    @pytest.mark.parametrize("tool", OPERATOR_ONLY_TOOLS)
    def test_tools_declare_operator_only(self, tool):
        assert ToolManager().caller_access(tool) == OPERATOR_ONLY

    def test_direct_construction_without_caller_stays_unrestricted(self, env):
        """caller=None (in-process unit use) keeps the old behaviour; only transports pass a caller."""
        from mcp.tools.list_price_alerts import ListPriceAlertsTool
        result = env.loop.run_until_complete(ListPriceAlertsTool().execute({}))
        assert result["success"] is True and result["total"] == 1
        refused = env.loop.run_until_complete(
            ListPriceAlertsTool().execute({}, caller=McpCaller.from_customer(_customer()))
        )
        assert refused == EXPECTED_REFUSAL


# ---------------------------------------------------------------------------
# AC8: STDIO is anonymous, so all four refuse
# ---------------------------------------------------------------------------

class TestStdio:
    @pytest.mark.parametrize("tool,args", [
        ("list_price_alerts", {}),
        ("delete_price_alert", {"alert_id": 1}),
        ("list_conversations", {}),
        ("delete_conversation", {"conversation_id": CONV_ID}),
    ])
    def test_ac8_stdio_refuses(self, env, tool, args):
        from mcp.server import MCPServer
        server = MCPServer()
        req = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        resp = env.loop.run_until_complete(server._handle_request(req))
        result = json.loads(resp["result"]["content"][0]["text"])
        _assert_refused(result, json.dumps(resp))
        assert _alert_ids(env) == [env.alert_id]
        assert _conv_ids(env) == [CONV_ID]


# ---------------------------------------------------------------------------
# AC19: the rest of /mcp is unchanged
# ---------------------------------------------------------------------------

class TestRestOfMcpUnchanged:
    def test_initialize(self, env):
        resp = _rpc("initialize")
        assert resp.status_code == 200
        assert resp.json()["result"]["serverInfo"]["name"]

    def test_tools_list_has_30_including_the_four_identical_for_every_caller(self, env):
        anon = _rpc("tools/list").json()["result"]["tools"]
        cust = _rpc("tools/list", headers=_headers("x-api-key", CUSTOMER_KEY)).json()["result"]["tools"]
        op = _rpc("tools/list", headers=_headers("bearer", OPERATOR_KEY)).json()["result"]["tools"]
        assert len(anon) == 30
        assert anon == cust == op
        names = {t["name"] for t in anon}
        assert set(OPERATOR_ONLY_TOOLS) <= names
        env.billing.get_customer_by_api_key.assert_not_awaited()

    @pytest.mark.parametrize("tool", OPERATOR_ONLY_TOOLS)
    def test_descriptions_say_operator_only(self, env, tool):
        tools = {t["name"]: t for t in _rpc("tools/list").json()["result"]["tools"]}
        desc = tools[tool]["description"]
        assert "Operator only" in desc and "MCP_API_KEY" in desc and "operator_key_required" in desc

    def test_delete_price_alert_description_points_to_the_operator(self, env):
        tools = {t["name"]: t for t in _rpc("tools/list").json()["result"]["tools"]}
        assert "contact the operator" in tools["delete_price_alert"]["description"]

    def test_input_schemas_unchanged(self, env):
        tools = {t["name"]: t for t in _rpc("tools/list").json()["result"]["tools"]}
        assert set(tools["list_price_alerts"]["inputSchema"]["properties"]) == set()
        assert tools["delete_price_alert"]["inputSchema"]["required"] == ["alert_id"]
        assert set(tools["list_conversations"]["inputSchema"]["properties"]) == {"limit"}
        assert tools["delete_conversation"]["inputSchema"]["required"] == ["conversation_id"]

    @pytest.mark.parametrize("style,key", NON_OPERATOR + [("x-api-key", OPERATOR_KEY)])
    def test_public_tool_still_works_for_every_caller(self, env, style, key):
        result, _ = _call("get_ide_pricing", {"provider": "Cursor"}, headers=_headers(style, key))
        assert result["success"] is True

    def test_register_price_alert_still_public_over_anonymous_mcp(self, env):
        result, _ = _call("register_price_alert", {"url": "https://new.example/hook", "threshold_pct": 3})
        assert result["success"] is True
        assert len(_alert_ids(env)) == 2

    def test_get_mcp_unchanged(self, env):
        resp = client.get("/mcp")
        assert resp.status_code == 200
        assert resp.json()["endpoint"] == "/mcp"


# ---------------------------------------------------------------------------
# AC20 (MCP part): the key never appears in a refusal, a log line or an exception
# ---------------------------------------------------------------------------

class TestNoKeyLeaks:
    @pytest.mark.parametrize("key", [CUSTOMER_KEY, UNKNOWN_KEY, OPERATOR_KEY])
    @pytest.mark.parametrize("style", ["x-api-key", "bearer"])
    def test_key_never_logged_or_echoed(self, env, caplog, key, style):
        caplog.set_level("DEBUG")
        texts = []
        for tool, args in (
            ("list_price_alerts", {}),
            ("delete_price_alert", {"alert_id": 987654}),
            ("list_conversations", {}),
            ("delete_conversation", {"conversation_id": "no-such-conv"}),
        ):
            _, text = _call(tool, args, headers=_headers(style, key))
            texts.append(text)
        for record in caplog.records:
            assert key not in record.getMessage()
            assert key not in str(record.args)
            if record.exc_text:
                assert key not in record.exc_text
        for t in texts:
            assert key not in t

    def test_operator_key_required_helper_is_constant(self):
        assert operator_key_required() == EXPECTED_REFUSAL
        assert operator_key_required() is not operator_key_required()

    def test_non_ascii_key_over_mcp_is_refused_not_500(self, env):
        resp = _rpc("tools/call", {"name": "list_price_alerts", "arguments": {}},
                    headers={"x-api-key": "clé".encode("latin-1")})
        assert resp.status_code == 200
        result = json.loads(resp.json()["result"]["content"][0]["text"])
        assert result == EXPECTED_REFUSAL


def test_refusal_is_not_used_for_unrelated_main_module_state():
    """Guard: the HTTP layer does not import the MCP refusal (HTTP refusals are plain 401s)."""
    assert not hasattr(main_module, "operator_key_required")
