# Architecture — LLM Pricing MCP Server

**Version**: v1.38.2 | **Last updated**: 2026-03-15

---

## System Overview

A production FastAPI service that aggregates real-time LLM pricing data from 12 providers (87+ models), exposes it via REST API and MCP protocol, and layers on a ReAct agent, self-serve SaaS billing, and a suite of browser UIs.

- **Primary deployment**: Fly.io (`llm-pricing-api.fly.dev`) — shared-cpu-1x, 512 MB, ~$3.40/mo
- **Secondary deployment**: Azure App Service (`llm-pricing-api.azurewebsites.net`) — parallel cutover pending
- **CI/CD**: GitHub Actions → test → lint → bandit → OSV scan → gitleaks → Fly.io deploy

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
│  REST API              MCP (14 tools)          Browser UIs (9 pages)        │
│  /pricing              STDIO transport          /  /chat  /calculator        │
│  /router/recommend     HTTP POST /mcp           /compare  /history           │
│  /billing/*            JSON-RPC 2.0             /trends   /widget            │
│  /agent/chat           MCP 2024-11-05           /billing  /admin             │
│  /v1/chat/completions                                                        │
│                                                                             │
│  Security middleware: billing DB key → global MCP_API_KEY fallback          │
│  Rate limiting: per {ip}:{tier} token-bucket (30/120/600 req/min)           │
└──────────┬──────────────────────┬──────────────────────┬────────────────────┘
           │                      │                      │
┌──────────▼──────────┐  ┌───────▼────────┐  ┌─────────▼──────────────────┐
│  Pricing Services   │  │  Agent / RAG   │  │  Billing Service           │
│                     │  │                │  │                            │
│  PricingAggregator  │  │  ReAct loop    │  │  BillingService            │
│  12 provider impls  │  │  TF-IDF RAG    │  │  billing.db (SQLite)       │
│  PricingHistory     │  │  Conv. memory  │  │  Free signup → API key     │
│  BenchmarkService   │  │  LLM backend   │  │  Stripe checkout/webhook   │
│  Router             │  │  (GPT-4o-mini  │  │  Tier sync (free/pro/ent)  │
│  SavingsTracker     │  │   or Anthropic)│  │                            │
│  PricingAlerts      │  └───────┬────────┘  └────────────────────────────┘
└──────────┬──────────┘          │
           │                     │
┌──────────▼─────────────────────▼────────────────────────────────────────────┐
│  Data Layer                                                                 │
│                                                                             │
│  pricing_history.db     billing.db          In-memory cache                │
│  price_history table    customers table     Aggregator (TTL)               │
│  routing_feedback       (api_key, tier,     Savings per org_id             │
│  table                  org_id, stripe_id)  Alert callbacks                │
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
│       ├── base_provider.py         # Abstract BasePricingProvider
│       ├── pricing_aggregator.py    # Orchestrates + caches all provider data (async)
│       ├── pricing_history.py       # SQLite price-history + routing_feedback tables
│       ├── benchmark_service.py     # Quality scores: static table + HF API fallback (24h TTL)
│       ├── router.py                # LLM routing recommendation engine
│       ├── savings_tracker.py       # Per-org router savings + acceptance_rate
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
│       └── ai21_pricing.py
│
├── agent/
│   ├── pricing_agent.py             # High-level agent entry point
│   ├── react_loop.py                # ReAct (Reason + Act) loop implementation
│   ├── llm_backend.py               # AnthropicBackend + OpenAIBackend (switch via env)
│   ├── conversation.py              # SQLite conversation memory, turn limit
│   └── tools.py                     # 14 MCP tool bindings for agent use
│
├── mcp/
│   ├── server.py                    # MCP STDIO transport (Claude Desktop)
│   ├── server_azure.py              # MCP HTTP transport variant
│   ├── tools/                       # Per-tool MCP handler modules
│   ├── schemas/                     # MCP JSON-RPC schema definitions
│   └── utils/                       # Session management, helpers
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
│   └── conversations/index.html     # /conversations — conversation history viewer
│
├── .github/workflows/
│   ├── ci-cd.yml                    # Full CI/CD: test→lint→osv→bandit→gitleaks→deploy
│   └── sync-develop.yml             # Auto-sync develop ← master after deploy
│
├── tests/                           # 625 pytest tests (as of v1.38.2)
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
All `init_*` calls live in the `startup_pricing_history()` FastAPI lifespan handler. Services: `PricingAggregator`, `PricingHistory`, `BenchmarkService`, `SavingsTracker`, `BillingService`.

### 2. Authentication Middleware (`src/main.py`)
Layered auth — billing DB first, global key fallback — so existing deployments without billing are unaffected:
```
Incoming request
  └─ Unauthenticated path? (/, /pricing, /mcp, /billing/signup, …) → pass through
  └─ Extract x-api-key header
       └─ BillingService.get_customer_by_api_key(key) → found?
            YES → request.state.customer = customer; tier from DB; bucket = customer.id
            NO  → check against global MCP_API_KEY; tier from X-Api-Key-Tier header; bucket = ip:tier
  └─ Rate limit check (token bucket per bucket key)
```

### 3. Provider Pattern
Each of 12 LLM providers implements `BasePricingProvider`:
- `fetch_pricing() -> list[PricingMetrics]`
- `get_models() -> list[str]`

`PricingAggregatorService` fetches all providers concurrently (`asyncio.gather`), merges results, and caches with a configurable TTL. Adding a new provider = new file + registration in aggregator.

### 4. MCP Dual Transport
- **STDIO** (`mcp/server.py`): JSON-RPC 2.0 over stdin/stdout for Claude Desktop local integration
- **HTTP** (`POST /mcp`): Same JSON-RPC 2.0 payload over HTTP for remote MCP clients — no local install needed
- Protocol version: `2024-11-05`; 14 tools exposed

### 5. Agent Architecture (ReAct Loop)
```
User message
  → react_loop.py: think → select tool → execute → observe → repeat
  → tools.py: wraps all 14 MCP tools as callable Python functions
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
All 9 browser UIs share the same CSS custom properties (defined in each page's `:root`):

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
| Pricing history | `pricing_history.db` | `price_history`, `routing_feedback` | Price snapshots, router feedback |
| Billing | `billing.db` | `customers` | API keys, tiers, Stripe IDs |
| Conversations | per-session SQLite | `messages` | Agent conversation memory |

Both `.db` files are gitignored and live on the Fly.io persistent volume (`/app/data`, 1 GB).

---

## API Endpoint Map

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health`, `/health/live`, `/health/ready`, `/health/detailed` | None | Health probes |
| GET | `/pricing` | None | All model pricing |
| GET | `/models` | None | Model list |
| GET | `/performance` | None | Performance metrics |
| GET | `/use-cases` | None | Use-case recommendations |
| POST | `/cost-estimate` | None | Single-model cost |
| POST | `/cost-estimate/batch` | None | Multi-model cost comparison |
| GET | `/rate-limits/tiers` | None | Tier rate limits |
| POST | `/router/recommend` | Required | LLM routing recommendation |
| POST | `/router/recommend/stream` | Required | SSE streaming router |
| POST | `/router/feedback` | Required | Accept/reject feedback |
| GET | `/telemetry/savings` | Required | Per-org savings stats |
| POST | `/agent/chat` | None | Blocking agent response |
| POST | `/agent/chat/stream` | None | SSE agent stream |
| POST | `/v1/chat/completions` | None | OpenAI-compatible proxy |
| POST | `/mcp` | None | MCP HTTP transport |
| POST | `/billing/signup` | None | Free-tier signup |
| GET | `/billing/me` | Required | Customer dashboard |
| POST | `/billing/checkout` | Required | Stripe checkout |
| GET | `/billing/portal` | Required | Stripe billing portal |
| POST | `/billing/webhook` | Stripe sig | Subscription sync |
| GET | `/billing` | None | Billing SPA |
| GET | `/admin` | None | Admin SPA |
| GET | `/{page}` | None | Browser UIs |

---

## CI/CD Pipeline

```
PR / push to master
  ├── test        pytest (625+ tests, coverage report)
  ├── lint        flake8 (syntax errors + undefined names)
  ├── osv_scan    Google OSV Scanner (dependency CVEs)
  ├── security    bandit (Python SAST)
  └── secret_scan gitleaks (full git history scan for leaked credentials)
            │
            └─ all pass? ──► deploy       Azure App Service (Docker via ACR)
                        └──► deploy_fly   Fly.io (flyctl deploy --remote-only)
                                  │
                                  └─ health check: /health version matches src/__init__.py
```

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
