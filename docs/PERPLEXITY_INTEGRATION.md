# Perplexity MCP Integration Guide

**Version**: 1.39.1
**Protocol**: MCP 2024-11-05, JSON-RPC 2.0
**Live server**: https://llm-pricing-api.fly.dev

---

## Overview

This guide walks developers through connecting the LLM Pricing MCP Server to Perplexity's desktop app. Two transport options are available:

| Option | When to use |
|--------|-------------|
| **Remote HTTP** (recommended) | No install needed — connect directly to the hosted server |
| **Local STDIO** | Run the server locally, work offline, or point at a custom deployment |

---

## Step 1 — Access the Repository

The source code is publicly available on GitHub:

```
https://github.com/skakumanu/llm-pricing-mcp-server
```

**Clone (only needed for local STDIO option):**

```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
cd llm-pricing-mcp-server
```

**Browse the live API without cloning:**
- Swagger UI: https://llm-pricing-api.fly.dev/docs
- ReDoc: https://llm-pricing-api.fly.dev/redoc
- Server info: https://llm-pricing-api.fly.dev/mcp (GET)

---

## Step 2 — Configure Perplexity MCP

Perplexity's desktop app reads MCP server configuration from a JSON file. Open or create the file at:

| Platform | Config file path |
|----------|-----------------|
| macOS | `~/Library/Application Support/Perplexity/mcp_config.json` |
| Windows | `%APPDATA%\Perplexity\mcp_config.json` |

> Verify the exact path in Perplexity's Settings → MCP Servers section if the above paths differ in your installed version.

### Option A — Remote HTTP (Recommended)

No cloning or Python required. Add the following to your config file:

```json
{
  "mcpServers": {
    "llm-pricing": {
      "url": "https://llm-pricing-api.fly.dev/mcp"
    }
  }
}
```

Restart Perplexity. All 15 pricing tools are immediately available.

### Option B — Local STDIO

Use this when you need to run against a local or custom server instance.

**1. Install:**

```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
cd llm-pricing-mcp-server

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS/Linux
.venv\Scripts\Activate.ps1       # Windows PowerShell

pip install -r requirements.txt
```

**2. Add to config (macOS/Linux):**

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

**2. Add to config (Windows):**

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

Restart Perplexity after saving the config.

**3. Optional — start the FastAPI server locally (for full REST access):**

```bash
python src/main.py
# Available at http://localhost:8000
```

---

## Step 3 — Verify the Connection

After restarting Perplexity, confirm tools loaded by asking it:

```
What LLM pricing tools do you have available?
```

Perplexity should list all 15 tools. You can also verify directly via curl:

```bash
# Check server is up
curl https://llm-pricing-api.fly.dev/health

# List all 15 tools via MCP protocol
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

---

## All 15 MCP Tools

These tools are available inside Perplexity once connected. You can invoke them naturally in conversation or call them programmatically via the HTTP MCP endpoint.

### Tool Reference

| Tool | Required args | Optional args | Description |
|------|--------------|---------------|-------------|
| `get_all_pricing` | — | — | Pricing for all 87+ models across 12 providers |
| `estimate_cost` | `model_name`, `input_tokens`, `output_tokens` | — | Cost breakdown for a single model |
| `compare_costs` | `model_names[]`, `input_tokens`, `output_tokens` | — | Side-by-side cost comparison |
| `get_performance_metrics` | — | `provider`, `include_cost` | Throughput, latency, context window, quality scores |
| `get_use_cases` | — | `provider` | Model recommendations by use case |
| `get_telemetry` | — | `include_details`, `limit` | Server usage analytics |
| `get_pricing_history` | — | `model_name`, `provider`, `days`, `limit` | Historical pricing snapshots |
| `get_pricing_trends` | — | `days`, `limit` | Price-change leaderboard |
| `register_price_alert` | `url` | `threshold_pct`, `provider`, `model_name` | Register a webhook for price changes |
| `list_price_alerts` | — | — | List all registered webhooks |
| `delete_price_alert` | `alert_id` | — | Remove a webhook |
| `get_pricing_export_url` | — | `format`, `model_name`, `provider`, `days`, `limit` | Generate CSV/JSON export URL |
| `list_conversations` | — | `limit` | List agent conversation history |
| `delete_conversation` | `conversation_id` | — | Delete a conversation |
| `ask_agent` | `message` | `session_id` | Ask the AI pricing agent a question |

### Example Tool Calls (HTTP MCP)

**Get all pricing:**
```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "get_all_pricing", "arguments": {}}
  }'
