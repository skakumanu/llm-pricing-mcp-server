# Architecture — LLM Pricing MCP Server

**Version**: v1.82.3 | **Last updated**: 2026-10-31

Canonical internals reference. Release history lives in `static/whats-new/`, not here.

---

## System Overview

FastAPI service that aggregates LLM pricing from 30 providers (600+ models) and serves it over REST and MCP, with a ReAct agent, a dispatch gateway, self-serve billing and 14 browser UIs.

- **Deploy**: Fly.io app `llm-pricing-api` (shared-cpu-1x, 512 MB, volume at `/app/data`)
- **CI/CD**: test → lint → OSV → bandit → gitleaks → Fly deploy (see [CI/CD](#cicd-pipeline))
- **Price integrity**: drifted prices are withheld per request; weekly `price-audit.yml` PRs re-date, retire and pick up registry models
- **Dispatch gateway**: `POST /v1/chat/completions` + `dispatch` object executes the best eligible model on the caller's own provider key
- **Local currency**: 13 currencies, additive `local_*` fields; pricing data itself stays USD
- **Dev tooling (not deployed)**: `/release-feature` phase-agent pipeline (see `CLAUDE.md`); `scripts/audit_repo.py` customer-run cost auditor

---

## Layer Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ Clients: browser UIs · MCP clients · npx bridge · REST · OpenAI SDKs │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ HTTPS / STDIO
┌───────────────────────────────▼──────────────────────────────────────┐
│ Presentation — src/main.py + mcp/                                    │
│  REST API          MCP: 31 tools, STDIO + POST /mcp    14 UI pages   │
│  security_middleware: operator / customer / global key, rate limit,  │
│  body cap · per-bucket sliding window (src/services/rate_limiter.py) │
└───────┬───────────────────────┬──────────────────────┬───────────────┘
┌───────▼──────────────┐ ┌──────▼─────────┐ ┌──────────▼──────────────┐
│ Pricing & usage      │ │ Agent / RAG    │ │ Billing                 │
│ PricingAggregator    │ │ ReAct loop     │ │ BillingService          │
│ 30 provider impls    │ │ TF-IDF RAG     │ │ free signup → API key   │
│ PriceOracle, Router  │ │ conv. memory   │ │ Stripe (optional)       │
│ Usage/Budget/Alerts  │ │ Anthropic or   │ │ ProviderKeyStore        │
│ WebhookGuard         │ │ OpenAI backend │ │ (AES-GCM, per org)      │
│ Currency · Dispatch  │ └──────┬─────────┘ └──────────┬──────────────┘
└───────┬──────────────┘        │                      │
┌───────▼───────────────────────▼──────────────────────▼───────────────┐
│ Data: pricing_history.db · billing.db · in-memory caches             │
└───────┬──────────────────────────────────────────────────────────────┘
┌───────▼──────────────────────────────────────────────────────────────┐
│ External: provider pricing/model APIs · reference price registry ·   │
│ Stripe · Hugging Face (benchmarks) · agent LLM · 9 dispatch          │
│ inference APIs (caller-keyed, httpx only)                            │
└──────────────────────────────────────────────────────────────────────┘
```

---

## File Structure

```
src/
  __init__.py               __version__ (single source of truth)
  main.py                   FastAPI app, all endpoints, auth/rate-limit/body-cap middleware
  config/settings.py        pydantic-settings; unknown .env keys fail startup (RETIRED_ENV_KEYS)
  models/                   pricing.py, billing.py, deployment.py, dispatch.py (DispatchPolicy, ChatCompletionsRequest)
  services/
    pricing_aggregator.py   PROVIDER_SERVICES list, async fan-out + TTL cache, get_pricing_aggregator() singleton
    base_provider.py        BasePricingProvider: build_metrics, registry-entry merge, live model sync
    *_pricing.py            one service per provider (30)
    price_oracle.py         reference registry: gap-fill, drift + retirement withholding (24h TTL + snapshot)
    data_quality.py         accuracy summary for /data-quality + get_data_quality
    cost_calculator.py      compute_token_cost(), shared by REST and MCP cost paths
    router.py               routing engine; rank_models() / hard_filter_reason() reused by dispatch
    recommendation_cache.py ~5 min decision cache + hit/miss stats
    benchmark_service.py    quality scores: static table + HF fallback
    task_profiles.py, portfolio_optimizer.py, token_counter.py, session_recommendation.py, quality_tradeoff_caveat.py
    pricing_history.py      snapshots + routing feedback tables
    savings_tracker.py, usage_tracker.py, budget_alerts.py   router savings, actual usage, spend alerts (+hard_stop)
    pricing_alerts.py       price-change webhooks; one-time alert_token (hash at rest)
    webhook_guard.py        SSRF rules at registration + PinnedWebhookTransport at delivery
    currency.py             13 currencies, country map, vendored rates, resolve_locale()
    dispatch_entry.py       prepare_and_dispatch(): one pre-dispatch pipeline for HTTP + MCP
    dispatch_service.py     eligibility → rank → cap → failover/shadow → ledgers → receipt (+ streaming)
    dispatch_executors.py   9 executable providers, 3 httpx wire families, one error table
    dispatch_receipts.py    metadata-only receipts table + org-scoped export
    provider_key_store.py   encrypted per-org provider keys; CompositeKeyResolver
    rate_limiter.py         the one sliding-window store, shared by HTTP and MCP dispatch
    billing_service.py      customers table, Stripe sync
    ide_pricing.py, telemetry.py, geolocation.py, data_fetcher.py, data_sources.py, deployment.py
mcp/
  server.py                 STDIO transport (always anonymous; transport='stdio')
  caller.py                 McpCaller + access levels PUBLIC / ORG_SCOPED / OPERATOR_ONLY / CUSTOMER_ONLY
  tools/                    one module per tool + tool_manager.py + _locale.py
  schema_generator.py, schemas/   generated JSON-RPC schemas
agent/                      pricing_agent.py, react_loop.py, llm_backend.py, conversation.py, tools.py
rag/                        document_loader, chunker, in-memory TF-IDF vector_store, pipeline
static/                     14 UI pages (see Design System); landing/ is served at /
data/
  model_prices_snapshot.json  vendored registry snapshot (weekly refresh PR)
  exchange_rates.json         vendored USD rate table (weekly refresh PR)
  registry_models/<slug>.json registry-sourced models per provider (written only by pickup script)
scripts/                    weekly audit scripts, validate_price_snapshot.py, sync_nav.py,
                            quick_validate.py, audit_repo.py, promote_branch_content.sh,
                            release_notes_from_whats_new.py (deploy job → GitHub Release notes)
npm/llm-pricing-mcp/        zero-dependency STDIO↔HTTP bridge published to npm
server.json                 MCP registry listing (remote + npm package)
.github/workflows/          ci-cd, sync-develop, price-audit, npm-publish, mcp-registry-publish
tests/                      pytest suite (count: README "passing tests" line)
fly.toml, Dockerfile        Fly config; image copies src/ static/ agent/ rag/ mcp/ docs/ data/
CLAUDE.md                   session rules + release checklist
```

---

## Key Patterns

### Singleton services
- Each stateful service exposes `init_x()` (async, at startup) and `get_x()` (raises if uninitialised).
- All `init_*` calls run in the `startup_pricing_history()` startup handler.
- `get_pricing_aggregator()` is lazy and lock-guarded; REST and both MCP transports share it → one pricing cache per process.

### Auth middleware (`security_middleware`)
Checked in this order:

| Step | Paths | Effect |
|---|---|---|
| 1. Operator-only request | GET/HEAD `/pricing/alerts`, `/agent/conversations`; DELETE `/pricing/alerts/{id}`, `/agent/conversations/{id}` | Decided first so the `/pricing` prefix bypass can't expose them |
| 2. Full bypass | OPTIONS, static UIs, `/pricing*`, `/mcp`, `_unauthenticated_paths` | No auth, no limiter; `_public_body_capped_paths` (`/v1/chat/completions`, `/mcp`) still body-capped |
| 3. Operator key | step 1 + `_sensitive_paths` (`/deployment/shutdown*`, `/admin/customers`, `/admin/stats`, `/admin/rate-limits`) | Global `MCP_API_KEY` in `x-api-key` only (byte compare); 503 if unset |
| 4. Anonymous, rate-limited | `/agent/chat*`, `/billing/signup`, POST `/pricing/alerts`, `/pricing/alerts/self/*`, `/cost-estimate*` | Per-IP limit + body cap; a customer key on `/cost-estimate*` gets its tier limit |
| 5. Service key | everything else | Billing customer key (tier from DB) or global key; 401 otherwise |

- Rate limit: 60 s sliding window per bucket (customer id, else `ip:tier`); free / pro / enterprise = 30 / 120 / 600 req/min.
- `X-Api-Key-Tier` is honoured only alongside a valid global key.
- Handlers that need an org (`/dispatch/*`, dispatch mode) also require `request.state.customer`; the global key gets 403 there.

### Provider pattern
- Each of 30 LLM providers implements `BasePricingProvider.fetch_pricing_data()`; `get_pricing_with_status()` adds registry entries and live sync.
- **Registry-sourced entries**: `data/registry_models/<slug>.json` merged at fetch time; curated `STATIC_PRICING` wins a name collision.
- Every `PricingMetrics` carries `price_source_kind` (`provider` | `registry`) and `registry_key`.
- **Live model sync**: `/v1/models` per provider (6 h cache) drops delisted models; falls back to the static list. Enabled for 12 providers.
- New provider = new `*_pricing.py` + one `PROVIDER_SERVICES` entry (+ `PROVIDER_FILTER_ALIASES`). `PROVIDER_COUNT` derives from the list; docs must agree (`tests/test_new_providers.py`).

### Price oracle and data quality
- Fills gaps from the reference registry; withholds a price that drifts >5% on input or output.
- Withholds a model once its registry deprecation date passes (`retired_on`).
- `get_data_quality` splits `confirmed_models` into `provider_confirmed` + `registry_sourced`; registry prices are never counted as provider-confirmed.

### MCP transports
- **STDIO** (`mcp/server.py`) and **HTTP** (`POST /mcp`): JSON-RPC 2.0, protocol `2024-11-05`, 31 tools.
- **npx bridge** (`npm/llm-pricing-mcp`): forwards stdin lines to `POST /mcp`; optional `LLM_PRICING_API_KEY` → `x-api-key`. No tool logic of its own.
- `ToolManager.execute_tool(name, args, caller)`; only HTTP `/mcp` resolves a caller, and only for non-PUBLIC tools.

| Access level | Tools | Caller needed |
|---|---|---|
| PUBLIC | the other 19 | none |
| ORG_SCOPED | `record_usage`, `import_session_usage`, `get_usage_summary`, `analyze_session_usage`, `register_budget_alert`, `list_budget_alerts`, `delete_budget_alert` | customer key (own org only) or operator |
| OPERATOR_ONLY | `list_price_alerts`, `delete_price_alert`, `list_conversations`, `delete_conversation` | global `MCP_API_KEY` |
| CUSTOMER_ONLY | `dispatch` | customer key; STDIO refuses with `dispatch_requires_hosted_mcp` |

- Refusals are tool results (`success: false`): `customer_key_required`, `org_mismatch`, `operator_key_required`, `dispatch_requires_api_key`.

### Agent (ReAct)
```
message → react_loop (think → tool → observe) → llm_backend (Anthropic | OpenAI, AGENT_LLM_PROVIDER)
        → conversation store (SQLite if CONVERSATION_DB_PATH, else memory) → SSE events
```
- `agent/tools.py` binds 27 of the 31 MCP tools; excludes `ask_agent`, `get_telemetry`, `get_cache_stats`, `dispatch`.
- The agent is an anonymous caller: org-scoped and operator-only tools refuse at runtime.
- Stored turns pass through `redact_alert_tokens()`.

### Billing (Stripe optional)
- `POST /billing/signup` → new customer row → service key shown once (409 for an existing email).
- `POST /billing/checkout` → Stripe session; `POST /billing/webhook` → verify signature → update tier.
- Without `STRIPE_SECRET_KEY`, checkout/portal/webhook return 503; signup still works.

### Local currency
- One dependency, `resolve_request_locale`: currency query > `X-Currency` > country query > `X-Country` > platform geo header > IP lookup (1 s) > USD.
- Unsupported explicit currency → 400 `unsupported_currency`.
- Conversion runs on per-request `model_copy()`s; `local_*` fields are omitted when nothing is stated or detected.
- MCP tools take explicit `currency`/`country` only (no detection).
- Responses send `Vary` on the locale headers; a detected locale is `Cache-Control: private`.
- Rates: `data/exchange_rates.json`, read-only at request time, flagged stale after 7 days.
- Privacy: the IP resolves a country transiently; never stored, logged or echoed.

### Dispatch gateway
```
POST /v1/chat/completions {…, dispatch: {policy}, stream?}
  no dispatch object        → plain recommendation proxy (public, unchanged)
  unsupported feature       → 400 unsupported_feature (tools, n>1, response_format, …)
  no customer key           → 401 dispatch_requires_api_key
  prepare_and_dispatch()    → shared with the MCP dispatch tool
DispatchService
  1. budget hard stop       → 402 budget_hard_stop
  2. catalogue + eligibility (price_confirmed, executable, keyed, policy, router filters, cost cap)
                            → 400 no_eligible_candidate / pinned_model_ineligible
  3. rank_models() + objective (cheapest | best_quality | lowest_latency | pinned)
  4. failover: timeout/429/5xx/model_not_found → next; context_too_long → larger window only;
     auth_error → 400 provider_auth_failed (terminal); exhausted → 502 all_attempts_failed
  5. ledgers: usage event, routing decision, budget alerts, receipt row
  6. chat.completion + usage + dispatch_receipt
```
- **Streaming** (`stream: true`): failover only before the first delta; trailing chunk carries usage + receipt, then `[DONE]`.
- **Shadow**: optional single-attempt comparison call; its text is never returned, its spend is recorded.
- **Executors**: `openai_wire`, `anthropic_messages`, `gemini_rest` over httpx; no vendor SDK (SDK retries would fight failover).
- **Settings**: `DISPATCH_ENABLED`, `DISPATCH_DEFAULT_MAX_TOKENS`, `DISPATCH_ATTEMPT_TIMEOUT_MS`, `DISPATCH_MAX_ATTEMPTS`, `DISPATCH_RECEIPT_RETENTION_DAYS`.
- **Privacy**: provider keys and prompt/completion text are never logged or echoed; provider error bodies are classified, then dropped.

### Stored provider keys
- `/dispatch/keys` CRUD, customer key only; used when a request has no `X-Provider-Key-<Provider>` header (header always wins).
- AES-256-GCM under an HKDF-SHA256 key from `PROVIDER_KEY_ENCRYPTION_KEY`; AAD binds org + provider.
- Without the secret: 503 `stored_keys_unavailable`, dispatch stays header-only.
- Undecryptable rows are listed `needs_reregistration`; receipts record `key_source` (`header` | `stored`), never key material.

### Webhook SSRF guard
- `webhook_guard.py` runs at registration (both alert services' `register()`) and again at every delivery.
- Rules: https only; no IP literals, no private/loopback/link-local/CGNAT/ULA/reserved resolved addresses, no `localhost`/`*.internal`/`*.flycast`.
- Rejection: HTTP 422 / MCP `error_code` `invalid_webhook_url` with a fixed reason category.
- Delivery via `PinnedWebhookTransport`: connects to the checked IP, keeps TLS on the hostname, no redirects, `trust_env=False`.
- Legacy non-compliant rows are skipped at delivery. `WEBHOOK_ALLOW_PRIVATE_TARGETS` is for local dev only.

### Price-alert self-service
- Registration returns a one-time `alert_token` (`lpa_` prefix); only its SHA-256 is stored (`pricing_alerts.token_hash`).
- `POST /pricing/alerts/self/view|delete` and MCP `manage_price_alert` look up by token only; every miss → `invalid_alert_token`.

---

## UI Design System

Pages: `/` (landing), `/chat`, `/calculator`, `/compare`, `/history`, `/trends`, `/widget`, `/ide-compare`, `/billing`, `/admin`, `/mcp-setup`, `/api-docs`, `/whats-new`, `/conversations` (operator key in-page).

| Token | Value |
|---|---|
| `--bg` | `#0f1117` |
| `--surface` / `--surface2` | `#1a1d27` / `#22263a` |
| `--border` | `#2e3347` |
| `--accent` | `#7c6af7` |
| `--accent2` | `#4ecdc4` (`/whats-new`: `#5b4fe0`; billing, conversations, chat omit it) |
| `--text` / `--muted` | `#e2e8f0` / `#8892a4` |
| `--success` / `--warn` / `--danger` | `#48bb78` / `#ed8936` / `#f56565` |
| `--radius` | `10px` |

- Font `'Segoe UI', system-ui, sans-serif`, 14px; sticky `.main-nav` (52px), hamburger at 760px. Admin page is the reference.
- **Nav**: canonical in `scripts/sync_nav.py`; run it to stamp every page, never hand-edit (`tests/test_nav_consistency.py`).
- **Currency control**: one identical `window.LLMPCurrency` block on `/calculator`, `/compare`, landing (`tests/test_currency_ui.py`).

---

## Data Stores

| DB | Tables | Notes |
|---|---|---|
| `pricing_history.db` | `pricing_snapshots`, `routing_savings`, `routing_feedback`, `usage_events`, `pricing_alerts`, `budget_alerts`, `dispatch_receipts` | Receipts are metadata only, purged after 90 days |
| `billing.db` | `customers`, `provider_keys` | Provider keys stored as ciphertext + last-4 fingerprint |
| `CONVERSATION_DB_PATH` (optional) | `conversations` | Unset on Fly → in-memory |

- Both `.db` files are gitignored and live on the Fly volume (`/app/data`).
- Never stored anywhere: prompts/completions, provider keys in plaintext, IPs, request headers.

---

## API Endpoint Map

Auth: **None** = public · **Rate-limited** = anonymous, per-IP limit + body cap · **Service key** = customer or global key · **Customer key** = billing customer only · **Operator key** = global `MCP_API_KEY`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health`, `/health/live`, `/health/ready`, `/health/detailed` | None | Health probes |
| GET | `/pricing`, `/pricing/public`, `/models` | None | Catalogue (filters; `currency`/`country` add local prices) |
| GET | `/pricing/history`, `/pricing/history/export` | None | Price snapshots; CSV/JSON export |
| GET | `/pricing/trends`, `/pricing/subscription-trends` | None | Price-change leaderboards (API / IDE subscriptions) |
| GET | `/data-quality` | None | Confirmed / withheld / unpriced / stale summary |
| GET | `/locale`, `/currency/rates` | None | Resolved locale; vendored rate table |
| GET | `/use-cases`, `/performance`, `/telemetry`, `/cache-stats`, `/rate-limits/tiers` | None | Public stats and recommendations |
| POST | `/cost-estimate`, `/cost-estimate/batch` | Rate-limited | Single / multi-model cost (1–50 models) |
| POST | `/router/recommend`, `/router/recommend/stream`, `/router/feedback` | Service key | Routing recommendation (SSE variant), feedback |
| GET | `/telemetry/savings`, `/telemetry/ide-savings` | Service key | Per-org savings |
| POST/GET | `/usage`, `/usage/batch`, `/usage/summary`, `/usage/session/{id}` | Service key | Record and analyse actual usage |
| POST/GET/DELETE | `/usage/alerts[/{id}]` | Service key | Spend webhooks; `hard_stop` needs a customer key |
| POST | `/pricing/alerts` | Rate-limited | Register price webhook; returns `alert_token` |
| POST | `/pricing/alerts/self/view`, `/pricing/alerts/self/delete` | Rate-limited | Manage one alert by token |
| GET/DELETE | `/pricing/alerts[/{id}]` | Operator key | List / delete all alerts |
| GET | `/pricing/alerts/signing-info` | None | Webhook HMAC info |
| POST | `/agent/chat`, `/agent/chat/stream` | Rate-limited | Agent (blocking / SSE) |
| GET/DELETE | `/agent/conversations[/{id}]` | Operator key | Conversation management |
| POST | `/v1/chat/completions` | None (plain) / Customer key (dispatch) | OpenAI-compatible proxy; `dispatch` executes |
| GET | `/dispatch/providers` | None | Executable providers, dispatchable models |
| GET | `/dispatch/receipts` | Customer key | Org receipts (JSON/CSV) |
| POST/GET/PUT/DELETE | `/dispatch/keys[/{provider}]` | Customer key | Stored provider keys |
| GET | `/mcp` | None | MCP server info |
| POST | `/mcp` | None (key per tool, see MCP table) | MCP JSON-RPC; 1 MB body cap |
| POST | `/billing/signup` | Rate-limited | Free-tier signup |
| GET/POST | `/billing/me`, `/billing/checkout`, `/billing/portal` | Service key | Dashboard, Stripe checkout/portal |
| POST | `/billing/webhook` | Stripe signature | Subscription sync |
| GET | `/admin/stats`, `/admin/rate-limits`, `/admin/customers` | Operator key | Admin data |
| GET | `/api/versions`, `/deployment/metadata`, `/deployment/info` | Service key | Version metadata |
| POST/GET | `/deployment/shutdown`, `/deployment/shutdown/status` | Operator key | Graceful shutdown |
| GET | `/docs`, `/redoc`, `/openapi.json` | None | OpenAPI |
| GET | `/`, UI pages (see Design System) | None | Static UIs |

---

## CI/CD Pipeline

```
PR to develop/master, push to master  (ci-cd.yml)
  test · lint (flake8) · osv_scan · security (bandit) · secret_scan (gitleaks)
    └─ all pass on a master push → deploy_fly → /health version must match src/__init__.py
         └─ confirmed → tag vX.Y.Z + GitHub Release (notes = that version's What's New entry,
            via scripts/release_notes_from_whats_new.py; idempotent, skipped on an unconfirmed deploy)
push to master → sync-develop.yml opens a ci/sync-develop-* PR if develop lags
push to master touching npm/llm-pricing-mcp/** → npm-publish.yml
push to master touching server.json            → mcp-registry-publish.yml
weekly → price-audit.yml (human-merged PRs)
```

| Weekly job (`price-audit.yml`) | Does |
|---|---|
| drift audit | `check_price_drift` report; issue when prices are withheld |
| snapshot refresh | new registry snapshot, gated by `validate_price_snapshot.py` (CI doesn't run on bot PRs) |
| `reconfirm_stale_prices.py` | re-dates curated prices the registry still matches |
| `retire_models.py` | deletes models ≥30 days past registry deprecation |
| `pickup_registry_models.py` | adds/re-syncs registry-sourced models; never edits `*_pricing.py` |
| `refresh_exchange_rates.py` | refreshes `exchange_rates.json`; any failed check leaves it untouched |

### npm publishing (`npm-publish.yml`)
- Trusted Publishing via OIDC (`id-token: write`); no token secret exists.
- The workflow **filename** is the npmjs.com trust key; renaming it breaks publishing.
- `npm/llm-pricing-mcp` is versioned on its own; publish skips if the version already exists.
- npm rejects OIDC from `issue_comment` / `pull_request_target`; never publish with a 2FA recovery code (72 h hold).
- Pinned by `tests/test_npm_publish_workflow.py`.

### MCP registry (`mcp-registry-publish.yml`)
- Publishes `server.json` to registry.modelcontextprotocol.io via `mcp-publisher login github-oidc`.
- `name` must keep the `io.github.skakumanu/` prefix; `description` ≤ 100 chars.
- Bridge release = bump `npm/llm-pricing-mcp/package.json` and both `server.json` versions in one PR; the workflow waits ≤10 min for npm.
- Pinned by `tests/test_mcp_registry_publish_workflow.py`.

### Node version floor
- Publish workflows pin exact Node releases (`check-latest: true`); the bridge's `engines.node` sets the patched floor.
- `tests/test_node_version_pins.py` blocks lowering it. The Fly image has no Node.

---

## When to Update This Document

Per `CLAUDE.md`: a new/removed service, endpoint group or auth change, DB table, UI page, external dependency, CI/CD job, deployment target, or design token. Bug fixes, style and test changes don't need an update.
