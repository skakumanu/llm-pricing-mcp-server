# llm-pricing-mcp

A zero-dependency STDIO launcher for the [LLM Pricing MCP server](https://github.com/skakumanu/llm-pricing-mcp-server) — drift-verified pricing, cost+quality model routing, and real usage tracking across 30 LLM providers, exposed as 31 MCP tools.

This package is a thin bridge, not the server itself: it reads MCP JSON-RPC requests from stdin and forwards each one to the hosted server at `https://llm-pricing-api.fly.dev/mcp`, writing the response back to stdout. There's nothing to install beyond Node — no Python, no `pip install`, no local pricing data to keep up to date.

**Node requirement:** 22.23.4+, 24.22.1+ or 26.12.1+. These are the builds that carry the October 2026 Node.js security fixes (including the Brotli decoder RCE, CVE-2026-111497); the bridge uses Node's built-in `fetch`, which decodes compressed responses, so run it on a patched Node. Node 18 and 20 are end-of-life and will not receive the fix.

## Use it

Most MCP clients (Claude Desktop, Cursor, Windsurf, etc.) that support remote/HTTP servers directly can skip this package entirely — point them at `https://llm-pricing-api.fly.dev/mcp` and you're done. This package exists for STDIO-only clients that can't talk HTTP directly.

Add to your client's MCP config:

```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "npx",
      "args": ["-y", "llm-pricing-mcp"]
    }
  }
}
```

Claude Desktop: add the block above to `claude_desktop_config.json` (Settings → Developer → Edit Config) and restart Claude Desktop.

## Configuration

All are optional — the defaults work out of the box.

| Env var | Default | Purpose |
|---|---|---|
| `LLM_PRICING_MCP_URL` | `https://llm-pricing-api.fly.dev/mcp` | Point at a self-hosted or local instance instead |
| `LLM_PRICING_MCP_TIMEOUT_MS` | `30000` | Per-request timeout to the upstream server. Set `120000` if you use the `dispatch` tool (a real model call with failover can outlast 30 s) |
| `LLM_PRICING_API_KEY` | _(unset)_ | Your service API key (free at `POST /billing/signup`), forwarded as the `x-api-key` header. Never logged. |

### Service API key (optional, v1.1.0)

19 of the 31 tools (pricing, routing, cost estimates, data quality) work with no key. Four more — `list_price_alerts`, `delete_price_alert`, `list_conversations`, `delete_conversation` — are operator-only (v1.79.2): they need the server operator's key (`MCP_API_KEY`) and return `error_code: "operator_key_required"` to anyone else. The 7 per-org usage and budget tools — `record_usage`, `import_session_usage`, `get_usage_summary`, `analyze_session_usage`, `register_budget_alert`, `list_budget_alerts`, `delete_budget_alert` — need your service API key and act only on that key's own org. Without a key they return `error_code: "customer_key_required"`; passing another org's `org_id` returns `error_code: "org_mismatch"`.

The `dispatch` tool (server v1.80.0) also needs your service key: it runs a text chat request through the dispatch gateway and makes a **real model call billed to your own provider keys** stored for your org with `POST /dispatch/keys`, returning the completion and the full `dispatch_receipt`. Without a key it returns `error: "dispatch_requires_api_key"`. Set `LLM_PRICING_MCP_TIMEOUT_MS` to `120000` for it, and bound calls with `dispatch.failover.timeout_ms` / `dispatch.failover.max_attempts`. No bridge upgrade is needed — the bridge forwards the call unchanged.

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

## Why a bridge instead of a bundled server?

The actual tool logic and pricing data live server-side and change independently of this package's release cycle — drift corrections, new providers, and new tools ship immediately to every client without anyone needing to update or republish this wrapper. The tradeoff is a network dependency on the hosted endpoint; if you need a fully offline server, clone [the main repo](https://github.com/skakumanu/llm-pricing-mcp-server) and run `mcp/server.py` directly instead (the local STDIO server is anonymous, so the 7 per-org usage/budget tools refuse there).

## License

MIT
