# LLM Pricing MCP Server

[![CI/CD Pipeline](https://github.com/skakumanu/llm-pricing-mcp-server/workflows/CI%2FCD%20Pipeline/badge.svg)](https://github.com/skakumanu/llm-pricing-mcp-server/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

LLM pricing you can verify, route on, and act on: **32 MCP tools**, a REST API and browser UIs over pricing data from **30 LLM providers**.

**Live at**: https://llm-pricing-api.fly.dev · **API reference**: [/api-docs](https://llm-pricing-api.fly.dev/api-docs) · **Swagger**: [/docs](https://llm-pricing-api.fly.dev/docs)

---

## What it does

- **Verified prices**: every curated price is checked against a live external registry; anything drifted more than 5% is withheld, never silently served.
- **Routes, not just quotes**: picks a model by cost *and* quality for your task.
- **Executes**: the dispatch gateway runs the best eligible model on your own provider keys.
- **Tracks real spend**: records usage, analyzes sessions, fires budget alerts.
- **Price alerts**: webhooks on price changes, self-managed with an `alert_token`.
- **Local currency**: optional `currency` / `country` on pricing and estimates (13 currencies).
- **Many surfaces**: MCP (STDIO + HTTP), REST, chat agent, calculator / compare / history UIs, billing dashboard, FAQ (`/faq`), published legal pages and security statement (`/terms`, `/privacy`, `/refunds`, `/dpa`, `/subprocessors`, `/security`).
- **Customer support**: a grounded support assistant (Support mode on `/chat`, `support_chat` MCP tool) that knows the FAQ, the plan catalogue and your own account, and escalates anything it cannot do to the owner with a reference.
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

All 32 tools are listed over `POST /mcp` and STDIO. MCP protocol version `2024-11-05`.

| Group | Tools |
|---|---|
| Pricing and estimates | `get_all_pricing`, `estimate_cost`, `compare_costs`, `predict_cost`, `get_performance_metrics`, `get_use_cases`, `get_ide_pricing`, `get_pricing_export_url` |
| Routing | `recommend_model`, `optimize_workload`, `get_cache_stats` |
| Data quality | `check_price_drift`, `get_data_quality` |
| History | `get_pricing_history`, `get_pricing_trends`, `get_telemetry` |
| Price alerts | `register_price_alert`, `manage_price_alert`, `list_price_alerts`, `delete_price_alert` |
| Usage and budget | `record_usage`, `get_usage_summary`, `analyze_session_usage`, `import_session_usage`, `register_budget_alert`, `list_budget_alerts`, `delete_budget_alert` |
| Agent, support and dispatch | `ask_agent`, `support_chat`, `list_conversations`, `delete_conversation`, `dispatch` |

Pass `client` (e.g. `Claude Code`, `cursor`, `github copilot`, `cline`) to `recommend_model`, `optimize_workload`, `compare_costs` or `POST /router/recommend` and the answer is restricted to models that tool can run before ranking; on the hosted MCP server it is inferred from the handshake when omitted, and an explicit value always wins. Recognised clients and the override order are in [docs/MCP_QUICK_START.md](docs/MCP_QUICK_START.md#client-aware-recommendations).

The chat agent binds 27 of the 32 MCP tools (all except `ask_agent`, `support_chat`, `get_telemetry`, `get_cache_stats` and `dispatch`).

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

### Customer support and plans
- Support mode on [/chat](https://llm-pricing-api.fly.dev/chat?mode=support) and the `support_chat` MCP tool answer plan, billing, key, rate-limit and how-to questions only from the published [FAQ](https://llm-pricing-api.fly.dev/faq) (`docs/FAQ.md`), the plan catalogue (`GET /billing/config`) and, with your service key, your own account. No invented prices, policies or response times.
- Everything outside its authority (refunds, credits, disputes, failed payments, a lost key without the current key, bug/feature/security reports, "talk to a human") becomes an escalation in the owner's operator-only queue with a reference; `POST /support/escalations/status` returns the owner's reply.
- Plans describe only what is implemented: rate limit, support assistant allowance and escalation priority. Upgrade and Manage Billing work when Stripe is configured; `POST /billing/key/rotate` rotates a key with the current one; `GET /billing/account/export` downloads everything held about your organisation and `POST /billing/account/delete` (body `{"confirm": "DELETE", "org_id": ...}`) irreversibly erases it, ends any Stripe subscription and opens a refund review when you delete inside the 14-day window (v1.87.0); service keys are stored only as a SHA-256 hash; the Stripe webhook handles failed renewals (14-day grace, then the free limit), refunds and disputes, and ignores duplicate deliveries.
- Legal pages (v1.86.0): [/terms](https://llm-pricing-api.fly.dev/terms), [/privacy](https://llm-pricing-api.fly.dev/privacy), [/refunds](https://llm-pricing-api.fly.dev/refunds), [/dpa](https://llm-pricing-api.fly.dev/dpa), [/subprocessors](https://llm-pricing-api.fly.dev/subprocessors) are generated from `docs/legal/*.md` (`scripts/sync_legal.py`), linked from the site footer, `/billing` and `/faq`, and show a draft notice until the owner turns on `LEGAL_PAGES_LIVE` and every placeholder is replaced. Paid checkout stays disabled until then; the Stripe customer portal is unaffected. Refunds: full within 14 days of the first payment, pro-rated after, every request reviewed by the owner via the support assistant. [/security](https://llm-pricing-api.fly.dev/security) states what the service does and does not have (no SOC 2, ISO 27001 or third-party penetration test). The pre-launch list is [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md).
- Spend: per-caller daily LLM caps by tier (anonymous strictest) under the orchestrator's global monthly ceiling; a cap hit returns a non-LLM fallback with FAQ links. Every support action leaves an operator-only, append-only record.

### Orchestrator core (operator-only)
- One place where the operator's inbound work (GitHub issues/PRs, the weekly price audit, owner requests) is accepted exactly once, routed by trigger class and owner-applied labels only, and held at human checkpoints.
- `/orchestrator/*` plus an Orchestrator block on `/admin`: approval queue, parked runs, run log (CSV), global pause, per-workflow mode, budgets. Nothing merges, moves money or sends anything without a recorded approval.
- The SDLC pipeline is wrapped as an owner checkpoint: the queue shows the exact `/release-feature` command; the PR it opens completes the run and posts one status comment on the issue.

---

## Access and keys

| Access | Who | What it unlocks |
|---|---|---|
| Public | Anyone | Pricing, estimates, data quality, history, registering a price alert, the chat agent, the support assistant in general mode: 19 public MCP tools + the identity-aware `support_chat` |
| Service key | You (free: `POST /billing/signup`) | Usage/budget tools and endpoints, router, `dispatch`, `/dispatch/keys`, `/billing/me`, `/billing/key/rotate`, `/billing/account/export`, `/billing/account/delete`; acts only on your own org, and `support_chat` / `POST /support/chat` can describe your own account |
| Operator key | Server operator (`MCP_API_KEY`) | Listing/deleting all price alerts and conversations, admin customer list and owner-side key rotation (`/admin/customers/{id}/key/rotate`), every `/orchestrator/*` route |
| Intake key | GitHub Actions executor (`ORCHESTRATOR_INTAKE_KEY`) | Only `POST /orchestrator/events`, `GET /orchestrator/runs/{id}/actions`, `POST …/actions/{aid}/result`; it carries the workflow's owner assertion for GitHub events, so treat it as a secret |

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
| `ORCHESTRATOR_DB_PATH` | Orchestrator SQLite file (runs, approvals, action records, spend, settings) |
| `ORCHESTRATOR_INTAKE_KEY` | Separate credential for the GitHub Actions executor; accepted on three routes only |
| `ORCHESTRATOR_RUN_RETENTION_DAYS` / `ORCHESTRATOR_IO_RETENTION_DAYS` | Orchestrator retention: rows (365) and redacted I/O summaries (90) |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` / `STRIPE_PRICE_ID_PRO` / `STRIPE_PRICE_ID_ENTERPRISE` | Paid tiers; checkout, portal and webhook stay 503 until set, and checkout also waits for the legal pages to be in force (`GET /billing/config` reports `checkout_disabled_reason`) |
| `LEGAL_PAGES_LIVE` | Marks the legal pages in force (default false). Even when true, a remaining `[PLACEHOLDER]` in `docs/legal/` keeps the pages draft and checkout disabled |
| `CONVERSATION_DB_PATH` / `CONVERSATION_RETENTION_DAYS` | Support conversations are stored per organisation in this SQLite file (`/app/data/conversations.db` on Fly, replicated) and purged after the retention days (90; `<= 0` disables). Unset path = in memory (v1.87.0) |
| `BILLING_REFUND_WINDOW_DAYS` | Full-refund window quoted by `/refunds` and the FAQ (14); policy copy, refunds are never automatic |
| `LITESTREAM_REPLICA_URL` / `LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY` | Fly secrets (process environment, not `.env`) that turn on continuous replication of the four production databases; restore steps in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#restore-from-the-litestream-replica) |
| `BILLING_PAST_DUE_GRACE_DAYS` | Days a failed renewal keeps the paid tier before the account is served at the free limit (14) |
| `SUPPORT_ENABLED` | `false` turns the support assistant into a FAQ-and-escalation fallback (default `true`) |
| `SUPPORT_DAILY_CAP_ANONYMOUS_USD` / `_FREE_USD` / `_PRO_USD` / `_ENTERPRISE_USD` | Per-caller daily LLM allowance for support by tier (0.10 / 0.50 / 3.00 / 10.00) |
| `SUPPORT_MAX_ESCALATIONS_PER_DAY` | Escalations an identified customer may open per day (3; anonymous callers get 1) |
| `SUPPORT_FALLBACK_PRICE_PER_1M_IN_USD` / `SUPPORT_FALLBACK_PRICE_PER_1M_OUT_USD` | Price a support turn when `AGENT_MODEL` is not in the catalogue (3.0 / 15.0) |

Full list: `src/config/settings.py`. Production settings: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Development and tests

```bash
pytest tests/ -q
```

- 3812 passing tests, CI/CD on every PR (test → lint → bandit → OSV → gitleaks → deploy); CodeQL and Dependabot run as additional scanners
- Git Flow: `feature/*` → `develop` → `master`. See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).
- Internals, services, data stores and CI: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Deploys to Fly.io from `master` when all CI gates pass: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Links

- [docs/INDEX.md](docs/INDEX.md): every doc
- [docs/archive/README.md](docs/archive/README.md): historical docs (blue-green deployment, MCP validation report)
- [/whats-new](https://llm-pricing-api.fly.dev/whats-new): release notes
- [/faq](https://llm-pricing-api.fly.dev/faq): plans, billing, keys, rate limits, how to get help
- [/terms](https://llm-pricing-api.fly.dev/terms) · [/privacy](https://llm-pricing-api.fly.dev/privacy) · [/refunds](https://llm-pricing-api.fly.dev/refunds) · [/dpa](https://llm-pricing-api.fly.dev/dpa) · [/subprocessors](https://llm-pricing-api.fly.dev/subprocessors) · [/security](https://llm-pricing-api.fly.dev/security): legal pages and security statement
- [/mcp-setup](https://llm-pricing-api.fly.dev/mcp-setup): MCP integration hub
- License: MIT, see [LICENSE](LICENSE)
