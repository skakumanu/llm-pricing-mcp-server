# Claude Desktop Integration

- **Version**: see `src/__init__.py`
- **Tools**: all 32 tools; see the [tool reference](MCP_QUICK_START.md#tool-reference-32) for args and access levels

## Pick a setup

| Setup | Install | Service key | Offline |
|---|---|---|---|
| A. Connector (remote) | none | no (connectors send no custom headers) | no |
| B. npx bridge | Node | yes, via `LLM_PRICING_API_KEY` | no |
| C. Local STDIO | Python + clone | no (always anonymous) | yes |

## A. Connector (recommended)

- Open **Settings → Connectors → Add custom connector** and paste `https://llm-pricing-api.fly.dev/mcp`.
- Remote servers go here only. A `url` entry in `claude_desktop_config.json` is ignored; that file only launches local commands.
- The 19 public tools work immediately, and `support_chat` answers general support questions (with no key it cannot describe your account).

## B. npx bridge (to pass your service key)

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config), then restart:

```json
{ "mcpServers": { "llm-pricing": {
    "command": "npx", "args": ["-y", "llm-pricing-mcp"],
    "env": { "LLM_PRICING_API_KEY": "<your-service-key>", "LLM_PRICING_MCP_TIMEOUT_MS": "120000" } } } }
```

- Get a free key with `POST /billing/signup`. Key-gated tools then act only on that key's org, and `support_chat` can describe your own plan, payment status and rate limit.
- `LLM_PRICING_MCP_TIMEOUT_MS` matters only for `dispatch`.
- Bridge options: [package README](../npm/llm-pricing-mcp/README.md).

| OS | Config file |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

## C. Local STDIO

```json
{ "mcpServers": { "llm-pricing": {
    "command": "python",
    "args": ["/absolute/path/to/llm-pricing-mcp-server/mcp/server.py"],
    "cwd": "/absolute/path/to/llm-pricing-mcp-server",
    "env": { "PYTHONUNBUFFERED": "1" } } } }
```

- Install first: clone, create a venv, `pip install -r requirements.txt` ([steps](MCP_QUICK_START.md#local-stdio-server)).
- Use absolute paths; `~` and relative paths fail. On Windows, escape backslashes (`C:\\path\\to\\...`).
- Anonymous: key-gated and operator tools refuse, `dispatch` returns `dispatch_requires_hosted_mcp` and `support_chat` returns `support_requires_hosted_mcp`.
- Fully quit and relaunch Claude; "llm-pricing" should show in the tools panel.

## Dispatch from Claude

- `dispatch` routes a chat request to the best eligible model under your policy. Needs setup B.
- **It makes a real model call billed to your own provider keys** (stored with `POST /dispatch/keys`).
- Try: *"Use dispatch to answer 'what is MCP?' with the cheapest model scoring at least 70."*
- Details and refusal codes: [MCP_QUICK_START.md § dispatch](MCP_QUICK_START.md#dispatch).

## Support from Claude

- `support_chat` answers questions about this service (plans, billing, keys, rate limits, how to connect) from the published [FAQ](FAQ.md); with setup B it also knows your own account. Anything it cannot do (refunds, disputes, a lost key) it escalates to the owner and returns a reference.
- Try: *"Use support_chat to ask what plan I am on and what my rate limit is."*

## Recommendations for the tool you run

- `recommend_model`, `optimize_workload` and `compare_costs` accept `client` and then only name models that tool can run (Claude Desktop / claude.ai and Claude Code are Anthropic-only). Connected through the hosted server, Claude Desktop, claude.ai and Claude Code are inferred from the MCP handshake when you omit `client` (best effort; older clients that do not echo the `Mcp-Session-Id` header get the unfiltered ranking plus a note). Local STDIO infers from the same handshake.
- To override, pass `client` explicitly (e.g. `"client": "cursor"`); an explicit value always wins. The old `ide_context` argument is a deprecated alias that now restricts instead of only boosting.
- Try: *"Use recommend_model with client claude_code to pick the cheapest model for refactoring a Python module."*
- Recognised clients and the full override order: [MCP_QUICK_START.md § Client-aware recommendations](MCP_QUICK_START.md#client-aware-recommendations).

## Example prompts

- *"Cheapest model under $2/1M tokens for summarization?"*
- *"Compare Claude Sonnet, GPT-4o and Gemini 2.5 Pro for 5k input, 2k output tokens."*
- *"Which models got cheaper in the last 30 days?"*
- *"Alert me when GPT-4o's price changes by more than 10%."*
- *"Export the last 90 days of OpenAI pricing history as CSV."*

## Troubleshooting

| Problem | Check |
|---|---|
| Server not listed | `python -m json.tool claude_desktop_config.json`; absolute paths; restart fully |
| Added a `url` to the config file | Ignored; use a connector (A) or the bridge (B) |
| `customer_key_required` | Connectors send no key; switch to the bridge (B) |
| `operator_key_required` | Operator-only tool; needs the server's `MCP_API_KEY` |
| STDIO tools fail | `echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \| python mcp/server.py` from the repo root |
| Remote down | `curl https://llm-pricing-api.fly.dev/mcp` should return server info |

Logs: `~/Library/Logs/Claude/mcp.log` (macOS) · `%APPDATA%\Claude\Logs\mcp.log` (Windows).

**See also**: [MCP_QUICK_START.md](MCP_QUICK_START.md) · [README.md](../README.md)
