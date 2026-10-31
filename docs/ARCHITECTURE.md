# Architecture — LLM Pricing MCP Server

**Version**: v1.82.1 | **Last updated**: 2026-10-31

---

## System Overview

A production FastAPI service that aggregates real-time LLM pricing data from 30 providers (600+ models), exposes it via REST API and MCP protocol, and layers on a ReAct agent, self-serve SaaS billing, and a suite of browser UIs.

- **Primary deployment**: Fly.io (`llm-pricing-api.fly.dev`) — shared-cpu-1x, 512 MB, ~$3.40/mo
- **CI/CD**: GitHub Actions → test → lint → bandit → OSV scan → gitleaks → Fly.io deploy
- **Weekly price audit**: a drifted price is withheld from serving the moment it's detected (on every request, not just weekly); the scheduled workflow re-runs `check_price_drift` to report what's currently withheld and refreshes the vendored registry snapshot, opening an issue/PR when either drifts
- **Snapshot self-validation**: `scripts/validate_price_snapshot.py` gates the refresh PR. GitHub does not run CI on `GITHUB_TOKEN`-created PRs, so the generating job verifies the data itself
- **Pricing integrity loops (v1.74.0)**: three guards in `tests/test_pricing_integrity.py` (registry-coverage floor, per-1M plausibility band, no confirmed price for a model past its registry deprecation date) plus two weekly, human-merged PRs from `price-audit.yml` — `scripts/reconfirm_stale_prices.py` re-dates stale prices that still match the registry on input *and* output, and `scripts/retire_models.py` deletes entries whose same-provider registry deprecation date is ≥30 days old (the oracle withholds them from cost math the day the date passes and sets `retired_on`). `get_data_quality` reports `registry_coverage_pct`, `unmatched_confirmed`, `retired_listed` so the gaps are visible, not inferred. Each guard maps to a failure the Oct 2026 reconfirmation pass found live: ~40% of the catalogue invisible to drift checks, Cloudflare served 1000x too low, 55 retired models still priced
- **Registry-sourced entries + weekly pickup (v1.75.0)**: five registry-backed hosts joined the catalogue (DeepInfra, Novita, Vercel AI Gateway, Nebius, Databricks). Beyond a small hand-maintained core, their models are *registry-sourced*: `data/registry_models/<slug>.json` holds the registry's price, key and date per model, `BasePricingProvider._merge_registry_models()` appends them at fetch time (a curated STATIC_PRICING name always wins a collision), and every `PricingMetrics` now carries `price_source_kind` (`provider` | `registry`) plus `registry_key` so the distinction is machine-readable. `price_confirmed` still means "has a usable price", so registry-sourced models flow into every estimate, comparison and route — but `get_data_quality` splits `confirmed_models` into `provider_confirmed` + `registry_sourced`, and a registry-sourced price is never counted as provider-confirmed. `scripts/pickup_registry_models.py` (a third weekly PR from `price-audit.yml`, staged via `--providers`) adds any chat model the registry prices under a covered provider that the catalogue lacks, re-syncs/re-dates existing entries while the registry still backs them, leaves dropped ones to age into stale, and never edits a `*_pricing.py`. Between runs, a stored registry-sourced price the live registry has moved >5% is withheld like any other drift (`find_drift` no longer skips registry sources). Databricks (OWNER DECISION) has no provider-confirmed core — all entries are registry-sourced with a DBU note
- **Local currency (v1.76.0)**: a presentation-layer service, `src/services/currency.py`, owns the 13 supported currencies, the deterministic country→currency map, the vendored rate table `data/exchange_rates.json` (7-day staleness; never fetched per request; refreshed by a fourth weekly PR from `price-audit.yml` via `scripts/refresh_exchange_rates.py`) and one precedence function `resolve_locale()`. Six REST endpoints reach it through a single FastAPI dependency (`resolve_request_locale`: currency/country query or header → platform geo header → best-effort IP lookup → USD), five MCP tools through explicit arguments only (MCP never detects), and the browser pages through `GET /locale` + `GET /currency/rates` with client-side conversion. Converted figures are additive `local_*` fields omitted entirely when nothing is stated or detected, so the no-input path is byte-identical to before; pricing data, the oracle, the aggregator and data-quality are untouched (conversion happens on per-request `model_copy()`s). Privacy rule: the IP is used transiently to resolve a country — never stored, never logged by this feature, never echoed
- **Dispatch gateway (v1.77.0)**: the service can now *act* on its recommendation. `POST /v1/chat/completions` gains an opt-in mode — a `dispatch` policy object in the body (OpenAI SDK `extra_body`) — that picks the best eligible model under the caller's policy (objective preset, the router's hard constraints, allow/deny lists, a per-request USD cap), executes it with the caller's own provider key (BYOK via `X-Provider-Key-<Provider>` headers; nine executable providers, everything else recommend-only), fails over on timeout / 5xx / 429 / context-too-long / model-not-found while never dropping below the quality floor (401/403 on the caller's key is terminal), and returns a normal `chat.completion` with real `usage` plus an additive `dispatch_receipt` (chosen model, reason, ranked candidates, exclusions with reasons, actual cost from provider token counts × catalogue price, savings vs the most expensive eligible and vs the requested/pinned model, attempts, latency, optional shadow A/B result, local-currency figures when a locale is stated). Three flat services: `dispatch_executors.py` (one httpx path per wire family — OpenAI-compatible, Anthropic Messages, Gemini — with one error-classification table and no vendor SDK, so the SDKs' built-in retries cannot fight the failover loop), `dispatch_service.py` (eligibility stages → `ModelRouter.rank_models()` → objective ordering → cap → failover/shadow → ledgers → receipt) and `dispatch_receipts.py` (metadata-only `dispatch_receipts` table, org-scoped export via `GET /dispatch/receipts`). Eligibility is a hard rule: only models with a provider-confirmed price (`price_confirmed`, so drift-withheld, never-priced and retired models are never dispatched) under an executable provider for which the caller supplied a key. Every successful dispatch writes exactly one exact usage event, one routing decision marked used, fires budget alerts, and a budget alert flagged `hard_stop` (new column) refuses further dispatch for its org with 402 once reached. Dispatch requires a free-tier customer key (`x-api-key` or `Authorization: Bearer`); anonymous callers and the global `MCP_API_KEY` are refused with 401 — the plain proxy stays public and byte-identical. Privacy rule (same as the IP rule above): provider keys and prompt/completion text are transient — never persisted (until v1.79.0's opt-in, encrypted stored keys below), never logged at any level, never echoed in a receipt or error; provider error bodies are classified into an outcome and dropped. No new MCP tool (count stays 29); no pricing data touched
- **Stored per-org provider keys (v1.79.0)**: a customer can register each executable provider's key once (`POST /dispatch/keys`; list/rotate/delete under `/dispatch/keys[/{slug}]`, customer key only, any tier) and dispatch uses it whenever a request carries no `X-Provider-Key-<Provider>` header — so a stock chat UI or OpenAI SDK needs only the service key and a `dispatch` object. One new service, `provider_key_store.py`: a `provider_keys` table in `billing.db` (one row per org × provider), values AES-256-GCM-encrypted under an HKDF-SHA256 key derived from `PROVIDER_KEY_ENCRYPTION_KEY` (org and provider bound in as AAD), a last-4 fingerprint, created/rotated/last-used times; and `CompositeKeyResolver`, the per-request `ProviderKeyResolver` that puts the stored keys behind the header keys (header always wins; an `auth_error` on the winning key is never retried with the other). Every receipt attempt now records `key_source` (`header` | `stored`), never any part of a key. Without the secret the endpoints answer 503 and dispatch stays header-only; keys that no longer decrypt (secret changed, row tampered) count as absent and are listed `needs_reregistration`. New dependency: `cryptography`
- **MCP caller identity (v1.79.1)**: the seven per-org MCP tools (`record_usage`, `import_session_usage`, `get_usage_summary`, `analyze_session_usage`, `register_budget_alert`, `list_budget_alerts`, `delete_budget_alert`) no longer trust an `org_id` typed into the arguments. `POST /mcp` resolves an optional caller from the connection's `x-api-key` / `Authorization: Bearer` header (same rule as dispatch); a customer key acts only on its own org (another org's `org_id` → `org_mismatch`, another org's alert reads as not found), the global `MCP_API_KEY` keeps all-org behaviour, and anonymous callers — including unknown keys, all of STDIO and the PricingAgent — are refused with `customer_key_required`. The other public tools are unchanged and stay anonymous. New module `mcp/caller.py`; no new tool (count stays 29)
- **Operator-only alerts and conversations (v1.79.2)**: the four MCP tools that list or delete every subscriber's price-alert webhooks and every visitor's chat conversations (`list_price_alerts`, `delete_price_alert`, `list_conversations`, `delete_conversation`) moved to a new `OPERATOR_ONLY` access level in `mcp/caller.py`: only the global `MCP_API_KEY` (as `x-api-key` or `Bearer` on `POST /mcp`) may run them; anonymous, customer and unknown-key callers — and therefore the PricingAgent and STDIO — get one constant `operator_key_required` refusal that reveals no existence, counts or id validity. 18 public tools remain anonymous; all 29 stay registered and the agent still binds 26. Over HTTP, `security_middleware` checks a small method-aware table (`_is_operator_only_request`: GET/HEAD `/pricing/alerts`, GET/HEAD `/agent/conversations`, DELETE `/pricing/alerts/{id}` and `/agent/conversations/{id}`) *before* the full bypass and sends matches through the `_sensitive_paths` operator branch (x-api-key only, UTF-8 byte compare so a non-ASCII key is 401 not 500). Creating stays public: `POST /pricing/alerts` is now anonymous-throttled and body-capped, and `/mcp` joined `_public_body_capped_paths` (1 MB cap before parsing, 413). `/conversations` became an operator-key management page; the landing page no longer links it or the four tools
- **`dispatch` MCP tool (v1.80.0)**: MCP clients can now run a request through the dispatch gateway. The 30th tool, `dispatch` (`mcp/tools/dispatch.py`), is the first `CUSTOMER_ONLY` tool: it acts only for the customer whose service key is on the `/mcp` connection (anonymous and operator callers get HTTP's `dispatch_requires_api_key`; a foreign `org_id` gets `org_mismatch`), uses only that org's stored provider keys (an empty header resolver; provider keys are never tool arguments), and returns `completion`, `usage`, `routing_metadata` and the full `dispatch_receipt`. There is no second dispatch implementation: the steps between a validated body and `DispatchService.dispatch()` (enabled → unsupported feature → customer → per-customer rate limit → locale → service → key resolver) moved out of `src/main.py` into `src/services/dispatch_entry.py::prepare_and_dispatch()`, which both the HTTP handler and the tool call, raising the existing `DispatchError` payloads; the request model moved to `src/models/dispatch.py::ChatCompletionsRequest` (`_ProxyRequest` subclasses it) so validation is identical, and the limiter store moved to `src/services/rate_limiter.py` so HTTP and MCP dispatch share one per-customer bucket. HTTP responses are byte-identical. On MCP every refusal is a `success: false` result with HTTP's error code and payload; validation errors keep HTTP's `type`/`loc`/`msg` but never `input`/`ctx`; unexpected exceptions are redacted to their type name. `ToolManager` gained a `transport` (`http` / `stdio`): STDIO returns a structured `dispatch_requires_hosted_mcp` refusal. The agent never binds it (still 26 of 30); 18 public tools unchanged. Non-streaming, text-only; no npx bridge change (dispatch users set `LLM_PRICING_MCP_TIMEOUT_MS=120000`)
- **Webhook SSRF guard (v1.80.1)**: price-change and budget-spend alert webhooks are the only feature that POSTs to a caller-chosen URL. New module `src/services/webhook_guard.py` applies one rule set at registration (first line of both services' `register()`, so `POST /pricing/alerts`, `POST /usage/alerts`, the MCP register tools over `/mcp` and STDIO, and the PricingAgent all share it) and again at every delivery: https only; no IP literal in any spelling (decimal/octal/hex/short IPv4, IPv4-mapped/compatible/6to4/Teredo/NAT64 IPv6) or resolved address that is loopback, private, link-local (incl. `169.254.169.254`), CGNAT, ULA (incl. Fly 6PN `fdaa::/16`), multicast or reserved; no `localhost`/`*.localhost`/`*.internal`/`*.flycast`; refused if ANY resolved address is blocked or the host does not resolve (2s timeout). Rejections are `422 invalid_webhook_url` (HTTP) / `error_code: invalid_webhook_url` (MCP) with a fixed reason category, never IPs or DNS detail. Delivery uses `PinnedWebhookTransport`: resolve + check, then connect to the checked IP (rebinding-safe) with `sni_hostname` keeping TLS verification on the original name; no redirects, `trust_env=False` (env `HTTPS_PROXY` no longer applies to webhooks). Legacy non-compliant rows are kept but skipped at delivery (logged, not retried, not counted). `WEBHOOK_ALLOW_PRIVATE_TARGETS` (local dev only, never on Fly) relaxes the scheme/destination rules with a startup WARNING
- **Price-alert self-service (v1.81.0)**: registering a price alert (`POST /pricing/alerts`, the public `register_price_alert` MCP tool, the chat agent) now also returns a one-time `alert_token` (`lpa_` + `secrets.token_urlsafe(32)`, 256 bits) and an `alert_token_notice`. Only its SHA-256 hash is stored, in a new nullable, unique-indexed `pricing_alerts.token_hash` column (migration-on-open in `PricingAlertService.initialize()`; pre-v1.81.0 rows stay NULL, never matchable, operator-managed). `get_by_token()` / `delete_by_token()` look an alert up by hash only — no request on any surface carries an alert id, so there is no id oracle — and every failure (wrong, malformed, missing, deleted, unknown, legacy) gets one identical refusal: HTTP 404 `{"error": "invalid_alert_token"}` from the anonymous, per-IP rate-limited, body-capped `POST /pricing/alerts/self/view` and `/self/delete` (token in the JSON body, never the URL, so telemetry never sees it), MCP `error_code: invalid_alert_token` from the new PUBLIC 31st tool `manage_price_alert` (`action: view|delete`, `redact_errors`; no per-IP limiter on `/mcp`, matching `register_price_alert`). The operator listing, `check_and_fire` and webhook payloads never select the hash; operator list/delete is unchanged. The agent binds `manage_price_alert` (27 of 31); `PricingAgent.chat()`/`chat_stream()` persist user and assistant turns through `redact_alert_tokens()` (live reply keeps the token, the stored turn shows `[redacted alert token]`), and agent log lines that can carry model/tool text are redacted the same way
- **Clean cross-branch promotion**: `scripts/promote_branch_content.sh` mirrors one branch's tree onto another (additions, updates, AND deletions) to work around develop/master squash-merge divergence, verifying the result matches exactly before returning
- **Repo cost auditor**: `scripts/audit_repo.py` statically scans an arbitrary codebase (customer-run, not deployed) for LLM SDK call sites and reuses `predict_cost`'s token/pricing math to recommend cheaper models, prompt caching, Batch API, task-based right-sizing, and duplicate-prompt consolidation — runs locally only, since the hosted `/mcp` HTTP transport has no access to a caller's filesystem
- **AI-native SDLC pipeline** (dev tooling, not deployed): `/release-feature` runs a feature through dedicated Spec, Design, Code, Review (parallel correctness/security/simplification), and Test & Release phase agents (`.claude/agents/phase-*.md`) orchestrated by `.claude/workflows/release-feature.js`, ending at an open PR against `develop` — Spec fixes *what*/acceptance criteria, Design fixes *how* (approach, version, branch name), so neither phase re-derives the other's decision; see `CLAUDE.md`'s "AI-Native SDLC Pipeline" section

---

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Clients                                                                    │
│  Browser UIs · Claude Desktop (MCP) · API callers · OpenAI-compat SDKs     │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │  HTTPS / STDIO
┌─────────────────────────────────▼───────────────────────────────────────────┐
│  Presentation Layer (src/main.py + mcp/)                                    │
│                                                                             │
│  REST API              MCP (31 tools)          Browser UIs (14 pages)       │
│  /pricing              STDIO transport          /  /chat  /calculator        │
│  /router/recommend     HTTP POST /mcp           /compare  /history           │
│  /cache-stats          JSON-RPC 2.0             /trends   /widget            │
│  /billing/*            MCP 2024-11-05           /billing  /admin             │
│  /agent/chat                                    /mcp-setup  /api-docs        │
│  /v1/chat/completions                           /whats-new  /ide-compare     │
│    (+ dispatch mode, v1.77.0)                                               │
│  /dispatch/providers  /dispatch/receipts        /conversations (operator)    │
│  /dispatch/keys[/{provider}]  (stored provider keys, v1.79.0)               │
│  /locale  /currency/rates  (+ currency/country on 6 pricing endpoints)      │
│                                                                             │
│  Security middleware: billing DB key → global MCP_API_KEY fallback          │
│  Rate limiting: per {ip}:{tier} sliding window (30/120/600 req/min)         │
│  (dispatch mode authenticates in-handler: customer key only, same bucket;   │
│   MCP `dispatch` shares the bucket via src/services/rate_limiter.py)        │
└──────────┬──────────────────────┬──────────────────────┬────────────────────┘
           │                      │                      │
┌──────────▼──────────┐  ┌───────▼────────┐  ┌─────────▼──────────────────┐
│  Pricing Services   │  │  Agent / RAG   │  │  Billing Service           │
│                     │  │                │  │                            │
│  PricingAggregator  │  │  ReAct loop    │  │  BillingService            │
│  30 provider impls  │  │  TF-IDF RAG    │  │  billing.db (SQLite)       │
│  PricingHistory     │  │  Conv. memory  │  │  Free signup → API key     │
│  BenchmarkService   │  │  LLM backend   │  │  Stripe checkout/webhook   │
│  Router             │  │  (GPT-4o-mini  │  │  Tier sync (free/pro/ent)  │
│  RecommendationCache│  │   or Anthropic)│  │                            │
│  SavingsTracker     │  └───────┬────────┘  └────────────────────────────┘
│  UsageTracker       │          │
│  BudgetAlerts       │          │
│  PricingAlerts      │          │
│  WebhookGuard (SSRF)│          │
│  Currency (locale)  │          │
│  DispatchService    │          │
│  DispatchExecutors  │          │
│  DispatchReceipts   │          │
│  ProviderKeyStore   │          │
└──────────┬──────────┘          │
           │                     │
┌──────────▼─────────────────────▼────────────────────────────────────────────┐
│  Data Layer                                                                 │
│                                                                             │
│  pricing_history.db     billing.db          In-memory cache                │
│  pricing_snapshots      customers table     Aggregator (TTL)               │
│  routing_savings        (api_key, tier,     Conversations (unless          │
│  routing_feedback       org_id, stripe_id)  CONVERSATION_DB_PATH set)      │
│  usage_events                                                              │
│  pricing_alerts (+token_hash)                                              │
│  budget_alerts (+hard_stop)                 Recommendation cache (5min TTL)│
│  dispatch_receipts (metadata only)          provider_keys (v1.79.0,        │
│                                             AES-GCM, org-scoped)           │
└──────────┬──────────────────────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────────────────────┐
│  External Services                                                          │
│  12 LLM provider APIs (pricing)   Stripe (checkout, webhooks, portal)      │
│  OpenAI GPT-4o-mini (agent LLM)   Hugging Face API (benchmark fallback)    │
│  9 provider inference APIs, caller-keyed (dispatch gateway, v1.77.0;       │
│    header or stored per-org key, v1.79.0):                                 │
│    OpenAI · Anthropic · Google Gemini · Groq · Together · Fireworks ·       │
│    DeepSeek · Mistral · xAI — via httpx, never a vendor SDK                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## File Structure

```
llm-pricing-mcp-server/
│
├── src/
│   ├── __init__.py                  # __version__ (bump on every release)
│   ├── main.py                      # FastAPI app; all endpoints; auth + rate-limit middleware
│   ├── config/
│   │   └── settings.py              # Pydantic-settings; all env vars incl. Stripe, billing
│   ├── models/
│   │   ├── deployment.py            # Deployment/health-check models (DeploymentMetadata, GracefulShutdownStatus, …)
│   │   ├── pricing.py               # PricingMetrics, PerformanceMetrics, RouterResponse, …; BudgetAlertRequest/Record gain hard_stop (v1.77.0)
│   │   ├── billing.py               # SignupRequest/Response, CheckoutRequest, CustomerDashboard
│   │   └── dispatch.py              # v1.77.0: DispatchPolicy (objective preset, router constraints, allow/deny, max_request_cost_usd, failover, pinned_model, shadow, request_id, currency/country) — provider keys deliberately NOT modelled (headers only)
│   └── services/
│       ├── base_provider.py         # BasePricingProvider: build_metrics() shared builder (incl. price_source_kind/registry_key), live sync, provenance; merges data/registry_models/<slug>.json registry-sourced entries at fetch time, curated names win (v1.75.0)
│       ├── pricing_aggregator.py    # Orchestrates + caches all provider data (async); also hosts the get_pricing_aggregator() process-wide singleton shared by REST and all MCP tools
│       ├── cost_calculator.py       # compute_token_cost(): single cost-math implementation shared by /cost-estimate(+batch)/usage and their estimate_cost/compare_costs/record_usage MCP-tool counterparts
│       ├── currency.py              # v1.76.0: SUPPORTED_CURRENCIES (13), COUNTRY_TO_CURRENCY, vendored rate table loader (data/exchange_rates.json, 7-day stale flag, no network), resolve_locale() precedence, convert/localize_* helpers; shared by the REST dependency, the five MCP tools and /locale + /currency/rates
│       ├── pricing_history.py       # SQLite price-history + routing_feedback tables
│       ├── benchmark_service.py     # Quality scores: static table + HF API fallback (24h TTL)
│       ├── router.py                # LLM routing recommendation engine; v1.77.0 exposes rank_models() + hard_filter_reason() (same filters/scoring get_optimal_model always ran) for the dispatch gateway
│       ├── dispatch_executors.py    # v1.77.0: EXECUTABLE_PROVIDERS registry (9, keyed by catalogue display names), HeaderKeyResolver (BYOK seam; repr redacted), OpenAIWire/Anthropic/Gemini executors over httpx.AsyncClient, one error-classification table (timeout/rate_limited/server_error/context_too_long/auth_error/model_not_found/provider_rejected); error bodies classified, never copied. v1.78.0: BaseExecutor.stream() (client.stream + _iter_sse + per-family build_stream/parse_stream_event → StreamEvent delta|finish|usage; non-2xx classified before the first yield), OUTCOME_CLIENT_DISCONNECTED (receipt-only)
│       ├── dispatch_service.py      # v1.77.0: DispatchService — _prepare(): budget hard stop → catalogue (unconfirmed included so they can be reported excluded) → eligibility stages (first failing reason per model; emptying stage = eliminating_constraint) → rank_models + objective ordering → per-request cap; dispatch(): failover loop / concurrent shadow → _finish(): ledgers (usage event, routing + feedback, budget alerts, receipt) → chat.completion + dispatch_receipt. v1.78.0: dispatch_stream() async generator (DispatchStreamEvent delta|end|error; failover only before first content; idle timeout per event; shielded settlement on client disconnect; partial estimated spend)
│       ├── dispatch_entry.py        # v1.80.0: prepare_and_dispatch() — the one pre-dispatch pipeline (enabled → unsupported feature → customer → rate limit → locale → service → key resolver → DispatchService) shared by HTTP and MCP dispatch; single-source refusal payloads; DispatchCustomer (id/org/tier only); DispatchDeps (collaborators injected per transport); never imports src.main
│       ├── rate_limiter.py          # v1.80.0: the one sliding-window limiter store/lock + rate_limit_allow(bucket, tier, settings); src.main re-exports the same objects
│       ├── provider_key_store.py    # v1.79.0: ProviderKeyStore — provider_keys table in billing.db (org_id, provider PK; AES-256-GCM ciphertext, AAD = org + provider, HKDF-SHA256 working key from PROVIDER_KEY_ENCRYPTION_KEY; last-4 fingerprint; key_version; created/rotated/last_used); register/rotate/delete/list (metadata only, status active|needs_reregistration) / load_for_dispatch / touch_last_used (monotonic, key_version-guarded); CompositeKeyResolver (header first, stored second; source/note_used/flush_usage); redacted reprs everywhere
│       ├── dispatch_receipts.py     # v1.77.0: dispatch_receipts table (metadata only, (org_id, created_at) index, retention purge) + org-scoped list/CSV for GET /dispatch/receipts. v1.78.0: streamed + usage_estimated columns (appended; ALTER TABLE migration on initialize)
│       ├── recommendation_cache.py  # ~5min TTL cache for recommend_model / POST /router/recommend decisions + process-lifetime hit/miss stats (get_cache_stats)
│       ├── task_profiles.py         # 12 task I/O-ratio profiles + keyword task inference
│       ├── portfolio_optimizer.py   # Per-task model allocation + savings vs single-model baseline
│       ├── price_oracle.py          # External price registry: fills gaps, withholds drifted prices on input OR output (v1.74.0), withholds retired models once the registry's deprecation_date passes (sets retired_on) (24h TTL + snapshot). Key matching (v1.73.11): exact, "/<name>" suffix (multi-segment names), last slash segment (dotted names), last dot segment (Bedrock cross-region keys); provider aliases keyed by the catalogue's display names, with a per-provider preference order (Google: gemini/ before vertex_ai/). Same-provider variants resolve to the bare canonical key (regional/tier keys are different SKUs); majority vote only among equal-depth keys. v1.75.0: runtime gap-fills are marked price_source_kind="registry"+registry_key; registry-sourced entries are drift-checked too (a stored price the live registry moved >5% is withheld until the pickup re-syncs); PLAUSIBLE_* band constants; load_from_dict()/set_price_oracle() for the pickup script
│       ├── data_quality.py          # Aggregate accuracy summary shared by /data-quality and get_data_quality; v1.75.0 splits confirmed_models into provider_confirmed + registry_sourced
│       ├── token_counter.py         # tiktoken token counting + prompt-cache savings math
│       ├── ide_pricing.py           # Subscription pricing for AI coding IDE tools
│       ├── savings_tracker.py       # Per-org router savings + acceptance_rate
│       ├── usage_tracker.py         # Actual LLM usage events (exact or text-estimated, `is_estimated` flag) + per-org spend summary + per-session lookup
│       ├── session_recommendation.py # Shared session-usage -> ModelRouter recommendation logic (GET /usage/session + analyze_session_usage)
│       ├── quality_tradeoff_caveat.py # Shared quality_tradeoff caveat (threshold + builder) used by recommend_model.py and session_recommendation.py
│       ├── budget_alerts.py         # Webhook alerts on actual spend crossing a USD threshold; v1.77.0 hard_stop flag + find_hard_stop() (org-scoped alerts only) for the dispatch gateway
│       ├── billing_service.py       # BillingService: customers table, Stripe sync
│       ├── pricing_alerts.py        # Webhook alert registration + delivery; v1.81.0 one-time alert_token (SHA-256 token_hash at rest), get_by_token/delete_by_token, redact_alert_tokens()
│       ├── webhook_guard.py         # v1.80.1 SSRF guard for alert webhooks: URL rules at registration + PinnedWebhookTransport (checked-IP delivery, no redirects)
│       ├── telemetry.py             # Request telemetry aggregation
│       ├── data_fetcher.py          # Async HTTP fetching helpers
│       ├── data_sources.py          # Static provider data fallbacks
│       ├── geolocation.py           # IP -> country lookup (telemetry aggregates; v1.76.0 currency preset via a 1s request-path timeout; the address is never logged)
│       ├── deployment.py            # Blue-green deployment helpers
│       │
│       │   # Provider implementations (one per LLM provider):
│       ├── openai_pricing.py
│       ├── anthropic_pricing.py
│       ├── google_pricing.py
│       ├── cohere_pricing.py
│       ├── mistral_pricing.py
│       ├── groq_pricing.py
│       ├── together_pricing.py
│       ├── fireworks_pricing.py
│       ├── perplexity_pricing.py
│       ├── bedrock_pricing.py
│       ├── ai21_pricing.py
│       ├── xai_pricing.py
│       ├── deepseek_pricing.py
│       ├── cerebras_pricing.py
│       ├── nvidia_pricing.py
│       ├── replicate_pricing.py
│       ├── salesforce_pricing.py
│       ├── promptql_pricing.py
│       ├── snowflake_pricing.py
│       ├── oracle_pricing.py
│       ├── azure_openai_pricing.py
│       ├── vertex_pricing.py
│       ├── huggingface_pricing.py
│       ├── cloudflare_pricing.py
│       │   # v1.75.0 — registry-backed hosts; core table + registry-sourced JSON
│       ├── deepinfra_pricing.py
│       ├── novita_pricing.py
│       ├── vercel_ai_gateway_pricing.py
│       ├── nebius_pricing.py
│       └── databricks_pricing.py    # empty STATIC_PRICING by decision; all models registry-sourced, DBU note
│
├── data/
│   ├── model_prices_snapshot.json   # Vendored reference-registry snapshot (offline fallback; weekly refresh PR)
│   ├── exchange_rates.json          # v1.76.0: vendored USD rate table (13 codes, as_of, source, placeholder flag). Read at request time by currency.py; written only by scripts/refresh_exchange_rates.py (weekly PR). The Dockerfile copies data/ into the image AND this file to /app/vendored/ because fly.toml's volume mount shadows /app/data (EXCHANGE_RATES_PATH points there)
│   └── registry_models/             # One <slug>.json per provider: registry-sourced entries (price, registry_key, price_as_of, added_on). Written only by scripts/pickup_registry_models.py; merged by BasePricingProvider at fetch time
│
├── scripts/
│   ├── refresh_exchange_rates.py    # Weekly PR (v1.76.0): Frankfurter -> ECB-XML fallback, re-based to USD; validates 13 codes / positive / USD==1 / as_of not future / <=15% move; any failure leaves the file untouched and exits 0; --from-file for offline tests
│   ├── pickup_registry_models.py    # Weekly PR: new registry chat models under covered providers -> registry-sourced entries; re-sync/re-date existing; never edits *_pricing.py (v1.75.0)
│   ├── reconfirm_stale_prices.py    # Weekly PR: re-date stale curated prices that still match the registry (skips registry-sourced)
│   ├── retire_models.py             # Weekly PR: delete entries retired >=30 days; registry-sourced rows are removed from their JSON
│   ├── validate_price_snapshot.py   # Gates the snapshot-refresh PR
│   ├── audit_repo.py                # Customer-run repo cost auditor (not deployed)
│   ├── sync_nav.py                  # Stamps the canonical nav into every static page (--check reports drift; v1.75.1)
│   ├── quick_validate.py            # Local smoke check: starts the STDIO MCP server, lists tools, calls a few
│   └── promote_branch_content.sh    # Mirror one branch's tree onto another
│
├── agent/
│   ├── pricing_agent.py             # High-level agent entry point
│   ├── react_loop.py                # ReAct (Reason + Act) loop implementation
│   ├── llm_backend.py               # AnthropicBackend + OpenAIBackend (switch via env)
│   ├── conversation.py              # SQLite conversation memory, turn limit
│   └── tools.py                     # 27 of 31 MCP tool bindings for agent use (+ RAG search)
│
├── rag/                             # Agent's docs/pricing retrieval (no external vector DB)
│   ├── document_loader.py           # Loads markdown docs + pricing data as documents
│   ├── chunker.py                   # Splits documents into retrieval chunks
│   ├── vector_store.py              # In-memory TF-IDF store
│   └── pipeline.py                  # RAGPipeline: index + retrieve
│
├── mcp/
│   ├── server.py                    # MCP STDIO transport (Claude Desktop) — always an anonymous caller; DEBUG logs method/id only (v1.79.1); ToolManager(transport='stdio') so dispatch refuses (v1.80.0)
│   ├── caller.py                    # v1.79.1: McpCaller (anonymous | customer | operator; id/org/tier only, never a key), access levels PUBLIC / ORG_SCOPED / OPERATOR_ONLY (v1.79.2) / CUSTOMER_ONLY (v1.80.0: dispatch), authorize(), effective_org_id(), operator_key_required()
│   ├── schema_generator.py          # Generates mcp/schemas/ from the Pydantic models
│   ├── tools/                       # Per-tool MCP handler modules (tool_manager.py: execute_tool(name, args, caller=ANONYMOUS); the seven usage/budget tools declare caller_access = ORG_SCOPED; list/delete_price_alert and list/delete_conversation declare OPERATOR_ONLY, v1.79.2; dispatch.py declares CUSTOMER_ONLY with access_refusal()/stdio_refusal() and redact_errors, v1.80.0; manage_price_alert.py is PUBLIC with redact_errors — alert_token view/delete, v1.81.0; ToolManager(transport='http'|'stdio'))
│   └── schemas/                     # Generated MCP JSON-RPC schema definitions
│
├── static/
│   ├── index.html                   # /chat — PricingAgent chat UI (static/ is mounted at /chat)
│   ├── landing/index.html           # / — marketing landing page (served by the root route)
│   ├── admin/index.html             # /admin — server stats, rate limits, customers
│   ├── billing/index.html           # /billing — SaaS signup + upgrade SPA
│   ├── calculator/index.html        # /calculator — interactive cost calculator
│   ├── compare/index.html           # /compare — side-by-side model comparison
│   ├── history/index.html           # /history — Chart.js pricing history
│   ├── trends/index.html            # /trends — price-change leaderboard
│   ├── widget/index.html            # /widget — embeddable pricing table
│   ├── widget/widget.js             # Embeddable script for third-party pages
│   ├── ide-compare/index.html       # /ide-compare — AI coding IDE subscription comparison
│   ├── conversations/index.html     # /conversations — operator-only conversation management (operator key in localStorage, shared with /admin; operator-key-required state otherwise; v1.79.2)
│   ├── mcp-setup/index.html         # /mcp-setup — MCP integration hub (6 client tabs, live test, all 31 tools)
│   ├── api-docs/index.html          # /api-docs — API reference (Swagger/ReDoc iframe + endpoint table)
│   └── whats-new/index.html         # /whats-new — release notes timeline (v1.35.0 → current)
│
├── .github/workflows/
│   ├── ci-cd.yml                    # Full CI/CD: test→lint→osv→bandit→gitleaks→deploy
│   ├── sync-develop.yml             # Auto-sync develop ← master after deploy
│   ├── price-audit.yml              # Weekly: drift issue + snapshot-refresh PR; (v1.74.0) reconfirm-stale-prices PR + retire-models PR; (v1.75.0) pickup-registry-models PR (staged via --providers; reads the vendored snapshot so its guards and the pickup see one registry); (v1.76.0) refresh-exchange-rates PR (v1.77.1: needs only `audit`, so a rejected snapshot never blocks it) — all human-merged. The snapshot validator (v1.77.1) discounts provider namespaces that vanish upstream wholesale (Oct 2026: all `openrouter/*`) from its 10% removal limit, keeps a 40% hard ceiling, and rejects outright if a provider the catalogue is priced from disappears
│   ├── npm-publish.yml              # npm Trusted Publishing (OIDC) of npm/llm-pricing-mcp on master push — filename is part of the npmjs.com trust config
│   └── mcp-registry-publish.yml     # Publishes server.json to registry.modelcontextprotocol.io via GitHub OIDC (`mcp-publisher login github-oidc`) on master push
│
├── server.json                      # MCP registry listing: hosted remote + npm package (llm-pricing-mcp, ownership via package.json mcpName) — published by mcp-registry-publish.yml
│
├── npm/llm-pricing-mcp/              # Published to npm by .github/workflows/npm-publish.yml — see "MCP Dual Transport" below
│   ├── package.json
│   ├── bin/llm-pricing-mcp.js       # Zero-dependency STDIO<->HTTP bridge to the hosted /mcp endpoint
│   ├── test/mock-server.js          # Local stand-in for POST /mcp, used only by tests/test_npm_wrapper.py
│   └── README.md
│
├── tests/                           # pytest suite (current count: README's "passing tests" line)
├── docs/
│   └── ARCHITECTURE.md              # THIS FILE — update on every structural change
├── fly.toml                         # Fly.io app config (volume at /app/data, health checks, EXCHANGE_RATES_PATH)
├── Dockerfile                       # Container image (copies src/ static/ agent/ rag/ mcp/ docs/ data/ + vendored/exchange_rates.json)
├── requirements.txt                 # Python dependencies
├── .pre-commit-config.yaml          # Local gitleaks + branch guard hooks
└── CLAUDE.md                        # Claude Code session rules (mandatory checklists)
```

---

## Key Architectural Patterns

### 1. Singleton Services (`init_X` / `get_X`)
Every stateful service follows the same pattern for safe async startup:
```python
_service: Optional[XService] = None

async def init_x_service(...) -> XService:
    global _service
    _service = await XService.create(...)
    return _service

def get_x_service() -> XService:
    if _service is None:
        raise RuntimeError("XService not initialized")
    return _service
```
All `init_*` calls live in the `startup_pricing_history()` FastAPI startup handler (`@app.on_event("startup")`). Services: `BillingService`, `ProviderKeyStore` (v1.79.0; `billing.db`, initialized right after billing), `PricingHistory`, `PricingAlertService`, `SavingsTracker`, `UsageTracker`, `BudgetAlertService`, the agent's conversation store (`init_conversation_store`), `PricingAggregator` (via `get_pricing_aggregator()`), `ModelRouter` (`init_router`), `DispatchReceiptStore`, `DispatchService` (v1.77.0; built from the router and the ledgers above; its default executor factory is `get_executor`, and tests inject a fake through the `executor_factory` parameter).

`PricingAggregatorService`'s singleton (`get_pricing_aggregator()`, lazy + `asyncio.Lock`-guarded, defined in `pricing_aggregator.py` itself) is the one exception reused across *both* the REST API and every MCP tool in-process: `ToolManager` takes an optional `aggregator` param forwarded to all 13 pricing-dependent tools, and both MCP transports (`mcp/server.py`'s STDIO `main()`, `src/main.py`'s HTTP `/mcp` `_get_http_mcp_tool_manager()`) resolve `get_pricing_aggregator()` once and hand it to their `ToolManager`. This means one warm pricing cache per process, not one per surface — a REST call and an MCP tool call for the same model agree within the cache's TTL. Each tool's constructor still falls back to building its own instance when `aggregator` is omitted (e.g. direct instantiation in tests), so no existing call site broke.

### 2. Authentication Middleware (`src/main.py`)
Layered auth — billing DB first, global key fallback — so existing deployments without billing are unaffected:
```
Incoming request
  └─ Operator-only request? (`_is_operator_only_request(method, path)`, v1.79.2: GET/HEAD
     /pricing/alerts, GET/HEAD /agent/conversations, DELETE /pricing/alerts/{id},
     DELETE /agent/conversations/{id}; never OPTIONS) → decided FIRST, so the "/pricing"
     prefix bypass below cannot expose them; handled by the operator-only branch further down
  └─ Full-bypass path? (OPTIONS, static UIs, /pricing, /mcp, and every `_unauthenticated_paths`
     entry such as /health, /docs, /billing/webhook) → skips auth, rate limit AND body cap —
     except `_public_body_capped_paths` (/v1/chat/completions, v1.77.0; /mcp, v1.79.2), which stay
     public but still go through `_body_too_large()` (header + actual bytes) before the body is
     ever parsed. /agent/conversations left the bypass in v1.79.2
  └─ Operator-only path? (the request above, or `_sensitive_paths`: /deployment/shutdown*,
     /admin/customers) → only the global MCP_API_KEY in x-api-key is accepted (UTF-8 byte
     compare since v1.79.2, so a non-ASCII key is 401 not 500), a customer key is not; 503 if unset.
     /admin/customers joined in v1.78.1: it lists every customer's email and org_id and had
     accepted any customer key, which public signup hands to anyone.
  └─ Anonymous-but-throttled path? (`_anonymous_rate_limited_paths`: /agent/chat,
     /agent/chat/stream, /billing/signup, POST /pricing/alerts since v1.79.2, POST /pricing/alerts/self/view
     and /self/delete since v1.81.0 (alert_token in the body)) → skips auth only; still rate-limited per IP and
     body-size-capped. Added in v1.73.3 — before it, no such tier existed: anonymous meant
     unthrottled, and the agent endpoints run an LLM on the operator's key. Signup moved here
     from the full bypass in v1.78.1: it mints a service key per call, and its 200-vs-409
     reply says whether an email is registered. Since v1.78.1 signup returns a key only for a
     new email (the key is shown once); it never returns an existing account's key.
  └─ Extract x-api-key header
       └─ BillingService.get_customer_by_api_key(key) → found?
            YES → request.state.customer = customer; tier from DB; bucket = customer.id
            NO  → check against global MCP_API_KEY; bucket = ip:tier where tier comes from the
                  X-Api-Key-Tier header ONLY if that global key was presented and valid — anonymous
                  requests get tier "" (default limit) regardless of the header (v1.73.5)
  └─ Rate limit check (60s sliding window per bucket key — `_rate_limit_allow(bucket_key, tier)`, v1.77.0 extracted
     so other call sites share the same store and lock; v1.80.0 moved the store/lock/cleanup to
     src/services/rate_limiter.py — src.main re-exports the same objects and keeps `_rate_limit_allow` as a
     wrapper that reads its own settings — so the MCP dispatch tool consumes the same per-customer bucket)
```

**Dispatch mode exception (v1.77.0).** `/v1/chat/completions` stays in `_unauthenticated_paths` so the plain
recommendation proxy remains public and byte-identical. When the body carries a `dispatch` object the handler
authenticates itself: the service key comes from `x-api-key` *or* `Authorization: Bearer <key>` (the OpenAI
SDK default) and must resolve to a billing customer — the global `MCP_API_KEY` is refused (401
`dispatch_requires_api_key` pointing at `/billing/signup`) because dispatch writes per-org ledgers and needs an
org. The customer's id/tier then go through the same `_rate_limit_allow` bucket the middleware uses. Since
v1.80.0 these in-handler steps live in `src/services/dispatch_entry.py::prepare_and_dispatch()` (see §8),
shared with the MCP `dispatch` tool; status codes, bodies and order are unchanged. The
body-size cap is *not* in the handler: the middleware applies `_body_too_large()` to the path before FastAPI
parses the body (an in-handler check would run after the whole body is in memory), and `DispatchPolicy`
bounds `allow`/`deny` (100 entries x 200 chars) because they are persisted per receipt row. Provider keys
travel in `X-Provider-Key-<Provider>`
headers (never the body: pydantic 422s echo the body, the body is buffered on `request._body`) and are read
once into a request-scoped `HeaderKeyResolver`. Since v1.79.0 the handler wraps it, when the org has stored keys
for providers the headers do not cover, in a request-scoped `CompositeKeyResolver` holding those decrypted
stored keys (header first); the two resolvers are the only objects that hold provider keys in memory, and
only for the request. `/dispatch/keys` and `/dispatch/keys/{provider}` (v1.79.0) are not allow-listed (and
deliberately not under `/providers`, whose prefix is a full bypass): the middleware authenticates them from
`x-api-key`, and the handlers require `request.state.customer` (anonymous / global key → 403
`customer_key_required`) before checking the store is available (503 `stored_keys_unavailable`). Their JSON
bodies are parsed by hand, not as pydantic parameters, so no validation error can echo a submitted key. `GET /dispatch/providers` is
public (allow-listed); `GET /dispatch/receipts` is not allow-listed, so the middleware authenticates it, and
the handler additionally requires `request.state.customer` (global-key callers get 403) and always scopes to
that customer's `org_id`.

**`/mcp` caller identity (v1.79.1).** `/mcp` stays a full-bypass path (the 19 public tools need no key; since v1.79.2 it is body-capped), so
the handler resolves the caller itself, and only when it has to: `_handle_mcp_request(body, request)` calls
`_resolve_mcp_caller(request)` for a `tools/call` whose tool declares a non-PUBLIC `caller_access`, never for
`initialize`, `tools/list` or a public tool (no billing lookup on the public path). The key comes from
`_dispatch_service_key()` (`x-api-key`, else `Authorization: Bearer`): equal to `MCP_API_KEY`
(`secrets.compare_digest`) → operator; a billing customer → `McpCaller.from_customer()` (id, org_id, tier — never
the key or email); no key, an unknown key, or billing not initialised → anonymous, silently. Nothing on this
path logs headers or the key, and no refusal echoes it. Refusals are normal tool results (`success: false`,
HTTP 200), not JSON-RPC errors: `customer_key_required` (with `signup`, header and npx-env guidance) or
`org_mismatch` (echoes neither org_id), and since v1.79.2 `operator_key_required` for the four `OPERATOR_ONLY` tools
(`list_price_alerts`, `delete_price_alert`, `list_conversations`, `delete_conversation`), returned identically to anonymous, customer and unknown-key callers. Unlike `/usage` (HTTP), there is no anonymous `org_id` NULL pool over
MCP: an anonymous caller cannot read or write any org's data at all.

### 3. Provider Pattern
Each of 30 LLM providers implements `BasePricingProvider`:
- `fetch_pricing_data() -> list[PricingMetrics]` — returns static pricing (with live data fallback where available)
- `get_pricing_with_status()` — wraps `fetch_pricing_data()`, merges **registry-sourced entries**, applies **live model sync**, returns `(data, status)`

#### Registry-sourced entries (v1.75.0)
A provider's catalogue is the union of its curated `STATIC_PRICING` (a human's statement about the rate card, `price_source_kind="provider"`) and `data/registry_models/<slug>.json` (the reference registry's statement, `price_source_kind="registry"`, with `registry_key`, `price_as_of` = registry date, `added_on`). `_merge_registry_models()` appends the JSON entries after `fetch_pricing_data()`; a curated name always wins a collision. Entries are listed under the identifier the provider's API accepts (registry key minus the provider prefix) and the oracle's `/<name>` suffix match resolves them back to the same-provider record. The JSON is written only by `scripts/pickup_registry_models.py` (weekly PR; staged rollout via `--providers`; run with `--snapshot-only` because the guard tests price the catalogue against `data/model_prices_snapshot.json`, so new registry data reaches the pickup via the snapshot-refresh PR); a `STATIC_PRICING` entry may also declare `"price_source_kind": "registry"` + `"registry_key"` when a core number could not be verified against the rate card and was taken from the registry instead (reported as a shortfall, never presented as provider-confirmed). `PROVIDER_SERVICES` in `pricing_aggregator.py` is the single ordered list of providers; `PROVIDER_COUNT` derives from it and every "N providers" string in the docs must agree (`tests/test_new_providers.py`).

#### Live Model Sync (automatic, per provider)
`BasePricingProvider._fetch_live_model_ids()` queries the provider's `/v1/models` API (cached 6 h).
`_apply_live_filter()` removes models absent from the live list — keeps any model whose name is an exact
or prefix match in the live set. Falls back to the full static list if the API is unreachable or no key is set.
Enabled for: OpenAI, Anthropic, Groq, Mistral AI, Together AI, Fireworks AI, xAI, DeepSeek, Cerebras, NVIDIA NIM, Hugging Face, PromptQL.

`PricingAggregatorService` fetches all providers concurrently (`asyncio.gather`), merges results, and caches with a configurable TTL. Adding a new provider = new file + one `PROVIDER_SERVICES` entry (+ `PROVIDER_FILTER_ALIASES` strings) in the aggregator. DeepInfra, Novita, Vercel AI Gateway, Nebius and Databricks have no live model sync configured: their candidate list endpoints could not be verified as public/key-optional from the authoring environment, and Databricks has none.

### 4. MCP Dual Transport
- **STDIO** (`mcp/server.py`): JSON-RPC 2.0 over stdin/stdout for Claude Desktop local integration
- **HTTP** (`POST /mcp`): Same JSON-RPC 2.0 payload over HTTP for remote MCP clients — no local install needed
- Protocol version: `2024-11-05`; 31 tools exposed
- Both transports' `ToolManager` is built with the same process-wide `get_pricing_aggregator()` singleton the REST API uses (see "Singleton Services" above) — one pricing cache per process shared by every surface, not one per transport. Cross-process sharing (STDIO process vs. the HTTP/REST process) is out of scope; each process still gets its own aggregator.
- The 3 confirmed duplicate cost-math pairs (`/cost-estimate` ↔ `estimate_cost`, `/cost-estimate/batch` ↔ `compare_costs`, `/usage` ↔ `record_usage`) now call the same `cost_calculator.compute_token_cost()` instead of each re-implementing the arithmetic inline.
- **Caller plumbing (v1.79.1)**: `ToolManager.execute_tool(name, arguments, caller=ANONYMOUS)`. A tool without a `caller_access` attribute is `PUBLIC` and is called exactly as before (`execute(arguments)`, never sees the caller). The seven usage/budget tools set `caller_access = ORG_SCOPED`; `ToolManager` runs `mcp.caller.authorize()` first (anonymous → `customer_key_required`; customer naming another org → `org_mismatch`; operator → unrestricted) and then calls `execute(arguments, caller=caller)`, where `effective_org_id()` forces a customer onto its own org (list/delete budget alerts pass `org_id` to the existing service-layer filters). `OPERATOR_ONLY` (v1.79.2; `list_price_alerts`, `delete_price_alert`, `list_conversations`, `delete_conversation`) admits only the operator and refuses everyone else with a constant `operator_key_required`. `CUSTOMER_ONLY` (customer only; operator refused) is used by the v1.80.0 `dispatch` tool, which also declares `access_refusal()` — `ToolManager` substitutes it for the generic `customer_key_required` so anonymous and operator callers get HTTP's `dispatch_requires_api_key` (`org_mismatch` passes through) — and `redact_errors` (an unexpected exception's text is never echoed). Only HTTP `/mcp` ever passes a caller; STDIO and the PricingAgent use the two-argument form and are anonymous. `tool.execute(arguments)` with no caller remains unrestricted for in-process unit tests, and a guard test asserts nothing outside `mcp/tools/` constructs those seven classes.
- **Transport-aware ToolManager (v1.80.0)**: `ToolManager(aggregator, transport='http'|'stdio')`; `mcp/server.py` builds it with `stdio`. For a non-PUBLIC tool that declares `stdio_refusal()` (dispatch), STDIO returns that structured refusal (`dispatch_requires_hosted_mcp`, pointing at the hosted `/mcp` or the npx bridge with `LLM_PRICING_API_KEY`) before authorising. `/mcp` is unchanged: dispatch is non-PUBLIC, so the caller resolves lazily; the 1 MB body cap applies; MCP dispatch is throttled only by the per-customer bucket it shares with HTTP dispatch.
- **Third access path** (`npm/llm-pricing-mcp`, published to npm as `llm-pricing-mcp`): a zero-dependency Node.js STDIO<->HTTP bridge for clients that only support `command`-based STDIO servers but not a direct remote/HTTP `url` config. It holds no tool logic or pricing data of its own — it forwards each stdin line to `POST /mcp` and writes the JSON-RPC response back to stdout — so it has nothing to keep in sync as tools or pricing change server-side. `tests/test_npm_wrapper.py` exercises it against `npm/llm-pricing-mcp/test/mock-server.js`, never the live server, so these tests need no network access. Since npm 1.1.0 (v1.79.1) it forwards the optional `LLM_PRICING_API_KEY` env var as the `x-api-key` header (omitted when unset) and never writes it to stdout/stderr.

### 5. Agent Architecture (ReAct Loop)
```
User message
  → react_loop.py: think → select tool → execute → observe → repeat
  → tools.py: wraps 27 of the 31 MCP tools as callable Python functions
      (excludes ask_agent to prevent recursion, get_telemetry/get_cache_stats as server-ops only, and
       dispatch (v1.80.0) — a real, paid model call on a customer's keys)
  → llm_backend.py: AnthropicBackend | OpenAIBackend (switch via AGENT_LLM_PROVIDER env)
  → conversation.py: persist turns to SQLite, enforce max_turns limit (v1.81.0: alert_tokens redacted
      from stored user/assistant turns; the live reply keeps them)
  → SSE stream: events [start, thinking, tool_call, tool_result, …, answer, done]
```

The agent is an anonymous MCP caller (v1.79.1): its bindings call `ToolManager.execute_tool(name, args)` with
no caller, so the seven org-scoped usage/budget tools stay bound (27 of the 31 MCP tools) but refuse at runtime
with `customer_key_required` — `/agent/chat` and `ask_agent` can never read, write or delete any org's data.
Likewise the four operator-only tools (v1.79.2) stay bound but refuse with `operator_key_required`, so the agent
can never list or delete price alerts or conversations. It can view or delete one price alert through the
public `manage_price_alert` (v1.81.0) when the user supplies that alert's `alert_token`.

### 6. Stripe Billing (optional)
If `STRIPE_SECRET_KEY` is not set, checkout/portal/webhook endpoints return `503`. Free-tier signup and API key issuance always work without Stripe:
```
POST /billing/signup (email) → create customer row → return secrets.token_urlsafe(32) API key
POST /billing/checkout      → Stripe checkout session → redirect URL
POST /billing/webhook       → verify HMAC sig → update tier in billing.db
```
Tier changes propagate to rate limits automatically: next request reads tier from `billing.db`.

### 7. Local Currency (v1.76.0)
```
request ──► resolve_request_locale (FastAPI dependency on the 6 pricing endpoints + /locale)
              currency query > X-Currency > country query > X-Country
              > platform geo header (CF-IPCountry, X-Vercel-IP-Country, CloudFront-Viewer-Country, X-Country-Code)
              > IP lookup (settings.currency_detection_enabled; reuses the telemetry middleware's
                request.state.geo_country_code — the middleware geolocates _client_ip_for_geo():
                Fly-Client-IP > XFF first hop > X-Real-IP > peer, public addresses only, so a
                client-prepended XFF hop cannot steer it — else GeolocationService with a 1s timeout) > USD
              │  unsupported explicit currency -> 400 {error: unsupported_currency, supported_currencies: [13]}
              ▼
          currency.resolve_locale() -> LocaleContext (pure; country mapped via COUNTRY_TO_CURRENCY;
              unmapped -> USD + fallback_to_usd/country_not_mapped; missing table -> rates_unavailable)
              │  ctx.active == currency != USD or a country was involved or fallback_to_usd
              ▼
          localize_metrics / localize_cost / localize_use_case_cost on per-request model_copy()s
              -> additive local_prices / local_cost / local_cost_range + currency_conversion
              (omitted entirely when inactive: Field(None, exclude_if=lambda v: v is None))
```
Rates: `data/exchange_rates.json` (USD base, 13 codes, `as_of`, `source`, `placeholder`), loaded once (lru-cached, `reset_rate_table_cache()` for tests), stale after 7 days but still served. MCP tools (`get_all_pricing`, `estimate_cost`, `compare_costs`, `get_use_cases`, `recommend_model`) call `mcp/tools/_locale.py::resolve_tool_locale(arguments)` with explicit `currency`/`country` only — no detection on STDIO or HTTP MCP. Caching: every response of the six endpoints (active or not — the plain USD variant too, so a shared cache never serves it to a header-driven request) sends `Vary: X-Currency, X-Country, CF-IPCountry, X-Vercel-IP-Country, CloudFront-Viewer-Country, X-Country-Code`; a *detected* locale (IP or platform header) is `Cache-Control: private` on every endpoint (`private, max-age=300` on `/pricing`, whose explicit query-param variants stay `public, max-age=300`). The weekly refresh's >15% move check is skipped while the previous table is the authored placeholder (it was never a market rate), so the first real refresh cannot be blocked by a hand-guessed value. Non-goals held: no model filtering/reordering by country, no regional prices, no localized billing or history, no IP/city/coordinates anywhere in a response. Settings: `CURRENCY_DETECTION_ENABLED`, `GEO_LOOKUP_TIMEOUT_SECONDS`, `EXCHANGE_RATES_PATH`.

### 8. Dispatch Gateway (v1.77.0; streaming v1.78.0)
```
POST /v1/chat/completions  {model, messages, max_tokens?, stream?, …, dispatch: {policy}}
  headers: x-api-key | Authorization: Bearer <service key>;  X-Provider-Key-<Provider>: <caller's key>
  │  no `dispatch`            -> today's recommendation-only proxy, unchanged (tests/test_proxy.py); `stream` ignored
  │  tools / functions / tool_choice / n>1 / response_format / non-string content -> 400 unsupported_feature (before any work)
  │  no customer (anonymous or global key)                          -> 401 dispatch_requires_api_key
  │  (v1.80.0: these checks + rate limit/locale/key resolver are dispatch_entry.prepare_and_dispatch(), also
  │   called by the MCP `dispatch` tool with the connection's customer and an empty header resolver = stored keys only)
  ▼
DispatchService.dispatch()  /  dispatch_stream() when `stream: true`  (both start with _prepare(), steps 1-5)
  1. BudgetAlertService.find_hard_stop(org_id)     -> 402 budget_hard_stop (org-scoped hard_stop alerts only)
  2. aggregator.get_all_pricing_async(include_unconfirmed=True) + enrich_models   (unconfirmed: reportable, never dispatched)
  3. eligibility stages, fixed order, first failing reason per model:
       catalogue (`_catalogue_reason`, shared with GET /dispatch/providers: subscription_pricing / price_unconfirmed
       (covers retired) / provider_not_executable) > no_provider_key
       > denied_by_policy > not_in_allow_list > router hard filters (below_quality_floor / above_cost_ceiling /
       context_window_too_small / task_type_mismatch / reasoning_model_excluded) > over_request_cost_cap
     the stage that empties the set names the error:  400 no_eligible_candidate {eliminating_constraint, excluded, excluded_summary}
     pinned: the named model must survive every stage, else 400 pinned_model_ineligible
  4. ModelRouter.rank_models(eligible, constraints)  (router order = "balanced"), then the objective re-orders:
       cheapest = blended $/1M asc · best_quality = quality desc · lowest_latency = latency asc, None last · pinned = [model]
  5. per-request cap: count_tokens(prompt) x input price + (max_tokens or DISPATCH_DEFAULT_MAX_TOKENS) x output price;
     when a cap is set that same max_tokens is sent to the provider (OpenAI as max_completion_tokens) so the cap is
     enforceable, not advisory; with a shadow the cap applies to primary + shadow combined (shadow skipped + reported)
  6. failover loop (asyncio.wait_for per attempt; max_attempts, 1 when failover off or pinned):
       ok -> stop · timeout / rate_limited / server_error / model_not_found -> next candidate
       context_too_long -> only candidates with a *larger* context window
       auth_error (401/403 on the caller's key) -> 400 provider_auth_failed, terminal · provider_rejected -> 502, terminal
       exhausted -> 502 all_attempts_failed {attempts, eligible_remaining, eliminating_constraint}  (never downgrades below the floor)
     shadow (optional): asyncio.gather with the primary, single attempt, its text never returned, its failure never fails
     the request; the primary loop skips the shadow's model so failover never bills the same model twice
     executors: OpenAI itself gets max_completion_tokens (its reasoning models reject max_tokens) and, for a catalogue
     is_reasoning_model, no temperature/top_p (they reject non-default sampling); other OpenAI-wire hosts keep max_tokens
  7. ledgers (after a successful primary): usage_tracker.record_event(is_estimated=False, request_id dedupes) ·
     savings_tracker.record_routing(routing_id=dispatch_id) + record_feedback(was_used=True) ·
     budget_alerts.check_and_fire() · dispatch_receipts.record(row)   (shadow spend is recorded too — real money)
  8. response: chat.completion {id chatcmpl-<dispatch_id>, model, choices[0], usage (real)} + routing_metadata
     {actual_forwarding: true, dispatch_id} + dispatch_receipt {chosen, reason, candidates, excluded, excluded_summary,
     usage {…, estimated}, cost, savings{vs_most_expensive_eligible, vs_requested_model, forgone_vs_cheapest_meeting_floor},
     latency, attempts, shadow, streamed, local + currency_conversion (only when currency/country stated — no IP detection here)}
  9. streaming (`stream: true`, v1.78.0) — same steps 1-5, then dispatch_stream():
       handler pulls the FIRST event before building the response -> every pre-provider refusal and every exhausted
       failover is the identical JSON error (no SSE bytes); only a committed attempt becomes a StreamingResponse
       (text/event-stream, Cache-Control: no-cache, X-Accel-Buffering: no — same as /router/recommend/stream)
       executor.stream(): client.stream(POST) -> non-2xx classified by the same table BEFORE the first yield ->
         _iter_sse -> per-family parse_stream_event -> StreamEvent(delta | finish | usage)
         (OpenAI-wire: stream + stream_options.include_usage · Anthropic: stream on /v1/messages, input tokens from
          message_start, output from message_delta · Gemini: :streamGenerateContent?alt=sse, last usageMetadata wins)
       attempt commits at its first delta (or a finish with no content): before that the failover rules of step 6
         apply unchanged (timeout = `failover.timeout_ms` to first content; the failed stream is aclose()d first);
         after it there is NO failover — the per-event idle timeout, a provider error or EOF-without-finish ends the
         stream with `data: {"error": {code/outcome, dispatch_id, model, provider, http_status, attempts}}` (no [DONE])
       relay (src/main.py::_relay_dispatch_stream): each delta -> chat.completion.chunk immediately (finish_reason null;
         role on the first); then one empty-delta chunk with finish_reason; then one chunk with choices: [] + usage +
         routing_metadata + dispatch_receipt (OpenAI's include_usage shape); then `data: [DONE]`
       usage: provider counts when the stream carried them, else count_tokens(prompt) / count_tokens(streamed text)
         with receipt.usage.estimated = true and the usage event is_estimated = 1
       shadow: asyncio task started with the primary, non-streaming, single attempt; deltas never wait for it, the
         trailing receipt does (bounded by its own attempt timeout); its spend is recorded as before
       client disconnect: GeneratorExit/CancelledError at a yield -> shielded settlement task (_settling, wait_settled())
         aclose()s the provider stream, awaits the shadow, writes an estimated usage event for the tokens delivered so
         far and a failed receipt row whose attempt outcome is `client_disconnected`
       receipt row: streamed = 1, usage_estimated = 0/1 (columns appended at the end; ALTER TABLE migration on open)
```
Executors: one `httpx.AsyncClient` per attempt (timeout = `failover.timeout_ms`), three wire families
(`openai_wire` for OpenAI/Groq/Together/Fireworks/DeepSeek/Mistral/xAI, `anthropic_messages`, `gemini_rest`),
catalogue `model_name` sent verbatim as the provider model id, `httpx.MockTransport` as the test seam (an
`httpx.Response` with an SSE byte body streams through `client.stream()` / `aiter_lines()` unchanged). Why not the
pinned SDKs: both auto-retry 429/5xx and would fight the failover loop and its timeout budget, and no Gemini SDK is
pinned. Settings: `DISPATCH_ENABLED` (kill switch → 503), `DISPATCH_DEFAULT_MAX_TOKENS`, `DISPATCH_ATTEMPT_TIMEOUT_MS`,
`DISPATCH_MAX_ATTEMPTS`, `DISPATCH_RECEIPT_RETENTION_DAYS`. Non-goals held: no pooled/service-paid keys, no
mid-stream failover or resume, the shadow and non-text (reasoning / tool-call) deltas are never streamed, the plain
proxy keeps ignoring `stream`, no tool-calling translation, no MCP tool.

#### Stored provider keys (v1.79.0)

```
POST   /dispatch/keys {provider, api_key}  -> 201 {provider, slug, fingerprint, created_at, rotated_at, last_used_at, status}
                                              409 provider_key_exists · 422 unsupported_provider (+accepted_providers)
                                              422 invalid_provider_key (empty / >512 / non-printable-ASCII) · 400 invalid_json
GET    /dispatch/keys                      -> 200 {keys: [...], count}   (metadata only; status active | needs_reregistration)
PUT    /dispatch/keys/{slug} {api_key}     -> 200 info (new fingerprint, rotated_at; last_used reset) · 404
DELETE /dispatch/keys/{slug}               -> 204 · 404       (another org's key = a missing key = 404)
  every call: no customer -> 403 customer_key_required; no usable secret -> 503 stored_keys_unavailable
dispatch key resolution (src/services/dispatch_entry.build_key_resolver, shared by HTTP + MCP dispatch):
  HeaderKeyResolver(headers) -> store initialized + available + some provider not header-keyed?
    -> store.load_for_dispatch(org_id, exclude=header providers)   (a losing stored key is never decrypted;
       undecryptable rows skipped with a warning naming org + provider only)
    -> CompositeKeyResolver(header, stored)   — any store error falls back to header-only, never fails dispatch
  no_provider_key stage: keyed = header OR stored;  every Attempt carries key_source = resolver.source(provider)
  note_used(provider) before each provider call (primary, failover, streaming, shadow); flush_usage() after the
  receipt row is written (_finish, _fail, mid-stream failure / disconnect settlement) ->
    UPDATE provider_keys SET last_used_at = MAX(COALESCE(last_used_at,0), ?) WHERE org_id=? AND provider=? AND key_version=?
```
Crypto: working key = HKDF-SHA256(length 32, salt None, info `llm-pricing/provider-keys/v1`) over the base64url-decoded
`PROVIDER_KEY_ENCRYPTION_KEY` (>= 32 bytes, else the store is unavailable and one startup ERROR names the setting);
blob = `0x01` ‖ 12-byte random nonce ‖ AES-GCM(ciphertext ‖ tag), AAD = `provider-key:v1\0` ‖ org_id ‖ `\0` ‖ provider,
so a row copied to another org or provider fails authentication. Decryption is all-or-nothing (`None` on a wrong
version byte, short blob, `InvalidTag` or bad UTF-8). No server-secret rotation: changing the secret makes every stored
key `needs_reregistration` (rotate = re-register). `key_source` rides inside `attempts_json`, so the receipts table,
its CSV column order and the export endpoints are unchanged. Plain proxy and router endpoints never read stored keys.
Non-goals: pooled keys, more than one key per provider per org, per-user permissions, stored keys for recommend-only
providers, provider calls at registration, operator access to a stored key, browser UI, MCP tool.

**Webhook delivery path (v1.80.1).** Price alerts fire from the background snapshot loop; budget alerts fire inline
from `POST /usage`, `POST /usage/batch`, `record_usage`, `import_session_usage` and dispatch settlement — every site
already wraps `check_and_fire` in `try/except`. Each `_fire` first runs `webhook_guard.check_url_static(stored_url)`
(a legacy row that breaks the rules is logged as `delivery blocked (<reason>)` and returns 0), then posts through
`httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False, transport=PinnedWebhookTransport())`.
The transport resolves the host, refuses if any address is blocked, rewrites the request URL to the first checked
IP and sets the `sni_hostname` extension (Host header and TLS certificate check stay on the original hostname), so
DNS rebinding between check and connect cannot reach a private address. A 3xx counts as delivered and is never
followed. Payload bytes and the `X-LLM-Pricing-Signature` HMAC are unchanged.

### 9. UI Design System
All 14 browser UIs share the same CSS custom properties (defined in each page's `:root`):

| Variable | Value |
|----------|-------|
| `--bg` | `#0f1117` |
| `--surface` | `#1a1d27` |
| `--surface2` | `#22263a` |
| `--border` | `#2e3347` |
| `--accent` | `#7c6af7` |
| `--accent2` | `#4ecdc4` (most pages; `/whats-new` uses `#5b4fe0`, billing/conversations/chat omit it) |
| `--text` | `#e2e8f0` |
| `--muted` | `#8892a4` |
| `--success` | `#48bb78` |
| `--warn` | `#ed8936` |
| `--danger` | `#f56565` |
| `--radius` | `10px` |

All use `'Segoe UI', system-ui, sans-serif`, `font-size: 14px`, sticky `.main-nav` (52px), hamburger at 760px breakpoint. **Admin page is the canonical design reference.**

**Shared currency control (v1.76.0)**: `/calculator`, `/compare` and the landing page embed one identical `<script>` block (`/* llmp-currency v1 */ window.LLMPCurrency`) — `tests/test_currency_ui.py` compares the three copies. It stores the visitor's choice under the `llmp.currency` localStorage key (user choices only; a detected default is re-resolved per load via `GET /locale`), fetches `GET /currency/rates` once, converts the pages' USD payloads client-side and formats with `Intl.NumberFormat` (JPY/KRW without minor units; per-token rates as unit-less numbers plus the code). The landing renders the selector and note only (OWNER DECISION: no converted numbers there).

**Shared navigation (v1.75.1)**: every page embeds the same nav block — markup, CSS and toggle script — with eight items (Calculator, Compare, Trends, Chat, MCP, API, What's New, Sign in); Admin, Widget, History, IDE Compare and Conversations are reached from page content or by URL, not from the bar. The pages are self-contained static files, so the block is copied rather than included: `scripts/sync_nav.py` holds the one canonical nav (links, CSS, toggle script) and stamps it into every page (`--check` reports drift), and `tests/test_nav_consistency.py` fails if any copy differs from the script's constants. Change the nav by editing the script and running it, never by editing a page's nav by hand.

---

## Data Stores

| Database | File | Tables | Purpose |
|----------|------|--------|---------|
| Pricing history | `pricing_history.db` | `pricing_snapshots`, `routing_savings`, `routing_feedback`, `pricing_alerts` (`token_hash`, v1.81.0), `usage_events` (nullable `session_id`, `is_estimated` flag), `budget_alerts` (`hard_stop` flag, v1.77.0), `dispatch_receipts` (v1.77.0) | Price snapshots, router feedback, actual usage/spend (optionally session-scoped, exact or text-estimated), spend-threshold webhooks (optionally a dispatch hard stop), dispatch receipt metadata — ids, model/provider, outcome, attempts, tokens, costs, savings, latencies, shadow summary; never messages, completions, provider error bodies, keys, IPs or headers; purged after `DISPATCH_RECEIPT_RETENTION_DAYS` (90) |
| Billing | `billing.db` | `customers`, `provider_keys` (v1.79.0) | API keys, tiers, Stripe IDs; per-org stored provider keys — AES-256-GCM ciphertext (never plaintext), last-4 fingerprint, key_version, created/rotated/last-used times; one row per (org_id, provider). On the Fly volume via `BILLING_DB_PATH`; encrypted under `PROVIDER_KEY_ENCRYPTION_KEY` |
| Conversations | `CONVERSATION_DB_PATH` (optional; in-memory when unset — `fly.toml` does not set it) | `conversations` (one row per conversation; turns as JSON in `messages`) | Agent conversation memory |

Both `.db` files are gitignored and live on the Fly.io persistent volume (`/app/data`, 1 GB).

---

## API Endpoint Map

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health`, `/health/live`, `/health/ready`, `/health/detailed` | None | Health probes |
| GET | `/pricing` | None | All model pricing; supports `provider`, `supports_vision`, `supports_function_calling`, `supports_json_mode`, `batch_available`, `is_reasoning_model` query filters; `currency` / `country` (or `X-Currency` / `X-Country`) add `models[].local_prices` + `currency_conversion` (v1.76.0) |
| GET | `/data-quality` | None | Aggregate accuracy summary: % confirmed/fresh, models withheld for drift, unpriced, stale |
| GET | `/models` | None | Model list |
| GET | `/pricing/public` | None | Embed-safe public pricing; `currency` / `country` add `*_per_1m_local`, `local_currency`, `currency_conversion` |
| GET | `/locale` | None | Currency/country the request resolves to + `source` + the 13 supported codes (browser preset; `no-store`; never echoes an IP) |
| GET | `/currency/rates` | None | Vendored USD rate table: `as_of`, `rate_age_days`, `stale` (7-day rule), `source`, `placeholder`, `zero_decimal` |
| GET | `/pricing/history` | None | Historical price snapshots |
| GET | `/pricing/trends` | None | Price-change leaderboard |
| GET | `/pricing/subscription-trends` | None | IDE/subscription tools whose monthly price changed the most |
| GET | `/pricing/history/export` | None | CSV / JSON export |
| GET | `/pricing/alerts/signing-info` | None | Webhook HMAC signing key info |
| GET | `/performance` | None | Performance metrics |
| GET | `/use-cases` | None | Use-case recommendations; `currency` / `country` add `models[].local_cost` (set, order and USD-band `cost_tier` unchanged) |
| POST | `/cost-estimate` | None (rate-limited, body-capped; a customer key gets its tier limit; v1.82.0) | Single-model cost; `currency` / `country` add `local_cost` + `currency_conversion` |
| POST | `/cost-estimate/batch` | None (rate-limited, body-capped; a customer key gets its tier limit; v1.82.0) | Multi-model cost comparison, 1-50 models (`MAX_COMPARE_MODELS`, shared with `compare_costs`); `currency` / `country` add `models[].local_cost`, `local_cost_range` (cheapest/most-expensive stay USD-derived) |
| GET | `/rate-limits/tiers` | None | Tier rate limits |
| GET | `/api/versions` | Required | API version negotiation |
| GET | `/telemetry` | None | Request telemetry |
| GET | `/cache-stats` | None | Process-lifetime hit/miss counts + hit rate for the recommendation cache, per call site (`recommend_model`, `router_recommend`) |
| POST | `/router/recommend` | Required | LLM routing recommendation (~5min in-memory cache; response `cached` field); `currency` / `country` add `local_prices` on `recommended` and `alternatives[]` (constraints stay USD) |
| POST | `/router/recommend/stream` | Required | SSE streaming router |
| POST | `/router/feedback` | Required | Accept/reject feedback |
| GET | `/telemetry/savings` | Required | Per-org savings stats |
| GET | `/telemetry/ide-savings` | Required | Routing decisions, savings and acceptance rate broken down by IDE context |
| POST | `/usage` | Required | Record one actual LLM usage event (cost computed server-side) |
| POST | `/usage/batch` | Required | Record multiple usage events at once |
| GET | `/usage/summary` | Required | Actual spend summary by model/provider, per-org, plus an org-wide exact-vs-estimated breakdown (`estimated_request_count`, `has_estimated_usage`) |
| GET | `/usage/session/{session_id}` | Required | On-demand analysis of one session's actual usage: totals (with an exact-vs-estimated breakdown), per-model breakdown, and a grounded model recommendation with estimated savings |
| POST | `/usage/alerts` | Required | Register a webhook for spend crossing a USD threshold; `hard_stop: true` (v1.77.0) also refuses dispatch for `org_id` once reached. A customer key's alert is bound to its own org (another org -> 403 `org_mismatch`); `hard_stop` requires a customer key (403 `customer_key_required` otherwise). v1.80.1: the URL must be public https — otherwise 422 `invalid_webhook_url` with a `reason` |
| GET | `/usage/alerts` | Required | List registered budget alerts (a customer key sees only its own org's) |
| DELETE | `/usage/alerts/{id}` | Required | Delete a budget alert (a customer key can only delete its own org's) |
| POST | `/agent/chat` | None | Blocking agent response |
| POST | `/agent/chat/stream` | None | SSE agent stream |
| GET | `/agent/conversations` | Operator key (`MCP_API_KEY`, x-api-key) | List agent conversations (v1.79.2: operator only; 401 otherwise, 503 if unset) |
| DELETE | `/agent/conversations/{id}` | Operator key (`MCP_API_KEY`, x-api-key) | Delete a conversation (v1.79.2: customer keys no longer suffice; 404 unknown id) |
| POST | `/v1/chat/completions` | None (plain) / customer key in-handler (dispatch mode) | OpenAI-compatible proxy; with a `dispatch` body object (v1.77.0) executes the best eligible model with the caller's `X-Provider-Key-*` and returns real `usage` + `dispatch_receipt`; with `stream: true` as well (v1.78.0) the completion is OpenAI `chat.completion.chunk` SSE with usage + receipt in a trailing chunk before `[DONE]` |
| GET | `/dispatch/providers` | None | Executable providers (key header and, v1.79.0, slug + `stored_key_path` each; nothing per-org), which catalogue models are dispatchable and why not (`?provider=`, `?dispatchable_only=`) |
| GET | `/dispatch/receipts` | Required customer key | Metadata-only JSON/CSV export of the caller's org's dispatch receipts (`format`, `since`, `until`, `outcome`, `limit`); global key → 403 |
| POST | `/dispatch/keys` | Required customer key | v1.79.0: store a provider key for the caller's org (`{provider, api_key}`; 201 metadata only, 409 exists, 422 unsupported provider / invalid key); global key → 403; 503 without `PROVIDER_KEY_ENCRYPTION_KEY` |
| GET | `/dispatch/keys` | Required customer key | v1.79.0: the org's stored keys — provider, slug, fingerprint (last 4), created/rotated/last-used, status; never a key value |
| PUT | `/dispatch/keys/{provider}` | Required customer key | v1.79.0: rotate (`{api_key}`; provider = slug or display name); 404 if the org stores none |
| DELETE | `/dispatch/keys/{provider}` | Required customer key | v1.79.0: delete (204); 404 if the org stores none |
| GET | `/mcp` | None | MCP server info |
| POST | `/mcp` | None; optional service key (`x-api-key` / `Bearer`) for the 7 org-scoped tools and `dispatch`; operator key for the four operator-only tools | MCP HTTP transport (JSON-RPC 2.0). v1.79.1: a customer key scopes the usage/budget tools to its own org; the global key is the operator; anonymous calls to those tools are refused (`customer_key_required`); v1.79.2: alert/conversation list/delete need the operator key (`operator_key_required` otherwise); body capped at 1 MB before parsing (413); v1.80.0: `dispatch` runs a real gateway call for the key's customer on its stored provider keys (`dispatch_requires_api_key` for anonymous/operator; shares the per-customer dispatch rate-limit bucket); the 19 public tools never need a key |
| POST | `/billing/signup` | None | Free-tier signup |
| GET | `/billing/me` | Required | Customer dashboard |
| POST | `/billing/checkout` | Required | Stripe checkout |
| GET | `/billing/portal` | Required | Stripe billing portal |
| POST | `/billing/webhook` | Stripe sig | Subscription sync |
| POST | `/pricing/alerts` | None (rate-limited) | Register price-change webhook (v1.79.2: per-IP anonymous throttle + body cap; v1.80.1: public https URL only, else 422 `invalid_webhook_url` with a `reason`) |
| GET | `/pricing/alerts` | Operator key (`MCP_API_KEY`, x-api-key) | List alerts (v1.79.2: operator only) |
| DELETE | `/pricing/alerts/{id}` | Operator key (`MCP_API_KEY`, x-api-key) | Delete alert (v1.79.2: operator only; 404 unknown id) |
| POST | `/pricing/alerts/self/view` | None (rate-limited; `alert_token` in body) | v1.81.0: view the one alert the token manages; any non-match → 404 `invalid_alert_token` (identical body) |
| POST | `/pricing/alerts/self/delete` | None (rate-limited; `alert_token` in body) | v1.81.0: delete the one alert the token manages; any non-match → 404 `invalid_alert_token` (identical body) |
| GET | `/docs` | None | Swagger UI (OpenAPI) |
| GET | `/redoc` | None | ReDoc (OpenAPI) |
| GET | `/openapi.json` | None | OpenAPI spec (JSON) |
| GET | `/billing` | None | Billing SPA |
| GET | `/admin` | None | Admin SPA |
| GET | `/admin/stats`, `/admin/rate-limits` | Operator key (`MCP_API_KEY`, x-api-key) | Admin dashboard data: aggregate telemetry/provider health; top rate-limit consumers by IP or customer id (v1.81.4: operator only — previously allow-listed) |
| GET | `/admin/customers` | Operator key (`MCP_API_KEY`, x-api-key) | Every customer's email, org_id and Stripe id (v1.78.1: operator only) |
| GET | `/deployment/metadata`, `/deployment/info` | Required | Version/API-versioning metadata; blue-green environment info |
| POST | `/deployment/shutdown` | Operator key (`MCP_API_KEY`, x-api-key) | Start graceful shutdown |
| GET | `/deployment/shutdown/status` | Operator key (`MCP_API_KEY`, x-api-key) | Graceful-shutdown status |
| GET | `/chat`, `/calculator`, `/compare`, `/history`, `/trends`, `/widget`, `/mcp-setup`, `/api-docs`, `/whats-new`, `/conversations` | None | Browser UIs (static mounts; `/conversations` asks for the operator key in-page) |
| GET | `/ide-compare` | None | IDE comparison UI (static mount; v1.81.4: added to the static-UI bypass, it previously returned 401 when `MCP_API_KEY` was set) |

---

## CI/CD Pipeline

```
PR to master or develop / push to master
  ├── test        pytest (full suite, coverage report)
  ├── lint        flake8 (syntax errors + undefined names)
  ├── osv_scan    Google OSV Scanner (dependency CVEs)
  ├── security    bandit (Python SAST)
  └── secret_scan gitleaks (full git history scan for leaked credentials)
            │
            └─ all pass? ──► deploy_fly   Fly.io (flyctl deploy --remote-only)
                                  │
                                  └─ health check: /health version matches src/__init__.py

push to master touching npm/llm-pricing-mcp/**   (or workflow_dispatch)
  └── publish     npm Trusted Publishing — OIDC via `id-token: write`, no NPM_TOKEN secret
        ├── skip if package.json's version is already on the registry (idempotent; a README-only
        │   change to the package does not fail the run)
        ├── npm publish --provenance
        └── verify: `npm view llm-pricing-mcp@<version>` resolves

push to master touching server.json   (or workflow_dispatch)
  └── publish     MCP Registry — OIDC via `id-token: write`, `mcp-publisher login github-oidc`
        ├── skip if server.json's name+version is already on the registry (idempotent)
        ├── wait (≤10 min) until each npm package named in server.json is readable on
        │   registry.npmjs.org with the right mcpName — npm-publish.yml runs concurrently
        ├── mcp-publisher validate && mcp-publisher publish
        └── verify: GET /v0.1/servers/{name}/versions/{version} returns 200
```

**npm publishing — one-time setup and the rules that bite.** `npm/llm-pricing-mcp` is versioned
independently of `src/__init__.py` (bump its own `package.json`). Publishing is authorised by an OIDC
trust relationship, configured once on npmjs.com: package → Settings → Trusted Publisher → GitHub
Actions, with owner `skakumanu`, repository `llm-pricing-mcp-server`, workflow filename
`npm-publish.yml`, environment blank. Three npm rules matter: (1) the workflow **filename** is the
trust key — renaming the file breaks publishing until npmjs.com is updated; (2) a trusted-publisher
config that has not yet produced a successful publish **expires 48 hours** after creation, so create
it and trigger the first publish (`workflow_dispatch` on master is fine) within the same window;
(3) npm rejects OIDC tokens from `issue_comment` and `pull_request_target` runs, and only
GitHub-hosted runners are supported. Never publish with a 2FA recovery code: since Sept 2026 that
places the account on a fixed 72-hour hold that blocks publishing and cannot be lifted early.
`tests/test_npm_publish_workflow.py` pins the filename, the `id-token: write` permission and the
banned-trigger rule.

**How the package was bootstrapped (historical, 2026-10-25).** A Trusted Publisher can only be attached
to a package that already exists on the registry, so the first-ever publish cannot use OIDC. `1.0.0`
was published once by a temporary, manual-only workflow (`npm-bootstrap-publish.yml`, removed in
v1.73.9) using a 7-day Granular Access Token held in a since-deleted `NPM_TOKEN` repository secret. The
Trusted Publisher was then configured on npmjs.com with the `npm publish` permission (npm now also
offers a staged-publish mode that requires website approval per release; this repo uses direct publish),
and `1.0.1` was published through it by `npm-publish.yml`, which validated the config permanently. There
is no token-based publish path in this repository anymore; if the package ever has to be re-created
under a new name, repeat that sequence rather than adding a standing token. npm accepts an upload and
then processes it asynchronously (a new version took ~90s to become readable), which is why the publish
workflow's verification loop waits up to 10 minutes.

**Node version floor.** Both publish workflows pin `actions/setup-node` to an exact Node release with
`check-latest: true`, and `npm/llm-pricing-mcp/package.json` sets `engines.node` to the oldest patched
line, so neither the publish runner nor an end user's bridge runs on a Node build missing a known
security fix (floor set by the October 2026 Node.js security release: 22.23.4 / 24.22.1 / 26.12.1).
`tests/test_node_version_pins.py` fails if either floor is lowered or the pin becomes a bare major
again. The Fly.io image contains no Node at all. Raise the floors together when Node ships the next
security release.

**MCP registry publishing — `server.json`.** A second, independent listing: `registry.modelcontextprotocol.io`,
the directory most MCP clients query directly, not just a browsing site. `server.json` at the repo
root lists two ways to run the server: a `remotes` entry pointing at the hosted `/mcp` endpoint
above, and (since v1.73.10) an npm `packages` entry for `llm-pricing-mcp`, the STDIO bridge, with
`runtimeHint: npx`, `transport: stdio` and its two optional env vars. Authorised the same way as
npm: `id-token: write` → `mcp-publisher login github-oidc`, no secret. The registry's `name` field
(`io.github.skakumanu/llm-pricing-mcp-server`) must stay prefixed with `io.github.skakumanu/` —
that's what ties the OIDC identity to the publish permission — and its `description` is capped at
100 characters, enforced at publish time with no local warning otherwise.

The npm package entry couples three values that must move together: the registry verifies ownership
by fetching `registry.npmjs.org/llm-pricing-mcp/<version>` for the exact `version` named in
`server.json` and requiring that package.json's `mcpName` to equal `server.json`'s `name`. So a bridge
release is: bump `npm/llm-pricing-mcp/package.json` **and** `server.json`'s package `version` (and
`server.json`'s own top-level `version`, which must be new for the registry to accept it) in one PR.
On the master push, `npm-publish.yml` and `mcp-registry-publish.yml` start concurrently; the registry
workflow waits up to 10 minutes for the npm version to become readable (npm processes uploads
asynchronously) before publishing. `tests/test_mcp_registry_publish_workflow.py` pins the name
prefix, the description cap, the schema URL shape, the package identifier/version coupling, the
`mcpName` marker, and the presence of the wait step.

---

## When to Update This Document

Update `docs/ARCHITECTURE.md` whenever any of the following change:

- New service added to `src/services/` or `agent/`
- New endpoint group or authentication change in `src/main.py`
- New database table or schema change
- New browser UI page added to `static/`
- New external dependency (provider, Stripe, etc.)
- Deployment target added or removed
- CI/CD pipeline job added or removed
- Design system token change (CSS variables)

Minor changes (bug fixes, style tweaks, test additions) do **not** require an architecture update.
