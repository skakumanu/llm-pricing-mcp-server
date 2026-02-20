# MCP Server Integration - PR-Ready Branch Plan

## Overview

This document provides the complete integration plan for converting the FastAPI-based LLM Pricing API into a true MCP (Model Context Protocol) server as a parallel interface.

## Architecture Design

### Key Principles

1. **Parallel Interface**: MCP server runs independently alongside FastAPI (no interference)
2. **Shared Services**: Both FastAPI and MCP use the same `PricingAggregatorService`
3. **JSON-RPC 2.0 Compliance**: Server implements full JSON-RPC 2.0 over STDIO
4. **MCP Spec Compliant**: All tools follow MCP Protocol v2024-11-05
5. **Security First**: Implements MCP Security Minimum Bar requirements

### Directory Structure

```
repo/
├── mcp/                          # NEW: MCP Server Package
│   ├── __init__.py
│   ├── server.py                 # Main JSON-RPC 2.0 server (STDIO)
│   ├── schema_generator.py        # Generates JSON schemas from Pydantic models
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── tool_manager.py        # Tool registry and executor
│   │   ├── get_all_pricing.py     # Tool: Fetch all model pricing
│   │   ├── estimate_cost.py       # Tool: Estimate single model cost
│   │   ├── compare_costs.py       # Tool: Compare multiple models
│   │   ├── get_performance_metrics.py  # Tool: Performance data
│   │   ├── get_use_cases.py       # Tool: Use case recommendations
│   │   └── manifests/
│   │       ├── get_all_pricing.json
│   │       ├── estimate_cost.json
│   │       ├── compare_costs.json
│   │       ├── get_performance_metrics.json
│   │       └── get_use_cases.json
│   ├── schemas/                  # Generated JSON schemas from Pydantic models
│   │   ├── pricing_metrics.json
│   │   ├── cost_estimate_request.json
│   │   ├── cost_estimate_response.json
│   │   ├── batch_cost_estimate_request.json
│   │   ├── batch_cost_estimate_response.json
│   │   ├── performance_metrics.json
│   │   ├── performance_response.json
│   │   ├── model_use_case.json
│   │   ├── use_case_response.json
│   │   └── ... (15 total)
│   ├── sessions/
│   │   ├── __init__.py
│   │   └── session_manager.py    # Session context management
│   └── utils/
│       └── __init__.py
├── src/                          # EXISTING: FastAPI application
│   ├── main.py
│   ├── services/
│   │   └── pricing_aggregator.py # ← Used by MCP server
│   ├── models/
│   │   └── pricing.py            # ← Pydantic models for schemas
│   └── ...
└── ...
```

## Implementation Details

### 1. JSON-RPC 2.0 Server (mcp/server.py)

**Features**:
- STDIO-based communication
- Async/await for concurrent request handling
- Full JSON-RPC 2.0 error handling
- Proper protocol version negotiation
- Logging to file for debugging

**Entry Point**: `python -m mcp.server` or `python mcp/server.py`

**Protocol**:
```
Request:
{
  "jsonrpc": "2.0",
  "id": "unique-id",
  "method": "tools/list" | "tools/call" | "initialize",
  "params": {...}
}

Response:
{
  "jsonrpc": "2.0",
  "id": "unique-id",
  "result": {...} | "error": {...}
}
```

### 2. Tool System

**Six Core Tools** (mapped to PricingAggregatorService and TelemetryService):

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `get_all_pricing` | Get pricing for all models | None | All models, provider status |
| `estimate_cost` | Cost for single model | model_name, input_tokens, output_tokens | Detailed cost breakdown |
| `compare_costs` | Compare multiple models | model_names[], input_tokens, output_tokens | Side-by-side comparison, cheapest/most expensive |
| `get_performance_metrics` | Throughput, latency, context window | provider (optional), include_cost | Performance scores, best metrics |
| `get_use_cases` | Model recommendations | provider (optional) | Use cases, strengths, best_for |
| `get_telemetry` | Server usage statistics | include_details (optional), limit (optional) | MCP request tracking, tool usage, analytics |