```

**Estimate cost for GPT-4o:**
```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {
      "name": "estimate_cost",
      "arguments": {"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}
    }
  }'
```

**Compare multiple models:**
```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {
      "name": "compare_costs",
      "arguments": {
        "model_names": ["gpt-4o", "claude-sonnet-4-6", "gemini-1.5-pro"],
        "input_tokens": 10000,
        "output_tokens": 2000
      }
    }
  }'
```

**Ask the pricing agent:**
```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 4, "method": "tools/call",
    "params": {
      "name": "ask_agent",
      "arguments": {"message": "What is the cheapest model for RAG pipelines under $2/1M tokens?"}
    }
  }'
```

**Get pricing trends (last 7 days):**
```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 5, "method": "tools/call",
    "params": {
      "name": "get_pricing_trends",
      "arguments": {"days": 7, "limit": 10}
    }
  }'
```

---

## REST API Endpoints

All endpoints are also accessible directly via HTTP — no MCP required. The base URL is `https://llm-pricing-api.fly.dev`.

### Public Endpoints (no authentication)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/pricing` | All model pricing (`?provider=` filter available) |
| GET | `/models` | All available models |
| GET | `/performance` | Throughput, latency, context window, quality scores |
| GET | `/use-cases` | Model use-case recommendations |
| GET | `/telemetry` | Server usage metrics |
| GET | `/pricing/history` | Historical pricing snapshots |
| GET | `/pricing/trends` | Price-change leaderboard |
| GET | `/pricing/public` | Public pricing (embed-safe, unauthenticated) |
| GET | `/pricing/history/export` | CSV or JSON export |
| GET | `/pricing/alerts/signing-info` | Webhook signing key info |
| GET | `/rate-limits/tiers` | API tier definitions (free/pro/enterprise) |
| GET | `/admin/stats` | Aggregated server statistics |
| GET | `/admin/rate-limits` | Current rate-limit state |
| GET | `/api/versions` | API version info |
| GET | `/mcp` | HTTP MCP server info |
| POST | `/mcp` | HTTP MCP JSON-RPC 2.0 endpoint |
| POST | `/cost-estimate` | Single model cost estimate |
| POST | `/cost-estimate/batch` | Multi-model cost comparison |
| POST | `/agent/chat` | Blocking agent chat |
| POST | `/agent/chat/stream` | Streaming agent chat (SSE) |
| GET | `/agent/conversations` | List agent conversations |
| DELETE | `/agent/conversations/{id}` | Delete a conversation |
| POST | `/billing/signup` | Free tier signup — returns API key |
| POST | `/billing/webhook` | Stripe webhook receiver |
| POST | `/v1/chat/completions` | OpenAI-compatible routing proxy |
| GET | `/health` | Health check |
| GET | `/health/live` | Liveness probe |
| GET | `/health/ready` | Readiness probe |
| GET | `/health/detailed` | Detailed environment + service statuses |

### Protected Endpoints (require API key)

Pass your API key as `x-api-key: <key>` header.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/router/recommend` | Smart model routing recommendation |
| POST | `/router/recommend/stream` | Streaming router (SSE) |
| POST | `/router/feedback` | Submit routing feedback |
| GET | `/telemetry/savings` | Router savings report (per-org) |
| POST | `/billing/checkout` | Create Stripe Checkout session |
| GET | `/billing/portal` | Stripe billing portal |
| GET | `/billing/me` | Usage dashboard (calls, savings, tier) |
| POST | `/pricing/alerts` | Register price-change webhook |
| GET | `/pricing/alerts` | List your registered alerts |
| DELETE | `/pricing/alerts/{id}` | Delete an alert |

### Get a Free API Key

```bash
curl -X POST https://llm-pricing-api.fly.dev/billing/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'
```

Response:
```json
{
  "api_key": "abc123...",
  "org_id": "org-xyz...",
  "tier": "free",
  "message": "Welcome! Your free API key is ready."
}
```

Use the returned `api_key` for all protected endpoints:

```bash
curl -H "x-api-key: abc123..." \
  https://llm-pricing-api.fly.dev/billing/me
