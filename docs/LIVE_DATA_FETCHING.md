# Live Data Fetching Architecture

## Overview

**API keys are completely optional.** The pricing the server serves comes primarily from
curated per-provider tables, checked against an external price registry; live fetching
from provider pages and APIs is a best-effort layer on top. The web-scraping /
status-page layer described further down dates from v1.4.2 and is still in the code for
the original providers, but it is no longer the main source of prices.

## How pricing is actually produced

`PricingAggregatorService.get_all_pricing_async()` (`src/services/pricing_aggregator.py`)
runs every provider service concurrently and then post-processes the combined list:

1. **Curated table.** Each provider service (`src/services/*_pricing.py`) builds its models
   from its hand-maintained `STATIC_PRICING` dict. About a dozen of the older services
   (OpenAI, Anthropic, Google, Cohere, Mistral, Groq, Together, Fireworks, Perplexity, ...)
   first try the `DataFetcher` web-scrape described below and fall back to
   `STATIC_PRICING`; the rest use `STATIC_PRICING` directly.
2. **Registry-sourced models.** `BasePricingProvider.get_pricing_with_status()` merges
   `data/registry_models/<provider>.json` (written by `scripts/pickup_registry_models.py`);
   a curated entry always wins a name collision.
3. **Live model sync (only with an API key).** If the provider has a model-list endpoint and
   a key is configured, the live model IDs (cached 6 h) filter out models the provider no
   longer serves and surface new ones as unpriced (`price_confirmed=False`).
4. **Price oracle.** `src/services/price_oracle.py` loads the LiteLLM price registry
   (`raw.githubusercontent.com/BerriAI/litellm/...`, cached 24 h, falling back to the vendored
   `data/model_prices_snapshot.json` on any failure). It fills prices for unpriced models,
   withholds (sets `price_confirmed=False` on) curated prices that drift more than 5% from the
   registry, and withholds models past their registry deprecation date. Curated numbers are
   never overwritten. If the oracle fails entirely, curated prices are served as-is.
5. **Confirmed-only by default.** Models with `price_confirmed=False` are dropped unless the
   caller asks for unconfirmed models.

The snapshot is rebuilt weekly by `.github/workflows/price-audit.yml`, which also opens an
issue when curated prices drift from the registry. `check_price_drift` and
`get_data_quality` (and `GET /data-quality`) show what is currently withheld.

## Legacy public data sources (web-scrape layer)

The original five providers were configured with these **public endpoints**:

| Provider | Pricing URL | Status Page |
|----------|-------------|-------------|
| **OpenAI** | https://openai.com/api/pricing/ | https://status.openai.com/ |
| **Anthropic** | https://www.anthropic.com/api | https://www.anthropic.com/ |
| **Google** | https://ai.google.dev/pricing | https://status.cloud.google.com/ |
| **Cohere** | https://cohere.com/pricing | https://status.cohere.com/ |
| **Mistral** | https://mistral.ai/technology/#pricing | https://status.mistral.ai/ |

## How the web-scrape layer works without API keys

For the services that use `DataFetcher`, the per-provider fallback is:

```
1. Web Scrape Pricing Pages (Public - No Auth)
       ↓ (no usable table found, or fetch failed)
2. Use the provider's curated STATIC_PRICING entry for each model
       ↓ (in parallel)
3. Check health endpoints / status pages for latency (Public - No Auth)
       ↓ (any exception)
4. Return the curated static data (Always Available)
```

Most provider pricing pages are rendered client-side, so the default table parser usually
finds nothing and step 2 supplies the price.

## Architecture Components

### 1. Data Sources Configuration (`src/services/data_sources.py`)

Centralized configuration for all data sources with:

```python
PricingDataSource:
  - provider: Provider name
  - source_type: API, WEB_SCRAPE, STATIC, or HYBRID
  - api_endpoint: Provider API URL
  - pricing_url: Pricing page URL
  - cache_ttl_seconds: Cache time-to-live (dataclass default 3600s; every
    configured provider sets 7200s)
  - requires_auth: Whether API key needed

PerformanceDataSource:
  - Same structure for performance metrics
  - cache_ttl_seconds: 300s (5 mins) for fresh metrics
```

**Configured Providers:** `PRICING_SOURCES` and `PERFORMANCE_SOURCES` each have 21
entries (OpenAI through Oracle OCI), all typed `HYBRID`. Providers added later have no
entry here and do not use the web-scrape layer.

### 2. Data Fetcher Utility (`src/services/data_fetcher.py`)

Core utility class providing:

