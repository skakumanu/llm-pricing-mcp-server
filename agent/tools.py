"""Agent tool wrappers: bridge MCP tools and the RAG pipeline for the ReAct loop."""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

from mcp.caller import OPERATOR_ONLY_NOTE


@dataclass
class AgentTool:
    """Metadata and async executor for a single agent tool."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    execute: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]

    def to_llm_schema(self) -> Dict[str, Any]:
        """Return the tool definition in the format expected by LLM backends.

        Strips ``required: []`` (empty list) from the input_schema — Anthropic's
        API rejects schemas where ``required`` is present but empty.
        """
        schema = dict(self.input_schema)
        if "required" in schema and not schema["required"]:
            del schema["required"]
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }


COMPACT_PRICING_LIMIT_DEFAULT = 25
COMPACT_PRICING_LIMIT_MAX = 100
COMPACT_PRICING_SORTS = ("avg", "input", "output", "name")

COMPACT_PRICING_DESCRIPTION = (
    "Current per-token prices as a compact list (model, provider, USD per 1M input and output "
    "tokens, status). ALWAYS narrow it: provider=\"Anthropic\" for one provider, "
    "model_contains=\"gpt-4\" for a name match, sort_by=\"avg\" with limit=10 for the cheapest "
    "models, sort_by=\"output\" for output price. At most 100 rows are returned (default 25) and "
    "total_models says how many matched, so never ask for everything."
)


def compact_pricing_schema(base_schema: Dict[str, Any]) -> Dict[str, Any]:
    """The agent-facing input schema for get_all_pricing: the MCP schema plus filters."""
    schema = dict(base_schema or {"type": "object"})
    props = dict(schema.get("properties") or {})
    props.update({
        "provider": {"type": "string", "description": "Only this provider (case-insensitive, e.g. 'OpenAI')"},
        "model_contains": {"type": "string", "description": "Only models whose name contains this text"},
        "sort_by": {
            "type": "string", "enum": list(COMPACT_PRICING_SORTS),
            "description": "Order: 'avg' (mean of input and output per 1M, cheapest first), 'input', 'output', or 'name'",
        },
        "limit": {
            "type": "integer", "minimum": 1, "maximum": COMPACT_PRICING_LIMIT_MAX,
            "description": f"Rows to return (default {COMPACT_PRICING_LIMIT_DEFAULT}, max {COMPACT_PRICING_LIMIT_MAX})",
        },
    })
    schema["properties"] = props
    schema.pop("required", None)
    return schema


def _price_status(m: Dict[str, Any]) -> str:
    if m.get("price_is_stale"):
        return "stale"
    if m.get("price_confirmed") is False:
        return "unconfirmed"
    kind = m.get("price_source_kind")
    return str(kind) if kind else "confirmed"


def compact_pricing_result(raw: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Project a get_all_pricing result to compact rows, filtered, sorted and limited.

    Pure and synchronous so it can be tested without the live catalogue. Each row is
    ~100 characters, so even the maximum of 100 rows stays far below the loop's
    tool-result bound.
    """
    if not isinstance(raw, dict) or not raw.get("success", True):
        return raw
    models = [m for m in (raw.get("models") or []) if isinstance(m, dict)]
    provider = str(arguments.get("provider") or "").strip().lower()
    contains = str(arguments.get("model_contains") or "").strip().lower()
    sort_by = arguments.get("sort_by") or "avg"
    if sort_by not in COMPACT_PRICING_SORTS:
        sort_by = "avg"
    try:
        limit = int(arguments.get("limit") or COMPACT_PRICING_LIMIT_DEFAULT)
    except (TypeError, ValueError):
        limit = COMPACT_PRICING_LIMIT_DEFAULT
    limit = max(1, min(limit, COMPACT_PRICING_LIMIT_MAX))

    rows = []
    for m in models:
        name = str(m.get("model_name", ""))
        prov = str(m.get("provider", ""))
        if provider and prov.lower() != provider:
            continue
        if contains and contains not in name.lower():
            continue
        try:
            inp = float(m.get("cost_per_input_token") or 0) * 1_000_000
            out = float(m.get("cost_per_output_token") or 0) * 1_000_000
        except (TypeError, ValueError):
            inp, out = 0.0, 0.0
        row = {
            "model": name,
            "provider": prov,
            "input_per_1m": round(inp, 4),
            "output_per_1m": round(out, 4),
            "context_window": m.get("context_window"),
            "status": _price_status(m),
            "price_as_of": m.get("price_as_of"),
        }
        local = m.get("local_prices")
        if isinstance(local, dict) and local.get("currency"):
            row["local_currency"] = local.get("currency")
        rows.append(row)

    keys = {
        "avg": lambda r: ((r["input_per_1m"] + r["output_per_1m"]) / 2, r["model"]),
        "input": lambda r: (r["input_per_1m"], r["model"]),
        "output": lambda r: (r["output_per_1m"], r["model"]),
        "name": lambda r: (r["model"].lower(), r["provider"]),
    }
    if sort_by in ("avg", "input", "output"):
        # Unpriced rows (0/0) go last so "cheapest" never answers with a model that has no price.
        rows.sort(key=lambda r: (r["input_per_1m"] == 0 and r["output_per_1m"] == 0, keys[sort_by](r)))
    else:
        rows.sort(key=keys[sort_by])

    out = {
        "success": True,
        "total_models": len(rows),
        "returned": min(limit, len(rows)),
        "sort_by": sort_by,
        "unit": "USD per 1M tokens",
        "models": rows[:limit],
        "timestamp": raw.get("timestamp"),
    }
    if provider and not rows:
        out["note"] = f"No models for provider '{arguments.get('provider')}'. Known providers: " + \
            ", ".join(sorted({str(m.get('provider', '')) for m in models})[:40])
    if len(rows) > limit:
        out["note"] = (out.get("note", "") + f" {len(rows) - limit} more rows match; raise limit "
                       f"(max {COMPACT_PRICING_LIMIT_MAX}) or narrow with provider / model_contains.").strip()
    return out


