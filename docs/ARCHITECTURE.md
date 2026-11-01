# Architecture — LLM Pricing MCP Server

**Version**: v1.87.0 | **Last updated**: 2026-11-01

Canonical internals reference. Release history lives in `static/whats-new/`, not here.

---

## System Overview

FastAPI service that aggregates LLM pricing from 30 providers (600+ models) and serves it over REST and MCP, with a ReAct pricing agent, a grounded support agent, a dispatch gateway, self-serve billing and 21 browser pages.

- **Deploy**: Fly.io app `llm-pricing-api` (shared-cpu-1x, 512 MB, volume at `/app/data`)
- **CI/CD**: test → lint → OSV → bandit → gitleaks → Fly deploy (see [CI/CD](#cicd-pipeline))
- **Price integrity**: drifted prices are withheld per request; weekly `price-audit.yml` PRs re-date, retire and pick up registry models
- **Dispatch gateway**: `POST /v1/chat/completions` + `dispatch` object executes the best eligible model on the caller's own provider key
- **Local currency**: 13 currencies, additive `local_*` fields; pricing data itself stays USD
- **Dev tooling (not deployed)**: `/release-feature` phase-agent pipeline (see `CLAUDE.md`); `scripts/audit_repo.py` customer-run cost auditor
- **Orchestrator core (v1.83.0)**: operator-only `/orchestrator/*` — idempotent intake of GitHub events / schedules / owner requests / payment events, finite workflows with human checkpoints, run log, approval queue, budgets; executors are `orchestrator-intake.yml`, the support agent and the owner on `/admin`; no LLM *step* runs inside a workflow
- **Support agent (v1.84.0)**: `POST /support/chat` + `support_chat` MCP tool — identity-aware (`_resolve_mcp_caller`), grounded in `docs/FAQ.md` + the tier catalogue (`src/services/tiers.py`) + the caller's own account, escalates to an `owner_handoff` checkpoint in the orchestrator, spends under per-caller daily caps and the orchestrator ledger, and never changes billing

---

## Layer Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│ Clients: browser UIs · MCP clients · npx bridge · REST · OpenAI SDKs │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ HTTPS / STDIO
┌───────────────────────────────▼──────────────────────────────────────┐
│ Presentation — src/main.py + mcp/                                    │
│  REST API          MCP: 32 tools, STDIO + POST /mcp    21 UI pages   │
│  security_middleware: operator / customer / global key, rate limit,  │
│  body cap · per-bucket sliding window (src/services/rate_limiter.py) │
└───────┬───────────────────────┬──────────────────────┬───────────────┘
┌───────▼──────────────┐ ┌──────▼─────────┐ ┌──────────▼──────────────┐
│ Pricing & usage      │ │ Agents / RAG   │ │ Billing · Orchestrator  │
│ PricingAggregator    │ │ ReAct loop     │ │ BillingService, tiers   │
│ 30 provider impls    │ │ PricingAgent   │ │ free signup → API key   │
│ PriceOracle, Router  │ │ SupportAgent   │ │ Stripe (optional)       │
│ Usage/Budget/Alerts  │ │ TF-IDF RAG     │ │ ProviderKeyStore        │
│ WebhookGuard         │ │ conv. memory   │ │ OrchestratorService     │
│ Currency · Dispatch  │ │ SupportBudget  │ │ (runs, approvals, budget)│
└───────┬──────────────┘ └──────┬─────────┘ └──────────┬──────────────┘
┌───────▼───────────────────────▼──────────────────────▼───────────────┐
│ Data: pricing_history · billing · orchestrator · conversations (.db) │
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
  models/                   pricing.py, billing.py, deployment.py, dispatch.py (DispatchPolicy, ChatCompletionsRequest),
                            orchestrator.py (event / owner-run / approval / result bodies)
  services/
    pricing_aggregator.py   PROVIDER_SERVICES list, async fan-out + TTL cache, get_pricing_aggregator() singleton
    legal_pages.py          live gate for the legal pages: placeholder scan of docs/legal + LEGAL_PAGES_LIVE (v1.86.0)
    base_provider.py        BasePricingProvider: build_metrics, registry-entry merge, live model sync
    *_pricing.py            one service per provider (30)
    price_oracle.py         reference registry: gap-fill, drift + retirement withholding (24h TTL + snapshot)
    data_quality.py         accuracy summary for /data-quality + get_data_quality
    cost_calculator.py      compute_token_cost(), shared by REST and MCP cost paths
    router.py               routing engine; rank_models() / hard_filter_reason() reused by dispatch and the SSE router;
                            restricts to the caller's client menu before ranking (v1.85.0)
    client_models.py        curated, dated table of AI coding clients and the models each can run; resolve_client(),
                            eligible_models() (v1.85.0)
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
    billing_service.py      customers table (+ billing_status / past_due_since / first_paid_at; service keys stored as
                            SHA-256 hash + 8-char prefix, in-place migration), stripe_events dedup, key rotation,
                            deleted_accounts + billing_meta (suppression marker), cancel_stripe_subscription(),
                            effective_tier(), within_refund_window(); Stripe sync
    account_data.py         self-service export + deletion of one org's data across every store (v1.87.0)
    tiers.py                single source of tier copy: prices, rate limits, support allowance, escalation
                            priority, entitlement strings, checkout/portal flags (v1.84.0)
    support_budget.py       per-caller daily LLM caps for the support agent + support_action() audit helper (v1.84.0)
    orchestrator_workflows.py  finite workflow definitions (validated at import), route(), redaction, templates
    orchestrator_store.py   orchestrator.db: runs, steps, events, approvals, append-only actions, spend, settings
    orchestrator_service.py the run engine: intake/dedup, step loop, checkpoints, executors, budgets, pause
    ide_pricing.py, telemetry.py, geolocation.py, data_fetcher.py, data_sources.py, deployment.py
mcp/
  server.py                 STDIO transport (always anonymous; transport='stdio')
  caller.py                 McpCaller + access levels PUBLIC / ORG_SCOPED / OPERATOR_ONLY / CUSTOMER_ONLY / IDENTITY_AWARE
  sessions.py               in-memory Mcp-Session-Id -> clientInfo.name store for hosted client inference (v1.85.0)
  tools/                    one module per tool + tool_manager.py + _locale.py + _client.py (support_chat.py is identity-aware)
  schema_generator.py, schemas/   generated JSON-RPC schemas
agent/                      pricing_agent.py, support_agent.py, support_tools.py, react_loop.py, llm_backend.py,
                            conversation.py, tools.py
rag/                        document_loader, chunker, in-memory TF-IDF vector_store, pipeline (optional doc allow-list)
static/                     21 UI pages (see Design System); landing/ is served at /; faq/ is generated from docs/FAQ.md;
                            terms/ privacy/ refunds/ dpa/ subprocessors/ security/ are generated from docs/legal/*.md
data/
  model_prices_snapshot.json  vendored registry snapshot (weekly refresh PR)
  exchange_rates.json         vendored USD rate table (weekly refresh PR)
  registry_models/<slug>.json registry-sourced models per provider (written only by pickup script)
scripts/                    weekly audit scripts, validate_price_snapshot.py, sync_nav.py, sync_faq.py,
                            sync_legal.py (docs/legal → legal pages, --check, --placeholders),
                            check_replication.py (every *_db_path DB is on the volume and in litestream.yml),
                            launch_checklist.py (parser/validator for docs/LAUNCH_CHECKLIST.md),
                            quick_validate.py, audit_repo.py, promote_branch_content.sh,
                            release_notes_from_whats_new.py (deploy job → GitHub Release notes)
npm/llm-pricing-mcp/        zero-dependency STDIO↔HTTP bridge published to npm
server.json                 MCP registry listing (remote + npm package)
.github/workflows/          ci-cd, sync-develop, price-audit (+ report-run), npm-publish, mcp-registry-publish,
                            orchestrator-intake (GitHub executor of the orchestrator core),
                            codeql (python code scanning; PR + weekly); .github/dependabot.yml (pip + github-actions)
tests/                      pytest suite (count: README "passing tests" line)
fly.toml, Dockerfile        Fly config; image copies src/ static/ agent/ rag/ mcp/ docs/ data/ (multi-stage: adds the Litestream binary)
entrypoint.sh, litestream.yml  container entrypoint (plain uvicorn, or under `litestream replicate` when LITESTREAM_REPLICA_URL is set;
                            restores missing DBs first) and the replicated database list (billing, pricing_history, orchestrator)
docs/legal/                 Markdown sources of /terms /privacy /refunds /dpa /subprocessors /security; docs/LAUNCH_CHECKLIST.md
SECURITY.md                 root pointer to /security for the GitHub security tab
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
| 1. Operator-only request | GET/HEAD `/pricing/alerts`, `/agent/conversations`; DELETE `/pricing/alerts/{id}`, `/agent/conversations/{id}`; any method under `/orchestrator/` and `/admin/customers/` (`_OPERATOR_ONLY_PREFIXES`; the latter is `/admin/customers/{id}/key/rotate`) | Decided first so the `/pricing` prefix bypass can't expose them |
| 2. Full bypass | OPTIONS, static UIs (incl. `/faq`), the legal pages (`_LEGAL_PAGE_PATHS`: `/terms`, `/privacy`, `/refunds`, `/dpa`, `/subprocessors`, `/security`), `/pricing*`, `/mcp`, `_unauthenticated_paths` (incl. `/billing/config`) | No auth, no limiter; `_public_body_capped_paths` (`/v1/chat/completions`, `/mcp`) still body-capped |
| 3. Operator key | step 1 + `_sensitive_paths` (`/deployment/shutdown*`, `/admin/customers`, `/admin/stats`, `/admin/rate-limits`) | Global `MCP_API_KEY` in `x-api-key` only (byte compare); 503 if unset. The three orchestrator executor routes (POST `/orchestrator/events`, GET `…/runs/{id}/actions`, POST `…/actions/{aid}/result`) also accept `ORCHESTRATOR_INTAKE_KEY`; it unlocks nothing else, but on those routes it carries the executor's owner assertion for GitHub events, so a `pull_request.opened` continuation is additionally bound to the repository the run was created from (`refused:repo_mismatch` otherwise) |
| 4. Anonymous, rate-limited | `/agent/chat*`, `/support/chat`, `/support/escalations/status`, `/billing/signup`, POST `/pricing/alerts`, `/pricing/alerts/self/*`, `/cost-estimate*` | Per-IP limit + body cap; a customer key on `/cost-estimate*` and `/support/*` gets its tier limit (the support handlers resolve identity themselves via `_resolve_mcp_caller`) |
| 5. Service key | everything else (incl. `/billing/me`, `/billing/key/rotate`, `/billing/account/export`, `/billing/account/delete`) | Billing customer key (tier from DB) or global key; 401 otherwise |

- Rate limit: 60 s sliding window per bucket (customer id, else `ip:tier`); free / pro / enterprise = 30 / 120 / 600 req/min. The tier is the **effective** tier (`billing_service.effective_tier`): a `past_due` customer past `BILLING_PAST_DUE_GRACE_DAYS` is served as free.
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
- **STDIO** (`mcp/server.py`) and **HTTP** (`POST /mcp`): JSON-RPC 2.0, protocol `2024-11-05`, 32 tools.
- **npx bridge** (`npm/llm-pricing-mcp`): forwards stdin lines to `POST /mcp`; optional `LLM_PRICING_API_KEY` → `x-api-key`. No tool logic of its own.
- **Client inference (v1.85.0):** hosted `initialize` returns an `Mcp-Session-Id` header and remembers only `clientInfo.name` (in-memory, bounded, 24 h TTL, lost on restart; an unknown or missing session is never rejected). A `tools/call` that echoes it lets `recommend_model` / `optimize_workload` / `compare_costs` infer the client when `client` is omitted; STDIO keeps the name for the process. Precedence: explicit `client` > `ide_context` > handshake > none.
- `ToolManager.execute_tool(name, args, caller)`; only HTTP `/mcp` resolves a caller, and only for non-PUBLIC tools. The resolved customer caller carries the *effective* tier.

| Access level | Tools | Caller needed |
|---|---|---|
| PUBLIC | the other 19 | none |
| IDENTITY_AWARE (v1.84.0) | `support_chat` | none required; `authorize` never refuses, the tool receives the caller and answers about that customer's own account when it is one. STDIO refuses with `support_requires_hosted_mcp` |
| ORG_SCOPED | `record_usage`, `import_session_usage`, `get_usage_summary`, `analyze_session_usage`, `register_budget_alert`, `list_budget_alerts`, `delete_budget_alert` | customer key (own org only) or operator |
| OPERATOR_ONLY | `list_price_alerts`, `delete_price_alert`, `list_conversations`, `delete_conversation` | global `MCP_API_KEY` |
| CUSTOMER_ONLY | `dispatch` | customer key; STDIO refuses with `dispatch_requires_hosted_mcp` |

- Refusals are tool results (`success: false`): `customer_key_required`, `org_mismatch`, `operator_key_required`, `dispatch_requires_api_key`.

### Agents (ReAct)
```
message → react_loop (think → tool → observe; system_prompt per agent) → llm_backend (Anthropic | OpenAI, AGENT_LLM_PROVIDER;
        LLMResponse.usage summed into LoopResult.usage) → conversation store (SQLite if CONVERSATION_DB_PATH, else memory)
```
- **PricingAgent** (`/agent/chat*`, `ask_agent`): anonymous, pricing-scoped, SSE streaming. `agent/tools.py` binds 27 of the 32 MCP tools; excludes `ask_agent`, `support_chat`, `get_telemetry`, `get_cache_stats`, `dispatch`. Org-scoped and operator-only tools refuse at runtime. Stored turns pass through `redact_alert_tokens()`.
- **SupportAgent** (v1.84.0, `agent/support_agent.py`; `POST /support/chat`, `support_chat`): sibling sharing the LLM backend and conversation store, own `SUPPORT_SYSTEM_PROMPT`, RAG over `SUPPORT_DOCS` (`FAQ.md`, `MCP_QUICK_START.md`, `CLAUDE_INTEGRATION.md`) only. Turn pipeline: server-minted `sup-<uuid4>` conversation id (400 if malformed) → `_sanitize_input` → `redact_support_text` (keys, Stripe ids, alert tokens, 32+ char tokens → `[redacted]`; emails kept in the operator-only store) → `is_instruction_like` flag (advisory) → `support_enabled` → orchestrator store present (else fallback `ledger_unavailable`) → per-caller daily cap (`support_budget.py`) + `OrchestratorService.budget_gate('support_triage')` (else fallback `spend_cap`) → ReAct with per-request tools (`agent/support_tools.py`: `faq_search`, `get_plan_catalog`, public `get_all_pricing` / `estimate_cost` / `get_data_quality`, `get_my_account`, `escalate_to_owner`, `check_escalation`; the caller is baked in, no tool takes an org/customer/email argument) → cost from usage × catalogue price → `record_agent_spend` → redacted reply stored → `support.answer` action record. The deterministic fallback still escalates an explicit "talk to a human" / security report.
- Identity: HTTP `_resolve_mcp_caller(request)`; MCP via ToolManager; the agent never reads `request.state`.

### Billing (Stripe optional)
- **Legal gate (v1.86.0):** `services/legal_pages.is_live(settings)` = `LEGAL_PAGES_LIVE` **and** no `[PLACEHOLDER]` left in `docs/legal/*.md` (fail closed; WARNING logged when the flag is on but placeholders remain). `checkout_enabled` = Stripe configured AND legal live; `portal_enabled` = Stripe configured only. A gated page (all but `/security`) gets a server-side "Draft, not yet in force" notice in place of `<!-- legal:notice -->` while not live. `tests/test_legal_pages.py` fails the build if the flag is on by default while a placeholder remains.
- `GET /billing/config` (public): `tiers.catalog_payload(settings)` — `checkout_enabled` (Stripe set AND legal pages live), `portal_enabled` (= Stripe secret set), `legal_pages_live`, `checkout_disabled_reason` (`stripe_not_configured` | `legal_pages_not_live` | null), per-tier `checkout_available` (price id set), `past_due_grace_days`, `tiers`; the agent's `get_plan_catalog` returns the same payload. `/billing`, the landing page and the agent render these exact strings; `tests/test_support_faq.py` pins them to each other and to `docs/FAQ.md`.
- `POST /billing/signup` → new customer row → service key shown once (409 for an existing email).
- `POST /billing/checkout` → Stripe session; `GET /billing/portal` → Stripe customer portal (cancel, card, invoices). Checkout returns 503 without `STRIPE_SECRET_KEY` or while the legal pages are not live, and the portal without Stripe; both with a "not enabled on this server" detail; signup, the dashboard and support still work.
- `POST /billing/key/rotate` (current key) and `POST /admin/customers/{id}/key/rotate` (operator) → `rotate_api_key()`; the new key is shown once, the old one stops at once; action `billing.key_rotate` (actor `customer` | `owner`).
- **Account data rights (v1.87.0, `services/account_data.py`):** `GET /billing/account/export` returns one JSON document (account, provider-key metadata only, usage events, dispatch receipts, budget alerts, routing savings + feedback, support conversations; per-table cap with a `truncated` flag). `POST /billing/account/delete` with `{confirm: "DELETE", org_id}` (both must match the caller, else 400 and nothing changes) runs in a fixed order: Stripe subscription cancelled **first** (any failure other than "already cancelled" is 502, subscription with Stripe unconfigured is 503, nothing deleted), then pricing_history.db rows, conversations and provider keys, then the `customers` row and a `deleted_accounts` suppression marker (timestamp + HMAC-SHA256 of the lower-cased email with the per-database salt in `billing_meta`) in one billing.db transaction, so the service key dies last and a mid-way failure is retryable. Both need a customer key (anonymous and operator-only are 401). Inside `BILLING_REFUND_WINDOW_DAYS` of `first_paid_at` (unknown counts as inside) it opens a `support_escalation` run via the event `account.deletion_refund_review` (trigger class `customer_message`, trusted only from `actor_kind` `account_service`) and returns its reference; no money moves. Audit actions `billing.account_export` / `billing.account_delete` target `org:<8>`, never the email. Re-signup after deletion is allowed.
- **Service keys are hashed (v1.87.0):** `customers` stores `api_key_hash` (SHA-256 hex, unique) and `api_key_prefix` (8 chars); the legacy `api_key` column holds only a `hashed:<id>` placeholder (its NOT NULL constraint cannot be dropped in place). `get_customer_by_api_key` hashes the presented key. `initialize()` runs `migrate_plaintext_keys()` (idempotent, logs counts). Creation and rotation return the plaintext once; `/admin/customers` and `/billing/me` show the prefix.
- `/billing/me` adds `effective_tier`, `billing_status`, `past_due_since`, `rate_limit_per_minute`.
- `POST /billing/webhook` → verify signature → `record_stripe_event(event.id)` (duplicate → `{ok, duplicate: true}`, nothing else) → state machine → one `billing.<event type>` action record (outcome `applied… | ignored | duplicate | escalated…`). If applying the event raises, the id is forgotten again (`forget_stripe_event`) and the handler returns 500 so Stripe's retry is processed rather than treated as a duplicate. A `charge.dispute.created` object has no `customer`; the handler reads it from the disputed Charge (`stripe.Charge.retrieve`):

| Stripe event | Tier | `billing_status` | Escalation (`payment_event` → `support_escalation`) |
|---|---|---|---|
| `checkout.session.completed` | from metadata | `active` | — |
| `customer.subscription.updated` | from price map | `past_due` if `status` is past_due/unpaid, else `active` | — |
| `customer.subscription.deleted` | `free` | `active` | — |
| `invoice.paid` | unchanged | `active` (clears `past_due_since`) | — |
| `invoice.payment_failed` | unchanged (effective → free after `BILLING_PAST_DUE_GRACE_DAYS`) | `past_due` | `payment_failure` |
| `charge.refunded` | unchanged | unchanged | `refund` |
| `charge.dispute.created` (customer via the charge) | unchanged | unchanged | `billing_dispute` |
| anything else | — | — | recorded `ignored` |

- Stored tiers change only through Stripe events; the support agent and the owner queue never write a tier.

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

### Orchestrator core (v1.83.0)
```
trigger (GitHub event · schedule · owner request · support escalation · Stripe payment event)
  ──► POST /orchestrator/events | POST /orchestrator/runs | in-process intake_event() from the support agent / webhook
  dedup_key UNIQUE (INSERT OR IGNORE → SELECT): duplicate attaches, no new side effect
  route(trigger class, event type, owner signals) → workflow   ← titles/bodies never reach routing
  run orc_xxxxxxxxxxxx · steps from the definition (≤ 20; agents cannot add/reorder/skip)
    deterministic → handler (3 attempts, 30 s)   checkpoint → approval row, run waits (never times out)
    external_action → approval bound to (run, step, artifact sha256, recipient) → executor fetches
       GET …/actions?executor=github → acts (at-most-once via <!-- orchestrator:run:step --> marker)
       → POST …/result {artifact_hash, provenance_echo}  (422 without echo · 409 + park on mismatch)
    llm (none in v1) → check_budget (per run · per workflow · global 150/mo, 80 % soft alert) → spend ledger
  every hand-off carries a provenance envelope; the next step refuses to start without one
  failure / block / deadline / max steps → status parked (reason + prior outputs); owner resumes or closes
  result ∈ {done, awaiting_owner, failed, deliberately_not_done}; public_summary is a template
```
- **Workflows (code, `orchestrator_workflows.py`)**: `github_accepted_bug` (intake → annotate → `sdlc_run` checkpoint → one status comment), `release_notes_draft` and `support_triage` (specialist missing → forced `dry_run`, owner checkpoint "do manually or skip"; `support_triage` is also the ledger workflow id for live support-chat spend), `support_escalation` (v1.84.0: intake → mandatory `owner_handoff` checkpoint; trigger classes `customer_message` with event `support.escalation` (from the support agent) or `account.deletion_refund_review` (v1.87.0, from the account service only), `payment_event` with `charge.refunded` / `charge.dispute.created` / `invoice.payment_failed`, `owner_request`), `weekly_price_audit` (parks on any failed job). `validate_registry()` runs at import and rejects `FORBIDDEN_ACTIONS` (`merge_pr`, `refund`, `charge`, `send_email`, `publish`, `push_branch`, `grant_permission`); the only executor action kind is `github_issue_comment_upsert`.
- **Support escalations (v1.84.0)**: dedup `msg:<conversation_id>` (one run per support conversation; a second attempt attaches and returns the same reference) or `stripe:<event id>`; actor kinds `support_agent` / `stripe`; structured inputs `category`, `priority`, `tier`, `identified`, `customer_ref` (org prefix), `conversation_ref`, `suggested_reply` (redacted, ≤ 500). The `owner_handoff` artifact is `{kind, label, priority, body (redacted ≤ 500), customer {identified, org_ref}, conversation_ref, instruction_like, suggested_reply}`; decisions are `done_manually` (with a reply note the customer can read) or `reject`, never `approve`; `approval_queue()` adds `priority` and sorts enterprise > pro > standard, then newest. `public_escalation_view(run_id)` → `{reference, status, owner_reply}` is all a customer ever sees (`POST /support/escalations/status`, `check_escalation`). No email, key or Stripe id reaches an artifact, event or action record.
- **Support spend**: `record_agent_spend()` writes every support LLM call to `orch_spend` (`run_id conv:<id>`, workflow `support_triage`, default `budget.workflow_monthly_usd.support_triage` = 40); `budget_gate(workflow_id)` (factored out of `check_budget`) is asked before every call, so the global 150 USD/month ceiling and the /admin budget bars cover support. Action records written outside a run: `support.answer` (outcome `answered | fallback_cap | fallback_disabled | error`), `support.cap_hit`, `support.account_read`, `support.escalation`, `support.escalation_status`, `billing.key_rotate`, `billing.<stripe event>`.
- **SDLC wrap**: the checkpoint shows `/release-feature Fix issue #N (orchestrator run orc_…)`; the skill stamps `Orchestrator-Run:` into the PR footer; `pull_request: opened` (same repo, owner) completes the checkpoint and approves the templated status comment (`decided_by: github_pr_marker`); the PR URL must be in the repository recorded on the run and match the event's PR number, so a leaked intake key cannot attach a foreign link. Merging stays the owner's.
- **Concurrency**: approval decisions, executor results, resume/close and PR continuations run under the service's one `asyncio.Lock` and continue the run inside it, so two identical concurrent requests apply once (the second gets 409 / `already_recorded`). Time spent paused or waiting on the owner is credited back to the 24 h executing deadline; an owner resume after a `deadline` park grants a fresh window.
- **Modes**: `standard` (mandatory kinds gate: merge, money, outbound, publish, audit_flag, missing_specialist), `supervised` (default; every external action gates), `dry_run` (actions rendered, never executed). A mode can only add supervision; `PUT /orchestrator/settings` answers 422 `specialist_missing` when a non-dry_run mode is set on a missing-specialist workflow.
- **Owner control surface** (`/admin` Orchestrator block): approval queue (approve / reject / edit-then-approve / done manually / skip; `owner_handoff` cards show priority, category, org prefix, conversation link and a "Reply to customer (they can read this)" textarea whose note becomes `owner_reply`), parked runs (resume / close), run log + CSV, global pause (parks in-flight runs at the next step boundary; `/actions` returns `[]`; release does not resume parked runs), per-workflow mode, budget bars. The Billing Customers table shows `billing_status` and a per-row "Rotate key". `update_settings()` refuses and logs any call made from inside a run.
- **Untrusted input**: `summarize_untrusted()` redacts `sk-`/`pk-`/`whsec_`/`AKIA`/Stripe-id/email/`lpa_` tokens, truncates (title 200, body 500) and sets `instruction_like` (advisory, logged, never routes). `redact_untrusted_text(keep_emails=True)` is used only for the operator-only support conversation store. Owner identity for GitHub events is asserted by the Actions workflow from `sender.login == repository_owner`; `payment_event` types are set by the webhook handler, never by customer text.
- **Notifications v1**: admin counters, the GitHub status comment, Actions step summary, WARNING logs. Email is a v1.1 follow-up; approvals are never accepted through a notification channel.

---

## UI Design System

Pages: `/` (landing), `/chat` (Pricing | Support toggle; the optional service key lives in `sessionStorage` only), `/calculator`, `/compare`, `/history`, `/trends`, `/widget`, `/ide-compare`, `/billing`, `/faq` (generated by `scripts/sync_faq.py` from `docs/FAQ.md`), `/terms`, `/privacy`, `/refunds`, `/dpa`, `/subprocessors`, `/security` (generated by `scripts/sync_legal.py` from `docs/legal/*.md`, linked from the landing footer, `/billing` and `/faq`; reachable from the footer, not the eight-item top nav), `/admin`, `/mcp-setup`, `/api-docs`, `/whats-new`, `/conversations` (operator key in-page).

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
| `billing.db` | `customers` (+ `billing_status`, `past_due_since` since v1.84.0; `api_key_hash`, `api_key_prefix`, `first_paid_at` since v1.87.0), `stripe_events` (event-id dedup), `provider_keys`, `deleted_accounts` (suppression markers), `billing_meta` (per-database salt) | Provider keys stored as ciphertext + last-4 fingerprint; new columns added by a `PRAGMA table_info`-guarded `ALTER TABLE` |
| `conversations.db` (`CONVERSATION_DB_PATH`) | `conversations` (+ nullable `org_id`, index on `(org_id, updated_at)` since v1.87.0) | Set to `/app/data/conversations.db` on Fly and replicated (unset locally → in-memory). Support turns (`sup-…` ids) are stored with secrets redacted; conversations of identified customers are attributed to their org (never detached by a later anonymous turn). Purged after `CONVERSATION_RETENTION_DAYS` (90; `<= 0` disables) at startup and after saves; erased with the account |
| `orchestrator.db` (`ORCHESTRATOR_DB_PATH`) | `orch_runs`, `orch_steps`, `orch_events`, `orch_approvals`, `orch_actions`, `orch_spend`, `orch_settings` | Rows purged after `ORCHESTRATOR_RUN_RETENTION_DAYS` (365); redacted I/O summaries nulled after `ORCHESTRATOR_IO_RETENTION_DAYS` (90); `orch_actions` is append-only |

- All `.db` files are gitignored and live on the Fly volume (`/app/data`). `billing.db`, `pricing_history.db`, `orchestrator.db` and `conversations.db` are replicated continuously to S3-compatible storage by Litestream (v1.86.0; `litestream.yml`, restore in `docs/DEPLOYMENT.md`); `scripts/check_replication.py` fails the build if a new `*_db_path` database is not on the volume and in `litestream.yml`. The mount hides the image's
  `data/` tree, so the registry snapshot, registry-sourced model files and exchange-rate table are read
  from the Dockerfile's copy under `/app/vendored` (`PRICE_SNAPSHOT_PATH`, `REGISTRY_MODELS_DIR`,
  `EXCHANGE_RATES_PATH`; startup logs the registry-sourced model count, v1.84.1).
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
| POST | `/router/recommend`, `/router/recommend/stream`, `/router/feedback` | Service key | Routing recommendation (SSE variant), feedback; optional `client` restricts candidates before ranking |
| GET | `/telemetry/savings`, `/telemetry/ide-savings` | Service key | Per-org savings |
| POST/GET | `/usage`, `/usage/batch`, `/usage/summary`, `/usage/session/{id}` | Service key | Record and analyse actual usage |
| POST/GET/DELETE | `/usage/alerts[/{id}]` | Service key | Spend webhooks; `hard_stop` needs a customer key |
| POST | `/pricing/alerts` | Rate-limited | Register price webhook; returns `alert_token` |
| POST | `/pricing/alerts/self/view`, `/pricing/alerts/self/delete` | Rate-limited | Manage one alert by token |
| GET/DELETE | `/pricing/alerts[/{id}]` | Operator key | List / delete all alerts |
| GET | `/pricing/alerts/signing-info` | None | Webhook HMAC info |
| POST | `/agent/chat`, `/agent/chat/stream` | Rate-limited | Agent (blocking / SSE) |
| GET/DELETE | `/agent/conversations[/{id}]` | Operator key | Conversation management |
| POST | `/support/chat` | Rate-limited (customer key → tier bucket + identity) | Support assistant; `escalation.reference`, `fallback` on cap |
| POST | `/support/escalations/status` | Rate-limited | `{reference}` in the body → `{reference, status, owner_reply}`; constant 404 |
| POST | `/v1/chat/completions` | None (plain) / Customer key (dispatch) | OpenAI-compatible proxy; `dispatch` executes |
| GET | `/dispatch/providers` | None | Executable providers, dispatchable models |
| GET | `/dispatch/receipts` | Customer key | Org receipts (JSON/CSV) |
| POST/GET/PUT/DELETE | `/dispatch/keys[/{provider}]` | Customer key | Stored provider keys |
| GET | `/mcp` | None | MCP server info |
| POST | `/mcp` | None (key per tool, see MCP table) | MCP JSON-RPC; 1 MB body cap |
| GET | `/billing/config` | None | Tier catalogue, `checkout_enabled` / `portal_enabled`, `legal_pages_live`, `checkout_disabled_reason`, grace days |
| POST | `/billing/signup` | Rate-limited | Free-tier signup |
| GET/POST | `/billing/me`, `/billing/checkout`, `/billing/portal` | Service key | Dashboard (+ `effective_tier`, `billing_status`), Stripe checkout/portal (503 without Stripe) |
| POST | `/billing/key/rotate` | Customer key | Rotate own service key (shown once) |
| GET | `/billing/account/export` | Customer key | Download everything held about the caller's org (JSON; never keys) |
| POST | `/billing/account/delete` | Customer key | Irreversibly delete the caller's account and data; body `{confirm: "DELETE", org_id}`; ends any Stripe subscription first |
| POST | `/billing/webhook` | Stripe signature | Subscription + payment-event sync (see Billing table); duplicates ignored |
| GET | `/admin/stats`, `/admin/rate-limits`, `/admin/customers` | Operator key | Admin data |
| POST | `/admin/customers/{id}/key/rotate` | Operator key | Owner-side key rotation (shown once, audited) |
| POST | `/orchestrator/events` | Operator or intake key | Idempotent intake of one inbound item (five trigger classes). `customer_message`/`support.escalation` and `payment_event` are refused here (`refused:untrusted_actor`): only the in-process support agent and Stripe webhook handler open escalations |
| GET/POST | `/orchestrator/runs/{id}/actions`, `/orchestrator/runs/{id}/actions/{aid}/result` | Operator or intake key | Executor fetch / result (provenance echo + artifact hash) |
| POST/GET | `/orchestrator/runs[/{id}]`, `/orchestrator/runs/{id}/resume`, `/orchestrator/runs/{id}/close` | Operator key | Owner request; run log (JSON/CSV); run detail; dead-letter controls |
| GET/POST | `/orchestrator/approvals[/{id}]` | Operator key | Approval queue; approve / reject / edit_approve / done_manually / skip |
| GET | `/orchestrator/parked`, `/orchestrator/budget`, `/orchestrator/workflows`, `/orchestrator/actions` | Operator key | Parked runs, budget view, workflow catalogue, action records (JSON/CSV) |
| GET/PUT | `/orchestrator/settings` | Operator key | Global pause, mode defaults, budgets (422 `specialist_missing`) |
| GET | `/api/versions`, `/deployment/metadata`, `/deployment/info` | Service key | Version metadata |
| POST/GET | `/deployment/shutdown`, `/deployment/shutdown/status` | Operator key | Graceful shutdown |
| GET | `/docs`, `/redoc`, `/openapi.json` | None | OpenAPI |
| GET | `/terms`, `/privacy`, `/refunds`, `/dpa`, `/subprocessors`, `/security` | None | Legal pages and security statement (generated from `docs/legal/`; draft notice until live, `/security` never) |
| GET | `/`, UI pages (see Design System) | None | Static UIs |

---

## CI/CD Pipeline

```
PR to develop/master, push to master  (ci-cd.yml)
  test · lint (flake8) · osv_scan · security (bandit) · secret_scan (gitleaks)   (the five deploy gates)
PR to master/develop + weekly → codeql.yml (python code scanning; additional, not a deploy gate)
weekly → .github/dependabot.yml opens grouped minor+patch PRs for pip and github-actions against develop (never auto-merged)
    └─ all pass on a master push → deploy_fly → /health version must match src/__init__.py
         └─ confirmed → tag vX.Y.Z + GitHub Release (notes = that version's What's New entry,
            via scripts/release_notes_from_whats_new.py; idempotent, skipped on an unconfirmed deploy)
push to master → sync-develop.yml opens a ci/sync-develop-* PR if develop lags
push to master touching npm/llm-pricing-mcp/** → npm-publish.yml
push to master touching server.json            → mcp-registry-publish.yml
weekly → price-audit.yml (human-merged PRs) → report-run posts one schedule event to the orchestrator
issues labeled · PR opened (same repo, develop) · push master · workflow_dispatch(run_id)
  → orchestrator-intake.yml: POST /orchestrator/events → GET …/actions?executor=github → comment upsert → POST …/result
```

`orchestrator-intake.yml` runs with `contents: read`; only its `execute` job has `issues: write`. It uses `GITHUB_TOKEN` (no PAT), picks fields with `jq` from the event payload (never `${{ github.event.* }}` text in a script), guards PRs to the same repo, and exits 0 with a step-summary line when `ORCHESTRATOR_INTAKE_KEY` is absent. Pinned by `tests/test_orchestrator_intake_workflow.py`.

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
