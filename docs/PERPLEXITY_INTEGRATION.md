# Perplexity Integration

- **Version**: see `src/__init__.py`
- **Protocol**: MCP 2024-11-05, JSON-RPC 2.0
- **Live server**: https://llm-pricing-api.fly.dev
- **Tools**: all 32 tools; see the [tool reference](MCP_QUICK_START.md#tool-reference-32)

## Config file

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Perplexity/mcp_config.json` |
| Windows | `%APPDATA%\Perplexity\mcp_config.json` |

If your version differs, check Perplexity's **Settings → MCP Servers**.

## Option A: remote HTTP (recommended)

```json
{ "mcpServers": { "llm-pricing": { "url": "https://llm-pricing-api.fly.dev/mcp" } } }
```

- No clone or Python. Restart Perplexity.
- If your Perplexity build only launches commands, use the npx bridge instead:
  `"command": "npx", "args": ["-y", "llm-pricing-mcp"]` ([details](../npm/llm-pricing-mcp/README.md)).
- To use the key-gated tools, give the bridge `"env": { "LLM_PRICING_API_KEY": "<your-service-key>" }`.

## Option B: local STDIO

```json
{ "mcpServers": { "llm-pricing": {
    "command": "python",
    "args": ["/absolute/path/to/llm-pricing-mcp-server/mcp/server.py"],
    "cwd": "/absolute/path/to/llm-pricing-mcp-server",
    "env": { "PYTHONUNBUFFERED": "1" } } } }
```

- Install first: clone, venv, `pip install -r requirements.txt` ([steps](MCP_QUICK_START.md#local-stdio-server)).
- Use absolute paths. On Windows, escape backslashes (`C:\\path\\to\\...`).
- Runs offline and anonymously, so key-gated and operator tools refuse.
- For the REST API locally too: `python src/main.py` (serves `http://localhost:8000`).

## Verify

- Ask Perplexity: *"What LLM pricing tools do you have available?"* It should list 32.
- Or from a shell:

```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## Example prompts

- *"Compare gpt-4o, claude-sonnet-4-6 and gemini-2.5-pro for 10k input, 2k output tokens."*
- *"Cheapest model for a RAG pipeline under $2/1M tokens?"*
- *"Show the biggest price changes in the last 7 days."*
- *"How fresh is your pricing data?"* (calls `get_data_quality`)

## REST API (no MCP needed)

The same data is plain HTTP at `https://llm-pricing-api.fly.dev`.

- **Full endpoint list and auth levels**: https://llm-pricing-api.fly.dev/api-docs
- Interactive: [Swagger](https://llm-pricing-api.fly.dev/docs) · [ReDoc](https://llm-pricing-api.fly.dev/redoc)

| Access | Examples | Credential |
|---|---|---|
| Public | `GET /pricing`, `POST /cost-estimate`, `POST /cost-estimate/batch` (1–50 models), `POST /agent/chat` | none (cost estimates and chat are rate-limited) |
| Service key | `POST /router/recommend`, `GET /billing/me`, `GET /telemetry/savings` | `x-api-key: <your-api-key>` |
| Operator | `GET /pricing/alerts`, `GET /agent/conversations`, `GET /admin/stats` | server's `MCP_API_KEY` |

Cost estimate, no key:

```bash
curl -X POST https://llm-pricing-api.fly.dev/cost-estimate -H "Content-Type: application/json" \
  -d '{"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}'
```

## Free API key

```bash
curl -X POST https://llm-pricing-api.fly.dev/billing/signup -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'
```

- The response includes `api_key`, `org_id` and `tier: "free"`.
- Send it as `x-api-key` on REST calls, or via the bridge's `LLM_PRICING_API_KEY` for MCP.

## Rate limits

| Tier | Limit | How |
|---|---|---|
| Free | 30 req/min | `POST /billing/signup` |
| Pro | 120 req/min | Upgrade via Stripe (`/billing`) |
| Enterprise | 600 req/min | Upgrade via Stripe |

- With the operator `MCP_API_KEY`, pick a bucket with `X-Api-Key-Tier: free|pro|enterprise`.
- That header is ignored on anonymous requests.

## Browser UIs

| Page | Purpose |
|---|---|
| [`/chat`](https://llm-pricing-api.fly.dev/chat) | Conversational pricing agent |
| [`/calculator`](https://llm-pricing-api.fly.dev/calculator) · [`/compare`](https://llm-pricing-api.fly.dev/compare) | Cost calculator, side-by-side comparison |
| [`/history`](https://llm-pricing-api.fly.dev/history) · [`/trends`](https://llm-pricing-api.fly.dev/trends) | Price history charts, change leaderboard |
| [`/widget`](https://llm-pricing-api.fly.dev/widget) | Embeddable pricing table |
| [`/billing`](https://llm-pricing-api.fly.dev/billing) | Free signup and upgrades |

## Troubleshooting

| Problem | Fix |
|---|---|
| Tools not showing | Check the config path and JSON syntax; restart Perplexity |
| `404` on `/mcp` | JSON-RPC needs `POST /mcp`; `GET /mcp` returns server info only |
| STDIO hangs with no output | Expected: it waits for JSON-RPC on stdin |
| `ModuleNotFoundError` (STDIO) | Run from the repo root with the venv active |
| `Model not found` | Call `get_all_pricing` for valid names |
| `401` on REST | Add `x-api-key`; get a key via `POST /billing/signup` |
| `customer_key_required` (MCP) | Key-gated tool; use the bridge with `LLM_PRICING_API_KEY` |

## Related

[MCP_QUICK_START.md](MCP_QUICK_START.md) · [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) · [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [README.md](../README.md)