def build_agent_tools(tool_manager, rag_pipeline) -> List[AgentTool]:
    """Build the full list of agent tools from the ToolManager and RAG pipeline.

    Existing MCP tools are wrapped as-is (no duplication of business logic).
    A new 'rag_retrieve' tool is added to expose the RAG pipeline.
    """
    tools: List[AgentTool] = []

    # Wrap existing MCP tools. This is an explicit allow-list: anything not named here is
    # never bound. Deliberately absent: ask_agent (self-recursion), get_telemetry and
    # get_cache_stats (server ops), and dispatch (v1.80.0 — a real, paid model call on a
    # customer's stored provider keys; the anonymous agent must never be able to make one).
    _mcp_tool_specs = [
        (
            "get_all_pricing",
            "Get current pricing for all LLM models from all providers",
        ),
        (
            "estimate_cost",
            "Estimate the cost of using a specific LLM model given input/output token counts",
        ),
        (
            "compare_costs",
            "Compare costs for multiple LLM models side-by-side",
        ),
        (
            "predict_cost",
            (
                "Predict the real cost of an LLM call from the actual prompt text, without "
                "needing token counts. Counts tokens, infers the task type, estimates output "
                "length, and ranks every model cheapest-first including prompt-cache savings. "
                "Use when the user pastes a prompt and asks what it will cost, or which model "
                "to pick for it."
            ),
        ),
        (
            "optimize_workload",
            (
                "Optimise a multi-task workload by assigning the cheapest qualifying model "
                "to each task instead of one model for everything. Returns a per-task "
                "allocation, total monthly cost, and the saving versus the best single-model "
                "deployment. Use when the user wants to cut LLM spend across several task "
                "types or asks whether tiered model routing is worth it."
            ),
        ),
        (
            "recommend_model",
            (
                "Recommend the optimal LLM model from a free-text description of a use case — "
                "classifies the description into a task type and runs the same routing engine "
                "behind /router/recommend. Returns the best match plus up to 3 alternatives. "
                "Use when the user describes what they want to do and asks which model to use, "
                "without already knowing a specific task_type."
            ),
        ),
        (
            "check_price_drift",
            (
                "Audit this server's own pricing data against an external reference "
                "registry and report models whose price has drifted. Use when the user "
                "asks whether a specific model's or provider's price is accurate, current, "
                "or trustworthy."
            ),
        ),
        (
            "get_data_quality",
            (
                "Get an aggregate accuracy summary for the whole pricing catalogue: percent "
                "confirmed and fresh, how many models are withheld for price drift, how many "
                "are newly discovered and unpriced, how many are stale. Use when the user asks "
                "how accurate or trustworthy the data is overall, rather than about one model."
            ),
        ),
        (
            "get_ide_pricing",
            (
                "Get subscription pricing for AI coding IDE tools (GitHub Copilot, Cursor, "
                "Windsurf, Claude.ai, JetBrains AI, Amazon Q, Tabnine). Use when the user asks "
                "about IDE assistants, coding subscriptions, or monthly per-seat AI tooling."
            ),
        ),
        (
            "get_performance_metrics",
            "Get throughput, latency, and context window metrics for LLM models",
        ),
        (
            "get_use_cases",
            "Get recommended use cases and key strengths for LLM models",
        ),
        (
            "get_pricing_history",
            (
                "Query historical pricing snapshots to look up past prices for a model or "
                "provider and see how prices have changed over time"
            ),
        ),
        (
            "get_pricing_trends",
            (
                "Find models whose prices changed the most recently. Use for questions like "
                "'which models got cheaper this month?' or 'which providers raised prices?'"
            ),
        ),
        (
            "register_price_alert",
            (
                "Register a webhook URL to be notified when a model's price changes beyond "
                "a threshold. Use when the user asks to set up a price alert or notification. "
                "The result contains an alert_token shown only once: always give it to the user "
                "verbatim and tell them to save it; it is the only way to view or delete the "
                "alert later."
            ),
        ),
        (
            "list_price_alerts",
            (
                "List all registered price-change webhook alerts with their IDs and settings. "
                f"{OPERATOR_ONLY_NOTE}"
            ),
        ),
        (
            "delete_price_alert",
            (
                "Delete a registered price-change alert by its ID. "
                f"{OPERATOR_ONLY_NOTE}"
                " To view or delete an alert you registered yourself, use manage_price_alert"
                " with the alert_token from registration."
            ),
        ),
        (
            "manage_price_alert",
            (
                "View or delete ONE price alert the user registered, using the alert_token the "
                "user supplies. Never invent or guess a token. On refusal, say no alert matched "
                "that token; never speculate about alert ids or other alerts."
            ),
        ),
        (
            "get_pricing_export_url",
            (
                "Generate a download URL for pricing history as CSV or JSON. "
                "Use when the user asks to export, download, or save pricing data."
            ),
        ),
        (
            "list_conversations",
            (
                "List stored chat conversation sessions with IDs, timestamps, turn counts, "
                "and message previews. Use when the user asks to see their chat history or "
                "past conversations. "
                f"{OPERATOR_ONLY_NOTE}"
            ),
        ),
        (
            "delete_conversation",
            (
                "Delete a specific past conversation by its ID. Use list_conversations first "
                "to find the ID. Use when the user asks to delete or clear a past conversation. "
                f"{OPERATOR_ONLY_NOTE}"
            ),
        ),
        (
            "record_usage",
            (
                "Record actual token usage for a completed LLM call — cost is computed "
                "server-side from current pricing, never trusted from the caller. Use when the "
                "user wants to log or track real LLM spend, not just estimate it."
            ),
        ),
        (
            "get_usage_summary",
            (
                "Get a summary of actual recorded LLM spend: total cost, request count, and "
                "breakdowns by model and provider. Use when the user asks what they've actually "
                "spent, as opposed to a hypothetical cost estimate."
            ),
        ),
        (
            "analyze_session_usage",
            (
                "Analyze one specific session's actual recorded LLM usage (tagged via "
                "record_usage's session_id): total tokens, total actual cost, a per-model "
                "breakdown, and a grounded model recommendation with estimated savings versus "
                "that session's real cost — or confirmation the current choice is already "
                "optimal. Use when the user asks what they should be doing differently for a "
                "specific session, coding-agent run, or chat session they've already logged "
                "usage for."
            ),
        ),
        (
            "import_session_usage",
            (
                "Bulk-import a completed session's usage in a single call — the retroactive "
                "counterpart to record_usage, for a session that wasn't instrumented live. "
                "Accepts a list of entries under one session_id, each resolving tokens from "
                "exact counts or estimating from raw turn text. Use when the user wants to "
                "backfill or reconstruct a past session's usage all at once."
            ),
        ),
        (
            "register_budget_alert",
            (
                "Register a webhook URL to be notified when actual recorded spend crosses a USD "
                "threshold. Use when the user asks to set up a spend or budget alert."
            ),
        ),
        (
            "list_budget_alerts",
            "List all registered budget-spend webhook alerts with their IDs and settings.",
        ),
        (
            "delete_budget_alert",
            "Delete a registered budget-spend alert by its ID.",
        ),
    ]

    for tool_name, description in _mcp_tool_specs:
        tool_meta = tool_manager.get_tool(tool_name)
        if not tool_meta:
            continue

        def _make_executor(name: str) -> Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]:
            async def _executor(arguments: Dict[str, Any]) -> Dict[str, Any]:
                return await tool_manager.execute_tool(name, arguments)
            return _executor

        if tool_name == "get_all_pricing":
            # v1.90.1: the agent gets a compact, filterable edition of the catalogue (the
            # full MCP result is ~140k tokens for 600+ models and overflowed the model's
            # context in production).
            def _make_compact_executor(name: str):
                async def _executor(arguments: Dict[str, Any]) -> Dict[str, Any]:
                    passthrough = {k: v for k, v in arguments.items() if k in ("currency", "country")}
                    raw = await tool_manager.execute_tool(name, passthrough)
                    return compact_pricing_result(raw, arguments)
                return _executor

            tools.append(AgentTool(
                name=tool_name,
                description=COMPACT_PRICING_DESCRIPTION,
                input_schema=compact_pricing_schema(tool_meta["input_schema"]),
                execute=_make_compact_executor(tool_name),
            ))
            continue

        tools.append(AgentTool(
            name=tool_name,
            description=description,
            input_schema=tool_meta["input_schema"],
            execute=_make_executor(tool_name),
        ))

    # RAG retrieve tool
    async def _rag_retrieve(arguments: Dict[str, Any]) -> Dict[str, Any]:
        query = arguments.get("query", "")
        # Clamp top_k to a safe range regardless of what the LLM sends
        top_k = max(1, min(int(arguments.get("top_k", 5)), 20))
        chunks = rag_pipeline.retrieve(query, top_k=top_k)
        return {
            "success": True,
            "chunks": [
                {"source": c.source, "content": c.content[:600]}
                for c in chunks
            ],
            "total": len(chunks),
        }

    tools.append(AgentTool(
        name="rag_retrieve",
        description=(
            "Search the knowledge base (documentation and pricing data) for relevant context. "
            "Use this before answering questions about LLM pricing, providers, or capabilities."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5, min: 1, max: 20)",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": ["query"],
        },
        execute=_rag_retrieve,
    ))

    return tools
