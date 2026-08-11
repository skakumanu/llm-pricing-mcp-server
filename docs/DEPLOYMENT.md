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
