# llm-pricing-mcp

A zero-dependency STDIO bridge to the [LLM Pricing MCP server](https://github.com/skakumanu/llm-pricing-mcp-server). It gives you drift-verified pricing, cost and quality model routing, and usage tracking across 30 LLM providers, as 31 MCP tools.

- Reads JSON-RPC from stdin, forwards it to `https://llm-pricing-api.fly.dev/mcp`, and writes replies to stdout.
- Needs only Node: no Python, no clone, no local pricing data.
- **Node 22.23.4+, 24.22.1+ or 26.12.1+.** These builds carry the October 2026 security fixes, including Brotli decoder RCE CVE-2026-111497. The bridge's `fetch` decodes compressed responses. Node 18 and 20 are end-of-life and unpatched.

## When to use it

| Client | Use |
|---|---|
| Cursor, VS Code, Claude Code | Point at `https://llm-pricing-api.fly.dev/mcp` directly; no bridge needed |
| Claude Desktop, no key | Settings → Connectors → Add custom connector with the URL above |
| Claude Desktop, with a service key | This bridge (connectors send no custom headers) |
| Any STDIO-only client | This bridge |

## Setup

```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "npx",
      "args": ["-y", "llm-pricing-mcp"],
      "env": { "LLM_PRICING_API_KEY": "<your-service-key>", "LLM_PRICING_MCP_TIMEOUT_MS": "120000" }
    }
  }
}
```

- `env` is optional; drop it for the public tools only.
- Claude Desktop: paste into `claude_desktop_config.json` (Settings → Developer → Edit Config), then restart.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_PRICING_MCP_URL` | `https://llm-pricing-api.fly.dev/mcp` | Point at a self-hosted or local instance |
| `LLM_PRICING_MCP_TIMEOUT_MS` | `30000` | Upstream timeout per request. Use `120000` for `dispatch` |
| `LLM_PRICING_API_KEY` | unset | Your service key (free via `POST /billing/signup`). Sent as `x-api-key`, never logged |

## Tool access

| Tools | Credential | Without it |
|---|---|---|
| 19 public (pricing, routing, estimates, data quality) | none | — |
| 7 usage/budget (`record_usage`, `get_usage_summary`, `register_budget_alert`, ...) | `LLM_PRICING_API_KEY`; acts only on that key's org | `customer_key_required`; another org's `org_id` gives `org_mismatch` |
| `dispatch` | `LLM_PRICING_API_KEY` | `dispatch_requires_api_key` |
| 4 operator-only (`list_price_alerts`, `delete_price_alert`, `list_conversations`, `delete_conversation`) | server operator's `MCP_API_KEY` | `operator_key_required` |

- `manage_price_alert` (public) views or deletes your own alert. Its only credential is the one-time `alert_token` (`lpa_...`) from `register_price_alert`.
- `dispatch` makes a **real model call billed to your own provider keys**, stored with `POST /dispatch/keys`. It returns the completion and a `dispatch_receipt`.
- Cap `dispatch` with `dispatch.failover.timeout_ms` / `max_attempts`.
- Full tool list: [MCP Quick Start](https://github.com/skakumanu/llm-pricing-mcp-server/blob/master/docs/MCP_QUICK_START.md#tool-reference-31).

## Why a bridge?

- Tools and pricing data live server-side. Fixes, providers and tools ship without republishing this package.
- The cost is a network dependency on the hosted endpoint.
- For offline use, clone the repo and run `mcp/server.py`.
- That local server is anonymous: key-gated and operator tools refuse, and `dispatch` returns `dispatch_requires_hosted_mcp`.

## License

MIT
