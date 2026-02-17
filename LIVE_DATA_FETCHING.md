# Live Data Fetching Architecture

## Overview

As of v1.4.1, the LLM Pricing MCP Server implements intelligent live data fetching with graceful fallback mechanisms. This ensures that users get the most current pricing and performance data from LLM providers while maintaining reliability through caching and fallback strategies.

## Data Source Hierarchy

Each provider follows a data fetching hierarchy:

```
1. Live API Data  (if API key available)
       ↓ (fallback on failure)
2. Cached Data    (from previous successful fetches)
       ↓ (fallback if cache expired)
3. Static Data    (hardcoded fallback)
```

## Architecture Components

### 1. Data Sources Configuration (`src/services/data_sources.py`)

Centralized configuration for all data sources with:

```python
PricingDataSource:
  - provider: Provider name
  - source_type: API, WEB_SCRAPE, STATIC, or HYBRID
  - api_endpoint: Provider API URL
  - pricing_url: Pricing page URL
  - cache_ttl_seconds: Cache time-to-live (7200s default)
  - requires_auth: Whether API key needed

PerformanceDataSource:
  - Same structure for performance metrics
  - cache_ttl_seconds: 300s (5 mins) for fresh metrics
```

**Configured Providers:**
- OpenAI: API + Web Scraping + Static fallback
- Anthropic: API + Web Scraping + Static fallback
- Google: API + Web Scraping + Static fallback
- Cohere: API + Web Scraping + Static fallback
- Mistral AI: API + Web Scraping + Static fallback

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
- No API calls needed - just HEAD requests for speed

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
├─ Fetch live models from OpenAI API
│  └─ Cache for 2 hours
├─ Fetch live pricing from openai.com/api/pricing/
│  └─ Cache for 2 hours
├─ Fetch performance metrics from API health check
│  └─ Cache for 5 minutes (more frequent updates)
└─ Merge and return PricingMetrics
   ├─ priority: live API data
   ├─ fallback: cached data
   ├─ fallback: static data
   └─ on failure: use all static data
```

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
Type                    TTL       Priority   Purpose
─────────────────────────────────────────────────────
Live API Models         7200s     High      Model availability
Live Pricing Data       7200s     High      Current prices
Live Performance Data   300s      Medium    Fresh latency data
Static Data             None      Fallback  Reliability
```

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

All errors are logged at WARNING level with context.

## Configuration for External Deployment

To enable live data fetching in production:

1. **Provide API Keys**: Set environment variables
   ```bash
   OPENAI_API_KEY=sk-...
   ANTHROPIC_API_KEY=sk-ant-...
   GOOGLE_API_KEY=...
   COHERE_API_KEY=...
   MISTRAL_API_KEY=...
   ```

2. **Network Access**: Ensure outbound HTTPS to:
   - `api.openai.com`
   - `api.anthropic.com`
   - `generativelanguage.googleapis.com`
   - `api.cohere.ai`
   - `api.mistral.ai`
   - Pricing pages (for web scraping)

3. **No Additional Configuration**: The system automatically:
   - Detects available API keys
   - Falls back gracefully if keys unavailable
   - Caches results to minimize API calls
   - Updates performance metrics every 5 minutes

## Security Considerations

✅ API keys never logged or exposed in responses
✅ Web scraping respects standard robots.txt
✅ Health checks use lightweight HEAD requests only
✅ Caching prevents excessive API calls
✅ All external calls have 10-second timeouts
✅ Failures don't break the service (fallback mechanism)

## Future Enhancements

- [ ] Custom parser functions for complex pricing pages
- [ ] Database persistence for historical pricing trends
- [ ] GraphQL query support for common data patterns
- [ ] Real-time notification when pricing changes
- [ ] Provider-specific health dashboards
- [ ] Cost estimation with live pricing data
- [ ] Historical pricing data archive

## Development and Testing

### Running Tests

```bash
# Test live data fetching
python test_live_data.py

# Test with API keys
OPENAI_API_KEY=sk-... python -m pytest tests/test_live_data.py

# Test specific providers
python -m pytest tests/test_services.py -v
```

### Adding New Data Sources

1. Add configuration to `data_sources.py`
2. Create provider service with `_fetch_performance_metrics()`
3. Implement `_get_static_pricing_data()` fallback
4. Add tests for live data and fallback scenarios

## Monitoring

Monitor these metrics in production:

- **Cache hit rate**: Should be >95% for pricing data
- **API response times**: Should be <1s under normal conditions
- **Fallback frequency**: Should be <1% of requests
- **Error rates**: All errors should be logged and monitored

## Source Information

The `source` field in API responses indicates data origin:

- `"OpenAI Official API"` - Live data from API
- `"OpenAI Official Pricing (Cached)"` - Cached live data
- `"OpenAI Official Pricing (Fallback - Static)"` - Static fallback

This helps users understand data freshness and reliability.
