"""The support_chat MCP tool and the IDENTITY_AWARE access level (v1.84.0).

ToolManager passes the resolved caller through, STDIO refuses, HTTP /mcp resolves anonymous vs
customer, and the pricing agent never binds the tool. Keys are assembled at runtime.
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
from src.main import app  # noqa: E402
from agent.support_agent import SupportResponse  # noqa: E402
from agent.tools import build_agent_tools  # noqa: E402
from mcp.caller import ANONYMOUS, IDENTITY_AWARE, PUBLIC, McpCaller, authorize  # noqa: E402
from mcp.server import MCPServer  # noqa: E402
from mcp.tools.support_chat import SupportChatTool  # noqa: E402
from mcp.tools.tool_manager import TRANSPORT_STDIO, ToolManager  # noqa: E402
from src.services.billing_service import CustomerRecord  # noqa: E402

client = TestClient(app)
CUSTOMER_KEY = "-".join(["svc", "mcp", "support", "c1d2"])
OPERATOR_KEY = "-".join(["op", "mcp", "support", "e3f4"])


def _customer():
    return CustomerRecord(id="cust-mcp", email="mcp@example.org", stripe_customer_id=None, stripe_subscription_id=None,
                          api_key=CUSTOMER_KEY, tier="pro", org_id="org-mcp-" + "0" * 20, created_at=time.time(),
                          updated_at=time.time())


class RecordingAgent:
    def __init__(self):
        self.calls = []

    async def chat(self, message, conversation_id=None, caller=None, client_ip=None, trigger=None):
        self.calls.append({"message": message, "conversation_id": conversation_id, "caller": caller,
                           "client_ip": client_ip, "trigger": trigger})
        identified = bool(caller and caller.is_customer)
        return SupportResponse(reply="own account: " + (caller.tier if identified else "n/a"),
                               conversation_id=conversation_id or "sup-00000000-0000-4000-8000-000000000000",
                               identified=identified, tier=caller.tier if identified else "anonymous")


def _run(coro):
    return asyncio.run(coro)


class TestAccessLevel:
    def test_tool_declares_identity_aware_and_redacts(self):
        tm = ToolManager()
        assert tm.caller_access("support_chat") == IDENTITY_AWARE
        assert getattr(tm.tools["support_chat"]["instance"], "redact_errors") is True
        assert tm.tools["support_chat"]["input_schema"]["required"] == ["message"]

    def test_authorize_never_refuses(self):
        for caller in (None, ANONYMOUS, McpCaller.operator(), McpCaller.from_customer(_customer())):
            assert authorize(caller, IDENTITY_AWARE, {"org_id": "someone-else"}) is None
        assert IDENTITY_AWARE != PUBLIC

    def test_public_count_unchanged(self):
        tm = ToolManager()
        assert len([n for n in tm.tools if tm.caller_access(n) == PUBLIC]) == 19
        assert len(tm.tools) == 32

    def test_pricing_agent_never_binds_support_chat(self):
        bound = {t.name for t in build_agent_tools(ToolManager(), rag_pipeline=None)}
        assert "support_chat" not in bound and "dispatch" not in bound


class TestToolManagerPassesCaller:
    def test_customer_caller_reaches_the_agent(self):
        tm = ToolManager()
        agent = RecordingAgent()
        tm.set_support_agent(agent)
        caller = McpCaller.from_customer(_customer())
        out = _run(tm.execute_tool("support_chat", {"message": "my plan?"}, caller=caller))
        assert out["success"] is True and out["identified"] is True and out["tier"] == "pro"
        assert agent.calls[0]["caller"] is caller and agent.calls[0]["trigger"] == "mcp:support_chat"
        assert agent.calls[0]["client_ip"] is None

    def test_anonymous_default(self):
        tm = ToolManager()
        agent = RecordingAgent()
        tm.set_support_agent(agent)
        out = _run(tm.execute_tool("support_chat", {"message": "plans?"}))
        assert out["identified"] is False and out["tier"] == "anonymous"
        assert agent.calls[0]["caller"].is_anonymous

    def test_operator_is_not_a_customer(self):
        tm = ToolManager()
        agent = RecordingAgent()
        tm.set_support_agent(agent)
        out = _run(tm.execute_tool("support_chat", {"message": "x"}, caller=McpCaller.operator()))
        assert out["identified"] is False

    def test_result_shape_and_conversation_id_passthrough(self):
        tm = ToolManager()
        tm.set_support_agent(RecordingAgent())
        conv = "sup-11111111-2222-3333-4444-555555555555"
        out = _run(tm.execute_tool("support_chat", {"message": "x", "conversation_id": conv}))
        assert set(out) == {"success", "reply", "conversation_id", "identified", "tier", "escalation", "fallback",
                            "sources"}
        assert out["conversation_id"] == conv

    def test_validation_and_unavailable(self):
        tool = SupportChatTool()
        assert _run(tool.execute({"message": "x"}))["error"] == "support_unavailable"
        tool.set_agent(RecordingAgent())
        assert "required" in _run(tool.execute({"message": "  "}))["error"]
        assert "maximum length" in _run(tool.execute({"message": "x" * 10_001}))["error"]
        assert _run(tool.execute("not a dict"))["error"].startswith("'message'")

    def test_agent_errors_never_echo(self):
        tool = SupportChatTool()
        sentinel = "SENTINEL-" + "m" * 12

        class Boom:
            async def chat(self, *a, **k):
                raise RuntimeError(sentinel)
        tool.set_agent(Boom())
        out = _run(tool.execute({"message": sentinel}))
        assert out == {"success": False, "error": "support_internal_error", "error_type": "RuntimeError"}

        class Bad:
            async def chat(self, *a, **k):
                raise ValueError("invalid_conversation_id")
        tool.set_agent(Bad())
        assert _run(tool.execute({"message": "x", "conversation_id": "nope"}))["error"] == "invalid_conversation_id"

    def test_tool_manager_redacts_unexpected_exception(self):
        tm = ToolManager()
        sentinel = "SENTINEL-" + "z" * 12
        instance = tm.tools["support_chat"]["instance"]
        with patch.object(instance, "execute", AsyncMock(side_effect=ValueError(sentinel))):
            out = _run(tm.execute_tool("support_chat", {"message": sentinel}))
        assert out["success"] is False and sentinel not in json.dumps(out)


class TestStdio:
    def test_stdio_refuses_with_hosted_only_guidance(self):
        server = MCPServer()
        assert server.tool_manager.transport == TRANSPORT_STDIO
        agent = RecordingAgent()
        server.tool_manager.set_support_agent(agent)
        resp = _run(server._handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                            "params": {"name": "support_chat", "arguments": {"message": "hi"}}}))
        out = json.loads(resp["result"]["content"][0]["text"])
        assert out["success"] is False and out["error"] == "support_requires_hosted_mcp"
        assert "/mcp" in out["message"] and "LLM_PRICING_API_KEY" in out["message"]
        assert out["faq"] == "/faq" and agent.calls == []

    def test_stdio_refusal_even_with_customer_caller(self):
        tm = ToolManager(transport=TRANSPORT_STDIO)
        agent = RecordingAgent()
        tm.set_support_agent(agent)
        out = _run(tm.execute_tool("support_chat", {"message": "x"}, caller=McpCaller.from_customer(_customer())))
        assert out["error"] == "support_requires_hosted_mcp" and agent.calls == []


class TestHttpMcp:
    @pytest.fixture
    def http(self):
        billing = MagicMock()
        customer = _customer()
        billing.get_customer_by_api_key = AsyncMock(side_effect=lambda k: customer if k == CUSTOMER_KEY else None)
        tm = ToolManager()
        agent = RecordingAgent()
        tm.set_support_agent(agent)

        async def _tm():
            return tm
        patches = [
            patch("src.main.settings.mcp_api_key", OPERATOR_KEY),
            patch("src.main.get_billing_service", return_value=billing),
            patch("src.main._get_http_mcp_tool_manager", _tm),
        ]
        for p in patches:
            p.start()
        try:
            yield SimpleNamespace(agent=agent, billing=billing, customer=customer)
        finally:
            for p in reversed(patches):
                p.stop()

    def _call(self, args, headers=None):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "support_chat", "arguments": args}}
        resp = client.post("/mcp", json=body, headers=headers or {})
        assert resp.status_code == 200, resp.text
        return json.loads(resp.json()["result"]["content"][0]["text"])

    def test_anonymous_vs_customer_vs_bearer(self, http):
        anon = self._call({"message": "plans?"})
        assert anon["success"] and anon["identified"] is False
        keyed = self._call({"message": "my plan?"}, headers={"x-api-key": CUSTOMER_KEY})
        assert keyed["identified"] is True and keyed["tier"] == "pro"
        bearer = self._call({"message": "my plan?"}, headers={"Authorization": f"Bearer {CUSTOMER_KEY}"})
        assert bearer["identified"] is True
        operator = self._call({"message": "x"}, headers={"x-api-key": OPERATOR_KEY})
        assert operator["identified"] is False
        callers = [c["caller"] for c in http.agent.calls]
        assert callers[0].is_anonymous and callers[1].customer_id == "cust-mcp" and callers[3].is_operator
        assert all(CUSTOMER_KEY not in json.dumps(c["message"]) for c in http.agent.calls)

    def test_tools_list_includes_support_chat_identically_for_every_caller(self, http):
        def _list(headers=None):
            body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
            return client.post("/mcp", json=body, headers=headers or {}).json()
        anon, cust = _list(), _list({"x-api-key": CUSTOMER_KEY})
        names = {t["name"] for t in anon["result"]["tools"]}
        assert "support_chat" in names and anon == cust
        desc = next(t for t in anon["result"]["tools"] if t["name"] == "support_chat")["description"]
        for needle in ("never reveals keys", "escalated to the owner", "support_requires_hosted_mcp",
                       "Identity-aware"):
            assert needle in desc, needle


class TestHttpMcpBindsTheRealAgent:
    """The hosted /mcp ToolManager is not the agents' own: the agent must be bound on first use,
    and the connection's address must reach the agent's spend bucket (per-caller, not shared)."""

    def _body(self, args):
        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "support_chat", "arguments": args}}

    def test_support_chat_is_bound_from_get_support_agent_and_gets_client_address(self):
        import src.main as main_module
        tm = ToolManager()  # fresh: nothing bound, exactly like production after startup
        agent = RecordingAgent()
        assert not tm.agent_bound("support_chat")
        getter = AsyncMock(return_value=agent)
        with patch.object(main_module, "_http_mcp_tool_manager", tm), \
                patch.object(main_module, "get_support_agent", getter):
            first = client.post("/mcp", json=self._body({"message": "plans?"}),
                                headers={"fly-client-ip": "203.0.113.9", "x-forwarded-for": "10.9.9.9"})
            second = client.post("/mcp", json=self._body({"message": "again"}),
                                 headers={"x-forwarded-for": "10.1.1.1"})
        out = json.loads(first.json()["result"]["content"][0]["text"])
        assert out["success"] is True and out["identified"] is False
        assert json.loads(second.json()["result"]["content"][0]["text"])["success"] is True
        assert tm.agent_bound("support_chat") and getter.await_count == 1  # bound once, reused
        # the trusted Fly header wins over a client-prepended X-Forwarded-For hop; else the XFF hop
        assert [c["client_ip"] for c in agent.calls] == ["203.0.113.9", "10.1.1.1"]
        assert all(c["trigger"] == "mcp:support_chat" for c in agent.calls)
        from mcp.tools.support_chat import client_address
        assert client_address.get() is None  # reset after the call

    def test_missing_llm_key_leaves_tool_reporting_unavailable(self):
        import src.main as main_module
        tm = ToolManager()
        with patch.object(main_module, "_http_mcp_tool_manager", tm), \
                patch.object(main_module, "get_support_agent", AsyncMock(side_effect=ValueError("no key"))):
            resp = client.post("/mcp", json=self._body({"message": "plans?"}))
        out = json.loads(resp.json()["result"]["content"][0]["text"])
        assert out["success"] is False and out["error"] == "support_unavailable" and "no key" not in resp.text
        assert not tm.agent_bound("support_chat")

    def test_set_agent_refuses_unknown_tool(self):
        tm = ToolManager()
        with pytest.raises(KeyError):
            tm.set_agent("get_all_pricing", object())
        tm.set_pricing_agent(object())
        assert tm.agent_bound("ask_agent")
