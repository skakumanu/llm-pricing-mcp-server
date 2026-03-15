# MCP Server — Quick Start & Validation Guide

**Version**: 1.38.0
**Protocol**: MCP 2024-11-05, JSON-RPC 2.0
**Transports**: STDIO (local) · HTTP POST (remote)

---

## What's Available

14 MCP tools across two transports:

| Transport | Endpoint | Use case |
|-----------|----------|----------|
| HTTP POST | `https://llm-pricing-api.fly.dev/mcp` | Remote clients (Claude Desktop, Cursor, CI) |
| STDIO | `python mcp/server.py` | Local Claude Desktop, offline |

---

## HTTP Transport (Remote)

No install needed.

### Test initialize

```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

Expected response:
```json
{
  "jsonrpc": "2.0",
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {"tools": {}},
    "serverInfo": {"name": "LLM Pricing MCP Server", "version": "1.1.0"}
  },
  "id": 1
}
```

### List all 14 tools

```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

### Call a tool

```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {
      "name": "estimate_cost",
      "arguments": {"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}
    }
  }'
```

---

## STDIO Transport (Local)

### Setup

```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
cd llm-pricing-mcp-server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Start and send requests

```bash
python mcp/server.py
# Server waits for JSON-RPC input on stdin

# In another terminal (pipe input):
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp/server.py
```

---

## All 14 Tools

| Tool | Required args | Optional args |
|------|--------------|---------------|
| `get_all_pricing` | — | — |
| `estimate_cost` | `model_name`, `input_tokens`, `output_tokens` | — |
| `compare_costs` | `model_names[]`, `input_tokens`, `output_tokens` | — |
| `get_performance_metrics` | — | `provider`, `include_cost` |
| `get_use_cases` | — | `provider` |
| `get_telemetry` | — | `include_details`, `limit` |
| `get_pricing_history` | — | `model_name`, `provider`, `days`, `limit` |
| `get_pricing_trends` | — | `days`, `limit` |
| `register_price_alert` | `url` | `threshold_pct`, `provider`, `model_name` |
| `list_price_alerts` | — | — |
| `delete_price_alert` | `alert_id` | — |
| `get_pricing_export_url` | — | `format`, `model_name`, `provider`, `days`, `limit` |
| `list_conversations` | — | `limit` |
| `delete_conversation` | `conversation_id` | — |

---

## Validation Checklist

### ✅ HTTP transport
```bash
# Server info
curl https://llm-pricing-api.fly.dev/mcp

# initialize
curl -X POST https://llm-pricing-api.fly.dev/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
# → result.protocolVersion == "2024-11-05"

# tools/list
curl -X POST https://llm-pricing-api.fly.dev/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
# → result.tools has 14 entries

# tools/call
curl -X POST https://llm-pricing-api.fly.dev/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_all_pricing","arguments":{}}}'
# → result.content[0].type == "text"

# Error handling — unknown method
curl -X POST https://llm-pricing-api.fly.dev/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"unknown"}'
# → error.code == -32601

# Notification (no id, no response body)
curl -X POST https://llm-pricing-api.fly.dev/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialized"}'
# → HTTP 204 No Content
```

### ✅ Automated tests
```bash
pytest tests/test_mcp_http.py -v  # 12 tests covering all methods and error cases
```

---

## Claude Desktop Config

### Remote (no install)
```json
{
  "mcpServers": {
    "llm-pricing": {
      "url": "https://llm-pricing-api.fly.dev/mcp"
    }
  }
}
```

### Local STDIO
```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "python",
      "args": ["/absolute/path/to/mcp/server.py"],
      "cwd": "/absolute/path/to/llm-pricing-mcp-server",
      "env": { "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| 401 on `/mcp` | Upgrade to v1.38.0+ — `/mcp` is now public |
| `ModuleNotFoundError` (STDIO) | Run from repo root; activate venv |
| No output from STDIO server | Expected — server waits for stdin |
| `Model not found` | Call `get_all_pricing` first to see valid names |

---

**See Also**: [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) · [README.md](../README.md)
