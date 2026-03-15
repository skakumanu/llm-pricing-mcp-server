# Claude Desktop Integration Guide

**Version**: 1.38.0
**Last Updated**: March 2026
**Status**: Production-ready

---

## Overview

Two ways to integrate with Claude Desktop: **remote HTTP** (easiest — no local install) or **local STDIO** (classic, works offline).

---

## Option A — Remote HTTP (Recommended)

No installation required. Claude Desktop connects directly to the hosted server.

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "llm-pricing": {
      "url": "https://llm-pricing-api.fly.dev/mcp"
    }
  }
}
```

Restart Claude Desktop. All 14 tools are available immediately.

**Config file locations:**
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

---

## Option B — Local STDIO

Use this when you want to run against a local server or work offline.

### Step 1: Install

```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
cd llm-pricing-mcp-server
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Step 2: Configure

**macOS / Linux:**
```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "python",
      "args": ["/absolute/path/to/llm-pricing-mcp-server/mcp/server.py"],
      "cwd": "/absolute/path/to/llm-pricing-mcp-server",
      "env": { "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

**Windows:**
```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "python",
      "args": ["C:\\path\\to\\llm-pricing-mcp-server\\mcp\\server.py"],
      "cwd": "C:\\path\\to\\llm-pricing-mcp-server",
      "env": { "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

Use absolute paths — `~` and relative paths are not supported.

### Step 3: Restart Claude Desktop

Fully quit and relaunch. Look for "llm-pricing" in the tools panel with a green status indicator.

---

## Available Tools (14)

| Tool | What it does |
|------|-------------|
| `get_all_pricing` | All models + prices from 12 providers |
| `estimate_cost` | Cost for a specific model + token count |
| `compare_costs` | Side-by-side cost comparison of multiple models |
| `get_performance_metrics` | Throughput, latency, context window, quality scores |
| `get_use_cases` | Best-use recommendations per model |
| `get_telemetry` | Server usage statistics |
| `get_pricing_history` | Historical pricing snapshots |
| `get_pricing_trends` | Price-change leaderboard |
| `register_price_alert` | Webhook alert when prices change by X% |
| `list_price_alerts` | List registered alerts |
| `delete_price_alert` | Remove an alert |
| `get_pricing_export_url` | Download link for CSV/JSON export |
| `list_conversations` | Past agent chat sessions |
| `delete_conversation` | Remove a conversation |

---

## Example Prompts

- *"What's the cheapest model under $2/1M tokens for summarization?"*
- *"Estimate the cost to process 10,000 input tokens with GPT-4o"*
- *"Compare Claude 3.5 Sonnet vs GPT-4o vs Gemini 1.5 Pro for 5k input, 2k output tokens"*
- *"Which models got cheaper in the last 30 days?"*
- *"Alert me when GPT-4o price changes by more than 10%"*
- *"Export the last 90 days of OpenAI pricing history as CSV"*

---

## Troubleshooting

### Server not appearing in Claude

1. Validate JSON syntax: `python -m json.tool claude_desktop_config.json`
2. Check paths are absolute (no `~` or `./`)
3. Review logs:
   - macOS: `~/Library/Logs/Claude/mcp.log`
   - Windows: `%APPDATA%\Claude\Logs\mcp.log`

### Tools not working (STDIO mode)

Test the server directly:
```bash
cd /path/to/llm-pricing-mcp-server
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp/server.py
```
Should print a JSON response with 14 tools.

### Verify tool count

```bash
python -c "from mcp.tools.tool_manager import ToolManager; print(len(ToolManager().list_tools()), 'tools')"
# → 14 tools
```

### HTTP endpoint not responding

```bash
curl https://llm-pricing-api.fly.dev/mcp
# → {"name":"LLM Pricing MCP Server","version":"1.1.0",...}
```

---

## Running Multiple MCP Servers

```json
{
  "mcpServers": {
    "llm-pricing": {
      "url": "https://llm-pricing-api.fly.dev/mcp"
    },
    "other-server": {
      "command": "python",
      "args": ["/path/to/other/server.py"]
    }
  }
}
```

---

**See Also**:
- [MCP_QUICK_START.md](MCP_QUICK_START.md) — Local server setup and testing
- [README.md](../README.md) — Full API reference and deployment guide
