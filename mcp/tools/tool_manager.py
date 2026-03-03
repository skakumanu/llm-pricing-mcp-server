"""Tool management for MCP server."""
from typing import Dict, Any, Callable
from mcp.tools.get_all_pricing import GetAllPricingTool
from mcp.tools.estimate_cost import EstimateCostTool
from mcp.tools.compare_costs import CompareCostsTool
from mcp.tools.get_performance_metrics import GetPerformanceMetricsTool
from mcp.tools.get_use_cases import GetUseCasesTool
from mcp.tools.get_telemetry import GetTelemetryTool
from mcp.tools.ask_agent import AskAgentTool


class ToolManager:
    """Manages all MCP tools and their metadata."""
    
    def __init__(self):
        """Initialize all tools."""
        self.tools: Dict[str, Any] = {}
        self._ask_agent_tool = AskAgentTool()  # agent bound later via set_pricing_agent()
        self._register_tools()

    def set_pricing_agent(self, agent) -> None:
        """Bind the PricingAgent to the ask_agent tool (called after agent.initialize())."""
        self._ask_agent_tool.set_agent(agent)
    
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
                "description": "Get MCP server telemetry and usage statistics including tool usage, response times, and error rates",
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
            "ask_agent": {
                "instance": self._ask_agent_tool,
                "name": "ask_agent",
                "description": (
                    "Ask the LLM pricing agent a natural language question. "
                    "The agent uses RAG and live pricing tools to produce a sourced answer. "
                    "Supports multi-turn conversations via conversation_id."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Natural language question or task for the agent",
                        },
                        "conversation_id": {
                            "type": "string",
                            "description": "Optional UUID to continue an existing conversation",
                        },
                        "autonomous": {
                            "type": "boolean",
                            "description": "If true, run as autonomous multi-step task (no history)",
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
