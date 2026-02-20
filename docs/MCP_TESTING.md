# MCP Server Testing Guide

**Last Updated**: February 20, 2026  
**Version**: 1.6.0

## Overview

This guide covers all testing approaches for the MCP server, from quick validation to comprehensive integration testing. The server includes **3 automated test suites** designed for different scenarios:

| Test Script | Purpose | Duration | Tests | Best For |
|-------------|---------|----------|-------|----------|
| `quick_validate.py` | Fast pre-deployment validation | ~15 sec | 6 core tests | CI/CD, blue-green deployment |
| `test_mcp_server.py` | Integration testing | ~30 sec | Tool discovery + 5 tools | Development, integration testing |
| `validate_mcp_client.py` | Comprehensive validation | ~2 min | 16 scenarios | Full QA, release validation |

## Quick Start Testing

### 1. Quick Validation (Recommended for Pre-Deployment)

**Purpose**: Fast validation of core MCP server functionality before deployment.

**Location**: `scripts/quick_validate.py`

**Tests Performed** (6 total):
1. ✅ Server startup
2. ✅ MCP initialization handshake
3. ✅ Tool discovery (expects 5/5 tools)
4. ✅ Tool execution (all 5 tools with valid parameters)
5. ✅ Error handling (invalid tool name)
6. ✅ Response format validation

**Usage**:
```bash
# Run quick validation
python scripts/quick_validate.py

# Expected output:
# [PASS] Server started (PID: xxxx)
# [PASS] Received initialization message
# [PASS] Initialize request-response successful
# [PASS] All 5 tools discovered
# [PASS] get_all_pricing executes successfully
# [PASS] estimate_cost executes successfully
# [PASS] compare_costs executes successfully
# [PASS] get_performance_metrics executes successfully
# [PASS] get_use_cases executes successfully
# [PASS] Error handling works correctly
# [SUCCESS] ALL TESTS PASSED - Server is ready for production
```

**Exit Codes**:
- `0`: All tests passed (safe to deploy)
- `1`: One or more tests failed (deployment blocked)

**When to Use**:
- Before production deployment
- In CI/CD pipelines
- Blue-green deployment validation (Phase 2)
- Quick smoke testing after code changes

---

### 2. Integration Testing

**Purpose**: Comprehensive tool testing with response validation.

**Location**: `scripts/test_mcp_server.py`

**Tests Performed**:
- Tool discovery via `tools/list` RPC method
- All 5 tool executions with sample parameters
- Response schema validation
- Performance timing
- Success rate calculation

**Usage**:
```bash
# Run integration tests
python scripts/test_mcp_server.py

# Expected output:
# Test Results:
# Tool Discovery: [PASS] 5/5 tools
# Tool Execution: [PASS] 5/5 tools
#   - get_all_pricing: [PASS]
#   - estimate_cost: [PASS]
#   - compare_costs: [PASS]
#   - get_performance_metrics: [PASS]
#   - get_use_cases: [PASS]
# Pass Rate: 100%
# Status: All tools operational
```

**When to Use**:
- Local development testing
- Integration with backend services
- Verifying tool functionality after changes
- Generating test reports

---

### 3. Comprehensive Validation

**Purpose**: Exhaustive testing with strict schema validation and edge cases.

**Location**: `scripts/validate_mcp_client.py`

**Tests Performed** (16 scenarios):
1. Server startup verification
2. MCP initialization protocol
3. Tool discovery validation
4. `get_all_pricing` - no filters
5. `get_all_pricing` - with provider filter
6. `get_all_pricing` - with category filter
7. `estimate_cost` - single model
8. `estimate_cost` - with volume
9. `compare_costs` - multiple models
10. `compare_costs` - 5+ models
11. `get_performance_metrics` - all providers
12. `get_performance_metrics` - specific provider
13. `get_use_cases` - all use cases
14. `get_use_cases` - category filter
15. Error handling - invalid tool
16. Error handling - invalid parameters