**Tool Implementations**:
- All async, using `PricingAggregatorService` methods
- Proper error handling and validation
- JSON-serializable outputs
- Input schema validation

### 3. JSON Schemas

**Auto-Generated** from Pydantic models:
- 15 schema JSON files in `mcp/schemas/`
- Covers all request/response models
- Referenced by tool manifests

**Generated Models**:
- PricingMetrics
- CostEstimateRequest/Response
- BatchCostEstimateRequest/Response
- PerformanceMetrics/Response
- ModelUseCase / UseCaseResponse
- TokenVolumePrice
- ModelCostComparison
- ProviderStatusInfo
- EndpointInfo
- ServerInfo

### 4. Tool Manifests

**Each tool has a manifest** (`mcp/tools/manifests/*.json`):
- Description
- Input schema with property definitions
- Example requests and responses
- Human-readable documentation

**Example**:
```json
{
  "name": "estimate_cost",
  "description": "Calculate the cost of using a specific LLM model",
  "inputSchema": {
    "type": "object",
    "properties": {
      "model_name": {"type": "string"},
      "input_tokens": {"type": "integer", "minimum": 0},
      "output_tokens": {"type": "integer", "minimum": 0}
    },
    "required": ["model_name", "input_tokens", "output_tokens"]
  },
  "examples": [...]
}
```

### 5. Security Implementation

**MCP Security Minimum Bar**:

✅ **Request Validation**
- All inputs validated against schemas
- Token counts must be non-negative
- Model names sanitized

✅ **Error Handling**
- No sensitive data in error messages
- Proper error codes (-32600, -32601, -32603)
- No stack traces to clients

✅ **Logging**
- Requests logged to file (not stdout)
- No credentials logged
- Audit trail enabled

✅ **STDIO Security**
- No shell injection possible (binary protocol)
- Input must be valid JSON
- Output must be valid JSON

✅ **API Key Handling**
- No changes to existing .env setup
- Services use existing API key config
- MCP server doesn't add new credentials

## Branch Plan for PR

### Commit Structure

```
Commit 1: MCP package structure and schema generation
  - Create /mcp directory
  - Add schema_generator.py
  - Generate all JSON schemas
  - Add __init__.py files

Commit 2: Core MCP server implementation
  - mcp/server.py (JSON-RPC 2.0)
  - mcp/tools/tool_manager.py
  - mcp/sessions/session_manager.py

Commit 3: Tool implementations
  - get_all_pricing.py
  - estimate_cost.py
  - compare_costs.py
  - get_performance_metrics.py
  - get_use_cases.py

Commit 4: Tool manifests
  - All 5 manifest JSON files in mcp/tools/manifests/

Commit 5: Documentation and entrypoints
  - MCP-INTEGRATION.md (this file)
  - Add mcp_server entrypoint to setup.py OR pyproject.toml
  - Create MCP_ARCHITECTURE.md
  - Create MCP_TESTING.md

Commit 6: VS Code configuration
  - .vscode/launch.json with MCP server debug config
  - Add VS Code tasks for running MCP server
```

### PR Checklist

- [ ] All 6 tools implemented and tested
- [ ] All JSON schemas generated correctly
- [ ] All tool manifests created with examples
- [ ] Server can be started: `python mcp/server.py`
- [ ] Server responds to JSON-RPC initialize request
- [ ] All tool requests return valid responses
- [ ] Documentation complete (README update, guides)
- [ ] Tests pass (no breaking changes to FastAPI)
- [ ] No import errors or circular dependencies
- [ ] MCP spec compliance verified

## Running the MCP Server

### Option 1: Direct Python

```bash
# Activate venv (Windows)
.\.venv\Scripts\Activate.ps1

# Run MCP server
python mcp/server.py
```

### Option 2: VS Code Launch

