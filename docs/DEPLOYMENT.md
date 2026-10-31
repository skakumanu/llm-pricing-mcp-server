# Deployment

Production runs on **Fly.io** (app `llm-pricing-api`). Version: see `src/__init__.py`.
The old Azure blue-green docs are historical only:
[archive/BLUE_GREEN_DEPLOYMENT.md](archive/BLUE_GREEN_DEPLOYMENT.md),
[archive/DEPLOYMENT_IMPLEMENTATION.md](archive/DEPLOYMENT_IMPLEMENTATION.md).

## How a release deploys

- Git flow: `feature/*` → PR to `develop` → promotion PR to `master` (see [HOUSEKEEPING.md](HOUSEKEEPING.md)).
- Push to `master` runs `.github/workflows/ci-cd.yml`.
- `deploy_fly` waits for `test`, `lint`, `osv_scan`, `security`, `secret_scan`, then runs
  `flyctl deploy --remote-only` (token: `FLY_API_TOKEN` Actions secret).
- It polls `https://llm-pricing-api.fly.dev/health` for 3 min, expecting `200` and the
  `src/__init__.py` version. A timeout warns; it does not fail the build.
- No staging slot, no manual step: a green `master` deploys itself.

## Fly configuration (`fly.toml`)

| Setting | Value |
|---|---|
| Region / VM | `iad`, shared-cpu-1x, 512 MB |
| HTTP | port 8000, `force_https`, `GET /health` every 30s, `min_machines_running = 1` |
| Volume | `llm_pricing_data` → `/app/data` (both SQLite DBs live here) |
| `PRICING_HISTORY_DB_PATH` | `/app/data/pricing_history.db` |
| `BILLING_DB_PATH` | `/app/data/billing.db` (unset = ephemeral image disk) |
| `EXCHANGE_RATES_PATH` | `/app/vendored/exchange_rates.json` (the volume shadows the image's `/app/data`) |
| Build | repo `Dockerfile`: non-root uvicorn, `--workers 2`, port 8000; `Procfile` unused |

## Settings and secrets

- Settings are pydantic-settings (`src/config/settings.py`): field `mcp_api_key` ← env `MCP_API_KEY`.
- Provider API keys are optional; the server uses whichever are set.
- Secrets: `flyctl secrets set` (triggers a redeploy). Non-secrets may go in `fly.toml` `[env]`.
- `flyctl secrets list` shows names only.

```bash
# export values in your own shell first; never paste them into docs or chat
flyctl secrets set MCP_API_KEY="$MCP_API_KEY" OPENAI_API_KEY="$OPENAI_API_KEY"
```

- **`MCP_API_KEY` is required in production.** Unset: operator routes (`/deployment/shutdown`,
  `/admin/customers`, `GET /pricing/alerts`, ...) return 503; other keyed routes run open.
- `SERVER_HOST`/`SERVER_PORT` only apply to `python -m src.main`; on Fly the Dockerfile fixes port 8000.

## Stored provider keys (`PROVIDER_KEY_ENCRYPTION_KEY`)

- Encrypts customers' `/dispatch/keys` rows in `billing.db` (AES-256-GCM).
- Unset: `/dispatch/keys` returns 503; dispatch works with `X-Provider-Key-*` headers only.
- Value: 32 random bytes, base64url. Fly secret only, never `fly.toml` or git.

```bash
export PROVIDER_KEY_ENCRYPTION_KEY="$(python -c 'import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
flyctl secrets set PROVIDER_KEY_ENCRYPTION_KEY="$PROVIDER_KEY_ENCRYPTION_KEY"
```

- **Back it up outside Fly.** The DB cannot be decrypted without it.
- Changing or losing it: rows show `needs_reregistration`, are never sent to a provider, and
  every customer must re-register. No in-place rotation.
- Startup logs `Stored provider keys enabled` / `disabled`; a bad value logs the variable name only.

## Webhook destinations (`WEBHOOK_ALLOW_PRIVATE_TARGETS`)

- Price and budget alert webhooks deliver only to **public https URLs** (`src/services/webhook_guard.py`).
- Registration rejects with 422 `invalid_webhook_url`: http; private, loopback, link-local,
  metadata, CGNAT and ULA addresses (incl. Fly 6PN `fdaa::/16`); `localhost`, `*.internal`,
  `*.flycast`; hosts resolving to any private address; hosts that don't resolve.
- Each delivery re-checks the URL, connects only to the checked IP and never follows redirects.
- Older alerts that break the rules are kept but skipped at delivery (log: `delivery blocked (<reason>)`).
  Remove them via operator `GET`/`DELETE /pricing/alerts` and `/usage/alerts`.
- **`WEBHOOK_ALLOW_PRIVATE_TARGETS` must stay unset on Fly.** It is for local receivers only.
  When on, startup logs `WEBHOOK_ALLOW_PRIVATE_TARGETS is ON ...` at WARNING; if that shows in
  `flyctl logs`, run `flyctl secrets unset WEBHOOK_ALLOW_PRIVATE_TARGETS`.
- Webhook delivery ignores `HTTPS_PROXY` / `HTTP_PROXY` (`trust_env=False`, pinned transport).
  Other outbound calls still honour them.

## Operations

| Task | Command |
|---|---|
| Break-glass deploy (CI down) | `flyctl deploy --remote-only` |
| Health / version | `curl -s https://llm-pricing-api.fly.dev/health \| jq .version` |
| Spot-check | `curl https://llm-pricing-api.fly.dev/models`, `/data-quality` |
| Logs / status | `flyctl logs`, `flyctl status` |
| Volume attached? | `flyctl volumes list` |
| Scale | `flyctl scale vm shared-cpu-2x --memory 1024`, `flyctl scale count 2` |
| Restart | `flyctl apps restart llm-pricing-api` |

## Troubleshooting

- **`deploy_fly` skipped:** runs only on `master` push or `workflow_dispatch`, after all five gates pass.
- **Health check timed out:** usually a slow cold start; check `flyctl logs` / `flyctl status`.
- **App won't start:** `flyctl logs`; usually a missing env var or a Docker build error.
- **Data missing after deploy:** confirm `llm_pricing_data` is still attached.
- **Rollback:** revert on `master` via git flow and let CI redeploy, or break-glass deploy a known-good commit.
