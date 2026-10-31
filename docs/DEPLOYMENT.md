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
- After a confirmed deploy it tags `vX.Y.Z` and publishes a GitHub Release whose notes are
  that version's What's New entry (`scripts/release_notes_from_whats_new.py`). An unconfirmed
  health check publishes nothing, so the Releases page only ever reflects what is live.
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
| `PRICE_SNAPSHOT_PATH` | `/app/vendored/data/model_prices_snapshot.json` (same reason, v1.84.1) |
| `REGISTRY_MODELS_DIR` | `/app/vendored/data/registry_models` (same reason, v1.84.1; without it production serves only the curated ~270 models) |
| `ORCHESTRATOR_DB_PATH` | `/app/data/orchestrator.db` (orchestrator core, v1.83.0) |
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

## Orchestrator intake key (`ORCHESTRATOR_INTAKE_KEY`)

- The GitHub Actions executor (`.github/workflows/orchestrator-intake.yml` and the weekly
  audit's `report-run` job) authenticates with this separate credential. The service accepts
  it only on `POST /orchestrator/events`, `GET /orchestrator/runs/{id}/actions` and
  `POST /orchestrator/runs/{id}/actions/{aid}/result`; every other `/orchestrator/*` route and
  the `/admin` Orchestrator block need `MCP_API_KEY`.
- Unset: those three routes still take the operator key; the Actions jobs print one
  step-summary line and exit 0 (nothing fails).
- Set it in **two places** with the same value: a Fly secret and a GitHub repository secret of
  the same name. Generate it locally; never paste it into docs, chat, issues or the workflow file.
- What it does unlock: the events it posts carry the workflow's `actor_is_owner` assertion, so a
  holder could complete a run's `sdlc_run` checkpoint with a pull request **in the run's own
  repository** (the service refuses any other repository or PR number with `refused:repo_mismatch`)
  and could post executor results. It cannot approve anything in the admin queue, change settings,
  read the run log, or start owner-request workflows. Rotate it if it may have leaked.

```bash
export ORCHESTRATOR_INTAKE_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
flyctl secrets set ORCHESTRATOR_INTAKE_KEY="$ORCHESTRATOR_INTAKE_KEY"
gh secret set ORCHESTRATOR_INTAKE_KEY --body "$ORCHESTRATOR_INTAKE_KEY"
# optional, defaults to https://llm-pricing-api.fly.dev
gh variable set ORCHESTRATOR_BASE_URL --body "https://llm-pricing-api.fly.dev"
```

- Rotation: set the new value in both places (two commands, no downtime); the old value stops
  working at the next deploy. Startup logs `Orchestrator initialized ... (intake key set|unset)`.

## Stripe (paid tiers, v1.84.0)

- Checkout, the customer portal and the webhook stay off (503, and `/billing` shows "Checkout
  unavailable on this server") until these four secrets are set. Everything else, including
  free signup and the support assistant, works without them.

```bash
# export STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_ID_PRO and STRIPE_PRICE_ID_ENTERPRISE
# in your own shell first (never paste them into docs or chat), then import them as Fly secrets:
env | grep '^STRIPE_' | flyctl secrets import
```

- Point the Stripe webhook at `POST /billing/webhook` and subscribe it to exactly these events:
  `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`,
  `invoice.paid`, `invoice.payment_failed`, `charge.refunded`, `charge.dispute.created`. Other
  event types are acknowledged and recorded as `ignored`; redelivered event ids are ignored.
- `BILLING_PAST_DUE_GRACE_DAYS` (14): a failed renewal keeps the stored tier for this long, then
  the account is served at the free rate limit until `invoice.paid` arrives. Refunds, disputes and
  failed renewals also open an escalation in the `/admin` queue.
- `GET /billing/config` shows what the running server can sell (`checkout_enabled`, per-tier
  `checkout_available`).

## Support assistant (`SUPPORT_*`)

- The support agent runs on the same LLM key as the pricing agent (`ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY`). `SUPPORT_ENABLED=false` turns it into a deterministic FAQ-and-escalation
  fallback without touching the pricing agent.
- Per-caller daily caps: `SUPPORT_DAILY_CAP_ANONYMOUS_USD` (0.10), `_FREE_USD` (0.50), `_PRO_USD`
  (3.00), `_ENTERPRISE_USD` (10.00); `SUPPORT_MAX_ESCALATIONS_PER_DAY` (3; anonymous callers get 1).
  The counters are per worker process; the authoritative ceiling is the orchestrator spend ledger
  (every call is recorded under workflow `support_triage`, default 40 USD/month, under the global
  150 USD/month cap). The agent refuses to call the LLM when the orchestrator DB is unavailable.
- `SUPPORT_FALLBACK_PRICE_PER_1M_IN_USD` / `_OUT_USD` price a turn only when `AGENT_MODEL` is not
  in the pricing catalogue.

## Vendored data and the volume mount

- The Fly volume is mounted at `/app/data`, which hides the image's `data/` directory. Anything the
  app reads from the repo's `data/` must come from the duplicate the Dockerfile keeps at
  `/app/vendored/data` (`PRICE_SNAPSHOT_PATH`, `REGISTRY_MODELS_DIR`, `EXCHANGE_RATES_PATH`).
- Startup logs `Registry-sourced models: N across F file(s) at <dir>`; a WARNING `No registry-sourced
  models visible` means the catalogue is curated-only and `get_data_quality` will show a few hundred
  models instead of 600+. Before v1.84.1 production ran this way unnoticed (273 models, 62.6% confirmed).

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
