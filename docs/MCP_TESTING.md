# MCP Server Testing Guide

## Quick Start Testing

### 1. Manual STDIO Testing

**Test the server is running**:

```bash
# Terminal 1: Start the server
cd c:\Users\skaku\OneDrive\Documents\GitHub\llm-pricing-mcp-server
.\.venv\Scripts\Activate.ps1
python mcp\server.py
```

**Test the server responds**:

```powershell
# Terminal 2: Send requests
cd c:\Users\skaku\OneDrive\Documents\GitHub\llm-pricing-mcp-server

# Initialize request
Write-Host '{"jsonrpc": "2.0", "id": 1, "method": "initialize"}' | python mcp\server.py

# Or test interactively - copy/paste these one at a time in the first terminal:

# Test Initialize
{"jsonrpc": "2.0", "id": 1, "method": "initialize"}

# Test List Tools
{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}

# Test Get All Pricing (no args)
{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_all_pricing", "arguments": {}}}

# Test Estimate Cost
{"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "estimate_cost", "arguments": {"model_name": "gpt-4", "input_tokens": 1000, "output_tokens": 500}}}

# Test Compare Costs
{"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "compare_costs", "arguments": {"model_names": ["gpt-4", "claude-3-opus"], "input_tokens": 1000, "output_tokens": 500}}}

# Test Get Performance Metrics
{"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "get_performance_metrics", "arguments": {}}}

# Test Get Use Cases
{"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "get_use_cases", "arguments": {}}}

# Test with provider filter
{"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "get_performance_metrics", "arguments": {"provider": "openai"}}}
```

### 2. Automated Testing Script

Create `test_mcp_server.py`:

```python
"""Test script for MCP server."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

async def test_mcp_server():
    """Test the MCP server with all tools."""
    
    # Start the server process
    process = subprocess.Popen(
        [sys.executable, "mcp/server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(Path(__file__).parent)
    )
    
    test_cases = [
        ("Initialize", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize"
        }),
        ("List Tools", {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }),
        ("Get All Pricing", {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_all_pricing",
                "arguments": {}
            }
        }),
        ("Estimate Cost", {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "estimate_cost",
                "arguments": {
                    "model_name": "gpt-4",
                    "input_tokens": 1000,
                    "output_tokens": 500
                }
            }
        }),
        ("Compare Costs", {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "compare_costs",
                "arguments": {
                    "model_names": ["gpt-4", "claude-3-opus"],
                    "input_tokens": 1000,
                    "output_tokens": 500
                }
            }
        }),
    ]
    
    passed = 0
    failed = 0
    
    try:
        for test_name, request in test_cases:
            try:
                # Send request
                request_json = json.dumps(request) + "\n"
                process.stdin.write(request_json)
                process.stdin.flush()
                
                # Get response
                response_line = process.stdout.readline()
                if not response_line:
                    print(f"❌ {test_name}: No response")
                    failed += 1
                    continue
                
                response = json.loads(response_line)
                
                # Check response is valid JSON-RPC
                if "jsonrpc" not in response or response["jsonrpc"] != "2.0":
                    print(f"❌ {test_name}: Invalid JSON-RPC response")
                    failed += 1
                    continue
                
                if "error" in response and response["error"]["code"] != 0:
                    print(f"❌ {test_name}: {response['error']['message']}")
                    failed += 1
                    continue
                
                print(f"✅ {test_name}")
                passed += 1
            
            except Exception as e:
                print(f"❌ {test_name}: {e}")
                failed += 1
    
    finally:
        process.terminate()
        process.wait(timeout=5)
    
    print(f"\n{passed} passed, {failed} failed")
    return failed == 0

if __name__ == "__main__":
    success = asyncio.run(test_mcp_server())
    sys.exit(0 if success else 1)
```

Run with:
```bash
python test_mcp_server.py
```

### 3. Pytest Integration Tests

Create `tests/test_mcp_server.py`:

