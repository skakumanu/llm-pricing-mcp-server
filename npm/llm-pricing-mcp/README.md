# llm-pricing-mcp

A zero-dependency STDIO launcher for the [LLM Pricing MCP server](https://github.com/skakumanu/llm-pricing-mcp-server) — drift-verified pricing, cost+quality model routing, and real usage tracking across 26 LLM providers, exposed as 29 MCP tools.

This package is a thin bridge, not the server itself: it reads MCP JSON-RPC requests from stdin and forwards each one to the hosted server at `https://llm-pricing-api.fly.dev/mcp`, writing the response back to stdout. There's nothing to install beyond Node — no Python, no `pip install`, no local pricing data to keep up to date.

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

Both are optional — the defaults work out of the box.

| Env var | Default | Purpose |
|---|---|---|
| `LLM_PRICING_MCP_URL` | `https://llm-pricing-api.fly.dev/mcp` | Point at a self-hosted or local instance instead |
| `LLM_PRICING_MCP_TIMEOUT_MS` | `30000` | Per-request timeout to the upstream server |

## Why a bridge instead of a bundled server?

The actual tool logic and pricing data live server-side and change independently of this package's release cycle — drift corrections, new providers, and new tools ship immediately to every client without anyone needing to update or republish this wrapper. The tradeoff is a network dependency on the hosted endpoint; if you need a fully offline server, clone [the main repo](https://github.com/skakumanu/llm-pricing-mcp-server) and run `mcp/server.py` directly instead.

## License

MIT