#### `fetch_with_cache(cache_key, fetch_func, ttl_seconds, fallback_data)`
- Implements smart caching with TTL
- Returns cached data if still valid (500x+ faster)
- Falls back to provided default data on failure
- Logs all operations for debugging

#### `fetch_api_models(api_endpoint, api_key, require_auth)`
- Fetches available models from provider APIs
- Handles authentication headers
- Parses different API response formats (OpenAI, Anthropic-like)
- Returns list of available model names

#### `fetch_pricing_from_website(url, parser_func)`
- Implements web scraping for pricing pages
- Supports custom parser functions
- Includes default BeautifulSoup-based HTML parser
- Looks for common pricing table patterns

#### `check_api_health(endpoint, api_key)`
- Performs lightweight health checks
- Measures actual API latency
- Returns: status, latency (ms), timestamp
- Sends `HEAD` requests: the public status/health URL first, then the API endpoint
  (with the key, if one is set)

#### Smart Cache Management
```python
class CachedData:
  - Stores fetched data with timestamp
  - Checks expiration based on TTL
  - Automatic cleanup of expired entries
```

## Provider Implementation Pattern

All providers follow enhanced data fetching:

```python
async def fetch_pricing_data(self) -> List[PricingMetrics]:
    """
    1. Fetch live models from API (if API key available)
    2. Fetch live pricing from website (with web scraping)
    3. Fetch live performance metrics (API health check)
    4. Merge all data sources
    5. Fall back to static data if any step fails
    """
```

### Example Flow for OpenAI

```
fetch_pricing_data()
├─ Fetch live models from OpenAI API (only when OPENAI_API_KEY is set)
│  └─ Cache for 2 hours
├─ Fetch live pricing from openai.com/api/pricing/
│  └─ Cache for 2 hours
├─ Fetch performance metrics from API health check
│  └─ Cache for 5 minutes (more frequent updates)
└─ Merge and return PricingMetrics
   ├─ priority: scraped website price (if a table was parsed)
   ├─ fallback: curated STATIC_PRICING entry
   └─ on failure: use all static data
```

After the service returns, the shared steps in "How pricing is actually produced"
(registry models, live model sync, price oracle) apply.

## Performance Metrics

Performance data is now calculated from real API measurements:

```python
# Method 1: Direct API Health Check
latency_ms = actual measured latency from API call

# Method 2: Calculated Throughput
# Based on latency instead of hard-coded estimates
throughput = base_throughput * (expected_latency / actual_latency)
```

**Cache Benefits Measured:**
- First call: ~550ms (real API health check)
- Cached calls: ~1ms (442x faster)
- 5-minute refresh for performance data keeps metrics fresh

## Data Source Types

### API Sources
- **OpenAI**: `/v1/models` endpoint
- **Anthropic**: `/v1/models` endpoint (with format adaptation)
- **Google**: `/v1beta/models` endpoint
- **Cohere**: `/v1/models` endpoint
- **Mistral AI**: `/v1/models` endpoint

### Web Scraping Sources
- **OpenAI**: https://openai.com/api/pricing/
- **Anthropic**: https://www.anthropic.com/api
- **Google**: https://ai.google.dev/pricing
- **Cohere**: https://cohere.com/pricing
- **Mistral AI**: https://mistral.ai/technology/#pricing

### Static Fallback
- Comprehensive hardcoded pricing data for each provider
- Use cases, strengths, and best_for metadata
- Ensures service continues even if external sources unavailable

## Caching Strategy

```
Type                         TTL       Where                         Purpose
──────────────────────────────────────────────────────────────────────────────
Price registry (oracle)      86400s    price_oracle.py               Gap-fill + drift check
Live model IDs (API key)     21600s    base_provider.py              Model availability
Web-scrape models / pricing  7200s     data_sources.py PRICING_*     Legacy live layer
Web-scrape performance       300s      data_sources.py PERFORMANCE_* Fresh latency data
Static Data                  None      *_pricing.py STATIC_PRICING   Primary prices
```

All caches are in-memory and per process; the aggregator itself is a process-wide
singleton, so REST endpoints and MCP tools share them.

**Cache Key Naming Convention:**
- `{provider_lowercase}_models`: Available models
- `{provider_lowercase}_pricing_web`: Web-scraped pricing
- `{provider_lowercase}_performance`: API health metrics

## Error Handling and Fallback

```
Step 1: Try Live API
    ↓ (Exception caught)
Step 2: Return Cached Data (if valid and available)
    ↓ (No cache)
Step 3: Return Static Data (always available)
    ↓ (or)
Step 4: Log error and continue with best available
```