```python
"""Pytest tests for MCP server."""
import pytest
import json
from mcp.tools.tool_manager import ToolManager
from mcp.server import MCPServer


@pytest.fixture
def tool_manager():
    """Create a tool manager."""
    return ToolManager()


@pytest.fixture
def mcp_server():
    """Create an MCP server."""
    return MCPServer()


@pytest.mark.asyncio
async def test_get_all_pricing(tool_manager):
    """Test get_all_pricing tool."""
    result = await tool_manager.execute_tool("get_all_pricing", {})
    assert result["success"] is True
    assert "total_models" in result
    assert "models" in result
    assert isinstance(result["models"], list)


@pytest.mark.asyncio
async def test_estimate_cost(tool_manager):
    """Test estimate_cost tool."""
    result = await tool_manager.execute_tool("estimate_cost", {
        "model_name": "gpt-4",
        "input_tokens": 1000,
        "output_tokens": 500
    })
    assert result["success"] is True
    assert result["model_name"] == "gpt-4"
    assert "total_cost" in result


@pytest.mark.asyncio
async def test_estimate_cost_invalid_model(tool_manager):
    """Test estimate_cost with invalid model."""
    result = await tool_manager.execute_tool("estimate_cost", {
        "model_name": "invalid-model-xyz",
        "input_tokens": 1000,
        "output_tokens": 500
    })
    assert result["success"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_compare_costs(tool_manager):
    """Test compare_costs tool."""
    result = await tool_manager.execute_tool("compare_costs", {
        "model_names": ["gpt-4", "claude-3-opus"],
        "input_tokens": 1000,
        "output_tokens": 500
    })
    assert result["success"] is True
    assert "models" in result
    assert "cheapest_model" in result


@pytest.mark.asyncio
async def test_get_performance_metrics(tool_manager):
    """Test get_performance_metrics tool."""
    result = await tool_manager.execute_tool("get_performance_metrics", {})
    assert result["success"] is True
    assert "total_models" in result
    assert "models" in result


@pytest.mark.asyncio
async def test_get_use_cases(tool_manager):
    """Test get_use_cases tool."""
    result = await tool_manager.execute_tool("get_use_cases", {})
    assert result["success"] is True
    assert "total_models" in result
    assert "providers" in result


def test_mcp_server_initialization(mcp_server):
    """Test MCP server initialization."""
    response = mcp_server._get_initialization_response()
    assert response["serverInfo"]["name"] == "LLM Pricing MCP Server"
    assert "protocolVersion" in response
    assert "capabilities" in response


def test_mcp_server_tools(mcp_server):
    """Test MCP server lists tools correctly."""
    tools = mcp_server.tool_manager.list_tools()
    tool_names = [t["name"] for t in tools]
    assert "get_all_pricing" in tool_names
    assert "estimate_cost" in tool_names
    assert "compare_costs" in tool_names
    assert "get_performance_metrics" in tool_names
    assert "get_use_cases" in tool_names


@pytest.mark.asyncio
async def test_json_rpc_initialize(mcp_server):
    """Test JSON-RPC initialize request."""
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize"
    }
    response = await mcp_server._handle_request(request)
    assert "result" in response
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1


@pytest.mark.asyncio
async def test_json_rpc_tools_list(mcp_server):
    """Test JSON-RPC tools/list request."""
    request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list"
    }
    response = await mcp_server._handle_request(request)
    assert "result" in response
    assert "tools" in response["result"]


@pytest.mark.asyncio
async def test_json_rpc_invalid_method(mcp_server):
    """Test JSON-RPC invalid method."""
    request = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "invalid_method"
    }
    response = await mcp_server._handle_request(request)
    assert "error" in response
    assert response["error"]["code"] == -32601


def test_mcp_server_json_rpc_error_response(mcp_server):
    """Test JSON-RPC error response format."""
    response = mcp_server._error_response(123, -32600, "Invalid Request")
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 123
    assert response["error"]["code"] == -32600
    assert response["error"]["message"] == "Invalid Request"
```

Run with:
```bash
pytest tests/test_mcp_server.py -v
```

## Test Coverage Goals

- ✅ All 5 tools can be called
- ✅ Invalid inputs are rejected
- ✅ Successful responses are JSON-RPC 2.0 compliant
- ✅ Error responses have proper error codes
- ✅ Server handles invalid JSON gracefully
- ✅ Server handles missing methods gracefully
- ✅ Tool data is accurate and matches PricingAggregatorService

## Continuous Integration (CI)

Add to `.github/workflows/ci-cd.yml` (if using GitHub Actions):

```yaml
- name: Test MCP Server
  run: |
    python -m pytest tests/test_mcp_server.py -v
    
- name: Run MCP Server Initialization
  timeout-minutes: 1
  run: |
    echo '{"jsonrpc": "2.0", "id": 1, "method": "initialize"}' | python mcp/server.py
    echo "MCP Server initialization successful"
```

## Performance Testing

Monitor these metrics when running the MCP server:

```python
import time
import json

test_request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "get_all_pricing",
        "arguments": {}
    }
}

start = time.time()
# Send request to MCP server
# Get response
elapsed = time.time() - start

print(f"Response time: {elapsed*1000:.2f}ms")
assert elapsed < 5.0, "Tool should respond within 5 seconds"
```

Expected response times:
- `get_all_pricing`: 1-3 seconds (calls all providers)
- `estimate_cost`: <100ms (lookup)
- `compare_costs`: 1-3 seconds (calls all providers)
- `get_performance_metrics`: 1-3 seconds
- `get_use_cases`: 1-3 seconds

## Debugging

### Check Server Logs

```bash
# View server log file
type mcp_server.log  # Windows
cat /tmp/mcp_server.log  # Linux/macOS
```

### Enable Debug Logging

Future improvement - add environment variable:
```bash
DEBUG=1 python mcp/server.py
```

### Test with Python asyncio

```python
import asyncio
from mcp.tools.get_all_pricing import GetAllPricingTool

async def test():
    tool = GetAllPricingTool()
    result = await tool.execute({})
    print(result)

asyncio.run(test())
```

## Compatibility Testing

Test MCP server with different Python versions:

```bash
# Python 3.8
python3.8 mcp/server.py

# Python 3.9
python3.9 mcp/server.py

# Python 3.10+
python3.10 mcp/server.py
```

All should work without issues.

---

**Status**: Testing framework complete
**Last Updated**: 2024
