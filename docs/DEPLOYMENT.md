# Deployment Guide

**Version**: see `src/__init__.py` (single source of truth)

This guide covers deploying the LLM Pricing MCP Server to production on Fly.io.

> This replaces an earlier version of this guide that covered Azure App Service.
> Azure was the original deployment target; the project moved to Fly.io in v1.49.0
> for lower cost and simpler operations, and the Azure CI job was removed at the
> same time. If you're looking for the old Azure instructions, they're in git
> history (`git log -- docs/DEPLOYMENT.md`), but they no longer match anything
> this repo does.

## How deployment actually works

Deployment is entirely CI/CD-driven — there is no manual deploy step for normal
releases:

1. Changes ship through the standard git flow: `feature/*` branch → PR to `develop`
   → clean promotion (`scripts/promote_branch_content.sh`) → PR to `master`. See
   `CLAUDE.md`'s Git Flow section for the full process.
2. Merging to `master` triggers `.github/workflows/ci-cd.yml`'s `CI/CD Pipeline`.
3. The `deploy_fly` job runs only after `test`, `lint`, `osv_scan`, `security`, and
   `secret_scan` all pass. It runs `flyctl deploy --remote-only`, then polls
   `https://llm-pricing-api.fly.dev/health` for up to 3 minutes, confirming both a
   `200` response and that the reported version matches `src/__init__.py`.

That's the whole loop. There's no staging slot, no manual promotion step, no `az`
CLI — a green `master` build deploys itself.

## Prerequisites (for infrastructure changes only)

You only need these if you're changing the Fly.io app configuration itself, not
for a normal code deploy:

