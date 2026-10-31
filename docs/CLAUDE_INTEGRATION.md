# Claude Desktop Integration Guide

**Version**: see `src/__init__.py` (single source of truth)
**Last Updated**: October 2026
**Status**: Production-ready

---

## Overview

Two ways to integrate with Claude Desktop: **remote HTTP** (easiest — no local install) or **local STDIO** (classic, works offline).

---

## Option A — Remote HTTP (Recommended)

No installation required. Claude Desktop connects directly to the hosted server.

Open **Settings → Connectors → Add custom connector** and paste `https://llm-pricing-api.fly.dev/mcp`. Remote servers are added here, not in `claude_desktop_config.json`, which only launches local commands (a `url` entry there is ignored). All 31 tools are then listed.

A connector sends no custom headers. To pass your service key, use the npx bridge in `claude_desktop_config.json` instead:

```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "npx",
      "args": ["-y", "llm-pricing-mcp"],
      "env": { "LLM_PRICING_API_KEY": "<your-service-key>" }
    }
  }
}
```

**Service key (optional, v1.79.1).** The pricing tools need no key. The 7 usage/budget tools (`record_usage`, `import_session_usage`, `get_usage_summary`, `analyze_session_usage`, `register_budget_alert`, `list_budget_alerts`, `delete_budget_alert`) need your service API key on the connection — with the npx bridge above, `"env": { "LLM_PRICING_API_KEY": "<your-service-key>" }`; clients that support HTTP headers (Cursor, VS Code, Claude Code) send `x-api-key` directly. They then act only on that key's org; without a key they return `customer_key_required`. The local STDIO server (Option B) is always anonymous, so those tools refuse there. The four operator-only tools (`list_price_alerts`, `delete_price_alert`, `list_conversations`, `delete_conversation`, v1.79.2) need the server operator's key (`MCP_API_KEY`); any other caller gets `operator_key_required`.

**Dispatch from Claude (v1.80.0).** The `dispatch` tool runs a text chat request through the dispatch gateway — your policy picks the best eligible model — and returns the completion, `usage` and the full `dispatch_receipt`. It makes a **real model call billed to your own provider keys** stored for your org with `POST /dispatch/keys`, so it needs your service key on the connection (same `x-api-key` header or `LLM_PRICING_API_KEY` env as above); anonymous and operator callers get `dispatch_requires_api_key`, and the local STDIO server (Option B) returns `dispatch_requires_hosted_mcp`. Ask, for example, *"Use dispatch to answer 'what is MCP?' with the cheapest model scoring at least 70"*. Pass `prompt` (+ optional `system`) or `messages`, plus an optional `dispatch` policy object identical to HTTP's. Text-only and non-streaming. With the npx bridge, set `"LLM_PRICING_MCP_TIMEOUT_MS": "120000"` in `env` (failover can outlast the 30 s default), and bound calls with `dispatch.failover.timeout_ms` / `dispatch.failover.max_attempts`.

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

## Available Tools (31)

Public (no key, 19):

| Tool | What it does |
|------|-------------|
| `get_all_pricing` | All models + prices from every provider |
| `estimate_cost` | Cost for a specific model + token count |
| `compare_costs` | Side-by-side cost comparison of multiple models |
| `predict_cost` | Estimate output tokens and cost for a prompt across models, before running it |
| `optimize_workload` | Pick the cheapest model per workload within a quality floor and budget |
| `recommend_model` | Ranked model recommendations from a plain-language task description |
| `check_price_drift` | Audit pricing against an external reference registry; drifted prices are withheld |
| `get_data_quality` | Pricing data-quality summary (confirmed, withheld for drift, never priced, stale) |
| `get_performance_metrics` | Throughput, latency, context window, quality scores |
| `get_use_cases` | Best-use recommendations per model |
| `get_ide_pricing` | AI coding assistant / IDE subscription plans |
| `get_telemetry` | Server usage statistics |
| `get_cache_stats` | Hit/miss counts for the recommendation cache |
| `get_pricing_history` | Historical pricing snapshots |
| `get_pricing_trends` | Price-change leaderboard |
| `register_price_alert` | Webhook alert when prices change by X%; returns a one-time `alert_token` |
| `manage_price_alert` | View or delete the alert you registered, with its one-time `alert_token` |
| `get_pricing_export_url` | Download link for CSV/JSON export |
| `ask_agent` | Ask the pricing agent a natural-language question |

Service key required (7 org-scoped tools, plus `dispatch`):

| Tool | What it does |
|------|-------------|
| `record_usage` | Record one request's token usage and cost for your org |
| `import_session_usage` | Bulk-import a session's usage entries |
| `get_usage_summary` | Your org's spend summary over N days |
| `analyze_session_usage` | Cost breakdown for one session |
| `register_budget_alert` | Webhook alert when your org's spend crosses a USD threshold |
| `list_budget_alerts` | List your org's budget alerts |
| `delete_budget_alert` | Remove one of your org's budget alerts |
| `dispatch` | Run a chat request through the dispatch gateway on your stored provider keys (customer key only, hosted MCP only) |

Operator only (`MCP_API_KEY`):

| Tool | What it does |
|------|-------------|
| `list_price_alerts` | List every registered price alert |
| `delete_price_alert` | Remove any price alert |
| `list_conversations` | Past agent chat sessions |
| `delete_conversation` | Remove a conversation |

------|-------------|
| `get_all_pricing` | All models + prices from every provider |
| `estimate_cost` | Cost for a specific model + token count |
| `compare_costs` | Side-by-side cost comparison of multiple models |
| `get_performance_metrics` | Throughput, latency, context window, quality scores |
| `get_use_cases` | Best-use recommendations per model |
| `get_telemetry` | Server usage statistics |
| `get_pricing_history` | Historical pricing snapshots |
| `get_pricing_trends` | Price-change leaderboard |
| `register_price_alert` | Webhook alert when prices change by X% |
| `list_price_alerts` | List registered alerts |
| `delete_price_alert` | Remove an alert (operator only) |
| `manage_price_alert` | View or delete the alert you registered, with its one-time `alert_token` (no key) |
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
Should print a JSON response with 31 tools.

### Verify tool count

```bash
python -c "from mcp.tools.tool_manager import ToolManager; print(len(ToolManager().list_tools()), 'tools')"
# → 31 tools
```

### HTTP endpoint not responding

```bash
curl https://llm-pricing-api.fly.dev/mcp
# → {"name":"LLM Pricing MCP Server","version":"<current release>",...}
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