```

### curl Examples

**Get all pricing:**
```bash
curl https://llm-pricing-api.fly.dev/pricing
```

**Filter by provider:**
```bash
curl "https://llm-pricing-api.fly.dev/pricing?provider=OpenAI"
```

**Estimate cost:**
```bash
curl -X POST https://llm-pricing-api.fly.dev/cost-estimate \
  -H "Content-Type: application/json" \
  -d '{"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}'
```

**Batch compare costs:**
```bash
curl -X POST https://llm-pricing-api.fly.dev/cost-estimate/batch \
  -H "Content-Type: application/json" \
  -d '{
    "model_names": ["gpt-4o", "gpt-4o-mini", "claude-haiku-4-5-20251001"],
    "input_tokens": 50000,
    "output_tokens": 10000
  }'
```

**Chat with the AI agent:**
```bash
curl -X POST https://llm-pricing-api.fly.dev/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Which model gives the best quality/cost ratio for coding tasks?"}'
```

**Stream the agent response (SSE):**
```bash
curl -X POST https://llm-pricing-api.fly.dev/agent/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Compare GPT-4o vs Claude Sonnet pricing"}' \
  --no-buffer
```

**Get router recommendation (requires API key):**
```bash
curl -X POST https://llm-pricing-api.fly.dev/router/recommend \
  -H "x-api-key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"max_cost_per_1m_tokens": 5, "task_type": "code"}'
```

**OpenAI-compatible proxy:**
```bash
curl -X POST https://llm-pricing-api.fly.dev/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [{"role": "user", "content": "What is the cheapest LLM?"}]
  }'
```

---

## Browser UIs

The live server also hosts interactive UIs accessible from any browser:

| URL | Description |
|-----|-------------|
| https://llm-pricing-api.fly.dev/ | Marketing landing page |
| https://llm-pricing-api.fly.dev/chat | Conversational AI agent |
| https://llm-pricing-api.fly.dev/calculator | Interactive cost calculator |
| https://llm-pricing-api.fly.dev/compare | Side-by-side model comparison |
| https://llm-pricing-api.fly.dev/history | Pricing history charts |
| https://llm-pricing-api.fly.dev/trends | Price-change leaderboard |
| https://llm-pricing-api.fly.dev/widget | Embeddable pricing table |
| https://llm-pricing-api.fly.dev/billing | Free signup + upgrade dashboard |
| https://llm-pricing-api.fly.dev/docs | Swagger UI (interactive API docs) |
| https://llm-pricing-api.fly.dev/redoc | ReDoc API reference |

---

## API Rate Limits

| Tier | Rate limit | How to get |
|------|-----------|------------|
| Free | 30 req/min | Sign up at `/billing/signup` |
| Pro | 120 req/min | Upgrade via Stripe |
| Enterprise | 600 req/min | Upgrade via Stripe |

Pass the tier via header if not using a billing key: `X-Api-Key-Tier: free|pro|enterprise`

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Tools not showing in Perplexity | Check config file path and JSON syntax; restart Perplexity |
| `404` on `/mcp` | Ensure you're using `POST /mcp` for JSON-RPC calls; `GET /mcp` returns server info only |
| `ModuleNotFoundError` (STDIO mode) | Run from repo root with venv activated |
| STDIO server hangs with no output | Expected — the server waits for JSON-RPC input on stdin |
| `Model not found` on `estimate_cost` | Call `get_all_pricing` first to get valid model names |
| `401 Unauthorized` on protected endpoints | Pass `x-api-key` header; get a free key via `POST /billing/signup` |
| Empty response from `ask_agent` | Ensure `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set (remote server has this configured) |

---

## Related Documentation

- [README.md](../README.md) — Full project overview and features
- [MCP_QUICK_START.md](MCP_QUICK_START.md) — Quick validation and curl examples
- [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) — Claude Desktop integration (same MCP protocol)
- [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md) — VS Code + Copilot MCP setup
- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — System design and component details
