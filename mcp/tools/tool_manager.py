"""Tool management for MCP server."""
from typing import Dict, Any
from mcp.tools.get_all_pricing import GetAllPricingTool
from mcp.tools.estimate_cost import EstimateCostTool
from mcp.tools.compare_costs import CompareCostsTool
from mcp.tools.get_performance_metrics import GetPerformanceMetricsTool
from mcp.tools.get_use_cases import GetUseCasesTool
from mcp.tools.get_telemetry import GetTelemetryTool
from mcp.tools.get_pricing_history import GetPricingHistoryTool
from mcp.tools.get_pricing_trends import GetPricingTrendsTool
from mcp.tools.register_price_alert import RegisterPriceAlertTool
from mcp.tools.list_price_alerts import ListPriceAlertsTool
from mcp.tools.delete_price_alert import DeletePriceAlertTool
from mcp.tools.get_pricing_export_url import GetPricingExportUrlTool
from mcp.tools.list_conversations import ListConversationsTool
from mcp.tools.delete_conversation import DeleteConversationTool
from mcp.tools.ask_agent import AskAgentTool
from mcp.tools.get_ide_pricing import GetIDEPricingTool
from mcp.tools.predict_cost import PredictCostTool
from mcp.tools.optimize_workload import OptimizeWorkloadTool
from mcp.tools.check_price_drift import CheckPriceDriftTool
from mcp.tools.get_data_quality import GetDataQualityTool
from mcp.tools.record_usage import RecordUsageTool
from mcp.tools.get_usage_summary import GetUsageSummaryTool


