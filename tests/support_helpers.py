"""Shared fixtures for the v1.84.0 support-agent tests (not collected: no ``test_`` prefix).

Secret-shaped literals are assembled at runtime so none lands in a commit.
"""
import asyncio
import copy
import dataclasses
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent.conversation import ConversationStore  # noqa: E402
from agent.llm_backend import LLMBackend, LLMResponse  # noqa: E402
from agent.support_agent import SupportAgent  # noqa: E402
from mcp.tools.tool_manager import ToolManager  # noqa: E402
from src.config.settings import settings  # noqa: E402
from src.services.billing_service import BillingService  # noqa: E402
from src.services.orchestrator_service import OrchestratorService  # noqa: E402
from src.services.orchestrator_store import OrchestratorStore  # noqa: E402
from src.services.support_budget import SupportBudget  # noqa: E402

# Secret-shaped strings, built at runtime.
SERVICE_KEY_SHAPED = "".join(["Qm", "x7", "Zp"] * 7) + "ab"                  # 44 url-safe chars, no prefix
SK_KEY = "sk-" + "ant" + "-" + "k" * 30
STRIPE_CUS = "cus_" + "R" * 14
ALERT_TOKEN = "lpa_" + "t" * 43
OTHER_EMAIL = "victim" + "@" + "example.org"
MY_EMAIL = "me" + "@" + "example.org"


class FakeClock:
    def __init__(self, start: float = 1_800_000_000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def say(text: str, tokens_in: int = 100, tokens_out: int = 50) -> LLMResponse:
    return LLMResponse(content=text, usage={"input_tokens": tokens_in, "output_tokens": tokens_out})


def tool(name: str, args: Optional[Dict[str, Any]] = None, tool_id: str = "t1") -> LLMResponse:
    return LLMResponse(content="", tool_calls=[{"id": tool_id, "tool": name, "args": args or {}}],
                       stop_reason="tool_use", usage={"input_tokens": 80, "output_tokens": 20})


def echo_last_user() -> Callable[[List[Dict[str, Any]]], LLMResponse]:
    """A script step that repeats the last user message (what the model actually saw)."""
    def _step(messages: List[Dict[str, Any]]) -> LLMResponse:
        last = next((m for m in reversed(messages) if m.get("role") == "user" and isinstance(m.get("content"), str)),
                    {"content": ""})
        return say("You said: " + str(last.get("content")))
    return _step


class FakeBackend(LLMBackend):
    """Scripted LLM: each ``complete`` pops the next step (an LLMResponse or a callable of messages)."""

    def __init__(self, script: List[Any]):
        self.script = list(script)
        self.calls: List[Dict[str, Any]] = []

    async def complete(self, messages, tools, system=None) -> LLMResponse:
        self.calls.append({"messages": copy.deepcopy(messages), "tools": [t["name"] for t in tools],
                           "system": system})
        if not self.script:
            return say("(script exhausted)")
        step = self.script.pop(0)
        if callable(step):
            step = step(messages)
        return step

    def tool_results_seen(self) -> List[Dict[str, Any]]:
        """Every tool result fed back to the model (text format of the base class)."""
        out = []
        for call in self.calls:
            for m in call["messages"]:
                content = m.get("content")
                if m.get("role") == "user" and isinstance(content, str) and content.startswith("Tool results:"):
                    out.append(m)
        return out


def build_env(tmp_path, script: List[Any], clock: Optional[FakeClock] = None, make_customers: bool = True):
    """Real orchestrator store/service and billing service in tmp_path; fake LLM; patched singletons.

    Returns a SimpleNamespace with ``agent``, ``backend``, ``svc``, ``store``, ``billing``, ``budget``,
    ``conversations``, ``customers`` (dict tier -> CustomerRecord) and ``stop()`` to undo the patches.
    """
    clock = clock or FakeClock()
    store = OrchestratorStore(str(tmp_path / "orch.db"))
    asyncio.run(store.initialize())
    svc = OrchestratorService(store, clock=clock.now)

    billing = BillingService(str(tmp_path / "billing.db"))
    asyncio.run(billing.initialize())
    customers: Dict[str, Any] = {}
    if make_customers:
        free = asyncio.run(billing.create_customer(MY_EMAIL))
        pro = asyncio.run(billing.create_customer("pro" + "@" + "example.org"))
        asyncio.run(billing.update_tier(pro.id, "pro", stripe_customer_id=STRIPE_CUS,
                                        stripe_subscription_id="sub_" + "S" * 14))
        other = asyncio.run(billing.create_customer(OTHER_EMAIL))
        customers = {"free": free, "pro": dataclasses.replace(asyncio.run(billing.get_customer_by_id(pro.id)),
                                                    api_key=pro.api_key),
                     "other": other}

    conversations = ConversationStore()
    budget = SupportBudget(settings, clock=clock.now)
    backend = FakeBackend(script)

    aggregator = MagicMock()
    aggregator.get_all_pricing = AsyncMock(return_value=[])

    async def _get_aggregator():
        return aggregator

    patches = [
        patch("src.services.orchestrator_service.get_orchestrator_service", return_value=svc),
        patch("src.services.orchestrator_store.get_orchestrator_store", return_value=store),
        patch("src.services.billing_service.get_billing_service", return_value=billing),
        patch("agent.support_agent.get_conversation_store", return_value=conversations),
        patch("src.services.pricing_aggregator.get_pricing_aggregator", _get_aggregator),
        patch.object(settings, "rag_docs_path", str(project_root / "docs")),
        patch.object(settings, "support_enabled", True),
    ]
    for p in patches:
        p.start()

    agent = SupportAgent(tool_manager=ToolManager(), budget=budget)
    agent._llm_backend = backend
    asyncio.run(agent.initialize())

    def stop() -> None:
        for p in reversed(patches):
            p.stop()

    return SimpleNamespace(agent=agent, backend=backend, svc=svc, store=store, billing=billing, budget=budget,
                           conversations=conversations, customers=customers, clock=clock, stop=stop,
                           aggregator=aggregator)


def run(coro):
    return asyncio.run(coro)


def caller_for(record, tier: Optional[str] = None):
    from mcp.caller import McpCaller
    return McpCaller.from_customer(record, tier=tier)


def dump_actions(store: OrchestratorStore) -> List[Dict[str, Any]]:
    return run(store.list_actions(limit=1000))


def dump_everything(store: OrchestratorStore) -> str:
    """Every orchestrator row as text, for 'sentinel never appears' sweeps."""
    import json
    parts = []
    parts.append(json.dumps(run(store.list_runs(limit=1000)), default=str))
    parts.append(json.dumps(run(store.list_events(limit=1000)), default=str))
    parts.append(json.dumps(run(store.list_approvals(status=None, limit=1000)), default=str))
    parts.append(json.dumps(run(store.list_actions(limit=1000)), default=str))
    for r in run(store.list_runs(limit=1000)):
        parts.append(json.dumps(run(store.get_steps(r["run_id"])), default=str))
    return "\n".join(parts)


__all__ = [
    "FakeBackend", "FakeClock", "build_env", "say", "tool", "echo_last_user", "run", "caller_for", "dump_actions",
    "dump_everything", "SERVICE_KEY_SHAPED", "SK_KEY", "STRIPE_CUS", "ALERT_TOKEN", "OTHER_EMAIL", "MY_EMAIL",
    "settings", "time",
]
