# Housekeeping

Repo-specific operating procedures. The per-commit **Release Checklist** (version bump,
architecture/README updates, secret scan, data-accuracy check, version test, what's-new)
lives in [CLAUDE.md](../CLAUDE.md) and is not repeated here.

## Branch model

| Branch | Created by | Merges into | Notes |
|---|---|---|---|
| `feature/<topic>-v<version>` | you, from `develop` | `develop` (PR) | The only hand-made branch type; fixes and docs too |
| `promote-*` | `scripts/promote_branch_content.sh` | `master` (PR) | Ships `develop`'s content |
| `ci/sync-develop-*` | `sync-develop.yml` | `develop` (PR) | Back-syncs `master` after a push |
| `ci/<audit-job>-<date>` | `price-audit.yml` | `develop` (PR) | Weekly data PRs |
| `develop` / `master` | — | — | Protected; never commit directly |

- No `hotfix/*`, `release/*` or `fix/*`. Urgent and security fixes take the same path.
- **The owner merges every PR.** Agents open PRs and stop (CLAUDE.md Git Flow rule 5).
- Both `develop` and `master` are squash-merge targets, so their histories diverge.

## Feature workflow

```bash
git checkout develop && git pull origin develop
git checkout -b feature/<topic>-v<version>
# change + tests, then run the CLAUDE.md Release Checklist
git push -u origin feature/<topic>-v<version>   # open PR → develop
```

- One focused change per branch; every code change ships with tests.
- Conventional commits: `feat`, `fix`, `style`, `refactor`, `test`, `docs`, `chore`, `ci`.
- For non-trivial features, use the phase-agent pipeline: `/release-feature <description>`.

## Promoting develop to master

A direct `develop` → `master` PR shows the full diverged history. Build a clean branch instead:

```bash
git fetch origin master develop
scripts/promote_branch_content.sh promote-vX.Y.Z origin/master origin/develop
git commit -m "chore: promote develop to master (vX.Y.Z)"
git push -u origin promote-vX.Y.Z   # open PR → master
```

- The script uses `git read-tree -u --reset`, so deletions carry over too.
- It refuses to finish unless the new tree matches `develop` byte for byte.
- It never commits or pushes; you do.

## After a master merge

| Workflow | Trigger | Effect |
|---|---|---|
| `ci-cd.yml` `deploy_fly` | push to `master` | Deploys to Fly.io, then tags `vX.Y.Z` and publishes a GitHub Release from the What's New entry (see [DEPLOYMENT.md](DEPLOYMENT.md)) |
| `sync-develop.yml` | push to `master` | Opens `ci/sync-develop-*` PR if `develop` lacks `master` commits |
| `npm-publish.yml` | `npm/llm-pricing-mcp/**` changed | Publishes the npm wrapper (own version) |
| `mcp-registry-publish.yml` | `server.json` changed | Updates the MCP registry entry |

- Merge the sync PR once CI passes. Each new sync PR closes older open ones.
- Rollback: revert on a `feature/*` branch and promote, or break-glass `flyctl deploy` (DEPLOYMENT.md).

## CI gates

Every PR to `develop`/`master` and every `master` push runs:

| Gate | Tool | Local equivalent |
|---|---|---|
| `test` | pytest + coverage (reported, not gated) | `pytest tests/ -q` |
| `lint` | flake8 (errors gate; style is `--exit-zero`) | `flake8 src tests --select=E9,F63,F7,F82` |
| `osv_scan` | OSV scanner on dependencies | — |
| `security` | bandit | `bandit -r src -q` |
| `secret_scan` | gitleaks, full history (`.gitleaks.toml`) | `pre-commit run gitleaks --all-files` |

## Scanners and automation workflows (v1.86.0)

Additional to the five gates above; none of them is a deploy gate.

| Workflow / config | Runs | Does | How to handle |
|---|---|---|---|
| `.github/workflows/codeql.yml` | PR to `develop`/`master`, weekly (Mondays 05:23 UTC), `workflow_dispatch` | CodeQL code scanning for Python; results in the Security tab | Triage alerts; fix or dismiss with a reason |
| `.github/dependabot.yml` | Weekly | Version-update PRs for `pip` and `github-actions`, minor and patch updates grouped into one PR per ecosystem | Review and merge like any PR (never auto-merged); keep `requirements.txt` and `pyproject.toml` in sync |
| GitHub secret-scanning push protection | On push | Blocks pushes containing secrets | A manual GitHub UI setting (launch checklist LC-17), not code |

## Weekly price-audit PRs

`.github/workflows/price-audit.yml` runs Mondays 06:00 UTC (or `workflow_dispatch`).
Pipeline details: [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md#weekly-price-audit).

| Output | Branch / title | What changes | How to handle |
|---|---|---|---|
| `price-drift` issue | rolling issue | nothing | Fix `STATIC_PRICING` on a `feature/*` branch (below) |
| Snapshot refresh | `ci/refresh-price-snapshot-*` | `data/model_prices_snapshot.json` | Read the validation report in the body, merge |
| Exchange rates | `ci/refresh-exchange-rates-*` | `data/exchange_rates.json` | Merge; rates older than 7 days are served flagged stale |
| Reconfirm | `ci/reconfirm-prices-*` | `price_as_of` dates only | Check no price value moved, merge |
| Retire | `ci/retire-models-*` | removes entries retired >30 days | Check each has a same-provider deprecation date, merge |
| Pickup | `ci/pickup-registry-models-*` | `data/registry_models/*.json` | Check only registry files changed, merge |

- **These PRs arrive with no CI run** (opened with `GITHUB_TOKEN`). Each job validates its own
  output before opening the PR; merge only if the body shows that step passed.
- If you need CI, push an empty commit to the branch or close and reopen the PR.
- `gh pr create blocked` in the log means the branch was pushed but no PR opened.
  Open it by hand, or enable *Settings → Actions → Allow GitHub Actions to create and approve pull requests*.
- They target `develop`; they reach production with the next promotion.

Fixing a drift issue:
- Confirm the provider's current price on its pricing page, not just the registry.
- Update `STATIC_PRICING` and `price_as_of` on a `feature/*` branch (patch bump).
- Run CLAUDE.md step 7; `withheld_for_drift` should drop and `confirmed_pct` must not.
- If the registry is wrong rather than the table, leave the model withheld and note it on the issue.

## Dependency and security hygiene

- Dependencies: `requirements.txt` and `pyproject.toml` stay in sync; run `pip check` after changes.
- `osv_scan` failure blocks deploy: bump the package or pin a fixed version in the same PR.
- bandit findings: fix, or add a justified `# nosec` on that line only.
- Secrets: env vars locally, `flyctl secrets set` in production. Never commit `.env`, `*.db`, keys.
- `.db` files (`pricing_history.db`, `billing.db`) are gitignored; never `git add -f` them.
- Off-site backups: Litestream replicates `billing.db`, `pricing_history.db` and `orchestrator.db`; restore steps are in [DEPLOYMENT.md](DEPLOYMENT.md#restore-from-the-litestream-replica).
- Doc and shell examples use `"$VAR"` references; gitleaks flags quoted placeholders.
- Optional local hooks: `pip install pre-commit && pre-commit install`
  (gitleaks, `no-commit-to-branch`, large files, merge conflicts, whitespace).

## Docs rules enforced by tests

| Rule | Test |
|---|---|
| No `**Version**:` header pinning a stale version in `docs/*.md` | `tests/test_version_consistency.py` |
| MCP tool counts match the registry | `tests/test_tool_registry.py` |
| Provider counts match the aggregator | `tests/test_new_providers.py` |
| `DEPLOYMENT.md` keeps the webhook-safety wording | `tests/test_webhook_ssrf_registration.py` |

| FAQ prices / limits match `src/services/tiers.py` and the settings; `/faq` is generated from `docs/FAQ.md` | `tests/test_support_faq.py` |

- Never hardcode the test count outside README's "passing tests" line.
- `static/whats-new/` and `docs/archive/` are historical; never rewrite old entries.
- After editing the nav, run `scripts/sync_nav.py` to restamp every `static/` page.
- After editing `docs/FAQ.md`, run `scripts/sync_faq.py` to regenerate `static/faq/index.html` (`--check` in CI).
- After editing `docs/legal/*.md`, run `scripts/sync_legal.py` to regenerate the legal pages (`--check` in tests). `docs/LAUNCH_CHECKLIST.md` is parsed by `scripts/launch_checklist.py` (`tests/test_launch_checklist.py`).

## Repo layout

| Path | Contents |
|---|---|
| `src/` | FastAPI app (`main.py`), `config/settings.py`, `models/`, `services/` (one `*_pricing.py` per provider) |
| `mcp/` | MCP server, transport and `tools/` |
| `agent/`, `rag/` | ReAct agent and its retrieval pipeline |
| `static/` | Browser UI pages (incl. `whats-new/`, `api-docs/`, `mcp-setup/`) |
| `data/` | Vendored price snapshot, `registry_models/`, `exchange_rates.json` |
| `scripts/` | Promotion, price-audit scripts, `sync_nav.py`, `sync_faq.py`, `sync_legal.py`, `check_replication.py`, `launch_checklist.py`, `quick_validate.py` (MCP STDIO smoke test) |
| `npm/llm-pricing-mcp/` | npm wrapper, versioned separately |
| `.claude/` | Phase agents, `/release-feature` skill, `release-feature.js` workflow |
| `docs/` | [INDEX.md](INDEX.md), [ARCHITECTURE.md](ARCHITECTURE.md); superseded docs in `archive/` |
| Root | `Dockerfile`, `fly.toml`, `server.json` (MCP registry), `CLAUDE.md`, `.env.example` |

Archived (historical, not current practice): [archive/BLUE_GREEN_DEPLOYMENT.md](archive/BLUE_GREEN_DEPLOYMENT.md),
[archive/DEPLOYMENT_IMPLEMENTATION.md](archive/DEPLOYMENT_IMPLEMENTATION.md),
[archive/MCP_INTEGRATION.md](archive/MCP_INTEGRATION.md),
[archive/MCP_VALIDATION_REPORT.md](archive/MCP_VALIDATION_REPORT.md).