A provider service that raises is reported in `provider_status` as unavailable and its
models are skipped; the other providers are still returned. Fetch failures are logged at
WARNING (a provider service falling back to static data logs at ERROR).

## Configuration for External Deployment

**API keys are optional** and not required to get live pricing and performance data.

### Option 1: Without API Keys (Recommended for Most Users)

The system automatically uses public pricing pages and status pages:

```bash
# No configuration needed! The system works out-of-the-box with:
# - Web scraping of official pricing pages
# - Public status page health checks
# - Smart caching (2 hours for pricing, 5 minutes for performance)
```

Just deploy and run - no API keys needed!

### Option 2: With API Keys (For API Model Lists)

If you want the absolute latest model list from provider APIs, set optional API keys:

```bash
# Optional: Set only if you want authenticated API access for model lists
OPENAI_API_KEY=sk-...              # Optional
ANTHROPIC_API_KEY=sk-ant-...       # Optional
GOOGLE_API_KEY=...                 # Optional
COHERE_API_KEY=...                 # Optional
MISTRAL_API_KEY=...                # Optional
```

**Benefits of providing API keys:**
- Get latest available models directly from provider APIs
- Fallback to web scraping if API models unavailable
- Performance slightly improved with direct API health checks

**Without API keys:**
- Prices come from the curated tables plus the price registry (no key needed)
- Still get performance metrics from public status pages
- Still get model list from static data (always available)
- Service **never fails** - always working

### Network Requirements

The system needs outbound HTTPS access to:
- Price registry: `raw.githubusercontent.com` (without it the vendored snapshot is used)
- Pricing pages (for web scraping): `openai.com`, `anthropic.com`, `ai.google.dev`, `cohere.com`, `mistral.ai`, and the other `pricing_url`s in `data_sources.py`
- Status pages (for health checks): `status.openai.com`, `status.cloud.google.com`, `status.cohere.com`, `status.mistral.ai`

No auth required - all public domains with standard HTTPS.

## Security Considerations

✅ API keys never logged or exposed in responses
✅ Health checks use lightweight HEAD requests only
✅ Caching prevents excessive API calls
✅ External calls have timeouts (10s for `DataFetcher` and live model sync, 20s for the
   price-registry fetch)
✅ Failures don't break the service (fallback mechanism)

## Future Enhancements

- [ ] Custom parser functions for complex pricing pages
- [x] Database persistence for historical pricing trends (`pricing_history.db`, `/pricing/history`, `/pricing/trends`)
- [ ] GraphQL query support for common data patterns
- [x] Notification when pricing changes (price-change alert webhooks, `/pricing/alerts`)
- [ ] Provider-specific health dashboards
- [x] Cost estimation with live pricing data (`/cost-estimate`)
- [x] Historical pricing data archive (snapshots every `PRICING_SNAPSHOT_INTERVAL_HOURS`, default 6)

## Development and Testing

### Running Tests

```bash
# Price oracle, provenance and data quality
python -m pytest tests/test_price_oracle.py tests/test_price_provenance.py tests/test_data_quality.py -q

# Test specific providers
python -m pytest tests/test_services.py -v
```

### Adding New Data Sources

New providers no longer need a `data_sources.py` entry:

1. Create a provider service in `src/services/<name>_pricing.py` subclassing
   `BasePricingProvider`, with a curated `STATIC_PRICING` table
2. Optionally configure the live model-list endpoint (`_live_model_api_endpoint` and
   friends in `base_provider.py`) so an API key enables model sync
3. Register the service in `pricing_aggregator.py`
4. Add tests (see `tests/test_new_providers.py`)

## Monitoring

Monitor these metrics in production:

- **Cache hit rate**: Should be >95% for pricing data
- **API response times**: Should be <1s under normal conditions
- **Fallback frequency**: Should be <1% of requests
- **Error rates**: All errors should be logged and monitored

## Source Information

The `source` field in API responses indicates data origin (OpenAI shown; other
`DataFetcher` providers use the same pattern):

- `"OpenAI Official API"` - price parsed from the scraped pricing page
- `"OpenAI Official Pricing (Cached)"` - the curated `STATIC_PRICING` entry, used when no
  scraped price matched (despite the label, not a cached live fetch)
- `"OpenAI Official Pricing (Fallback - Static)"` - curated table after the live path raised
- `"LiteLLM price registry (<registry key>)"` - price taken from the price registry
  (runtime gap-fill or a `data/registry_models` entry)

Each model also carries `price_confirmed` and `price_as_of` provenance fields.

This helps users understand data freshness and reliability.
