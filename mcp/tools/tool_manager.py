"""Tool management for MCP server."""
from typing import Dict, Any, Callable
from mcp.tools.get_all_pricing import GetAllPricingTool
from mcp.tools.estimate_cost import EstimateCostTool
from mcp.tools.compare_costs import CompareCostsTool
from mcp.tools.get_performance_metrics import GetPerformanceMetricsTool
from mcp.tools.get_use_cases import GetUseCasesTool


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
