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
        """Return the tool definition in the format expected by LLM backends."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
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
