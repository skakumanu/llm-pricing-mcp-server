# LLM Pricing MCP Server

[![CI/CD Pipeline](https://github.com/skakumanu/llm-pricing-mcp-server/workflows/CI%2FCD%20Pipeline/badge.svg)](https://github.com/skakumanu/llm-pricing-mcp-server/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A production-ready **Model Context Protocol (MCP)** server for LLM pricing data. Provides a RESTful API (FastAPI), **14 MCP tools** over STDIO and HTTP, a conversational Agent + RAG pipeline, browser-based UIs, and a self-serve billing dashboard — for pricing data from **12 major LLM providers**.

**Live at**: https://llm-pricing-api.fly.dev

---

## Features

### MCP Interface (STDIO + HTTP)
- **14 MCP Tools**: `get_all_pricing`, `estimate_cost`, `compare_costs`, `get_performance_metrics`, `get_use_cases`, `get_telemetry`, `get_pricing_history`, `get_pricing_trends`, `register_price_alert`, `list_price_alerts`, `delete_price_alert`, `get_pricing_export_url`, `list_conversations`, `delete_conversation`
- **STDIO transport** — JSON-RPC 2.0 over STDIO for local Claude Desktop integration
- **HTTP transport** — `POST /mcp` (JSON-RPC 2.0 over HTTP) for remote MCP clients — no local install needed
- MCP Protocol version: `2024-11-05`

### RESTful API
- Real-time pricing from 12 providers (87+ models), async fetching with smart caching
- Cost estimation: single model (`POST /cost-estimate`) and batch comparison (`POST /cost-estimate/batch`)
- Performance metrics, use-case recommendations, pricing history, trends
- Router recommendation (`POST /router/recommend`) with feedback loop
- OpenAI-compatible proxy (`POST /v1/chat/completions`) for drop-in SDK integration
- Webhook alerts for price changes (HMAC-SHA256 signed)
- Interactive docs: `/docs` (Swagger) and `/redoc`

### Agent + RAG Pipeline
- **Configurable LLM backend**: OpenAI GPT-4o-mini (default) or Anthropic Claude via env vars
- **ReAct loop agent** with access to all 14 MCP tools
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
| `/admin` | Server stats, rate limits, customers |

All UIs are mobile-responsive with a consistent navigation bar.

### SaaS Billing (Stripe)
- **Free tier signup**: `POST /billing/signup` (email → API key, no Stripe required)
- Paid tiers (Pro/Enterprise) via Stripe Checkout → auto-updates rate limits
- Self-serve portal: `GET /billing/portal`
- Usage dashboard: `GET /billing/me` (router calls, savings, acceptance rate)
- Stripe webhook handler: `POST /billing/webhook`

### API Key Tiers & Rate Limiting
- **Free**: 30 req/min · **Pro**: 120 req/min · **Enterprise**: 600 req/min
- Tier auto-detected from billing DB (customer API key) or `X-Api-Key-Tier` header
- `GET /rate-limits/tiers` — public endpoint listing tier details

### Security & Quality
- Most endpoints are public (read-only pricing data, UIs, MCP tools)
- Protected endpoints (`/billing/me`, `/router/recommend`, `/router/feedback`, `/billing/portal`) require a billing API key or the global `MCP_API_KEY`
- Rate limiting per client IP + tier bucket
- Request size limit (1MB default)
- 625 passing tests, CI/CD on every PR

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

Restart Claude Desktop. All 14 pricing tools are immediately available.

### Option B — Local STDIO

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
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]}'
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
| `tools/list` | List all 14 tools with input schemas |
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

`GET /mcp` returns server info and a config snippet.

---

## API Reference

### Public Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Landing page (HTML) |
| GET | `/pricing` | All model pricing (`?provider=` filter) |
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
│   ├── __init__.py                  # Version (1.38.0)
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
│   ├── server_azure.py              # STDIO server proxying remote API
│   └── tools/                       # 14 MCP tool implementations
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
├── tests/                           # 625 tests
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
# Run all 625 tests
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
1. Runs all 625 tests on every PR
2. Deploys to Fly.io on `master` push (via `FLY_API_TOKEN` secret)
3. Performs a health check after deploy

### Health Check Endpoints

```bash
GET /health        → {"status":"healthy","version":"1.38.0"}
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
