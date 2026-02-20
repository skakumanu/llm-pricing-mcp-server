"""MCP Server implementation with JSON-RPC 2.0 over STDIO."""
import sys
import json
import logging
import asyncio
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp.tools.tool_manager import ToolManager

# Configure logging for debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('/tmp/mcp_server.log') if sys.platform != 'win32' else logging.FileHandler('mcp_server.log')]
)
logger = logging.getLogger(__name__)


class MCPServer:
    """Model Context Protocol Server with JSON-RPC 2.0 over STDIO."""
    
    def __init__(self):
        """Initialize the MCP server."""
        self.version = "1.0.0"
        self.name = "LLM Pricing MCP Server"
        self.tool_manager = ToolManager()
        self.request_id_counter = 0
        logger.info(f"Initializing {self.name} v{self.version}")
    
    async def run(self):
        """Run the MCP server in STDIO mode."""
        logger.info("MCP Server starting (STDIO mode)")
        print(json.dumps(self._get_initialization_response()), flush=True)
        
        try:
            loop = asyncio.get_event_loop()
            while True:
                # Read a line from stdin
                try:
                    line = await loop.run_in_executor(None, sys.stdin.readline)
                    if not line:
                        logger.info("EOF reached, shutting down")
                        break
                    
                    line = line.strip()
                    if not line:
                        continue
                    
                    # Parse JSON-RPC request
                    request = json.loads(line)
                    logger.debug(f"Received request: {request}")
                    
                    # Handle the request
                    response = await self._handle_request(request)
                    
                    # Send response
                    logger.debug(f"Sending response: {response}")
                    print(json.dumps(response), flush=True)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON: {e}")
                    print(json.dumps(self._error_response(
                        None,
                        -32700,
                        "Parse error"
                    )), flush=True)
                except Exception as e:
                    logger.error(f"Error processing request: {e}", exc_info=True)
                    print(json.dumps(self._error_response(
                        None,
                        -32603,
                        "Internal error"
                    )), flush=True)
        
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error(f"Server error: {e}", exc_info=True)
    
    async def _handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a JSON-RPC 2.0 request."""
        # Validate JSON-RPC 2.0 structure
        if "jsonrpc" not in request or request["jsonrpc"] != "2.0":
            return self._error_response(
                request.get("id"),
                -32600,
                "Invalid Request: jsonrpc must be '2.0'"
            )
        
        if "method" not in request:
            return self._error_response(
                request.get("id"),
                -32600,
                "Invalid Request: method is required"
            )
        
        method = request["method"]
        request_id = request.get("id")
        params = request.get("params", {})
        
        logger.info(f"Handling method: {method}")
        
        # Handle different methods
        if method == "initialize":
            return self._success_response(request_id, self._get_initialization_response())
        
        elif method == "tools/list":
            return self._success_response(request_id, {
                "tools": self.tool_manager.list_tools()
            })
        
        elif method == "tools/call":
            if not isinstance(params, dict) or "name" not in params:
                return self._error_response(
                    request_id,
                    -32602,
                    "Invalid params: name is required"
                )
            
            tool_name = params["name"]
            tool_arguments = params.get("arguments", {})
            
            result = await self.tool_manager.execute_tool(tool_name, tool_arguments)
            return self._success_response(request_id, result)
        
        else:
            return self._error_response(
                request_id,
                -32601,
                f"Method not found: {method}"
            )
    
    def _get_initialization_response(self) -> Dict[str, Any]:
        """Get server initialization response."""
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {}
            },
            "serverInfo": {
                "name": self.name,
                "version": self.version
            }
        }
    
    def _success_response(
        self,
        request_id: Optional[str],
        result: Any
    ) -> Dict[str, Any]:
        """Create a successful JSON-RPC 2.0 response."""
        response = {
            "jsonrpc": "2.0",
            "result": result
        }
        if request_id is not None:
            response["id"] = request_id
        return response
    
    def _error_response(
        self,
        request_id: Optional[str],
        code: int,
        message: str,
        data: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Create a JSON-RPC 2.0 error response."""
        error = {
            "code": code,
            "message": message
        }
        if data is not None:
            error["data"] = data
        
        response = {
            "jsonrpc": "2.0",
            "error": error
        }
        if request_id is not None:
            response["id"] = request_id
        return response


async def main():
    """Main entry point for the MCP server."""
    server = MCPServer()
    await server.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
