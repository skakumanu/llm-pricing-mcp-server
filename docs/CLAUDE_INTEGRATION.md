# Claude Desktop Integration

- **Version**: see `src/__init__.py`
- **Tools**: all 31 tools; see the [tool reference](MCP_QUICK_START.md#tool-reference-31) for args and access levels

## Pick a setup

| Setup | Install | Service key | Offline |
|---|---|---|---|
| A. Connector (remote) | none | no (connectors send no custom headers) | no |
| B. npx bridge | Node | yes, via `LLM_PRICING_API_KEY` | no |
| C. Local STDIO | Python + clone | no (always anonymous) | yes |

## A. Connector (recommended)

- Open **Settings → Connectors → Add custom connector** and paste `https://llm-pricing-api.fly.dev/mcp`.
- Remote servers go here only. A `url` entry in `claude_desktop_config.json` is ignored; that file only launches local commands.
- The 19 public tools work immediately.

## B. npx bridge (to pass your service key)

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config), then restart:

```json
{ "mcpServers": { "llm-pricing": {
    "command": "npx", "args": ["-y", "llm-pricing-mcp"],
    "env": { "LLM_PRICING_API_KEY": "<your-service-key>", "LLM_PRICING_MCP_TIMEOUT_MS": "120000" } } } }
```

- Get a free key with `POST /billing/signup`. Key-gated tools then act only on that key's org.
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
- Anonymous: key-gated and operator tools refuse, and `dispatch` returns `dispatch_requires_hosted_mcp`.
- Fully quit and relaunch Claude; "llm-pricing" should show in the tools panel.

## Dispatch from Claude

- `dispatch` routes a chat request to the best eligible model under your policy. Needs setup B.
- **It makes a real model call billed to your own provider keys** (stored with `POST /dispatch/keys`).
- Try: *"Use dispatch to answer 'what is MCP?' with the cheapest model scoring at least 70."*
- Details and refusal codes: [MCP_QUICK_START.md § dispatch](MCP_QUICK_START.md#dispatch).

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
