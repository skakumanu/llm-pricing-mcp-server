# Architecture — LLM Pricing MCP Server

**Version**: v1.73.9 | **Last updated**: 2026-10-25

---

## System Overview

A production FastAPI service that aggregates real-time LLM pricing data from 26 providers (150+ models), exposes it via REST API and MCP protocol, and layers on a ReAct agent, self-serve SaaS billing, and a suite of browser UIs.

- **Primary deployment**: Fly.io (`llm-pricing-api.fly.dev`) — shared-cpu-1x, 512 MB, ~$3.40/mo
- **CI/CD**: GitHub Actions → test → lint → bandit → OSV scan → gitleaks → Fly.io deploy
- **Weekly price audit**: a drifted price is withheld from serving the moment it's detected (on every request, not just weekly); the scheduled workflow re-runs `check_price_drift` to report what's currently withheld and refreshes the vendored registry snapshot, opening an issue/PR when either drifts
- **Snapshot self-validation**: `scripts/validate_price_snapshot.py` gates the refresh PR. GitHub does not run CI on `GITHUB_TOKEN`-created PRs, so the generating job verifies the data itself
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
│  REST API              MCP (29 tools)          Browser UIs (13 pages)       │
│  /pricing              STDIO transport          /  /chat  /calculator        │
│  /router/recommend     HTTP POST /mcp           /compare  /history           │
│  /cache-stats          JSON-RPC 2.0             /trends   /widget            │
│  /billing/*            MCP 2024-11-05           /billing  /admin             │
│  /agent/chat                                    /mcp-setup  /api-docs        │
│  /v1/chat/completions                           /whats-new                   │
│                                                                             │
│  Security middleware: billing DB key → global MCP_API_KEY fallback          │
│  Rate limiting: per {ip}:{tier} token-bucket (30/120/600 req/min)           │
└──────────┬──────────────────────┬──────────────────────┬────────────────────┘
           │                      │                      │
┌──────────▼──────────┐  ┌───────▼────────┐  ┌─────────▼──────────────────┐
│  Pricing Services   │  │  Agent / RAG   │  │  Billing Service           │
│                     │  │                │  │                            │
│  PricingAggregator  │  │  ReAct loop    │  │  BillingService            │
│  26 provider impls  │  │  TF-IDF RAG    │  │  billing.db (SQLite)       │
│  PricingHistory     │  │  Conv. memory  │  │  Free signup → API key     │
│  BenchmarkService   │  │  LLM backend   │  │  Stripe checkout/webhook   │
│  Router             │  │  (GPT-4o-mini  │  │  Tier sync (free/pro/ent)  │
│  RecommendationCache│  │   or Anthropic)│  │                            │
│  SavingsTracker     │  └───────┬────────┘  └────────────────────────────┘
│  UsageTracker       │          │
│  BudgetAlerts       │          │
│  PricingAlerts      │          │
└──────────┬──────────┘          │
           │                     │
┌──────────▼─────────────────────▼────────────────────────────────────────────┐
│  Data Layer                                                                 │
│                                                                             │
│  pricing_history.db     billing.db          In-memory cache                │
│  price_history table    customers table     Aggregator (TTL)               │
│  routing_feedback       (api_key, tier,     Savings per org_id             │
│  usage_events table     org_id, stripe_id)  Alert callbacks                │
│                                             Recommendation cache (5min TTL)│
└──────────┬──────────────────────────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────────────────────────┐
│  External Services                                                          │
│  12 LLM provider APIs (pricing)   Stripe (checkout, webhooks, portal)      │
│  OpenAI GPT-4o-mini (agent LLM)   Hugging Face API (benchmark fallback)    │
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
│   │   ├── pricing.py               # PricingMetrics, PerformanceMetrics, RouterResponse, …
│   │   └── billing.py               # SignupRequest/Response, CheckoutRequest, CustomerDashboard
│   └── services/
│       ├── base_provider.py         # BasePricingProvider: build_metrics() shared builder, live sync, provenance
│       ├── pricing_aggregator.py    # Orchestrates + caches all provider data (async); also hosts the get_pricing_aggregator() process-wide singleton shared by REST and all MCP tools
│       ├── cost_calculator.py       # compute_token_cost(): single cost-math implementation shared by /cost-estimate(+batch)/usage and their estimate_cost/compare_costs/record_usage MCP-tool counterparts
│       ├── pricing_history.py       # SQLite price-history + routing_feedback tables
│       ├── benchmark_service.py     # Quality scores: static table + HF API fallback (24h TTL)
│       ├── router.py                # LLM routing recommendation engine
│       ├── recommendation_cache.py  # ~5min TTL cache for recommend_model / POST /router/recommend decisions + process-lifetime hit/miss stats (get_cache_stats)
│       ├── task_profiles.py         # 12 task I/O-ratio profiles + keyword task inference
│       ├── portfolio_optimizer.py   # Per-task model allocation + savings vs single-model baseline
│       ├── price_oracle.py          # External price registry: fills gaps, withholds drifted prices (24h TTL + snapshot). Same-provider variants resolve to the bare canonical key (regional/tier keys are different SKUs); majority vote only among equal-depth keys
│       ├── data_quality.py          # Aggregate accuracy summary shared by /data-quality and get_data_quality
│       ├── token_counter.py         # tiktoken token counting + prompt-cache savings math
│       ├── ide_pricing.py           # Subscription pricing for AI coding IDE tools
│       ├── savings_tracker.py       # Per-org router savings + acceptance_rate
│       ├── usage_tracker.py         # Actual LLM usage events (exact or text-estimated, `is_estimated` flag) + per-org spend summary + per-session lookup
│       ├── session_recommendation.py # Shared session-usage -> ModelRouter recommendation logic (GET /usage/session + analyze_session_usage)
│       ├── quality_tradeoff_caveat.py # Shared quality_tradeoff caveat (threshold + builder) used by recommend_model.py and session_recommendation.py
│       ├── budget_alerts.py         # Webhook alerts on actual spend crossing a USD threshold
│       ├── billing_service.py       # BillingService: customers table, Stripe sync
│       ├── pricing_alerts.py        # Webhook alert registration + delivery
│       ├── telemetry.py             # Request telemetry aggregation
│       ├── data_fetcher.py          # Async HTTP fetching helpers
│       ├── data_sources.py          # Static provider data fallbacks
│       ├── geolocation.py           # IP geolocation for rate-limit buckets
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
│       ├── anyscale_pricing.py
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
│       └── cloudflare_pricing.py
│
├── agent/
│   ├── pricing_agent.py             # High-level agent entry point
│   ├── react_loop.py                # ReAct (Reason + Act) loop implementation
│   ├── llm_backend.py               # AnthropicBackend + OpenAIBackend (switch via env)
│   ├── conversation.py              # SQLite conversation memory, turn limit
│   └── tools.py                     # 26 of 29 MCP tool bindings for agent use (+ RAG search)
│
├── mcp/
│   ├── server.py                    # MCP STDIO transport (Claude Desktop)
│   ├── schema_generator.py          # Generates mcp/schemas/ from the Pydantic models
│   ├── tools/                       # Per-tool MCP handler modules
│   └── schemas/                     # Generated MCP JSON-RPC schema definitions
│
├── static/
│   ├── index.html                   # / — marketing landing page
│   ├── landing/index.html           # /  (alias)
│   ├── admin/index.html             # /admin — server stats, rate limits, customers
│   ├── billing/index.html           # /billing — SaaS signup + upgrade SPA
│   ├── calculator/index.html        # /calculator — interactive cost calculator
│   ├── compare/index.html           # /compare — side-by-side model comparison
│   ├── history/index.html           # /history — Chart.js pricing history
│   ├── trends/index.html            # /trends — price-change leaderboard
│   ├── widget/index.html            # /widget — embeddable pricing table
│   ├── conversations/index.html     # /conversations — conversation history viewer
│   ├── mcp-setup/index.html         # /mcp-setup — MCP integration hub (5 client tabs, live test, all 29 tools)
│   ├── api-docs/index.html          # /api-docs — API reference (Swagger/ReDoc iframe + endpoint table)
│   └── whats-new/index.html         # /whats-new — release notes timeline (v1.35.0 → current)
│
├── .github/workflows/
│   ├── ci-cd.yml                    # Full CI/CD: test→lint→osv→bandit→gitleaks→deploy
│   ├── sync-develop.yml             # Auto-sync develop ← master after deploy
│   ├── price-audit.yml              # Weekly: surface withheld/drifted prices as an issue + open the registry-snapshot refresh PR
│   ├── npm-publish.yml              # npm Trusted Publishing (OIDC) of npm/llm-pricing-mcp on master push — filename is part of the npmjs.com trust config
│   └── mcp-registry-publish.yml     # Publishes server.json to registry.modelcontextprotocol.io via GitHub OIDC (`mcp-publisher login github-oidc`) on master push
│
├── server.json                      # Remote-only MCP registry listing (no packages/ownership-verification marker needed) — published by mcp-registry-publish.yml
│
├── npm/llm-pricing-mcp/              # Published to npm by .github/workflows/npm-publish.yml — see "MCP Dual Transport" below
│   ├── package.json
│   ├── bin/llm-pricing-mcp.js       # Zero-dependency STDIO<->HTTP bridge to the hosted /mcp endpoint
│   ├── test/mock-server.js          # Local stand-in for POST /mcp, used only by tests/test_npm_wrapper.py
│   └── README.md
│
├── tests/                           # 667 pytest tests (as of v1.48.0)
├── docs/
│   └── ARCHITECTURE.md              # THIS FILE — update on every structural change
├── fly.toml                         # Fly.io app config (volume, health checks)
├── Dockerfile                       # Container image
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
All `init_*` calls live in the `startup_pricing_history()` FastAPI lifespan handler. Services: `PricingAggregator`, `PricingHistory`, `BenchmarkService`, `SavingsTracker`, `UsageTracker`, `BudgetAlertService`, `BillingService`.

`PricingAggregatorService`'s singleton (`get_pricing_aggregator()`, lazy + `asyncio.Lock`-guarded, defined in `pricing_aggregator.py` itself) is the one exception reused across *both* the REST API and every MCP tool in-process: `ToolManager` takes an optional `aggregator` param forwarded to all 13 pricing-dependent tools, and both MCP transports (`mcp/server.py`'s STDIO `main()`, `src/main.py`'s HTTP `/mcp` `_get_http_mcp_tool_manager()`) resolve `get_pricing_aggregator()` once and hand it to their `ToolManager`. This means one warm pricing cache per process, not one per surface — a REST call and an MCP tool call for the same model agree within the cache's TTL. Each tool's constructor still falls back to building its own instance when `aggregator` is omitted (e.g. direct instantiation in tests), so no existing call site broke.

### 2. Authentication Middleware (`src/main.py`)
Layered auth — billing DB first, global key fallback — so existing deployments without billing are unaffected:
```
Incoming request
  └─ Full-bypass path? (static UIs, /pricing, /mcp, and every `_unauthenticated_paths`
     entry such as /health, /docs, /billing/signup) → skips auth, rate limit AND body cap
  └─ Anonymous-but-throttled path? (`_anonymous_rate_limited_paths`: /agent/chat,
     /agent/chat/stream) → skips auth only; still rate-limited per IP and body-size-capped.
     Added in v1.73.3 — before it, no such tier existed: anonymous meant unthrottled, and
     these endpoints run an LLM on the operator's key.
  └─ Extract x-api-key header
       └─ BillingService.get_customer_by_api_key(key) → found?
            YES → request.state.customer = customer; tier from DB; bucket = customer.id
            NO  → check against global MCP_API_KEY; bucket = ip:tier where tier comes from the
                  X-Api-Key-Tier header ONLY if that global key was presented and valid — anonymous
                  requests get tier "" (default limit) regardless of the header (v1.73.5)
  └─ Rate limit check (token bucket per bucket key)
```

### 3. Provider Pattern
Each of 26 LLM providers implements `BasePricingProvider`:
- `fetch_pricing_data() -> list[PricingMetrics]` — returns static pricing (with live data fallback where available)
- `get_pricing_with_status()` — wraps `fetch_pricing_data()`, applies **live model sync**, returns `(data, status)`

#### Live Model Sync (automatic, per provider)
`BasePricingProvider._fetch_live_model_ids()` queries the provider's `/v1/models` API (cached 6 h).
`_apply_live_filter()` removes models absent from the live list — keeps any model whose name is an exact
or prefix match in the live set. Falls back to the full static list if the API is unreachable or no key is set.
Enabled for: OpenAI, Anthropic, Groq, Mistral AI, Together AI, Fireworks AI, xAI, DeepSeek, Cerebras, NVIDIA NIM.

`PricingAggregatorService` fetches all providers concurrently (`asyncio.gather`), merges results, and caches with a configurable TTL. Adding a new provider = new file + registration in aggregator.

### 4. MCP Dual Transport
- **STDIO** (`mcp/server.py`): JSON-RPC 2.0 over stdin/stdout for Claude Desktop local integration
- **HTTP** (`POST /mcp`): Same JSON-RPC 2.0 payload over HTTP for remote MCP clients — no local install needed
- Protocol version: `2024-11-05`; 29 tools exposed
- Both transports' `ToolManager` is built with the same process-wide `get_pricing_aggregator()` singleton the REST API uses (see "Singleton Services" above) — one pricing cache per process shared by every surface, not one per transport. Cross-process sharing (STDIO process vs. the HTTP/REST process) is out of scope; each process still gets its own aggregator.
- The 3 confirmed duplicate cost-math pairs (`/cost-estimate` ↔ `estimate_cost`, `/cost-estimate/batch` ↔ `compare_costs`, `/usage` ↔ `record_usage`) now call the same `cost_calculator.compute_token_cost()` instead of each re-implementing the arithmetic inline.
- **Third access path** (`npm/llm-pricing-mcp`, published to npm as `llm-pricing-mcp`): a zero-dependency Node.js STDIO<->HTTP bridge for clients that only support `command`-based STDIO servers but not a direct remote/HTTP `url` config. It holds no tool logic or pricing data of its own — it forwards each stdin line to `POST /mcp` and writes the JSON-RPC response back to stdout — so it has nothing to keep in sync as tools or pricing change server-side. `tests/test_npm_wrapper.py` exercises it against `npm/llm-pricing-mcp/test/mock-server.js`, never the live server, so these tests need no network access.

### 5. Agent Architecture (ReAct Loop)
```
User message
  → react_loop.py: think → select tool → execute → observe → repeat
  → tools.py: wraps 26 of the 29 MCP tools as callable Python functions
      (excludes ask_agent to prevent recursion, and get_telemetry/get_cache_stats as server-ops only)
  → llm_backend.py: AnthropicBackend | OpenAIBackend (switch via AGENT_LLM_PROVIDER env)
  → conversation.py: persist turns to SQLite, enforce max_turns limit
  → SSE stream: events [start, thinking, tool_call, tool_result, …, answer, done]
```

### 6. Stripe Billing (optional)
If `STRIPE_SECRET_KEY` is not set, checkout/portal/webhook endpoints return `503`. Free-tier signup and API key issuance always work without Stripe:
```
POST /billing/signup (email) → create customer row → return secrets.token_urlsafe(32) API key
POST /billing/checkout      → Stripe checkout session → redirect URL
POST /billing/webhook       → verify HMAC sig → update tier in billing.db
```
Tier changes propagate to rate limits automatically: next request reads tier from `billing.db`.

### 7. UI Design System
All 13 browser UIs share the same CSS custom properties (defined in each page's `:root`):

| Variable | Value |
|----------|-------|
| `--bg` | `#0f1117` |
| `--surface` | `#1a1d27` |
| `--surface2` | `#22263a` |
| `--border` | `#2e3347` |
| `--accent` | `#7c6af7` |
| `--text` | `#e2e8f0` |
| `--muted` | `#8892a4` |
| `--success` | `#48bb78` |
| `--warn` | `#ed8936` |
| `--danger` | `#f56565` |
| `--radius` | `10px` |

All use `'Segoe UI', system-ui, sans-serif`, `font-size: 14px`, sticky `.main-nav` (52px), hamburger at 640px breakpoint. **Admin page is the canonical design reference.**

---

## Data Stores

| Database | File | Tables | Purpose |
|----------|------|--------|---------|
| Pricing history | `pricing_history.db` | `price_history`, `routing_feedback`, `usage_events` (nullable `session_id`, `is_estimated` flag), `budget_alerts` | Price snapshots, router feedback, actual usage/spend (optionally session-scoped, exact or text-estimated), spend-threshold webhooks |
| Billing | `billing.db` | `customers` | API keys, tiers, Stripe IDs |
| Conversations | per-session SQLite | `messages` | Agent conversation memory |

Both `.db` files are gitignored and live on the Fly.io persistent volume (`/app/data`, 1 GB).

---

## API Endpoint Map

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health`, `/health/live`, `/health/ready`, `/health/detailed` | None | Health probes |
| GET | `/pricing` | None | All model pricing; supports `provider`, `supports_vision`, `supports_function_calling`, `supports_json_mode`, `batch_available`, `is_reasoning_model` query filters |
| GET | `/data-quality` | None | Aggregate accuracy summary: % confirmed/fresh, models withheld for drift, unpriced, stale |
| GET | `/models` | None | Model list |
| GET | `/pricing/public` | None | Embed-safe public pricing |
| GET | `/pricing/history` | None | Historical price snapshots |
| GET | `/pricing/trends` | None | Price-change leaderboard |
| GET | `/pricing/history/export` | None | CSV / JSON export |
| GET | `/pricing/alerts/signing-info` | Required | Webhook HMAC signing key info |
| GET | `/performance` | None | Performance metrics |
| GET | `/use-cases` | None | Use-case recommendations |
| POST | `/cost-estimate` | None | Single-model cost |
| POST | `/cost-estimate/batch` | None | Multi-model cost comparison |
| GET | `/rate-limits/tiers` | None | Tier rate limits |
| GET | `/api/versions` | None | API version negotiation |
| GET | `/telemetry` | None | Request telemetry |
| GET | `/cache-stats` | None | Process-lifetime hit/miss counts + hit rate for the recommendation cache, per call site (`recommend_model`, `router_recommend`) |
| POST | `/router/recommend` | Required | LLM routing recommendation (~5min in-memory cache; response `cached` field) |
| POST | `/router/recommend/stream` | Required | SSE streaming router |
| POST | `/router/feedback` | Required | Accept/reject feedback |
| GET | `/telemetry/savings` | Required | Per-org savings stats |
| POST | `/usage` | Required | Record one actual LLM usage event (cost computed server-side) |
| POST | `/usage/batch` | Required | Record multiple usage events at once |
| GET | `/usage/summary` | Required | Actual spend summary by model/provider, per-org, plus an org-wide exact-vs-estimated breakdown (`estimated_request_count`, `has_estimated_usage`) |
| GET | `/usage/session/{session_id}` | Required | On-demand analysis of one session's actual usage: totals (with an exact-vs-estimated breakdown), per-model breakdown, and a grounded model recommendation with estimated savings |
| POST | `/usage/alerts` | Required | Register a webhook for spend crossing a USD threshold |
| GET | `/usage/alerts` | Required | List registered budget alerts |
| DELETE | `/usage/alerts/{id}` | Required | Delete a budget alert |
| POST | `/agent/chat` | None | Blocking agent response |
| POST | `/agent/chat/stream` | None | SSE agent stream |
| GET | `/agent/conversations` | None | List agent conversations |
| DELETE | `/agent/conversations/{id}` | Required | Delete a conversation |
| POST | `/v1/chat/completions` | None | OpenAI-compatible proxy |
| GET | `/mcp` | None | MCP server info |
| POST | `/mcp` | None | MCP HTTP transport (JSON-RPC 2.0) |
| POST | `/billing/signup` | None | Free-tier signup |
| GET | `/billing/me` | Required | Customer dashboard |
| POST | `/billing/checkout` | Required | Stripe checkout |
| GET | `/billing/portal` | Required | Stripe billing portal |
| POST | `/billing/webhook` | Stripe sig | Subscription sync |
| POST | `/pricing/alerts` | Required | Register price-change webhook |
| GET | `/pricing/alerts` | Required | List alerts |
| DELETE | `/pricing/alerts/{id}` | Required | Delete alert |
| GET | `/docs` | None | Swagger UI (OpenAPI) |
| GET | `/redoc` | None | ReDoc (OpenAPI) |
| GET | `/openapi.json` | None | OpenAPI spec (JSON) |
| GET | `/billing` | None | Billing SPA |
| GET | `/admin` | None | Admin SPA |
| GET | `/{page}` | None | Browser UIs |

---

## CI/CD Pipeline

```
PR / push to master
  ├── test        pytest (667+ tests, coverage report)
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
root is **remote-only** — a `remotes` entry pointing at the hosted `/mcp` endpoint above, no
`packages` array — so publishing needs no npm coupling and no package-ownership-verification
marker. Authorised the same way as npm: `id-token: write` → `mcp-publisher login github-oidc`, no
secret. The registry's `name` field (`io.github.skakumanu/llm-pricing-mcp-server`) must stay
prefixed with `io.github.skakumanu/` — that's what ties the OIDC identity to the publish permission
— and its `description` is capped at 100 characters, enforced at publish time with no local
warning otherwise. `tests/test_mcp_registry_publish_workflow.py` pins both, plus the schema URL
shape and the deliberate absence of `packages` (adding one later needs its own ownership marker,
e.g. npm's `mcpName` field, and should not land silently alongside an unrelated change).

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
