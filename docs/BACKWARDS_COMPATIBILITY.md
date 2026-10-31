# Backwards Compatibility

- The public API is **v1**, the only version. No v2 is planned.
- App releases (see `src/__init__.py`) are semver; every v1 change is additive.
- `GET /api/versions` reports `current_version`, `all_versions`, `deprecated_versions` and the
  link to this page. App release: `GET /health` → `version`.

## Change policy: additive only

| Bump | Allowed |
|---|---|
| Patch | Bug fixes, no contract change |
| Minor | New endpoints, MCP tools, optional params, optional response fields |
| Major | Breaking changes (none planned) |

Allowed in any v1 release:
- New endpoints, MCP tools, models and providers
- New **optional** query params, body fields or response fields
- More descriptive error messages; faster responses

## What counts as breaking

- Removing or renaming an endpoint, MCP tool, query param or response field
- Making an optional input required, or changing a field's type or meaning
- Changing an endpoint's HTTP method or response shape (e.g. `/pricing`'s object → bare array)
- Tightening auth on a public route without a deprecation window

Data changes are **not** breaking: models added, retired or withheld, prices corrected, counts changing.

## Stable v1 surface

| Group | Endpoints | Auth |
|---|---|---|
| Info | `GET /`, `GET /models` | public |
| Pricing | `GET /pricing` (`?provider=`), `GET /performance` (`?sort_by=`), `GET /use-cases` | public |
| Estimates | `POST /cost-estimate`, `POST /cost-estimate/batch` | public, rate-limited |
| Health | `GET /health`, `/health/detailed`, `/health/ready`, `/health/live` | public |
| Telemetry | `GET /telemetry` | public |
| Meta | `GET /api/versions`, `GET /deployment/metadata` | `x-api-key` when `MCP_API_KEY` is set |

A free key from `POST /billing/signup` satisfies `x-api-key`.

## Guaranteed fields

| Model | Fields |
|---|---|
| `PricingMetrics` | `model_name`, `provider`, `cost_per_input_token`, `cost_per_output_token`, `currency`, `unit`, `source`, `last_updated` |
| `PricingMetrics` (computed) | `cost_at_10k_tokens`, `cost_at_100k_tokens`, `cost_at_1m_tokens`, `estimated_time_1m_tokens` |
| `CostEstimateResponse` | `model_name`, `provider`, `input_tokens`, `output_tokens`, `input_cost`, `output_cost`, `total_cost`, `currency`, `timestamp` |

## Deprecation process

1. Add the replacement first; the old path keeps working unchanged.
2. Announce in `static/whats-new/index.html` and the API docs, naming the replacement.
3. List deprecated API versions in `/api/versions` `deprecated_versions`.
4. Remove only in a major release, never in a minor or patch.

## Client rules

- Ignore unknown fields; read fields by name with defaults (`.get()`).
- Don't hardcode model, provider or field counts.
- Treat `price_confirmed=False` and `retired_on` as normal data states.
- Gate new features on `/health` `version`, not on `/api/versions`.
