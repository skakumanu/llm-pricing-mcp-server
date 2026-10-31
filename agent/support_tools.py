"""Support agent tools (v1.84.0): built per request with the caller baked in.

Unlike ``agent/tools.py`` (one shared tool list for the anonymous pricing agent), these
closures are constructed for every support turn with the resolved ``McpCaller`` inside
them. No tool takes an ``org_id``, ``customer_id`` or ``email`` argument, so nothing the
customer types (or an injected instruction asks the model to pass) can name another
customer: ``get_my_account`` can only ever read the caller's own record.

Tools:
- ``faq_search``            TF-IDF search over the support knowledge base (FAQ + MCP guides)
- ``get_plan_catalog``      tiers, prices, rate limits, support allowance, escalation priority
- ``get_all_pricing`` / ``estimate_cost`` / ``get_data_quality``  the PUBLIC pricing tools, wrapped
- ``get_my_account``        the caller's own tier, payment status, rate limit, key preview, savings
- ``escalate_to_owner``     opens (or re-joins) this conversation's escalation run
- ``check_escalation``      public view of an escalation by reference
"""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from agent.tools import AgentTool
from mcp.caller import McpCaller, customer_key_required
from src.services.orchestrator_workflows import ESCALATION_CATEGORIES, RUN_ID_RE

ESCALATION_SUMMARY_MAX = 500

# PUBLIC pricing tools the support agent may call (same executor pattern as agent/tools.py).
_PUBLIC_PRICING_TOOLS = (
    ("get_all_pricing", "Get current pricing for all LLM models from all providers"),
    ("estimate_cost", "Estimate the cost of using a specific LLM model given input/output token counts"),
    ("get_data_quality",
     "Aggregate accuracy summary of the pricing catalogue (confirmed, withheld, unpriced, stale). "
     "Use when the customer asks how trustworthy the data is."),
)


@dataclass
class SupportTurnContext:
    """Everything a support turn's tools need about the caller, and the escalation they produce."""
    caller: McpCaller
    conversation_id: str
    caller_key: str
    tier: str                       # 'anonymous' | 'free' | 'pro' | 'enterprise' (effective)
    identified: bool
    trigger: str                    # 'http:/support/chat' | 'mcp:support_chat'
    # Set by the agent: opens/re-joins the escalation run; returns the tool result.
    escalate: Optional[Callable[[str, str, Optional[str]], Awaitable[Dict[str, Any]]]] = None
    # Set by the agent: reads an escalation's public view by reference.
    escalation_view: Optional[Callable[[str], Awaitable[Optional[Dict[str, Any]]]]] = None
    # Set by the agent: the caller's own account facts (already allow-listed).
    account: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None
    # Filled when escalate_to_owner succeeds, so the HTTP response can carry the reference.
    escalation: Optional[Dict[str, Any]] = None
    tools_used: List[str] = field(default_factory=list)