1. A Fly.io account with the app already provisioned (`llm-pricing-api`)
2. [`flyctl`](https://fly.io/docs/flyctl/install/) installed locally
3. Fly.io API token — the deploy job reads it from the `FLY_API_TOKEN` GitHub
   Actions secret; for local `flyctl` commands, run `flyctl auth login` instead

## App Configuration

The app's Fly.io configuration lives in `fly.toml` at the repo root:

- **App**: `llm-pricing-api`, region `iad`
- **VM**: shared-cpu-1x, 512MB
- **HTTP service**: port 8000, `force_https`, health check on `GET /health` every 30s
- **Persistent volume**: `llm_pricing_data` mounted at `/app/data` — this is where
  `pricing_history.db` and `billing.db` live, so data survives redeploys
- **Build**: uses the repo's `Dockerfile` unchanged

## Environment Variables & Secrets

Configuration is pydantic-settings (`src/config/settings.py`) — every setting reads
from an environment variable of the same name, uppercased (e.g. `mcp_api_key` ←
`MCP_API_KEY`). All provider API keys are optional; the server works with whichever
subset is configured.

Set secrets on the running app with `flyctl secrets set` (this triggers a redeploy):

```bash
flyctl secrets set MCP_API_KEY="your-strong-random-key"
flyctl secrets set OPENAI_API_KEY="sk-..." ANTHROPIC_API_KEY="sk-ant-..."
# ...and so on for any other provider key you want live-synced pricing for
```

List what's currently set (values are never shown) with `flyctl secrets list`.

Non-secret settings (`SERVER_PORT`, `RATE_LIMIT_PER_MINUTE`, `AGENT_LLM_PROVIDER`,
etc.) can be set the same way, or via the `[env]` block in `fly.toml` for values
that aren't sensitive.

### Stored provider keys (`PROVIDER_KEY_ENCRYPTION_KEY`, v1.79.0)

Customers' stored dispatch provider keys (`/dispatch/keys`) are kept in `billing.db`
(on the volume via `BILLING_DB_PATH`), AES-256-GCM-encrypted under a key derived from
this secret. Without it, `/dispatch/keys` answers 503 and dispatch works with
`X-Provider-Key-*` headers only. Generate 32 random bytes, base64url-encoded, and set
it as a Fly secret — never in `fly.toml`, never committed:

```bash
python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
flyctl secrets set PROVIDER_KEY_ENCRYPTION_KEY="<the value printed above>"
```

- **Back it up** somewhere outside Fly (a password manager / vault). The database
  alone cannot be decrypted without it.
- **Changing or losing it forces every customer to re-register their stored keys**:
  existing rows stop decrypting, are listed as `needs_reregistration`, and are never
  sent to a provider until the customer rotates them. There is no in-place secret
  rotation.
- The startup log says `Stored provider keys enabled` / `disabled`; a missing or
  too-short value logs one error naming the variable (never its value).

### Webhook alert destinations (`WEBHOOK_ALLOW_PRIVATE_TARGETS`, v1.80.1)

Price-change and budget-spend alert webhooks only deliver to **public https URLs**
(`src/services/webhook_guard.py`). Registration refuses http, private/loopback/
link-local/metadata/CGNAT/ULA (incl. Fly 6PN `fdaa::/16`) addresses in any spelling,
`localhost`/`*.localhost`/`*.internal`/`*.flycast`, hosts where any resolved address
is private, and hosts that do not resolve (422 `invalid_webhook_url`). Every delivery
re-checks the stored URL, connects only to the checked IP, and never follows redirects.
Alerts stored before v1.80.1 that break the rules are kept but skipped at delivery
(logged as `delivery blocked (<reason>)`); remove them with the operator-only list/delete
(`GET`/`DELETE /pricing/alerts`, `/usage/alerts`).

- **`WEBHOOK_ALLOW_PRIVATE_TARGETS` must stay unset on Fly.** It exists only for local
  development (e.g. a webhook receiver on `http://127.0.0.1:PORT`). When it is on, the
  server logs a `WARNING` at startup: `WEBHOOK_ALLOW_PRIVATE_TARGETS is ON ...`. If you
  ever see that line in `flyctl logs`, unset it (`flyctl secrets unset
  WEBHOOK_ALLOW_PRIVATE_TARGETS` / remove it from `fly.toml`). No request can turn it on.
- **Webhook delivery ignores `HTTPS_PROXY` / `HTTP_PROXY`.** The webhook client uses
  `trust_env=False` and a pinned transport so the destination check cannot be bypassed
  by a proxy; webhooks always connect directly. Other outbound calls (pricing fetchers,
  provider executors) are unchanged.

## Manual Deploy (rare — normally CI/CD does this)

Only needed for local testing of a Fly.io-specific change, or as a break-glass path
if CI/CD is down:

```bash
flyctl deploy --remote-only
```

## Post-Deployment Verification

```bash
# Health check
curl https://llm-pricing-api.fly.dev/health

# Confirm the deployed version matches what you expect
curl https://llm-pricing-api.fly.dev/health | jq .version

# Spot-check a couple of real endpoints
curl https://llm-pricing-api.fly.dev/models
curl https://llm-pricing-api.fly.dev/data-quality
```

## Logs & Monitoring

```bash
# Stream logs in real-time
flyctl logs

# Check machine status
flyctl status

# Check resource usage
flyctl vm status
```

## Troubleshooting

1. **`deploy_fly` job didn't run** — it only fires on push to `master` (or manual
   `workflow_dispatch`), and only after `test`/`lint`/`osv_scan`/`security`/
   `secret_scan` all pass. Check the other jobs first.
2. **Health check times out in CI** — the job polls for 3 minutes and warns
   (doesn't fail the build) rather than blocking on a slow cold start. Check
   `flyctl logs` and `flyctl status` directly if this happens repeatedly.
3. **App won't start** — `flyctl logs` first; the most common cause is a missing
   required environment variable or a Dockerfile build failure (`flyctl deploy` output
   shows the build log).
4. **Data missing after a deploy** — confirm the `llm_pricing_data` volume is still
   attached (`flyctl volumes list`); the databases live there, not in the container
   image, so they should survive normal deploys.

## Scaling

```bash
# Scale VM size
flyctl scale vm shared-cpu-2x --memory 1024

# Scale out (more machines)
flyctl scale count 2
```

## Restart

```bash
flyctl apps restart llm-pricing-api
```