**Usage**:
```bash
# Run comprehensive validation
python scripts/validate_mcp_client.py

# Expected output:
# [SCENARIO 1/16] Server Startup... [PASS]
# [SCENARIO 2/16] MCP Initialization... [PASS]
# [SCENARIO 3/16] Tool Discovery... [PASS]
# ...
# [SCENARIO 16/16] Error Handling... [PASS]
#
# Summary: 16/16 scenarios passed (100%)
```

**When to Use**:
- Release candidate validation
- Full QA testing cycle
- Regression testing
- Performance baseline establishment

---

## Testing Decision Matrix

Choose the right test based on your scenario:

```
┌─────────────────────────────────────────────────────────────┐
│ Scenario                     │ Recommended Test Script       │
├──────────────────────────────┼──────────────────────────────┤
│ Pre-deployment check         │ quick_validate.py            │
│ CI/CD pipeline               │ quick_validate.py            │
│ Blue-green validation        │ quick_validate.py            │
│ Local development            │ test_mcp_server.py           │
│ Integration testing          │ test_mcp_server.py           │
│ Release candidate            │ validate_mcp_client.py       │
│ Full QA cycle                │ validate_mcp_client.py       │
│ Performance baseline         │ validate_mcp_client.py       │
│ After tool changes           │ test_mcp_server.py +         │
│                              │ quick_validate.py            │
└─────────────────────────────────────────────────────────────┘
```

---

### 4. Manual STDIO Testing (For Debugging)

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

### 4. Manual STDIO Testing (For Debugging)

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

---

## Automated Test Scripts Details

### Script 1: quick_validate.py

**File**: `scripts/quick_validate.py`  
**Lines**: ~180  
**Purpose**: Fast pre-deployment validation

**Implementation Details**:
```python
# Key features:
# - Spawns MCP server as subprocess
# - Tests via STDIO JSON-RPC protocol
# - 6 critical tests in ~15 seconds
# - Returns exit code 0 (pass) or 1 (fail)
# - Used by blue-green deployment manager
```

**Test Flow**:
1. Start server subprocess
2. Wait for initialization message
3. Send initialize request
4. Send tools/list request → validate 5 tools found
5. Execute all 5 tools with valid params
6. Test error handling with invalid tool name
7. Terminate server gracefully

**Error Handling**:
- Catches subprocess failures
- Validates JSON-RPC responses
- Checks for server crashes
- Ensures cleanup on exit

---

### Script 2: test_mcp_server.py

**File**: `scripts/test_mcp_server.py`  
**Purpose**: Integration and functional testing

### Script 2: test_mcp_server.py

**File**: `scripts/test_mcp_server.py`  
**Purpose**: Integration and functional testing

**Example Run**:
```bash
python scripts/test_mcp_server.py

# Output example:
# ========================================
# MCP Server Integration Tests
# ========================================
# 
# [TEST 1/6] Tool Discovery
#   Discovered 5 tools: [PASS]
#   - estimate_cost
#   - get_all_pricing
#   - compare_costs
#   - get_performance_metrics
#   - get_use_cases
#
# [TEST 2/6] get_all_pricing
#   Execution: [PASS]
#   Response time: 1.2s
#
# [TEST 3/6] estimate_cost
#   Execution: [PASS]
#   Response time: 0.08s
#
# [TEST 4/6] compare_costs
#   Execution: [PASS]
#   Response time: 1.5s
#
# [TEST 5/6] get_performance_metrics
#   Execution: [PASS]
#   Response time: 1.3s
#
# [TEST 6/6] get_use_cases
#   Execution: [PASS]
#   Response time: 0.2s
#
# ========================================
# Test Summary
# ========================================
# Total Tests: 6
# Passed: 6
# Failed: 0
# Pass Rate: 100%
# Status: All tools operational
```

**Features**:
- Tests all 5 MCP tools
- Validates tool responses
- Measures execution time
- Reports success/failure rates
- Windows-compatible output (no emoji)

---

### Script 3: validate_mcp_client.py

**File**: `scripts/validate_mcp_client.py`  
**Purpose**: Comprehensive validation with strict schema checks

