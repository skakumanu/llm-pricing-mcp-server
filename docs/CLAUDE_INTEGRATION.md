# Claude Desktop Integration Guide

**Version**: 1.6.0  
**Last Updated**: February 20, 2026  
**Status**: Ready for Integration Testing

---

## Overview

This guide walks through integrating the LLM Pricing MCP Server with Claude Desktop, enabling you to use the pricing tools directly within Claude's interface.

---

## Prerequisites

- Claude Desktop installed (Mac or Windows)
- Python 3.8+ with venv activated
- MCP Server running on localhost
- ~5 minutes setup time

---

## Setup Steps

### Step 1: Locate Claude Desktop Configuration

**macOS**:
```bash
open ~/Library/Application\ Support/Claude/
```
Look for `claude_desktop_config.json`

**Windows**:
```powershell
cd $env:APPDATA
ls Claude/
```

### Step 2: Configure MCP Server

Edit your Claude Desktop config file and add the MCP server entry:

```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "python",
      "args": [
        "/absolute/path/to/mcp/server.py"
      ],
      "cwd": "/absolute/path/to/llm-pricing-mcp-server",
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": "/absolute/path/to/llm-pricing-mcp-server"
      }
    }
  }
}
```

**Important**: Use absolute paths, not relative paths.

#### Windows Example:
```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "python",
      "args": [
        "C:\\Users\\skaku\\OneDrive\\Documents\\GitHub\\llm-pricing-mcp-server\\mcp\\server.py"
      ],
      "cwd": "C:\\Users\\skaku\\OneDrive\\Documents\\GitHub\\llm-pricing-mcp-server",
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": "C:\\Users\\skaku\\OneDrive\\Documents\\GitHub\\llm-pricing-mcp-server"
      }
    }
  }
}
```

#### macOS Example:
```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "python",
      "args": [
        "/Users/username/projects/llm-pricing-mcp-server/mcp/server.py"
      ],
      "cwd": "/Users/username/projects/llm-pricing-mcp-server",
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": "/Users/username/projects/llm-pricing-mcp-server"
      }
    }
  }
}
```

### Step 3: Restart Claude Desktop

Fully quit and restart Claude Desktop to load the new configuration.

**macOS**:
```bash
killall Claude
# Then reopen Claude from Applications
```

**Windows**:
```powershell
Get-Process Claude | Stop-Process
# Then reopen Claude
```

### Step 4: Verify Integration

In Claude, look for the tool icon (wrench/tools) in the bottom-right of the interface. You should see "llm-pricing" listed with a green status indicator if the connection is successful.

---

## Using the Pricing Tools

Once integrated, you can ask Claude to use the pricing tools directly:

### Example 1: Get All Pricing
**User Message**:
> "What's the current pricing for all LLM models?"

**Claude Response**: Claude will call `get_all_pricing` and display a formatted table of all models and their pricing.

### Example 2: Estimate Cost
**User Message**:
> "How much would it cost to use GPT-4 for a 5000-token input and 2000-token output?"

**Claude Response**: Claude will call `estimate_cost` with those parameters and show the cost estimate.

### Example 3: Compare Models
**User Message**:
> "Compare the costs of GPT-4, Claude 3 Opus, and Gemini Pro for processing 10,000 input tokens and 5,000 output tokens."

**Claude Response**: Claude will call `compare_costs` and display a comparison table.

### Example 4: Performance Metrics
**User Message**:
> "What are the performance metrics for GPT-4?"

**Claude Response**: Claude will call `get_performance_metrics` with throughput, latency, and context window info.

### Example 5: Use Cases
**User Message**:
> "What are the best use cases for Claude 3 Opus?"

**Claude Response**: Claude will call `get_use_cases` and provide recommendations.

### Example 6: Server Telemetry
**User Message**:
> "Show me the server usage statistics and telemetry data."

**Claude Response**: Claude will call `get_telemetry` and display MCP request tracking, tool usage metrics, and client analytics.

---

## Troubleshooting

### Server Not Appearing in Claude

1. **Check Configuration Syntax**:
   ```bash
   python -m json.tool claude_desktop_config.json
   ```
   Should output valid JSON without errors.

2. **Verify Absolute Paths**:
   - Don't use `~` or relative paths
   - Use full file system paths
   - Check that paths contain no spaces (or properly escape them)

3. **Check Server Startup**:
   ```bash
   cd /path/to/llm-pricing-mcp-server
   python mcp/server.py
   ```
   Should start without errors.

4. **Review Claude Logs**:
   
   **macOS**:
   ```bash
   cat ~/Library/Logs/Claude/mcp.log
   ```
   
   **Windows**:
   ```powershell
   Get-Content "$env:APPDATA\Claude\Logs\mcp.log" -Tail 20
   ```

### Tools Available but Not Working

1. **Test Server Directly**:
   ```bash
   python scripts/test_mcp_server.py
   ```
   All 6 tools should pass.

2. **Check Import Paths**:
   ```bash
   python -c "from mcp.tools.tool_manager import ToolManager; print(ToolManager().list_tools())"
   ```
   Should show 6 tools.

3. **Enable Debug Logging**:
   Set `PYTHONUNBUFFERED=1` and `PYTHONDONTWRITEBYTECODE=1` in config.

### Server Crashes Immediately

1. **Test Python Environment**:
   ```bash
   python --version
   source .venv/bin/activate  # or .venv\Scripts\Activate.ps1
   python -c "import mcp.server; print('OK')"
   ```

2. **Check Dependencies**:
   ```bash
   pip list | grep -E "pydantic|asyncio|httpx"
   ```

3. **Review Error Output**:
   Enable `PYTHONUNBUFFERED` to see real-time errors in Claude logs.

---

## Testing Workflow

