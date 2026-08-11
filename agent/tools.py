"""Agent tool wrappers: bridge MCP tools and the RAG pipeline for the ReAct loop."""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List


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


def build_agent_tools(tool_manager, rag_pipeline) -> List[AgentTool]:
    """Build the full list of agent tools from the ToolManager and RAG pipeline.

    Existing MCP tools are wrapped as-is (no duplication of business logic).
    A new 'rag_retrieve' tool is added to expose the RAG pipeline.
    """
    tools: List[AgentTool] = []

    # Wrap existing MCP tools
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
                "a threshold. Use when the user asks to set up a price alert or notification."
            ),
        ),
        (
            "list_price_alerts",
            "List all registered price-change webhook alerts with their IDs and settings.",
        ),
        (
            "delete_price_alert",
            "Delete a registered price-change alert by its ID.",
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
                "past conversations."
            ),
        ),
        (
            "delete_conversation",
            (
                "Delete a specific past conversation by its ID. Use list_conversations first "
                "to find the ID. Use when the user asks to delete or clear a past conversation."
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
