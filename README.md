# LLM Pricing MCP Server

[![CI/CD Pipeline](https://github.com/skakumanu/llm-pricing-mcp-server/workflows/CI%2FCD%20Pipeline/badge.svg)](https://github.com/skakumanu/llm-pricing-mcp-server/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

LLM pricing you can verify, route on, and act on: **31 MCP tools**, a REST API and browser UIs over pricing data from **30 LLM providers**.

**Live at**: https://llm-pricing-api.fly.dev · **API reference**: [/api-docs](https://llm-pricing-api.fly.dev/api-docs) · **Swagger**: [/docs](https://llm-pricing-api.fly.dev/docs)

---

## What it does

- **Verified prices**: every curated price is checked against a live external registry; anything drifted more than 5% is withheld, never silently served.
- **Routes, not just quotes**: picks a model by cost *and* quality for your task.
- **Executes**: the dispatch gateway runs the best eligible model on your own provider keys.
- **Tracks real spend**: records usage, analyzes sessions, fires budget alerts.
- **Price alerts**: webhooks on price changes, self-managed with an `alert_token`.
- **Local currency**: optional `currency` / `country` on pricing and estimates (13 currencies).
- **Many surfaces**: MCP (STDIO + HTTP), REST, chat agent, calculator / compare / history UIs, billing dashboard.
- **Retired providers**: Anyscale (2026-10)

---

## Quick start

**Claude Desktop (remote, no install)**: open **Settings → Connectors → Add custom connector** and paste:

```
https://llm-pricing-api.fly.dev/mcp
```

- Claude Desktop ignores a `url` entry in `claude_desktop_config.json`; that file only launches local commands.
- A connector sends no custom headers. For tools that need your service key, use the npx bridge below.

**npx bridge (STDIO, no clone, no Python)**: add to `claude_desktop_config.json`:

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

- `LLM_PRICING_API_KEY` is optional; it is forwarded as `x-api-key`. Options are in [npm/llm-pricing-mcp/README.md](npm/llm-pricing-mcp/README.md).
- Other clients (Cursor, VS Code, Claude Code): [docs/MCP_QUICK_START.md](docs/MCP_QUICK_START.md) · Claude details and troubleshooting: [docs/CLAUDE_INTEGRATION.md](docs/CLAUDE_INTEGRATION.md).

**Run locally** (Python 3.11):

```bash
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8000   # REST + POST /mcp; STDIO server: python mcp/server.py
```

---

## MCP tools

All 31 tools are listed over `POST /mcp` and STDIO. MCP protocol version `2024-11-05`.

| Group | Tools |
|---|---|
| Pricing and estimates | `get_all_pricing`, `estimate_cost`, `compare_costs`, `predict_cost`, `get_performance_metrics`, `get_use_cases`, `get_ide_pricing`, `get_pricing_export_url` |
| Routing | `recommend_model`, `optimize_workload`, `get_cache_stats` |
| Data quality | `check_price_drift`, `get_data_quality` |
| History | `get_pricing_history`, `get_pricing_trends`, `get_telemetry` |
| Price alerts | `register_price_alert`, `manage_price_alert`, `list_price_alerts`, `delete_price_alert` |
| Usage and budget | `record_usage`, `get_usage_summary`, `analyze_session_usage`, `import_session_usage`, `register_budget_alert`, `list_budget_alerts`, `delete_budget_alert` |
| Agent and dispatch | `ask_agent`, `list_conversations`, `delete_conversation`, `dispatch` |

The chat agent binds 27 of the 31 MCP tools (all except `ask_agent`, `get_telemetry`, `get_cache_stats` and `dispatch`).

---

## Key features

### Dispatch gateway
- Add a `dispatch` policy to `POST /v1/chat/completions`. The best eligible model runs for real and you get a `dispatch_receipt` with the chosen model, reason, actual cost and savings.
- Objectives: `balanced`, `cheapest`, `best_quality`, `lowest_latency`, `pinned`. Failover never drops below your `min_quality_score`.
- Supports `stream: true` (OpenAI-format SSE; the receipt arrives in the final chunk).
- Text-only, single-choice. `tools`, `n > 1` and image parts are rejected up front, with the feature named.
- Also available as the `dispatch` MCP tool. Executable providers are listed at `GET /dispatch/providers`.

### Stored provider keys
- Store each provider key once per org with `POST /dispatch/keys`. After that, callers need only their service key.
- Keys are AES-256-GCM-encrypted at rest and never returned; listings show only a last-4 fingerprint.
- A per-request `X-Provider-Key-<Provider>` header still overrides the stored key.

### Price alerts with `alert_token`
- Registering a price alert returns a one-time `alert_token`. It is stored only as a hash and nobody can recover it.
- View or delete your alert with no account: `POST /pricing/alerts/self/view` or `/self/delete` with the token in the JSON body, or the `manage_price_alert` tool.
- Listing or deleting *all* alerts is operator-only.

### Webhook SSRF guard
- Webhook URLs must be **public https**. Private, loopback, metadata and unresolvable hosts are refused with 422 `invalid_webhook_url`.
- The URL is checked at registration and again at every delivery, connecting only to the checked IP; redirects are never followed.
- Older alerts that break the rules are kept but skipped at delivery.
- `WEBHOOK_ALLOW_PRIVATE_TARGETS` is for local development only; never set it in production.

### Cost estimation
- `POST /cost-estimate` (one model) and `POST /cost-estimate/batch` (1–50 models). No key needed; requests are rate-limited.
- `predict_cost` and `scripts/audit_repo.py` estimate the cost of a workload or a codebase's LLM call sites.

### Data quality
- `GET /data-quality` gives one trust score, `confirmed_pct`, plus counts of drift-withheld, never-priced and stale models.
- Every price carries `price_source_kind` (`provider` or `registry`). `check_price_drift` gives per-model detail.

### Usage and budget
- `POST /usage` records actual spend. `GET /usage/summary` and `GET /usage/session/{session_id}` analyze it.
- Budget alerts (`POST /usage/alerts`) fire a webhook at a threshold; `hard_stop: true` also blocks dispatch for your org.

---

## Access and keys

| Access | Who | What it unlocks |
|---|---|---|
| Public | Anyone | Pricing, estimates, data quality, history, registering a price alert, the chat agent: 19 public MCP tools |
| Service key | You (free: `POST /billing/signup`) | Usage/budget tools and endpoints, router, `dispatch`, `/dispatch/keys`; acts only on your own org |
| Operator key | Server operator (`MCP_API_KEY`) | Listing/deleting all price alerts and conversations, admin customer list |

- Send keys as `x-api-key: <your-key>` (MCP also accepts `Authorization: Bearer <your-key>`).
- Tiers: Free 30 / Pro 120 / Enterprise 600 req/min. Full endpoint and auth map: [/api-docs](https://llm-pricing-api.fly.dev/api-docs).

---

## Configuration

Set via environment variables or `.env`. An unknown key in `.env` stops startup with an error naming it.

| Variable | Purpose |
|---|---|
| `AGENT_LLM_PROVIDER` / `AGENT_MODEL` | Chat agent backend: `anthropic` (default) or `openai` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Key for the chosen agent backend |
| `MCP_API_KEY` | Operator key; protected endpoints are open when unset |
| `PROVIDER_KEY_ENCRYPTION_KEY` | Enables stored provider keys (unset = 503 on `/dispatch/keys`) |
| `WEBHOOK_SECRET` | HMAC-SHA256 signing for alert webhooks |
| `WEBHOOK_ALLOW_PRIVATE_TARGETS` | Local dev only: allow http/private webhook targets |
| `BILLING_DB_PATH` / `PRICING_HISTORY_DB_PATH` | SQLite file locations |

Full list: `src/config/settings.py`. Production settings: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Development and tests

```bash
pytest tests/ -q
```

- 3080 passing tests, CI/CD on every PR (test → lint → bandit → OSV → gitleaks → deploy)
- Git Flow: `feature/*` → `develop` → `master`. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).
- Internals, services, data stores and CI: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Deploys to Fly.io from `master` when all CI gates pass: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Links

- [docs/INDEX.md](docs/INDEX.md): every doc
- [docs/archive/README.md](docs/archive/README.md): historical docs (blue-green deployment, MCP validation report)
- [/whats-new](https://llm-pricing-api.fly.dev/whats-new): release notes
- [/mcp-setup](https://llm-pricing-api.fly.dev/mcp-setup): MCP integration hub
- License: MIT, see [LICENSE](LICENSE)