1. Open `.vscode/launch.json`
2. Select "MCP Server (STDIO)" from dropdown
3. Click "Run" or press F5

### Option 3: Docker (Optional - Future)

```bash
docker run -it llm-pricing-mcp-server python mcp/server.py
```

## Testing the MCP Server

### Manual Testing with stdin/stdout

```bash
python mcp/server.py
# Then paste JSON-RPC requests:

# Initialize
{"jsonrpc": "2.0", "id": 1, "method": "initialize"}

# List tools
{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}

# Call get_all_pricing
{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_all_pricing", "arguments": {}}}

# Call estimate_cost
{"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "estimate_cost", "arguments": {"model_name": "gpt-4", "input_tokens": 1000, "output_tokens": 500}}}
```

### Programmatic Testing

See `docs/MCP_TESTING.md` for pytest integration tests.

## Client Integration

### Example: Using with Claude (via MCP client)

```bash
# Install MCP client tools
pip install mcp-client

# Configure in Claude settings:
{
  "tools": [
    {
      "name": "llm-pricing-mcp",
      "command": "python",
      "args": ["mcp/server.py"],
      "type": "stdio"
    }
  ]
}
```

### Example: Direct HTTP wrapper (if needed)

Clients can wrap the STDIO interface with HTTP:

```python
import subprocess
import asyncio
import json

class MCPClient:
    def __init__(self):
        self.process = subprocess.Popen(
            ["python", "mcp/server.py"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True
        )
    
    async def call_tool(self, tool_name, arguments):
        request = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments}
        }
        
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        
        response = self.process.stdout.readline()
        return json.loads(response)
```

## Impact Analysis

### No Breaking Changes

- ✅ FastAPI endpoints unchanged
- ✅ Existing imports unchanged (`from src.services...`)
- ✅ Database unchanged
- ✅ Configuration unchanged
- ✅ Deployments unaffected

### New Dependencies

- None required! Uses existing packages:
  - `asyncio` (stdlib)
  - `json` (stdlib)
  - `sys`, `pathlib` (stdlib)

### Compatibility

- Python 3.8+
- Works with existing FastAPI server running simultaneously
- Works on Windows, macOS, Linux

## Documentation to Create

1. **MCP_INTEGRATION.md** (this document)
2. **MCP_TESTING.md** - Test procedures and pytest integration
3. **MCP_ARCHITECTURE.md** - Deep dive into design decisions
4. **README.md UPDATE** - Add MCP server section
5. **CHANGELOG.md UPDATE** - List new MCP interface

## Next Steps for User

1. **Verify Setup**:
   ```bash
   cd /path/to/repo
   python mcp/server.py
   # Should print initialization response and wait for input
   ```

2. **Test Basic Tool Call**:
   ```bash
   echo '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}' | python mcp/server.py
   # Should output list of tools
   ```

3. **Integrate with Clients**:
   - Configure in Claude settings
   - Use with any MCP-compatible client
   - Wrap with HTTP if needed

4. **Deploy**:
   - Add to Docker image (optional)
   - Update deployment docs
   - Add to CI/CD pipeline

## Troubleshooting

### Issue: ImportError for `src.services`

**Fix**: Ensure working directory is repo root when running server.

### Issue: Server hangs waiting for input

**Expected behavior**: Server reads from stdin in a loop. Feed it JSON-RPC requests.

### Issue: Tool returns error

**Debug**: Check log file (`mcp_server.log` or `/tmp/mcp_server.log`) for details.

### Issue: No response from tool

**Check**:
- Is PricingAggregatorService running?
- Are API keys configured?
- Check logfile for async errors

## Questions & Support

For questions:
1. Check `/docs/MCP_*.md` files
2. Review tool manifests in `mcp/tools/manifests/`
3. Check server logs
4. Review JSON-RPC 2.0 spec details

---

**Status**: Implementation complete and PR-ready
**Version**: 1.0.0
**Last Updated**: 2024