### Phase 1: Basic Connectivity (5 minutes)
1. [  ] Add config to Claude Desktop
2. [  ] Restart Claude
3. [  ] Verify server appears in tool list
4. [  ] Check green status indicator

### Phase 2: Tool Discovery (5 minutes)
1. [  ] Ask Claude to list available tools
2. [  ] Verify 6 tools shown: 
   - get_all_pricing
   - estimate_cost
   - compare_costs
   - get_performance_metrics
   - get_use_cases
   - get_telemetry
3. [  ] Check tool descriptions display correctly

### Phase 3: Tool Execution (15 minutes)
1. [  ] Test `get_all_pricing` - Request all model pricing
2. [  ] Test `estimate_cost` - Cost calculation for single model
3. [  ] Test `compare_costs` - Multi-model comparison
4. [  ] Test `get_performance_metrics` - Metrics retrieval
5. [  ] Test `get_use_cases` - Use case recommendations
6. [  ] Test `get_telemetry` - Server usage statistics

### Phase 4: Real-World Use (30+ minutes)
1. [  ] Test with various token counts
2. [  ] Test with different model names
3. [  ] Verify accuracy of calculations
4. [  ] Test edge cases (0 tokens, very large requests)
5. [  ] Collect feedback on usability

---

## Integration Validation Checklist

### Connectivity
- [  ] MCP server listed in Claude tools panel
- [  ] Green status indicator showing
- [  ] No connection errors in logs

### Tool Availability
- [  ] All 6 tools appear in Claude's tool list
- [  ] Tool descriptions are correct
- [  ] Input schemas match expected parameters

### Tool Functionality
- [  ] get_all_pricing returns pricing data
- [  ] estimate_cost calculates correctly
- [  ] compare_costs shows comparison
- [  ] get_performance_metrics returns data
- [  ] get_use_cases provides recommendations
- [  ] get_telemetry returns usage statistics

### Response Quality
- [  ] Tool responses are well-formatted
- [  ] Claude interprets results correctly
- [  ] No timeout errors (< 10 seconds per call)
- [  ] Error handling works for invalid inputs

---

## Performance Expectations

### Response Times
- Tool Discovery: < 500ms
- get_all_pricing: < 2 seconds
- estimate_cost: < 1 second
- compare_costs: < 2 seconds
- get_performance_metrics: < 1 second
- get_use_cases: < 1 second
- get_telemetry: < 500ms

### Reliability
- Success Rate: > 99%
- Error Recovery: Automatic retry
- Connection Stability: Maintains during session

---

## Advanced Configuration

### Custom Environment Variables

Add environment-specific settings:

```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "python",
      "args": ["C:\\path\\to\\mcp\\server.py"],
      "cwd": "C:\\path\\to\\llm-pricing-mcp-server",
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": "C:\\path\\to\\llm-pricing-mcp-server",
        "LOG_LEVEL": "INFO",
        "MCP_DEBUG": "false"
      }
    }
  }
}
```

### Running Multiple MCP Servers

If you have other MCP servers, add them alongside:

```json
{
  "mcpServers": {
    "llm-pricing": { ... },
    "other-server": {
      "command": "python",
      "args": ["/path/to/other/server.py"],
      "cwd": "/path/to/other"
    },
    "another-server": { ... }
  }
}
```

---

## Next Steps

### After Successful Integration:

1. **Monitor Tool Usage**:
   - Check Claude logs for tool calls
   - Collect usage statistics
   - Identify most-used tools

2. **Gather Feedback**:
   - Test with different model names
   - Try edge cases and error conditions
   - Verify response accuracy

3. **Performance Optimization**:
   - Benchmark response times
   - Profile tool execution
   - Optimize slow operations

4. **Feature Enhancement**:
   - Add caching for frequently accessed data
   - Create additional tools
   - Implement advanced filtering

---

## Troubleshooting Quick Reference

| Issue | Quick Fix |
|-------|-----------|
| Server not appearing | Check config syntax, restart Claude, verify paths |
| Tools not working | Run `test_mcp_server.py`, check imports |
| Slow responses | Check server logs for errors, profile code |
| Connection drops | Verify PYTHONUNBUFFERED=1, check system resources |
| Tool returns error | Test directly with `scripts/test_mcp_server.py` |

---

## Support & Debug

### Enable Verbose Logging

1. **Update config** to enable debug:
   ```json
   "env": {
     "PYTHONUNBUFFERED": "1",
     "DEBUG": "1",
     "LOG_LEVEL": "DEBUG"
   }
   ```

2. **Check server logs**:
   ```bash
   tail -100 mcp_server.log
   ```

3. **Monitor in real-time**:
   ```bash
   python mcp/server.py  # Run directly to see output
   ```

### Reporting Issues

When reporting problems, include:
1. Exact error message from Claude logs
2. Output of `python scripts/test_mcp_server.py`
3. Your `claude_desktop_config.json` (paths anonymized)
4. OS and Python version
5. Steps to reproduce

---

## Success Indicators

You'll know integration is working when:
- ✓ Claude shows "llm-pricing" in tools panel with green status
- ✓ Tool list shows all 5 pricing tools
- ✓ Claude can call tools in natural conversation
- ✓ Tool responses appear in Claude's response
- ✓ No errors in Claude's logs
- ✓ Response times are under 10 seconds

---

**See Also**:
- [MCP_QUICK_START.md](MCP_QUICK_START.md) - Server setup
- [MCP_TESTING.md](MCP_TESTING.md) - Testing procedures
- [MCP_MONITORING_GUIDE.md](MCP_MONITORING_GUIDE.md) - Production monitoring
- [MCP_PRODUCTION_CHECKLIST.md](MCP_PRODUCTION_CHECKLIST.md) - Deployment checklist
