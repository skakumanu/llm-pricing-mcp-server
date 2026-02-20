"""MCP Server wrapper that proxies to Azure REST API.

This module allows the MCP server to call the remote LLM Pricing API
hosted on Azure instead of using local tool implementations.

Usage:
    python -m mcp.server_azure
    
Environment variables:
    API_BASE_URL: Base URL for the pricing API (default: Azure endpoint)
    API_TIMEOUT: Request timeout in seconds (default: 30)
"""

import sys
import json
import asyncio
import logging
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.client_azure import AzurePricingAPIClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


class AzureMCPServer:
    """MCP Server that proxies to Azure REST API."""
    
    def __init__(self):
        """Initialize the Azure MCP server."""
        self.version = "1.1.0"
        self.name = "LLM Pricing MCP Server (Azure)"
        self.api_client = AzurePricingAPIClient()
        self.request_counter = 0
        logger.info(f"Initialized {self.name}")
    
    async def run(self):
        """Run the MCP server in STDIO mode."""
        logger.info("MCP Server starting (STDIO mode with Azure backend)")
        print(json.dumps(self._get_initialization_response()), flush=True)
        
        try:
            loop = asyncio.get_event_loop()
            while True:
                try:
                    line = await loop.run_in_executor(None, sys.stdin.readline)
                    if not line:
                        logger.info("EOF reached, shutting down")
                        break
                    
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        request = json.loads(line)
                        response = await self._handle_request(request)
                        print(json.dumps(response), flush=True)
                    except json.JSONDecodeError as e:
                        logger.error(f"Invalid JSON: {e}")
                        print(json.dumps(self._error_response(-32700, "Parse error")), flush=True)
                    
                except KeyboardInterrupt:
                    logger.info("Interrupted by user")
                    break
                except Exception as e:
                    logger.error(f"Error reading from stdin: {e}")
                    break
        
        finally:
            await self.api_client.close()
    
    async def _handle_request(self, request: dict) -> dict:
        """Handle an incoming JSON-RPC request."""
        try:
            method = request.get("method")
            params = request.get("params", {})
            req_id = request.get("id")
            
            if method == "initialize":
                return self._get_initialization_response(req_id)
            
            elif method == "tools/list":
                return await self._list_tools(req_id)
            
            elif method == "tools/call":
                return await self._call_tool(params, req_id)
            
            else:
                logger.warning(f"Unknown method: {method}")
                return self._error_response(req_id, -32601, "Method not found")
        
        except Exception as e:
            logger.error(f"Request handling error: {e}")
            return self._error_response(req_id, -32603, f"Internal error: {str(e)}")
    
    def _get_initialization_response(self, req_id: int = None) -> dict:
        """Get server initialization response."""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {
                        "listChanged": False
                    }
                },
                "serverInfo": {
                    "name": self.name,
                    "version": self.version
                }
            }
        }
    
    async def _list_tools(self, req_id: int) -> dict:
        """List available tools."""
        tools = [
            {
                "name": "get_all_pricing",
                "description": "Get current pricing for all LLM models across providers",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Optional provider filter (openai, anthropic, google, etc.)"
                        }
                    }
                }
            },
            {
                "name": "estimate_cost",
                "description": "Estimate the cost of using a specific LLM model",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "model_name": {
                            "type": "string",
                            "description": "Model name (e.g., gpt-4, claude-3-opus-20240229)"
                        },
                        "input_tokens": {
                            "type": "integer",
                            "description": "Number of input tokens",
                            "minimum": 0
                        },
                        "output_tokens": {
                            "type": "integer",
                            "description": "Number of output tokens",
                            "minimum": 0
                        }
                    },
                    "required": ["model_name", "input_tokens", "output_tokens"]
                }
            },
            {
                "name": "compare_costs",
                "description": "Compare costs across multiple LLM models",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "model_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of model names to compare"
                        },
                        "input_tokens": {
                            "type": "integer",
                            "description": "Number of input tokens",
                            "minimum": 0
                        },
                        "output_tokens": {
                            "type": "integer",
                            "description": "Number of output tokens",
                            "minimum": 0
                        }
                    },
                    "required": ["model_names", "input_tokens", "output_tokens"]
                }
            },
            {
                "name": "get_performance_metrics",
                "description": "Get performance metrics for LLM models",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "provider": {
                            "type": "string",
                            "description": "Optional provider filter"
                        },
                        "model_name": {
                            "type": "string",
                            "description": "Optional model name filter"
                        }
                    }
                }
            },
            {
                "name": "get_use_cases",
                "description": "Get recommended use cases for LLM models",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "model_name": {
                            "type": "string",
                            "description": "Optional model name filter"
                        }
                    }
                }
            }
        ]
        
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": tools}
        }
    
    async def _call_tool(self, params: dict, req_id: int) -> dict:
        """Call a tool via the Azure API."""
        try:
            tool_name = params.get("name")
            args = params.get("arguments", {})
            
            if tool_name == "get_all_pricing":
                result = await self.api_client.get_all_pricing(
                    provider=args.get("provider")
                )
            
            elif tool_name == "estimate_cost":
                result = await self.api_client.estimate_cost(
                    model_name=args.get("model_name"),
                    input_tokens=args.get("input_tokens"),
                    output_tokens=args.get("output_tokens")
                )
            
            elif tool_name == "compare_costs":
                result = await self.api_client.compare_costs(
                    model_names=args.get("model_names", []),
                    input_tokens=args.get("input_tokens"),
                    output_tokens=args.get("output_tokens")
                )
            
            elif tool_name == "get_performance_metrics":
                result = await self.api_client.get_performance_metrics(
                    provider=args.get("provider"),
                    model_name=args.get("model_name")
                )
            
            elif tool_name == "get_use_cases":
                result = await self.api_client.get_use_cases(
                    model_name=args.get("model_name")
                )
            
            else:
                return self._error_response(req_id, -32601, f"Unknown tool: {tool_name}")
            
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "type": "text",
                    "text": json.dumps(result, indent=2)
                }
            }
        
        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "type": "text",
                    "text": f"Error: {str(e)}"
                }
            }
    
    def _error_response(self, req_id: int, code: int, message: str) -> dict:
        """Generate a JSON-RPC error response."""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message
            }
        }


async def main():
    """Entry point."""
    server = AzureMCPServer()
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