class ToolManager:
    """Manages all MCP tools and their metadata."""

    def __init__(self):
        """Initialize all tools."""
        self.tools: Dict[str, Any] = {}
        self._register_tools()

    def _register_tools(self):
        """Register all available tools."""
        self.tools = {
            "get_all_pricing": {
                "instance": GetAllPricingTool(),
                "name": "get_all_pricing",
                "description": "Get current pricing for all LLM models from all providers",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            "estimate_cost": {
                "instance": EstimateCostTool(),
                "name": "estimate_cost",
                "description": "Estimate the cost of using a specific LLM model",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "model_name": {
                            "type": "string",
                            "description": "Name of the LLM model (e.g., 'gpt-4', 'claude-3-opus')",
                        },
                        "input_tokens": {
                            "type": "integer",
                            "description": "Number of input tokens",
                            "minimum": 0,
                        },
                        "output_tokens": {
                            "type": "integer",
                            "description": "Number of output tokens",
                            "minimum": 0,
                        },
                    },
                    "required": ["model_name", "input_tokens", "output_tokens"],
                },
            },
            "compare_costs": {
                "instance": CompareCostsTool(),
                "name": "compare_costs",
                "description": "Compare costs for multiple LLM models",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "model_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of model names to compare",
                        },
                        "input_tokens": {
                            "type": "integer",
                            "description": "Number of input tokens",
                            "minimum": 0,
                        },
                        "output_tokens": {
                            "type": "integer",
                            "description": "Number of output tokens",
                            "minimum": 0,
                        },
                    },
                    "required": ["model_names", "input_tokens", "output_tokens"],
                },
            },
            "get_performance_metrics": {
                "instance": GetPerformanceMetricsTool(),
                "name": "get_performance_metrics",
                "description": "Get performance metrics (throughput, latency, context window) for models",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Optional provider filter (e.g., 'openai', 'anthropic')",
                        },
                        "include_cost": {
                            "type": "boolean",
                            "description": "Include cost metrics in response (default: true)",
                            "default": True,
                        },
                    },
                    "required": [],
                },
            },
            "get_use_cases": {
                "instance": GetUseCasesTool(),
                "name": "get_use_cases",
                "description": "Get recommended use cases and strengths for LLM models",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Optional provider filter",
                        },
                    },
                    "required": [],
                },
            },
            "get_telemetry": {
                "instance": GetTelemetryTool(),
                "name": "get_telemetry",
                "description": (
                    "Get MCP server telemetry and usage statistics including "
                    "tool usage, response times, and error rates"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "include_details": {
                            "type": "boolean",
                            "description": "Include detailed statistics (default: true)",
                            "default": True,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum items per category (default: 10, max: 50)",
                            "default": 10,
                            "minimum": 1,
                            "maximum": 50,
                        },
                    },
                    "required": [],
                },
            },
            "get_pricing_history": {
                "instance": GetPricingHistoryTool(),
                "name": "get_pricing_history",
                "description": (
                    "Query historical pricing snapshots recorded over time. "
                    "Use this to look up past prices for a model or provider, "
                    "or to see how prices have changed over a given number of days."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "model_name": {
                            "type": "string",
                            "description": "Filter by model name (e.g. 'gpt-4o'). Omit for all models.",
                        },
                        "provider": {
                            "type": "string",
                            "description": "Filter by provider (e.g. 'openai'). Omit for all providers.",
                        },
                        "days": {
                            "type": "integer",
                            "description": "Look-back window in days (default: 30, max: 365)",
                            "default": 30,
                            "minimum": 1,
                            "maximum": 365,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of snapshot rows to return (default: 50, max: 500)",
                            "default": 50,
                            "minimum": 1,
                            "maximum": 500,
                        },
                    },
                    "required": [],
                },
            },
            "get_pricing_trends": {
                "instance": GetPricingTrendsTool(),
                "name": "get_pricing_trends",
                "description": (
                    "Find models whose prices changed the most over a given period. "
                    "Returns models sorted by absolute percentage price change with a "
                    "direction label ('increased', 'decreased', or 'unchanged'). "
                    "Use this to answer questions like 'which models got cheaper this month?' "
                    "or 'which providers raised prices recently?'"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Look-back window in days (default: 30, max: 365)",
                            "default": 30,
                            "minimum": 1,
                            "maximum": 365,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum models to return (default: 20, max: 100)",
                            "default": 20,
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                    "required": [],
                },
            },
            "register_price_alert": {
                "instance": RegisterPriceAlertTool(),
                "name": "register_price_alert",
                "description": (
                    "Register a webhook URL to receive notifications when a model's price "
                    "changes by more than a specified percentage. Optionally scope the alert "
                    "to a specific provider or model. Returns the alert ID."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "Webhook URL to POST when the alert fires",
                        },
                        "threshold_pct": {
                            "type": "number",
                            "description": "Minimum absolute % price change to trigger the alert (default: 5.0)",
                            "default": 5.0,
                        },
                        "provider": {
                            "type": "string",
                            "description": "Limit to a specific provider (e.g. 'openai'). Omit for all.",
                        },
                        "model_name": {
                            "type": "string",
                            "description": "Limit to a specific model name. Omit for all models.",
                        },
                    },
                    "required": ["url"],
                },
            },
            "list_price_alerts": {
                "instance": ListPriceAlertsTool(),
                "name": "list_price_alerts",
                "description": "List all registered price-change webhook alerts with their IDs and settings.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            "delete_price_alert": {
                "instance": DeletePriceAlertTool(),
                "name": "delete_price_alert",
                "description": "Delete a registered price-change alert by its ID.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "alert_id": {
                            "type": "integer",
                            "description": "The ID of the alert to delete (from list_price_alerts or register_price_alert)",
                        },
                    },
                    "required": ["alert_id"],
                },
            },
            "get_pricing_export_url": {
                "instance": GetPricingExportUrlTool(),
                "name": "get_pricing_export_url",
                "description": (
                    "Generate a download URL for the pricing history export. "
                    "Returns a URL the user can open in their browser to download "
                    "snapshot history as CSV (for spreadsheets) or JSON (for code). "
                    "Use this when the user asks to export, download, or save pricing data."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "format": {
                            "type": "string",
                            "description": "Output format: 'csv' (default) or 'json'",
                            "enum": ["csv", "json"],
                            "default": "csv",
                        },
                        "model_name": {
                            "type": "string",
                            "description": "Filter by model name (optional)",
                        },
                        "provider": {
                            "type": "string",
                            "description": "Filter by provider (optional)",
                        },
                        "days": {
                            "type": "integer",
                            "description": "Look-back window in days (default: 30, max: 365)",
                            "default": 30,
                            "minimum": 1,
                            "maximum": 365,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max rows to export (default: 10000, max: 100000)",
                            "default": 10000,
                            "minimum": 1,
                            "maximum": 100000,
                        },
                    },
                    "required": [],
                },
            },
            "list_conversations": {
                "instance": ListConversationsTool(),
                "name": "list_conversations",
                "description": (
                    "List stored chat conversation sessions with their IDs, last-updated "
                    "timestamp, turn count, and a preview of the most recent user message. "
                    "Use this when the user asks to see their past conversations or chat history."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of conversations to return (default: 20, max: 100)",
                            "default": 20,
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                    "required": [],
                },
            },
            "delete_conversation": {
                "instance": DeleteConversationTool(),
                "name": "delete_conversation",
                "description": (
                    "Delete a specific chat conversation by its ID. "
                    "Use when the user asks to delete, clear, or remove a past conversation. "
                    "Use list_conversations first to find the conversation ID."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "conversation_id": {
                            "type": "string",
                            "description": "The ID of the conversation to delete",
                        },
                    },
                    "required": ["conversation_id"],
                },
            },
            "get_ide_pricing": {
                "instance": GetIDEPricingTool(),
                "name": "get_ide_pricing",
                "description": (
                    "Get subscription pricing for AI coding IDE tools: GitHub Copilot, Cursor, "
                    "Windsurf, Claude.ai, JetBrains AI, Amazon Q Developer, and Tabnine. "
                    "Returns monthly subscription cost, inline completion support, context window, "
                    "and per-tool strengths. Use this when the user asks to compare IDE AI tools, "
                    "subscription-based coding assistants, or wants to know which IDE AI fits their budget."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": (
                                "Filter by provider name (partial match, case-insensitive). "
                                "Examples: 'GitHub Copilot', 'Cursor', 'Windsurf', 'JetBrains', 'Amazon Q', 'Tabnine'"
                            ),
                        },
                        "max_monthly": {
                            "type": "number",
                            "description": "Only return plans at or below this monthly USD price (e.g. 20 for $20/mo)",
                            "minimum": 0,
                        },
                        "inline_only": {
                            "type": "boolean",
                            "description": "If true, only return tools that support real-time inline code completion",
                            "default": False,
                        },
                    },
                    "required": [],
                },
            },
            "predict_cost": {
                "instance": PredictCostTool(),
                "name": "predict_cost",
                "description": (
                    "Predict the real cost of an LLM call from your actual prompt text — "
                    "no need to know token counts in advance. Counts tokens locally, infers "
                    "the task type (or accepts one explicitly), estimates output length from "
                    "empirical I/O profiles, and returns all models ranked cheapest-first with "
                    "effective cost after prompt-cache savings. Also surfaces the best "
                    "quality-per-dollar pick. Use this before making any LLM API call to avoid "
                    "surprise bills."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The actual prompt text to cost-estimate (required)",
                        },
                        "task_type": {
                            "type": "string",
                            "description": (
                                "Task type for output-length estimation. Auto-inferred from prompt "
                                "if omitted. Options: classification, extraction, summarization, qa, "
                                "code_generation, translation, chat, content_generation, reasoning, "
                                "function_calling, data_analysis, rewrite"
                            ),
                            "enum": [
                                "classification", "extraction", "summarization", "qa",
                                "code_generation", "translation", "chat", "content_generation",
                                "reasoning", "function_calling", "data_analysis", "rewrite",
                            ],
                        },
                        "top_n": {
                            "type": "integer",
                            "description": "Number of models to return, ranked cheapest-first (default: 10, max: 50)",
                            "default": 10,
                            "minimum": 1,
                            "maximum": 50,
                        },
                        "cache_hit_ratio": {
                            "type": "number",
                            "description": (
                                "Fraction of calls that hit the prompt cache (0–1). "
                                "Set this if you have a large reusable system prompt. "
                                "Supported providers: Anthropic (10% cache rate), "
                                "OpenAI (50%), Google (25%). Default: 0"
                            ),
                            "default": 0.0,
                            "minimum": 0.0,
                            "maximum": 1.0,
                        },
                        "require_function_calling": {
                            "type": "boolean",
                            "description": "Only include models that support function/tool calling (default: false)",
                            "default": False,
                        },
                        "require_vision": {
                            "type": "boolean",
                            "description": "Only include models that support image/vision input (default: false)",
                            "default": False,
                        },
                        "min_context_tokens": {
                            "type": "integer",
                            "description": "Minimum context window size required in tokens (optional)",
                            "minimum": 1,
                        },
                    },
                    "required": ["prompt"],
                },
            },
            "optimize_workload": {
                "instance": OptimizeWorkloadTool(),
                "name": "optimize_workload",
                "description": (
                    "Optimise a multi-task LLM workload by assigning the cheapest qualifying "
                    "model to each task instead of running everything through one model. "
                    "Takes a list of workloads (task type + monthly volume + optional quality "
                    "floor and capability requirements) and returns a per-task model allocation, "
                    "the total monthly cost, and the saving versus the best single-model "
                    "deployment. If a monthly budget is given, leftover headroom is spent on the "
                    "most cost-efficient quality upgrades. Use when the user asks how to cut LLM "
                    "spend across several task types, or whether tiered model routing is worth it."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "workloads": {
                            "type": "array",
                            "description": "One entry per task type in the workload mix",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "task_type": {
                                        "type": "string",
                                        "description": "Task category driving the input/output token ratio",
                                        "enum": [
                                            "classification", "extraction", "summarization", "qa",
                                            "code_generation", "translation", "chat",
                                            "content_generation", "reasoning", "function_calling",
                                            "data_analysis", "rewrite",
                                        ],
                                    },
                                    "monthly_requests": {
                                        "type": "integer",
                                        "description": "Expected requests per month for this task",
                                        "minimum": 0,
                                    },
                                    "avg_input_tokens": {
                                        "type": "integer",
                                        "description": "Average prompt size in tokens (default: 500)",
                                        "default": 500,
                                        "minimum": 1,
                                    },
                                    "avg_output_tokens": {
                                        "type": "integer",
                                        "description": (
                                            "Average completion size in tokens. Derived from the "
                                            "task profile when omitted."
                                        ),
                                        "minimum": 1,
                                    },
                                    "min_quality_score": {
                                        "type": "number",
                                        "description": "Quality floor (0-100) for this task specifically",
                                        "minimum": 0,
                                        "maximum": 100,
                                    },
                                    "require_function_calling": {
                                        "type": "boolean",
                                        "description": "Task needs tool/function calling (default: false)",
                                        "default": False,
                                    },
                                    "require_vision": {
                                        "type": "boolean",
                                        "description": "Task needs image input (default: false)",
                                        "default": False,
                                    },
                                    "min_context_tokens": {
                                        "type": "integer",
                                        "description": "Minimum context window required for this task",
                                        "minimum": 1,
                                    },
                                    "label": {
                                        "type": "string",
                                        "description": "Friendly name for this workload in the output",
                                    },
                                },
                                "required": ["task_type", "monthly_requests"],
                            },
                        },
                        "monthly_budget_usd": {
                            "type": "number",
                            "description": (
                                "Total monthly budget in USD. Leftover headroom is spent on the "
                                "most cost-efficient quality upgrades."
                            ),
                            "exclusiveMinimum": 0,
                        },
                        "min_quality_score": {
                            "type": "number",
                            "description": "Global quality floor (0-100) applied to every workload",
                            "minimum": 0,
                            "maximum": 100,
                        },
                    },
                    "required": ["workloads"],
                },
            },
            "check_price_drift": {
                "instance": CheckPriceDriftTool(),
                "name": "check_price_drift",
                "description": (
                    "Audit this server's own pricing data against an external reference "
                    "registry. Prices here are hand-maintained and go stale silently; a price "
                    "that disagrees with the registry by more than the threshold is "
                    "automatically withheld from serving the moment it's detected, not just "
                    "when this tool runs. This reports what's currently withheld and why. The "
                    "price value itself is never overwritten by the registry. Use when the "
                    "user asks whether the pricing data is accurate or up to date, or wants to "
                    "audit a specific provider's rates."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "threshold_pct": {
                            "type": "number",
                            "description": "Minimum % difference to report (default: 5)",
                            "default": 5.0,
                            "minimum": 0,
                        },
                        "provider": {
                            "type": "string",
                            "description": "Limit the audit to one provider (partial, case-insensitive)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum findings to return (default: 50, max: 200)",
                            "default": 50,
                            "minimum": 1,
                            "maximum": 200,
                        },
                    },
                    "required": [],
                },
            },
            "get_data_quality": {
                "instance": GetDataQualityTool(),
                "name": "get_data_quality",
                "description": (
                    "Report an aggregate accuracy summary for the pricing catalogue: what "
                    "percentage of models have a confirmed, fresh price, how many are "
                    "currently withheld due to price drift, how many are newly discovered "
                    "and not yet priced, and how many confirmed prices are over 90 days old. "
                    "A single trust signal, distinct from check_price_drift which lists "
                    "individual disputes. Use when the user asks how accurate or trustworthy "
                    "the pricing data is, overall."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            "record_usage": {
                "instance": RecordUsageTool(),
                "name": "record_usage",
                "description": (
                    "Record actual token usage for a completed LLM call. Cost is computed "
                    "server-side from current pricing, never trusted from the caller — this is "
                    "the 'actual spend' counterpart to estimate_cost's hypothetical numbers. "
                    "Optionally scope to an org_id for per-org spend tracking. Resubmitting the "
                    "same request_id for the same org is a no-op. Use when the user wants to log "
                    "or track real LLM spend, not just estimate it."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "model_name": {
                            "type": "string",
                            "description": "Name of the LLM model that was called",
                        },
                        "input_tokens": {
                            "type": "integer",
                            "description": "Number of input tokens actually used",
                            "minimum": 0,
                        },
                        "output_tokens": {
                            "type": "integer",
                            "description": "Number of output tokens actually used",
                            "minimum": 0,
                        },
                        "org_id": {
                            "type": "string",
                            "description": "Optional organisation ID to attribute this usage to",
                        },
                        "occurred_at": {
                            "type": "number",
                            "description": "Unix timestamp when the call happened (default: now)",
                        },
                        "request_id": {
                            "type": "string",
                            "description": "Optional idempotency key — resubmitting the same value is a no-op",
                        },
                    },
                    "required": ["model_name", "input_tokens", "output_tokens"],
                },
            },
            "get_usage_summary": {
                "instance": GetUsageSummaryTool(),
                "name": "get_usage_summary",
                "description": (
                    "Get a summary of actual recorded LLM spend: total cost, request count, and "
                    "breakdowns by model and provider, from usage events previously logged via "
                    "record_usage. Use when the user asks what they've actually spent, as opposed "
                    "to a hypothetical cost estimate."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "org_id": {
                            "type": "string",
                            "description": "Optional organisation ID filter",
                        },
                        "days": {
                            "type": "integer",
                            "description": "Look-back window in days (default: 30, max: 365)",
                            "default": 30,
                            "minimum": 1,
                            "maximum": 365,
                        },
                    },
                    "required": [],
                },
            },
            "ask_agent": {
                "instance": AskAgentTool(),
                "name": "ask_agent",
                "description": (
                    "Ask the LLM Pricing AI agent a question in natural language. "
                    "Use for complex queries about pricing, model recommendations, "
                    "cost optimisation advice, or anything that benefits from multi-step reasoning."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The question or task for the pricing agent",
                        },
                        "conversation_id": {
                            "type": "string",
                            "description": "Optional conversation ID to continue a previous session",
                        },
                        "autonomous": {
                            "type": "boolean",
                            "description": "Run in autonomous task mode (default: false)",
                            "default": False,
                        },
                    },
                    "required": ["message"],
                },
            },
        }

    def get_tool(self, name: str) -> Dict[str, Any]:
        """Get tool metadata by name."""
        return self.tools.get(name)

    def list_tools(self):
        """List all available tools with their metadata."""
        return [
            {
                "name": tool["name"],
                "description": tool["description"],
                "inputSchema": tool["input_schema"],
            }
            for tool in self.tools.values()
        ]

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool by name with given arguments."""
        tool = self.tools.get(name)
        if not tool:
            return {
                "success": False,
                "error": f"Tool '{name}' not found",
            }

        try:
            result = await tool["instance"].execute(arguments)
            return result
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to execute tool '{name}': {str(e)}",
                "error_type": type(e).__name__,
            }
