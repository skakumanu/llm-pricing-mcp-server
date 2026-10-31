# MCP Quick Start

- **Version**: see `src/__init__.py`
- **Protocol**: MCP 2024-11-05, JSON-RPC 2.0
- **Tools**: 31 MCP tools (full table below; this is the one canonical reference)

## Connect

| Transport | Endpoint | Use when |
|---|---|---|
| HTTP | `https://llm-pricing-api.fly.dev/mcp` | Client supports remote servers (Cursor, VS Code, Claude Code, Claude Desktop connectors) |
| STDIO via npx | `npx -y llm-pricing-mcp` | STDIO-only client, or you need to pass a service key from Claude Desktop |
| STDIO local | `python mcp/server.py` | Offline or developing the server; always anonymous |

- Client-specific setup: [Claude Desktop](CLAUDE_INTEGRATION.md) · [Perplexity](PERPLEXITY_INTEGRATION.md) · [VS Code](VS_CODE_INTEGRATION.md)
- One-click "Add to Cursor / VS Code" buttons: [`/mcp-setup`](../static/mcp-setup/index.html) (deep links are best-effort; the JSON beside them always works)

## Try it over HTTP

```bash
curl -X POST https://llm-pricing-api.fly.dev/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"estimate_cost","arguments":{"model_name":"gpt-4o","input_tokens":1000,"output_tokens":500}}}'
```

- Swap the method for `initialize` (expect `protocolVersion: "2024-11-05"`) or `tools/list` (expect 31 entries).
- `GET /mcp` returns server info; a notification (no `id`) returns HTTP 204.

## npx bridge (STDIO, no clone)

```json
{ "mcpServers": { "llm-pricing": {
    "command": "npx", "args": ["-y", "llm-pricing-mcp"],
    "env": { "LLM_PRICING_API_KEY": "<your-service-key>", "LLM_PRICING_MCP_TIMEOUT_MS": "120000" } } } }
```

- Zero-dependency Node bridge to the HTTP endpoint; `env` is optional. See the [package README](../npm/llm-pricing-mcp/README.md).
- The key is sent as `x-api-key` and never logged. The timeout is only needed for `dispatch`.

## Local STDIO server

```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git && cd llm-pricing-mcp-server
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp/server.py
```

- Works offline on bundled static pricing. Live refresh, `check_price_drift` and `ask_agent` need network.
- Anonymous only: key-gated and operator tools refuse, and `dispatch` returns `dispatch_requires_hosted_mcp`.

## Access levels

| Level | Count | Credential | Refusal without it |
|---|---|---|---|
| Public | 19 | none | — |
| Service key | 7 + `dispatch` | your key from `POST /billing/signup`, as `x-api-key` or `Authorization: Bearer` | `customer_key_required`; another org's `org_id` gives `org_mismatch` |
| Operator | 4 | server operator's `MCP_API_KEY` | `operator_key_required` (also for the chat agent and STDIO) |

- Service-key tools act only on that key's org.
- Refusals are `success: false` tool results, not JSON-RPC errors.

## Tool reference (31)

| Tool | Required | Key optional | Access |
|---|---|---|---|
| `get_all_pricing` | — | `currency`, `country` | public |
| `estimate_cost` | `model_name`, `input_tokens`, `output_tokens` | `currency`, `country` | public |
| `compare_costs` | `model_names[]` (1–50), `input_tokens`, `output_tokens` | `currency`, `country` | public |
| `predict_cost` | `prompt` | `task_type`, `top_n`, `cache_hit_ratio`, `require_vision` | public |
| `optimize_workload` | `workloads[]` | `monthly_budget_usd`, `min_quality_score` | public |
| `recommend_model` | `description` | `task_type`, `max_cost_per_1m_tokens`, `min_quality_score`, `monthly_budget_usd` | public |
| `check_price_drift` | — | `threshold_pct`, `provider`, `limit` | public |
| `get_data_quality` | — | — | public |
| `get_performance_metrics` | — | `provider`, `include_cost` | public |
| `get_use_cases` | — | `provider`, `currency` | public |
| `get_ide_pricing` | — | `provider`, `max_monthly`, `inline_only` | public |
| `get_pricing_history` | — | `model_name`, `provider`, `days` | public |
| `get_pricing_trends` | — | `days`, `limit` | public |
| `get_pricing_export_url` | — | `format`, `model_name`, `provider`, `days` | public |
| `register_price_alert` | `url` | `threshold_pct`, `provider`, `model_name` | public; returns a one-time `alert_token` |
| `manage_price_alert` | `action` (`view`/`delete`), `alert_token` | — | public; the token is the credential |
| `ask_agent` | `message` | `conversation_id`, `autonomous` | public |
| `get_telemetry` | — | `include_details`, `limit` | public |
| `get_cache_stats` | — | — | public |
| `record_usage` | `model_name`, `input_tokens`, `output_tokens` | `org_id`, `session_id`, `occurred_at` | service key |
| `import_session_usage` | `session_id`, `entries[]` | `org_id` | service key |
| `get_usage_summary` | — | `org_id`, `days` | service key |
| `analyze_session_usage` | `session_id` | `org_id` | service key |
| `register_budget_alert` | `url`, `threshold_usd` | `org_id`, `period_days` | service key |
| `list_budget_alerts` | — | — | service key |
| `delete_budget_alert` | `alert_id` | — | service key |
| `dispatch` | `prompt` or `messages` | `system`, `model`, `max_tokens`, `dispatch` | service key; hosted only |
| `list_price_alerts` | — | — | operator |
| `delete_price_alert` | `alert_id` | — | operator |
| `list_conversations` | — | `limit` | operator |
| `delete_conversation` | `conversation_id` | — | operator |

## `dispatch`

- Runs a chat request through the dispatch gateway, like `POST /v1/chat/completions` with a `dispatch` object. Returns the completion, `usage` and `dispatch_receipt`.
- **Makes a real model call billed to your own provider keys**, stored once per org with `POST /dispatch/keys`.
- Text-only, single choice, non-streaming. Shares one per-customer rate limit with HTTP dispatch.
- Refusals: `dispatch_requires_api_key` (anonymous or operator key), `dispatch_requires_hosted_mcp` (STDIO), plus HTTP's codes (`no_eligible_candidate`, `budget_hard_stop`, ...).
- Failover can outlast the bridge's 30 s default. Set `LLM_PRICING_MCP_TIMEOUT_MS=120000` and cap with `dispatch.failover.timeout_ms` / `max_attempts`.

## Troubleshooting

| Issue | Fix |
|---|---|
| No output from the STDIO server | Expected: it waits for JSON-RPC on stdin |
| `ModuleNotFoundError` (STDIO) | Run from the repo root with the venv active |
| `Model not found` | Call `get_all_pricing` for valid names |
| `-32601` error | Unknown JSON-RPC method |

Tests: `pytest tests/test_mcp_http.py -q`. More in [MCP_TESTING.md](MCP_TESTING.md).