**Features**:
- 16 test scenarios covering all tool variations
- Strict response schema validation
- Edge case testing (invalid params, error handling)
- Performance metrics collection
- Detailed failure diagnostics

**Example Scenarios**:
```python
# Scenario examples:
scenarios = [
    "Server startup verification",
    "MCP protocol initialization",
    "Tool discovery (5/5 expected)",
    "get_all_pricing without filters",
    "get_all_pricing with provider='openai'",
    "estimate_cost for gpt-4",
    "compare_costs with 3 models",
    "get_performance_metrics all",
    "get_use_cases with category filter",
    "Error handling: invalid tool name",
    "Error handling: missing required params",
    # ... 5 more scenarios
]
```

---

## Production Testing Workflow

### Pre-Deployment Testing

**Step 1**: Run quick validation
```bash
python scripts/quick_validate.py
# Must pass all 6 tests before deploying
```

**Step 2**: Run integration tests (optional for minor changes)
```bash
python scripts/test_mcp_server.py
```

**Step 3**: Deploy with automated validation
```bash
python scripts/mcp_blue_green_deploy.py deploy --version 1.6.0
# Automatically runs quick_validate.py in Phase 2
```

### Post-Deployment Testing

**Step 1**: Verify deployment status
```bash
python scripts/mcp_blue_green_deploy.py status
```

**Step 2**: Run health check
```bash
python scripts/monitor_mcp_server.py --check
```

**Step 3**: Run integration tests (recommended)
```bash
python scripts/test_mcp_server.py
```

---

## Testing in Different Environments

### Local Development
```bash
# Terminal 1: Start server
python mcp/server.py

# Terminal 2: Run tests
python scripts/test_mcp_server.py
```

### CI/CD Pipeline
```yaml
# GitHub Actions example
- name: Run MCP Tests
  run: |
    python scripts/quick_validate.py
    if [ $? -eq 0 ]; then
      echo "✅ MCP tests passed"
    else
      echo "❌ MCP tests failed"
      exit 1
    fi
```

### Blue-Green Deployment
```bash
# Validation happens automatically in Phase 2
python scripts/mcp_blue_green_deploy.py deploy --version 1.6.0

# Phase 2 output:
# [PHASE 2] Validating green environment...
# [PASS] Server started
# [PASS] Initialization successful
# [PASS] All 5 tools discovered
# [PASS] All 5 tools execute successfully
# [PASS] Error handling works
# [SUCCESS] GREEN validation passed
```

---

### 5. Pytest Integration Tests

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

## Test Results Archive

### Latest Test Run (v1.6.0)

**Date**: February 20, 2026  
**Version**: 1.6.0  
**Environment**: Production blue-green deployment

**quick_validate.py Results**:
- ✅ All 6/6 tests passed
- Server startup: PASS
- Initialization: PASS
- Tool discovery: PASS (5/5)
- Tool execution: PASS (5/5)
- Error handling: PASS
- Total time: 15 seconds

**test_mcp_server.py Results**:
- ✅ All 5/5 tools passing
- Tool discovery: 5/5
- Pass rate: 100%
- Status: All tools operational

**Deployment Validation**:
- ✅ Phase 1: Green environment started
- ✅ Phase 2: Validation passed (6/6 tests)
- ✅ Phase 3: Traffic switched successfully
- ✅ Phase 4: Blue environment shutdown
- ✅ Phase 5: Green promoted to production

---

## Related Documentation

- **[MCP_BLUE_GREEN_DEPLOYMENT.md](MCP_BLUE_GREEN_DEPLOYMENT.md)** - Deployment guide with automated testing
- **[MCP_PRODUCTION_CHECKLIST.md](MCP_PRODUCTION_CHECKLIST.md)** - Pre-deployment checklist
- **[MCP_MONITORING_GUIDE.md](MCP_MONITORING_GUIDE.md)** - Production monitoring
- **[MCP_QUICK_START.md](MCP_QUICK_START.md)** - Quick start guide
- **[CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md)** - Claude Desktop testing

---

**Status**: Testing framework complete and validated  
**Last Updated**: February 20, 2026  
**Current Version**: 1.6.0