def _schema(properties: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


def build_support_tools(tool_manager, rag_pipeline, caller: McpCaller, ctx: SupportTurnContext) -> List[AgentTool]:
    tools: List[AgentTool] = []

    # ------------------------------------------------------------------ knowledge
    async def _faq_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
        ctx.tools_used.append("faq_search")
        query = str(arguments.get("query", ""))
        top_k = max(1, min(int(arguments.get("top_k", 4) or 4), 10))
        if rag_pipeline is None:
            return {"success": True, "chunks": [], "total": 0}
        chunks = rag_pipeline.retrieve(query, top_k=top_k)
        return {
            "success": True,
            "chunks": [{"source": c.source.split("/")[-1].split("\\")[-1], "content": c.content[:700]}
                       for c in chunks],
            "total": len(chunks),
        }

    tools.append(AgentTool(
        name="faq_search",
        description=(
            "Search the published FAQ and setup guides (plans, billing, keys, rate limits, data accuracy, "
            "how to get help, MCP/API how-to). Use this before answering any policy, plan or how-to "
            "question; if it returns nothing relevant, say you don't know and offer escalation."
        ),
        input_schema=_schema({
            "query": {"type": "string", "description": "Natural language search query"},
            "top_k": {"type": "integer", "description": "Results to return (default 4, max 10)",
                      "default": 4, "minimum": 1, "maximum": 10},
        }, ["query"]),
        execute=_faq_search,
    ))

    async def _plan_catalog(arguments: Dict[str, Any]) -> Dict[str, Any]:
        ctx.tools_used.append("get_plan_catalog")
        from src.config.settings import settings
        from src.services.tiers import catalog_payload
        return {
            "success": True,
            **catalog_payload(settings),  # byte-identical to GET /billing/config
            "self_serve": {"signup_and_dashboard": "/billing", "faq": "/faq",
                           "upgrade": "Upgrade tab on /billing (Stripe Checkout)",
                           "cancel_or_card_or_invoices": "Manage Billing on /billing (Stripe customer portal)",
                           "rotate_key": "Rotate key on the /billing Dashboard tab (needs the current key)"},
        }

    tools.append(AgentTool(
        name="get_plan_catalog",
        description=(
            "The exact plan catalogue: price per month, requests per minute, support allowance and "
            "escalation priority for Free, Pro and Enterprise, plus whether checkout and the billing "
            "portal are enabled on this server. The ONLY source of prices and entitlements; never "
            "quote a price, limit or benefit that is not in this result."
        ),
        input_schema=_schema({}),
        execute=_plan_catalog,
    ))

    # ------------------------------------------------------------------ public pricing tools
    if tool_manager is not None:
        for tool_name, description in _PUBLIC_PRICING_TOOLS:
            meta = tool_manager.get_tool(tool_name)
            if not meta:
                continue

            def _make_executor(name: str):
                async def _executor(arguments: Dict[str, Any]) -> Dict[str, Any]:
                    ctx.tools_used.append(name)
                    # PUBLIC tools never see a caller; the anonymous default is deliberate.
                    return await tool_manager.execute_tool(name, arguments)
                return _executor

            tools.append(AgentTool(name=tool_name, description=description,
                                   input_schema=meta["input_schema"], execute=_make_executor(tool_name)))

    # ------------------------------------------------------------------ identity-aware
    async def _my_account(arguments: Dict[str, Any]) -> Dict[str, Any]:
        ctx.tools_used.append("get_my_account")
        if not caller.is_customer or not caller.customer_id:
            refusal = customer_key_required()
            refusal["how_to_identify"] = (
                "Paste your service API key into the key field of the support chat (sent as the x-api-key "
                "header) or connect over MCP with the key; without it the assistant can only answer in general."
            )
            return refusal
        if ctx.account is None:
            return {"success": False, "error": "account lookup unavailable"}
        return await ctx.account()

    tools.append(AgentTool(
        name="get_my_account",
        description=(
            "The calling customer's OWN account: plan (tier), effective tier and payment status, rate "
            "limit, key preview (first 8 characters only), 30-day router savings, support allowance used "
            "today. Takes no arguments and cannot look up anyone else. Anonymous callers get "
            "customer_key_required with instructions on how to identify themselves."
        ),
        input_schema=_schema({}),
        execute=_my_account,
    ))

    async def _escalate(arguments: Dict[str, Any]) -> Dict[str, Any]:
        ctx.tools_used.append("escalate_to_owner")
        category = str(arguments.get("category") or "other")
        if category not in ESCALATION_CATEGORIES:
            category = "other"
        summary = str(arguments.get("summary") or "")[:ESCALATION_SUMMARY_MAX].strip()
        suggested = arguments.get("suggested_reply")
        suggested = str(suggested)[:ESCALATION_SUMMARY_MAX].strip() if suggested else None
        if not summary:
            return {"success": False, "error": "summary is required"}
        if ctx.escalate is None:
            return {"success": False, "error": "escalation unavailable"}
        result = await ctx.escalate(category, summary, suggested)
        if result.get("success"):
            ctx.escalation = {"reference": result["reference"], "public_summary": result["public_summary"]}
        return result

    tools.append(AgentTool(
        name="escalate_to_owner",
        description=(
            "Hand the conversation to the human owner. REQUIRED for anything you cannot do yourself: "
            "refunds, credits, discounts, double charges, disputes, failed payments the customer cannot "
            "fix, a lost key without the current key, security reports, bugs, feature requests, or an "
            "explicit request to talk to a human. Returns a reference the customer must keep. Never "
            "promise an outcome or a response time: the owner's reply is best effort."
        ),
        input_schema=_schema({
            "category": {"type": "string", "enum": list(ESCALATION_CATEGORIES)},
            "summary": {"type": "string", "maxLength": ESCALATION_SUMMARY_MAX,
                        "description": "What the customer needs, in your words (no keys, no emails)"},
            "suggested_reply": {"type": "string", "maxLength": ESCALATION_SUMMARY_MAX,
                                "description": "Optional: what you would reply if you had authority"},
        }, ["category", "summary"]),
        execute=_escalate,
    ))

    async def _check_escalation(arguments: Dict[str, Any]) -> Dict[str, Any]:
        ctx.tools_used.append("check_escalation")
        ref = str(arguments.get("reference") or "").strip()
        if not RUN_ID_RE.match(ref):
            return {"success": False, "error": "unknown reference"}
        if ctx.escalation_view is None:
            return {"success": False, "error": "escalation lookup unavailable"}
        view = await ctx.escalation_view(ref)
        if view is None:
            return {"success": False, "error": "unknown reference"}
        return {"success": True, **view}

    tools.append(AgentTool(
        name="check_escalation",
        description=(
            "Look up an escalation by the reference the customer was given (orc_...): its status and the "
            "owner's reply if there is one. Unknown or malformed references all read as 'unknown reference'."
        ),
        input_schema=_schema({"reference": {"type": "string", "maxLength": 40}}, ["reference"]),
        execute=_check_escalation,
    ))

    return tools
