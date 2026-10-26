# LLM Pricing MCP Server

[![CI/CD Pipeline](https://github.com/skakumanu/llm-pricing-mcp-server/workflows/CI%2FCD%20Pipeline/badge.svg)](https://github.com/skakumanu/llm-pricing-mcp-server/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Most LLM pricing tools are a static lookup table. This one checks its own numbers against a live, independently-maintained price registry and tells you when they disagree, routes to a model by cost *and* quality instead of just quoting a price, and tracks what you actually spent — not just what you might. All of that is backed by a RESTful API (FastAPI), **29 MCP tools** over STDIO and HTTP, a conversational Agent + RAG pipeline, browser-based UIs, and a self-serve billing dashboard, for pricing data from **31 major LLM providers**.

**Live at**: https://llm-pricing-api.fly.dev

---

## Why this one

- **Prices you can verify, not just trust** — `check_price_drift` and `get_data_quality` compare every curated price against a live external registry and withhold (never silently serve) anything that's drifted more than 5%, with a single `confirmed_pct` trust score for the whole catalogue.
- **Routes, doesn't just quote** — `recommend_model` and `optimize_workload` pick a model by cost *and* quality for your actual task, flagging it when a cheaper pick trades away quality rather than silently recommending it.
- **Tracks what you spent, not just what you might** — `record_usage`, `analyze_session_usage`, and `import_session_usage` record and analyze real spend, including backfilling a coding-agent session that wasn't instrumented to track usage live.
- **More than an MCP server** — the same pricing engine also powers a REST API, browser UIs (calculator, side-by-side comparison, history/trends), and a self-serve billing dashboard.

---

## Features

### MCP Interface (STDIO + HTTP)
- **29 MCP Tools**: `get_all_pricing`, `estimate_cost`, `compare_costs`, **`predict_cost`**, **`optimize_workload`**, **`recommend_model`**, **`check_price_drift`**, **`get_data_quality`**, `get_performance_metrics`, `get_use_cases`, `get_ide_pricing`, `get_telemetry`, `get_pricing_history`, `get_pricing_trends`, `register_price_alert`, `list_price_alerts`, `delete_price_alert`, `get_pricing_export_url`, `list_conversations`, `delete_conversation`, `ask_agent`, **`record_usage`**, **`get_usage_summary`**, **`register_budget_alert`**, **`list_budget_alerts`**, **`delete_budget_alert`**, **`get_cache_stats`**, **`analyze_session_usage`**, **`import_session_usage`**
- `get_all_pricing`, `estimate_cost`, `compare_costs`, `get_use_cases` and `recommend_model` take optional `currency` / `country` arguments (v1.76.0) and add converted `local_*` figures plus a `currency_conversion` block; omit both and the output is unchanged
- **STDIO transport** — JSON-RPC 2.0 over STDIO for local Claude Desktop integration
- **HTTP transport** — `POST /mcp` (JSON-RPC 2.0 over HTTP) for remote MCP clients — no local install needed
- MCP Protocol version: `2024-11-05`

### RESTful API
- Real-time pricing from 31 providers (600+ models), async fetching with smart caching. Every price says where it came from: `price_source_kind` is `provider` when confirmed against the provider's rate card, `registry` when taken from the reference price registry (with `registry_key` and date); registry-sourced models are usable in estimates and comparisons but never counted as provider-confirmed, and the weekly pickup PR adds new registry models under covered providers automatically
- Cost estimation: single model (`POST /cost-estimate`) and batch comparison (`POST /cost-estimate/batch`)
- Prices in your currency (v1.76.0): `GET /pricing`, `POST /cost-estimate`, `POST /cost-estimate/batch`, `GET /use-cases`, `POST /router/recommend` and `GET /pricing/public` accept `currency` / `country` (query or `X-Currency` / `X-Country` headers) and return converted figures as additive `local_*` fields next to the unchanged USD ones; the browser calculator and compare pages preset the currency from the visitor's country and let them switch — see [Currency and locale](#currency-and-locale)
- Performance metrics, use-case recommendations, pricing history, trends
- Router recommendation (`POST /router/recommend`) with feedback loop; `GET /cache-stats` reports process-lifetime hit/miss stats for its ~5-minute recommendation cache
- Actual usage/spend tracking (`POST /usage`, `GET /usage/summary`) — the real-cost counterpart to `/cost-estimate`; tag events with `session_id` and analyze one session on demand (`GET /usage/session/{session_id}`) for a grounded recommendation with estimated savings
- OpenAI-compatible proxy (`POST /v1/chat/completions`) for drop-in SDK integration
- Webhook alerts for price changes and budget-spend thresholds (`POST /usage/alerts`, HMAC-SHA256 signed)
- Interactive docs: `/docs` (Swagger) and `/redoc`

### Repo Cost Auditor (`scripts/audit_repo.py`)
- Statically scans a Python codebase for LLM SDK call sites (OpenAI, Anthropic, Google, LangChain), no credentials or network calls to your code required
- Estimates per-call-site cost using the same pricing data and token math as `predict_cost`
- Recommends cheaper models, prompt caching, Batch API (for calls in a loop or an offline-looking path), and task-based right-sizing (flagship model on a simple task) — plus flags the same prompt duplicated across call sites — see `python scripts/audit_repo.py --help`
- Static estimates only (assumes a configurable calls/month) — pair with `POST /usage`'s actual recorded spend once deployed for real numbers

### AI-Native SDLC Pipeline (`/release-feature`, for contributors using Claude Code)
- Runs a feature request through dedicated phase agents instead of one agent doing everything at once: **Spec → Design → Code → Review → Test & Release**
- Spec fixes *what* the feature does (requirements, acceptance criteria, non-goals) with zero implementation detail; Design fixes *how* it's built (approach, version bump, branch name) given a fixed Spec — neither phase re-derives the other's decision
- Review fans out to three parallel agents (correctness, security, simplification) over the diff; Test & Release checks the result against the Spec's acceptance criteria, triages Review's findings, runs this repo's full release checklist, and is the only phase that commits, pushes, and opens the PR
- Defined in `.claude/agents/phase-*.md` (five phase agents) and `.claude/workflows/release-feature.js` (the Code→Review→Test orchestrator) — see `CLAUDE.md`'s "AI-Native SDLC Pipeline" section
- Ends at an open PR against `develop`; merging is still a human decision

### Agent + RAG Pipeline
- **Configurable LLM backend**: OpenAI GPT-4o-mini (default) or Anthropic Claude via env vars
- **ReAct loop agent** with access to 26 of the 29 MCP tools (excludes `ask_agent`, `get_telemetry`, and `get_cache_stats`)
- **TF-IDF RAG** over pricing docs with top-k retrieval
- **Conversation memory**: per-session SQLite persistence, configurable turn limit
- **Chat UI** at `/chat` — streams ReAct progress in real time
- `POST /agent/chat` — blocking JSON response
- `POST /agent/chat/stream` — SSE stream (`thinking` / `tool_call` / `tool_result` / `answer` / `done`)

### Browser UIs
| Path | Description |
|------|-------------|
| `/` | Marketing landing page |
| `/chat` | Conversational AI agent |
| `/calculator` | Interactive cost calculator |
| `/compare` | Side-by-side model comparison |
| `/history` | Pricing history charts (Chart.js) |
| `/trends` | Price-change leaderboard |
| `/widget` | Embeddable pricing table |
| `/billing` | Self-serve signup + upgrade dashboard |
| `/conversations` | Conversation history viewer |
| `/admin` | Server stats, rate limits, customers |
| `/mcp-setup` | MCP integration hub |
| `/api-docs` | API reference (Swagger/ReDoc) |
| `/whats-new` | Release notes timeline |

All UIs share a consistent dark design system (CSS variables, `'Segoe UI'` font, `#0f1117` background, `#7c6af7` accent) and are fully mobile-responsive with a sticky hamburger navigation bar.

### SaaS Billing (Stripe)
- **Free tier signup**: `POST /billing/signup` (email → API key, no Stripe required)
- Paid tiers (Pro/Enterprise) via Stripe Checkout → auto-updates rate limits
- Self-serve portal: `GET /billing/portal`
- Usage dashboard: `GET /billing/me` (router calls, savings, acceptance rate)
- Stripe webhook handler: `POST /billing/webhook`

### API Key Tiers & Rate Limiting
- **Free**: 30 req/min · **Pro**: 120 req/min · **Enterprise**: 600 req/min
- Tier comes from the billing DB (customer API key), or from the `X-Api-Key-Tier` header **only when the request also carries the valid global `MCP_API_KEY`** — anonymous requests always get the default bucket, so the header can't be used to self-select a higher limit
- `GET /rate-limits/tiers` — public endpoint listing tier details

### Security & Quality
- Most endpoints are public (read-only pricing data, UIs, MCP tools)
- Protected endpoints (`/billing/me`, `/router/recommend`, `/router/feedback`, `/billing/portal`) require a billing API key or the global `MCP_API_KEY`
- Rate limiting per client IP + tier bucket — includes the anonymous `/agent/chat` and `/agent/chat/stream` endpoints (no API key needed, but throttled, since each call runs an LLM on the operator's key)
- Request size limit (1MB default)
- 1692 passing tests, CI/CD on every PR (test → lint → bandit → OSV → gitleaks → deploy)

### Deployment
- **Primary**: [Fly.io](https://llm-pricing-api.fly.dev) — shared-cpu-1x, 512MB, ~$3.40/mo
- **CI/CD**: GitHub Actions — test → deploy on `master` push
- Blue-green deployment support, graceful shutdown (SIGTERM/SIGINT)
- Health probes: `/health`, `/health/live`, `/health/ready`, `/health/detailed`

---

## Table of Contents

- [Quick Start (Claude Desktop)](#quick-start-claude-desktop)
- [Quick Start (API)](#quick-start-api)
- [HTTP MCP Transport](#http-mcp-transport)
- [API Reference](#api-reference)
- [Configuration](#configuration)
- [Development](#development)
- [Testing](#testing)
- [Deployment](#deployment)
- [Contributing](#contributing)
- [Roadmap](#roadmap)

---

## Quick Start (Claude Desktop)

[![Add to Cursor](https://img.shields.io/badge/Cursor-Add_MCP_Server-000000?style=flat-square)](cursor://anysphere.cursor-deeplink/mcp/install?name=llm-pricing&config=eyJ1cmwiOiJodHRwczovL2xsbS1wcmljaW5nLWFwaS5mbHkuZGV2L21jcCJ9)
[![Add to VS Code](https://img.shields.io/badge/VS_Code-Add_MCP_Server-0098FF?style=flat-square)](vscode:mcp/install?%7B%22name%22%3A%22llm-pricing%22%2C%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fllm-pricing-api.fly.dev%2Fmcp%22%7D)

One-click links above open Cursor/VS Code directly (best-effort — deep-link support varies by client version). Every other client uses the copy-paste config below, which always works.

### Option A — Remote (no install needed)

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "llm-pricing": {
      "url": "https://llm-pricing-api.fly.dev/mcp"
    }
  }
}
```

Restart Claude Desktop. All 29 pricing tools are immediately available.

### Option B — STDIO via npx (no clone, no Python)

For clients that only support `command`-based STDIO servers (no direct remote/HTTP config support). This runs a zero-dependency [npm package](npm/llm-pricing-mcp) that bridges stdin/stdout to the hosted server above — nothing to clone, no Python or `pip install` needed.

```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "npx",
      "args": ["-y", "llm-pricing-mcp"]
    }
  }
}
```

Restart Claude Desktop. See [npm/llm-pricing-mcp/README.md](npm/llm-pricing-mcp/README.md) for configuration options (self-hosting, timeouts).

### Option C — Local STDIO, fully offline

1. Clone and install:
```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
cd llm-pricing-mcp-server
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

2. Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "llm-pricing": {
      "command": "python",
      "args": ["/absolute/path/to/mcp/server.py"],
      "cwd": "/absolute/path/to/llm-pricing-mcp-server",
      "env": { "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

See [docs/CLAUDE_INTEGRATION.md](docs/CLAUDE_INTEGRATION.md) for detailed setup, Windows paths, and troubleshooting.

---

## Quick Start (API)

### Running the Server

```bash
python src/main.py
# or
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Public Endpoints (no auth)

```bash
# Get all pricing
curl http://localhost:8000/pricing

# Estimate cost
curl -X POST http://localhost:8000/cost-estimate \
  -H "Content-Type: application/json" \
  -d '{"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}'

# Chat with the agent
curl -X POST http://localhost:8000/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the cheapest model for RAG pipelines?"}'

# OpenAI-compatible routing proxy
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}'
```

### Protected Endpoints (billing API key or MCP_API_KEY)

```bash
# Get your API key via free signup
curl -X POST http://localhost:8000/billing/signup \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'

# Use the returned api_key for protected endpoints
curl -H "x-api-key: <your-api-key>" \
  http://localhost:8000/billing/me

curl -X POST http://localhost:8000/router/recommend \
  -H "x-api-key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"max_cost_per_1m_tokens": 5}'
```

### Agent Streaming

```bash
curl -X POST http://localhost:8000/agent/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Compare GPT-4o vs Claude Sonnet pricing"}' \
  --no-buffer
```

Events: `thinking` → `tool_call` → `tool_result` → `answer` → `done`

---

## HTTP MCP Transport

The server exposes a JSON-RPC 2.0 endpoint at `POST /mcp` supporting the MCP protocol over HTTP (protocol version `2024-11-05`).

### Supported methods

| Method | Description |
|--------|-------------|
| `initialize` | Handshake — returns server info and capabilities |
| `initialized` | Notification — returns 204 |
| `tools/list` | List all 29 tools with input schemas |
| `tools/call` | Execute a tool |

### Example

```bash
# List tools
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# Call a tool
curl -X POST https://llm-pricing-api.fly.dev/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {
      "name": "estimate_cost",
      "arguments": {"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}
    }
  }'
```

`GET /mcp` returns server info and a config snippet. Full interactive docs at `/docs` (Swagger UI) and `/redoc`.

---

## API Reference

### Public Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Landing page (HTML) |
| GET | `/pricing` | All model pricing (`?provider=` filter; `?currency=` / `?country=` add `local_prices`) |
| GET | `/locale` | Currency/country this request resolves to (browser preset; never echoes an IP) |
| GET | `/currency/rates` | Vendored USD exchange-rate table (as-of date, 7-day stale flag) |
| GET | `/models` | All available models |
| GET | `/performance` | Throughput, latency, context window, quality scores |
| GET | `/use-cases` | Model use-case recommendations |
| GET | `/telemetry` | Server usage metrics and analytics |
| GET | `/admin/stats` | Aggregated server statistics |
| GET | `/admin/rate-limits` | Current rate-limit state |
| GET | `/rate-limits/tiers` | API tier definitions |
| GET | `/pricing/history` | Historical pricing snapshots |
| GET | `/pricing/trends` | Price-change leaderboard |
| GET | `/pricing/public` | Public pricing (unauthenticated, embed-safe) |
| GET | `/pricing/history/export` | CSV/JSON export |
| POST | `/cost-estimate` | Single model cost estimate |
| POST | `/cost-estimate/batch` | Multi-model cost comparison |
| POST | `/agent/chat` | Blocking agent chat |
| POST | `/agent/chat/stream` | Streaming agent chat (SSE) |
| GET | `/agent/conversations` | List conversations |
| DELETE | `/agent/conversations/{id}` | Delete a conversation |
| GET | `/api/versions` | API version info |
| GET | `/pricing/alerts/signing-info` | Webhook signing key info |
| POST | `/v1/chat/completions` | OpenAI-compatible routing proxy |
| GET | `/mcp` | HTTP MCP server info |
| POST | `/mcp` | HTTP MCP JSON-RPC 2.0 |
| POST | `/billing/signup` | Free tier signup → API key |
| GET | `/billing` | Billing dashboard (HTML) |
| POST | `/billing/webhook` | Stripe webhook |
| GET | `/health` | Health check |
| GET | `/health/live` | Kubernetes liveness probe |
| GET | `/health/ready` | Kubernetes readiness probe |
| GET | `/health/detailed` | Detailed health with service statuses |

### Protected Endpoints (billing API key or MCP_API_KEY)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/router/recommend` | Smart model routing recommendation |
| POST | `/router/recommend/stream` | Streaming router (SSE) |
| POST | `/router/feedback` | Submit routing feedback |
| GET | `/telemetry/savings` | Router savings report |
| POST | `/usage` | Record one actual usage event (cost computed server-side) |
| POST | `/usage/batch` | Record multiple usage events at once |
| GET | `/usage/summary` | Actual spend summary by model/provider, per org |
| POST | `/billing/checkout` | Stripe Checkout session |
| GET | `/billing/portal` | Stripe billing portal |
| GET | `/billing/me` | Usage dashboard |
| POST | `/pricing/alerts` | Register price-change webhook |
| GET | `/pricing/alerts` | List alerts |
| DELETE | `/pricing/alerts/{id}` | Delete alert |

### Key Request/Response Examples

#### `POST /cost-estimate`
```json
// Request
{"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}

// Response
{
  "model_name": "gpt-4o", "provider": "OpenAI",
  "input_tokens": 1000, "output_tokens": 500,
  "input_cost": 0.0000025, "output_cost": 0.000005,
  "total_cost": 0.0000075, "currency": "USD"
}
```

#### `POST /billing/signup`
```json
// Request
{"email": "you@example.com"}

// Response
{
  "api_key": "abc123...", "org_id": "org-xyz...",
  "tier": "free", "message": "Welcome! Your free API key is ready."
}
```

#### `POST /router/recommend`
```json
// Request (x-api-key header required)
{"max_cost_per_1m_tokens": 5, "task_type": "code"}

// Response
{
  "recommended": {"model_name": "gpt-4o-mini", "provider": "OpenAI", ...},
  "alternatives": [...],
  "reason": "Best quality/cost ratio for code tasks under $5/1M tokens",
  "routing_id": "uuid4..."
}
```

---

### Currency and locale

Every price the API serves is a USD list price. Since v1.76.0 the pricing listing, the single and
batch cost estimates, use-cases, the router recommendation and the public pricing endpoint can also
show each figure in one of **13 currencies** — USD, EUR, GBP, INR, JPY, CAD, AUD, BRL, CNY, SGD, CHF,
KRW, MXN — alongside the USD it was converted from:

```bash
curl "http://localhost:8000/pricing?currency=EUR"            # every model gains local_prices
curl -H "X-Country: IN" "http://localhost:8000/pricing/public" # INR via the country
curl -X POST "http://localhost:8000/cost-estimate?country=JP" \
  -H "Content-Type: application/json" \
  -d '{"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}'
```

- **Inputs and precedence**: `currency` query > `X-Currency` header > `country` query > `X-Country`
  header > platform geo header (`CF-IPCountry`, `X-Vercel-IP-Country`, `CloudFront-Viewer-Country`,
  `X-Country-Code`) > best-effort IP lookup > USD. A stated currency always beats a stated country
  (the country then only labels the response).
- **Shape**: converted figures are additive — `local_prices` (per model), `local_cost`,
  `local_cost_range`, plus one `currency_conversion` block (`currency`, `rate`, `rate_as_of`,
  `rate_age_days`, `rate_is_stale`, `rate_source`, `country`, `country_source`, `currency_source`,
  `fallback_to_usd`, `fallback_reason`, `note`). The USD fields are never renamed or re-denominated,
  and when nothing is stated or detected none of the new fields appear at all.
- **Rejected vs. fallback**: an explicitly requested code outside the 13 is a `400` whose body lists
  the supported set (never silently USD). A country whose currency is not supported gets USD with
  `fallback_to_usd: true`, `fallback_reason: "country_not_mapped"`.
- **Rates**: a vendored table, `data/exchange_rates.json`, refreshed weekly by
  `scripts/refresh_exchange_rates.py` through a human-merged PR (Frankfurter, falling back to ECB
  reference rates). Nothing is fetched during a request. A rate older than **7 days** is still served
  but flagged `rate_is_stale: true`; `GET /currency/rates` exposes the table, its `as_of` date and
  whether it is still the authored placeholder. Converted amounts are indicative — providers bill in USD.
- **Browser**: `/calculator` and `/compare` carry a currency selector preset to the detected country's
  currency (USD when undetected) and re-render every amount on change; the choice persists across
  pages and reloads (`llmp.currency` in localStorage). The landing page carries the selector and note only.
- **Privacy**: the client IP is used transiently to resolve a country and nothing more — never stored,
  never logged by this feature, never in any response. Only the resolved ISO country code may be
  echoed. The same models are shown to everyone in the same order; cost tiers stay USD bands.
- **MCP**: `get_all_pricing`, `estimate_cost`, `compare_costs`, `get_use_cases`, `recommend_model`
  accept optional `currency` and `country`. MCP never detects a location.

## Configuration

### Environment Variables

```env
# LLM Backend (Agent)
AGENT_LLM_PROVIDER=openai           # "openai" (default) or "anthropic"
AGENT_MODEL=gpt-4o-mini             # Model name
OPENAI_API_KEY=sk-...               # Required when AGENT_LLM_PROVIDER=openai
ANTHROPIC_API_KEY=sk-ant-...        # Required when AGENT_LLM_PROVIDER=anthropic

# Server
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DEBUG=false

# Security
MCP_API_KEY=your-strong-random-key  # Global admin key (optional — billing keys work too)
RATE_LIMIT_PER_MINUTE=60            # Default rate limit (overridden by tier)

# Local currency (v1.76.0)
CURRENCY_DETECTION_ENABLED=true     # false = never infer a country from the client IP
GEO_LOOKUP_TIMEOUT_SECONDS=1.0      # request-path cap on the IP -> country lookup
EXCHANGE_RATES_PATH=                # override for data/exchange_rates.json (Fly sets /app/vendored/exchange_rates.json)

# Billing (Stripe)
STRIPE_SECRET_KEY=sk_live_...       # Optional — free signup works without this
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID_PRO=price_...
STRIPE_PRICE_ID_ENTERPRISE=price_...
BILLING_DB_PATH=/app/data/billing.db
BILLING_BASE_URL=https://llm-pricing-api.fly.dev
```

**Switching LLM providers** requires only env var changes — no code modifications:

| Provider | `AGENT_LLM_PROVIDER` | `AGENT_MODEL` | Estimated cost |
|---|---|---|---|
| OpenAI (default) | `openai` | `gpt-4o-mini` | ~$0.73/mo |
| OpenAI (powerful) | `openai` | `gpt-4o` | ~$8/mo |
| Anthropic | `anthropic` | `claude-sonnet-4-6` | ~$15/mo |
| Anthropic (fast) | `anthropic` | `claude-haiku-4-5-20251001` | ~$2/mo |

---

## Development

### Project Structure

```
llm-pricing-mcp-server/
├── src/
│   ├── __init__.py                  # Version (1.48.0)
│   ├── main.py                      # FastAPI app + all endpoints
│   ├── config/settings.py           # Pydantic settings
│   ├── models/                      # Pydantic models (pricing, billing, router, …)
│   └── services/                    # Business logic
│       ├── pricing_aggregator.py    # Multi-provider pricing aggregator
│       ├── billing_service.py       # SQLite billing / customer DB
│       ├── router.py                # Model routing engine
│       ├── savings_tracker.py       # Router savings analytics
│       ├── benchmark_service.py     # Quality scores + HF API
│       ├── pricing_history.py       # Historical snapshot DB
│       └── …
├── mcp/
│   ├── server.py                    # STDIO JSON-RPC 2.0 server
│   └── tools/                       # 29 MCP tool implementations
│       ├── tool_manager.py
│       ├── get_all_pricing.py
│       ├── estimate_cost.py
│       └── …
├── agent/
│   ├── pricing_agent.py             # ReAct loop agent
│   ├── llm_backend.py               # OpenAI + Anthropic backends
│   └── tools.py                     # Agent tool wrappers
├── static/                          # Browser UIs (one dir per page)
│   ├── landing/                     # / (marketing page)
│   ├── chat/                        # /chat (agent UI)
│   ├── calculator/                  # /calculator
│   ├── compare/                     # /compare
│   ├── history/                     # /history
│   ├── trends/                      # /trends
│   ├── widget/                      # /widget
│   ├── billing/                     # /billing
│   └── admin/                       # /admin
├── tests/                           # 667 tests
├── docs/                            # Extended documentation
├── .github/workflows/ci-cd.yml      # CI/CD pipeline
├── Dockerfile
├── fly.toml                         # Fly.io deployment config
└── requirements.txt
```

### Adding a New Provider

1. Create `src/services/<provider>_pricing.py` implementing `BasePricingProvider`
2. Register in `src/services/pricing_aggregator.py`
3. Add env var for optional API key in `src/config/settings.py`
4. Add tests in `tests/`

---

## Testing

```bash
# Run all 667 tests
pytest

# With coverage
pytest --cov=src --cov-report=term-missing

# Specific suites
pytest tests/test_api.py -v
pytest tests/test_billing_endpoints.py -v
pytest tests/test_mcp_http.py -v     # HTTP MCP transport
pytest tests/test_router.py -v
```

### Test Files

| File | What it covers |
|------|---------------|
| `test_api.py` | Core API endpoints |
| `test_agent_endpoint.py` | Agent chat + streaming |
| `test_billing_endpoints.py` | Signup, checkout, webhook, me |
| `test_billing_service.py` | BillingService unit tests |
| `test_mcp_http.py` | HTTP MCP transport |
| `test_router.py` | Router recommendation + feedback |
| `test_security.py` | Auth middleware |
| `test_admin_dashboard.py` | Admin endpoints |
| `test_history_tools.py` | Pricing history + trends |
| `test_rag.py` | TF-IDF RAG pipeline |
| `test_agent.py` | ReAct agent unit tests |
| `test_currency.py` | Currency service: map, precedence, staleness, rounding, rate table |
| `test_currency_api.py` | REST locale resolution on the six endpoints, `/locale`, `/currency/rates`, privacy |
| `test_currency_mcp.py` | `currency` / `country` on the five MCP tools |
| `test_currency_ui.py` | Shared browser currency control |
| `test_refresh_exchange_rates.py` | Weekly rate-table refresh script + workflow job |

---

## Deployment

### Fly.io (Primary)

The app is deployed on [Fly.io](https://fly.io) and auto-deploys on every push to `master`.

```bash
# Install flyctl
# https://fly.io/docs/getting-started/installing-flyctl/

# Deploy manually
flyctl deploy

# Set secrets
flyctl secrets set \
  MCP_API_KEY=... \
  OPENAI_API_KEY=... \
  AGENT_LLM_PROVIDER=openai \
  AGENT_MODEL=gpt-4o-mini

# Set Stripe secrets (optional — free tier works without)
flyctl secrets set \
  STRIPE_SECRET_KEY=sk_live_... \
  STRIPE_WEBHOOK_SECRET=whsec_... \
  STRIPE_PRICE_ID_PRO=price_... \
  STRIPE_PRICE_ID_ENTERPRISE=price_... \
  BILLING_BASE_URL=https://llm-pricing-api.fly.dev
```

Configuration in `fly.toml`. Persistent volume at `/app/data/` stores all SQLite databases.

### GitHub Actions CI/CD

The `.github/workflows/ci-cd.yml` pipeline:
1. Runs all 667 tests on every PR
2. Deploys to Fly.io on `master` push (via `FLY_API_TOKEN` secret)
3. Performs a health check after deploy

### Health Check Endpoints

```bash
GET /health        → {"status":"healthy","version":"1.39.0"}
GET /health/live   → {"alive":true}
GET /health/ready  → {"ready":true,"checks":{...}}
GET /health/detailed → detailed environment + service statuses
```

### Blue-Green Deployment

Graceful shutdown with request draining is supported. See [docs/BLUE_GREEN_DEPLOYMENT.md](docs/BLUE_GREEN_DEPLOYMENT.md).

---

## Live Data

The server fetches pricing data from official public pricing pages using web scraping with smart caching. No provider API keys are required for pricing data.

- **Cache TTL**: 2 hours for pricing, 5 minutes for performance metrics
- **Fallback**: Static hardcoded data if live sources are unavailable
- **Providers**: OpenAI, Anthropic, Google, Cohere, Mistral AI, Groq, Together AI, Fireworks AI, Perplexity AI, AI21 Labs, Anyscale, Amazon Bedrock

Optional provider API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) improve model-list freshness but are not required.

---

## Contributing

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md). This repo uses **Git Flow**:

```
feature/<name>  →  develop (PR)  →  master (PR)  →  Fly.io auto-deploy
```

- Never commit directly to `develop` or `master`
- Branch names: `feature/<topic>-v<version>` (e.g. `feature/stripe-billing-v1.37.0`)
- Open PRs against `develop`; maintainers promote develop → master for releases

---

## License

MIT — see [LICENSE](LICENSE).

---

## Roadmap

### Completed

- [x] v1.39.0 — OpenAPI tags + grouped Swagger UI; 15th MCP tool (`ask_agent`)
- [x] v1.38.6 — MCP server fixes: initialize all services, register `ask_agent` tool, init PricingAlertService
- [x] v1.38.5 — Landing page MCP integration guide (Claude Desktop, VS Code, Cursor)
- [x] v1.38.4 — Billing beta UX: coming-soon copy, disabled paid buttons, graceful 503
- [x] v1.38.3 — Dark theme + shared nav for chat and conversations pages
- [x] v1.38.2 — Design system standardized across all 9 UIs (admin as canonical reference)
- [x] v1.38.1 — UI theme consistency pass
- [x] v1.38.0 — HTTP MCP transport (`POST /mcp`, `GET /mcp`) for remote clients
- [x] v1.37.3 — Mobile-responsive UI, consistent nav across all pages
- [x] v1.37.2 — SQLite directory auto-creation on Fly.io volume mount
- [x] v1.37.1 — Marketing landing page at `/`
- [x] v1.37.0 — Stripe SaaS billing: free signup, Pro/Enterprise tiers, self-serve portal
- [x] v1.36.0 — API key tiers (free/pro/enterprise), router feedback loop, streaming router, Fly.io migration
- [x] v1.35.0 — Quality value index, benchmark service, model router, savings tracker
- [x] v1.33.0 — Switched default LLM backend to GPT-4o-mini (~20× cheaper)
- [x] v1.27.0–v1.32.0 — Admin dashboard, widget, compare UI, calculator, price alerts, history export
- [x] v1.10.0–v1.26.0 — Agent + RAG pipeline, streaming, conversation memory, pricing history
- [x] v1.5.1 — 12 providers, 87+ models, live data fetching
- [x] v1.6.0 — Full MCP protocol (STDIO JSON-RPC 2.0), Claude Desktop integration

### Upcoming

- [ ] Stripe products live setup (configure products + set Fly.io secrets)
- [ ] Shut down Azure App Service (currently running in parallel at ~$27/mo)
- [ ] Custom domain
- [ ] Additional LLM backends (Groq direct, Ollama local)
- [ ] WebSocket support for live price subscriptions
