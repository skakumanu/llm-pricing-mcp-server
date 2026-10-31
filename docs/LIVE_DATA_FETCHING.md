# How a Price Gets into the Catalogue

- API keys are **optional**. Prices come from curated tables checked against an external registry.
- Entry point: `PricingAggregatorService.get_all_pricing_async()` in `src/services/pricing_aggregator.py`.
- It runs every provider service concurrently, then applies the stages below in order.

| # | Stage | Code | Effect |
|---|---|---|---|
| 1 | Curated tables | `src/services/*_pricing.py` `STATIC_PRICING` | Primary prices |
| 2 | Registry-sourced models | `data/registry_models/<provider>.json` | Adds models the tables lack |
| 3 | Live model sync | `base_provider.py` (needs API key) | Drops dead models, surfaces new ones |
| 4 | Price oracle | `src/services/price_oracle.py` | Gap-fill, drift withholding, retirement |
| 5 | Confirmed-only filter | aggregator | Drops `price_confirmed=False` unless `include_unconfirmed=True` |

## 1. Curated tables

- Each provider service builds models from its hand-maintained `STATIC_PRICING` dict.
- About a dozen older services (OpenAI, Anthropic, Google, Cohere, Mistral, Groq, ...) first try a
  `DataFetcher` web scrape (`src/services/data_fetcher.py`). Most pricing pages render client-side,
  so the scrape usually finds nothing and `STATIC_PRICING` supplies the price.
- `data_sources.py` lists 21 legacy providers for that scrape; newer providers have no entry.
- A provider service that raises is marked unavailable in `provider_status`; other providers still return.

## 2. Registry-sourced models

- `BasePricingProvider.get_pricing_with_status()` merges `data/registry_models/<provider>.json`.
- Written by `scripts/pickup_registry_models.py` (weekly pickup PR).
- A curated entry always wins a name collision.
- Labelled `price_source_kind: registry`: usable in estimates, never counted as provider-confirmed.

## 3. Live model sync (API key only)

- With a key and a model-list endpoint, live model IDs (cached 6 h) are compared to the catalogue.
- Models the provider no longer serves are filtered out.
- New models appear unpriced (`price_confirmed=False`) until stage 4 or a curated entry prices them.

## 4. Price oracle

- Loads the LiteLLM price registry from `raw.githubusercontent.com` (cached 24 h, 20 s timeout).
- On any failure it uses the vendored `data/model_prices_snapshot.json`; if that fails too, curated prices are served as-is.
- Curated numbers are **never overwritten**. The oracle runs three passes:

| Pass | Method | Rule |
|---|---|---|
| Gap-fill | `fill_missing_prices` | Prices unpriced models from the registry |
| Drift withholding | `demote_drifted` | Curated price >5% off the registry → `price_confirmed=False` |
| Retirement | `withhold_retired` | Registry `deprecation_date` ≤ today → withheld, `retired_on` set |

- Retired models stay visible as retired rather than vanishing or being priced.
- Inspect: `check_price_drift` (per model), `get_data_quality` / `GET /data-quality` (totals).

## Cache TTLs

All caches are in-memory and per process. The aggregator is a singleton, so REST and MCP share them.

| Cache | TTL | Where |
|---|---|---|
| Price registry | 24 h | `price_oracle.py` |
| Live model IDs | 6 h | `base_provider.py` |
| Web-scrape models / pricing | 2 h | `data_sources.py` `PRICING_SOURCES` |
| Web-scrape performance (latency) | 5 min | `data_sources.py` `PERFORMANCE_SOURCES` |
| `STATIC_PRICING` | none | `*_pricing.py` |

- Pricing history snapshots go to `pricing_history.db` every `PRICING_SNAPSHOT_INTERVAL_HOURS` (default 6).

## Weekly price audit

`.github/workflows/price-audit.yml`, Mondays 06:00 UTC. Each job opens a PR against `develop` only when something changed.

| Job output | Script | Changes |
|---|---|---|
| `price-drift` issue | `check_price_drift` tool | Rolling issue listing withheld models |
| Snapshot refresh PR | inline rebuild + `validate_price_snapshot.py` | `data/model_prices_snapshot.json` |
| Exchange-rate PR | `refresh_exchange_rates.py` | `data/exchange_rates.json` (stale after 7 days) |
| Reconfirm PR | `reconfirm_stale_prices.py` | `price_as_of` dates only |
| Retire PR | `retire_models.py --grace-days 30` | Removes entries retired >30 days |
| Pickup PR | `pickup_registry_models.py` | `data/registry_models/*.json` |

- Drift is withheld at request time anyway; the audit exists so a human fixes `STATIC_PRICING`.
- Handling these PRs: see [HOUSEKEEPING.md](HOUSEKEEPING.md#weekly-price-audit-prs).

## Response provenance

| `source` value | Meaning |
|---|---|
| `"<Provider> Official API"` | Parsed from the scraped pricing page |
| `"<Provider> Official Pricing (Cached)"` | Curated `STATIC_PRICING` entry (not a cached fetch) |
| `"<Provider> Official Pricing (Fallback - Static)"` | Curated table after the live path raised |
| `"LiteLLM price registry (<key>)"` | Registry gap-fill or `data/registry_models` entry |

- Every model also carries `price_confirmed` and `price_as_of`.

## Network and keys

- Outbound HTTPS: `raw.githubusercontent.com` (registry), provider pricing pages and status pages.
- Without network the vendored snapshot and curated tables still serve every price.
- Timeouts: 10 s for `DataFetcher` and live model sync, 20 s for the registry.
- API keys are never logged or returned.

## Adding a provider

1. Create `src/services/<name>_pricing.py` subclassing `BasePricingProvider` with a `STATIC_PRICING` table.
2. Optionally set `_live_model_api_endpoint` (see `base_provider.py`) so a key enables model sync.
3. Register it in `pricing_aggregator.py`.
4. Add tests (pattern: `tests/test_new_providers.py`); run the CLAUDE.md data-accuracy check.
