# Claude Code Instructions

## Git Flow — MANDATORY

This repo uses git flow. You MUST follow these rules for every coding session:

### Branch workflow
```
feature/<short-name>  →  develop (PR)  →  master (PR)  →  deploy
```

### Rules
1. **Never commit directly to `develop` or `master`.**
2. **Before writing any code**, create a feature branch:
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/<short-descriptive-name>
   ```
3. Name branches: `feature/<topic>-v<version>` (e.g. `feature/api-tiers-v1.36.0`)
4. Commit on the feature branch, then push and open a PR targeting `develop`.
5. Do not merge PRs yourself — leave that to the user.

### At the start of every session
- Run `git branch` to confirm you are NOT on `develop` or `master`.
- If on `develop` with uncommitted work, immediately branch off before committing.

---

## Release Checklist — MANDATORY BEFORE EVERY COMMIT

Run every item below before staging files on any feature branch. Do not skip any step.

### 1. Version bump (`src/__init__.py`)

| Change type | Bump |
|---|---|
| Bug fix, style, docs, refactor | Patch — `x.y.Z+1` |
| New feature, new endpoint, new UI page | Minor — `x.Y+1.0` |
| Breaking API/schema change | Major — `X+1.0.0` |

```bash
# Edit src/__init__.py
__version__ = "x.y.z"   # ← bump this
```

### 2. Update `docs/ARCHITECTURE.md` when structure changes

Update `docs/ARCHITECTURE.md` whenever any of these change:
- New or removed service in `src/services/` or `agent/`
- New endpoint group or auth change in `src/main.py`
- New database table or schema
- New browser UI page in `static/`
- New external dependency (provider, payment, etc.)
- CI/CD job added or removed
- Deployment target added or removed
- Design system token change (CSS variables)

**Minor changes do NOT need an architecture update**: bug fixes, style tweaks, test additions, wording changes.

The canonical file is `docs/ARCHITECTURE.md`. Keep the layer diagram, file structure, and endpoint map current.

### 3. Update README test count

After running tests, update the count in `README.md` if it changed:

```
- N passing tests, CI/CD on every PR (test → lint → bandit → OSV → gitleaks → deploy)
```

Run tests first:
```bash
py -m pytest tests/ -q
```

### 4. Secret scan — MUST PASS before `git add`

Check staged files for accidentally included secrets:

```bash
git diff --staged | grep -iE \
  "(sk-[a-zA-Z0-9]{20,}|pk-[a-zA-Z0-9]{20,}|whsec_[a-zA-Z0-9]{20,}|api[_-]key\s*=\s*['\"][^'\"]{8,}|password\s*=\s*['\"][^'\"]{4,}|secret\s*=\s*['\"][^'\"]{4,}|AKIA[A-Z0-9]{16})"
```

If any matches appear outside test fixtures or `# nosec` comments: **remove them before committing**. Use environment variables or Key Vault references instead.

Also verify no `.db` or `.env` files are staged:
```bash
git diff --staged --name-only | grep -E "\.(db|env|pem|key|p12|pfx|secret)$"
```
This must return empty. If not, unstage those files.

### 5. Verify `.db` / `.env` are gitignored

```bash
git status --short | grep -E "\.(db|env)$"
```
Should return empty. If `billing.db` or `pricing_history.db` appear untracked, they are already in `.gitignore` — do not `git add` them.

### 6. Commit message format

Use conventional commits:
```
type(scope): short description

- Detail line 1
- Detail line 2

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```
Types: `feat`, `fix`, `style`, `refactor`, `test`, `docs`, `chore`, `ci`

### 7. Data accuracy check — MANDATORY for pricing-related changes

If the change touches `src/services/*_pricing.py`, `price_oracle.py`, `pricing_aggregator.py`,
`STATIC_PRICING` data, or `mcp/tools/get_data_quality.py` / `check_price_drift.py`, run this
before committing — do this proactively as part of the checklist, not only when asked:

```bash
python -c "
import asyncio
from mcp.tools.get_data_quality import GetDataQualityTool
print(asyncio.run(GetDataQualityTool().execute({})))
"
pytest tests/test_price_oracle.py tests/test_price_provenance.py tests/test_data_quality.py tests/test_check_price_drift.py -q
```

Compare `confirmed_pct`, `withheld_for_drift`, `never_priced`, and `stale_models` against the
values before your change. A pricing change must never silently lower `confirmed_pct` or raise
`withheld_for_drift`/`never_priced` — that is a regression to investigate, not a number to shrug
off. See `/data-quality` and `get_data_quality` for what customers see; `check_price_drift` for
per-model detail.

### 8. Version accuracy check — MANDATORY before every commit

```bash
pytest tests/test_version_consistency.py -q
```

This must pass on every commit, not just ones that touch `src/__init__.py`. It catches a version
bumped in one place (MCP `serverInfo`, a doc header, `settings.app_version`) but not another —
exactly the class of bug that shipped for months before this guard existed. Do not skip it because
"it's just a docs change" or "the version didn't move" — the guard is what proves that.

### 9. Update `static/whats-new/index.html` — every version gets an entry

Add a new release entry at the **top** of the list (mark it `class="release latest"` and remove
that class from the previous top entry) for **every version bump**. The deploy job publishes the
GitHub Release for a version from this entry (`scripts/release_notes_from_whats_new.py`), and
`tests/test_new_providers.py` pins the top entry to `src/__init__.py` — a version without an
entry fails CI and has no release notes. Follow the existing entry format (version badge, date,
tag, title, change list, optional stats-strip) — copy an existing entry as a template.

Customer-visible changes (new endpoint, MCP tool, UI page, behavior change) get a full entry.
Internal-only changes (docs, CI/test, refactors) get a one-bullet entry tagged `Docs` or `Fix`
that says plainly that the product's behavior is unchanged.

Past entries are a historical record — never rewrite an old entry's content to match current
reality (e.g. don't update an old entry's tool count when the total changes later). Only ever
append new entries; this file is otherwise excluded from `tests/test_tool_registry.py`'s
doc-sync checks for exactly this reason.

### 10. Documentation sync — MANDATORY for every feature and fix

Documentation ships **in the same PR** as the code it describes, never in a follow-up. GitHub
shows `master`, so a docs change is not "released" until it is promoted; promote docs-only
changes promptly instead of holding them for the next feature.

Update every document that describes the surface you touched:

| You changed | Update |
|---|---|
| HTTP endpoint (new, removed, auth class, request/response shape) | `docs/ARCHITECTURE.md` endpoint map · `static/api-docs/index.html` · README access/endpoint tables · `docs/PERPLEXITY_INTEGRATION.md` if it lists the route |
| MCP tool (new, removed, arguments, access level) | tool description in `mcp/tools/tool_manager.py` · `docs/MCP_QUICK_START.md` tool table · `static/mcp-setup/index.html` · README tool table · `docs/CLAUDE_INTEGRATION.md` access tables |
| Setting / env var | `.env.example` (commented, placeholder value) · README Configuration · `docs/DEPLOYMENT.md` if it is a production secret |
| Browser UI page | README UI list · `docs/ARCHITECTURE.md` · nav (`scripts/sync_nav.py`) |
| Provider added/removed | provider counts everywhere (`tests/test_new_providers.py` pins them) |
| Bug fix | `static/whats-new/index.html` entry (step 9) · correct any doc that described the old behavior |
| CI job, workflow, deployment step | `docs/ARCHITECTURE.md` CI/CD section · `docs/DEPLOYMENT.md` · `docs/HOUSEKEEPING.md` workflow table |
| Anything | `static/whats-new/index.html` (step 9) · README test count (step 3) |

`tests/test_docs_sync.py` enforces the mechanical parts: every API route appears in the
ARCHITECTURE endpoint map and on `/api-docs`; every MCP tool appears in `MCP_QUICK_START.md` and
on `/mcp-setup`; every setting appears in `.env.example` or the README/DEPLOYMENT/ARCHITECTURE;
the current version has a What's New entry that renders as release notes. It does not check prose
accuracy — read what the doc says about the thing you changed and fix it.

---

## Local Pre-commit Hooks (optional but recommended)

Install once per machine to enforce secret scanning and branch protection locally:

```bash
pip install pre-commit
pre-commit install
```

Config is in `.pre-commit-config.yaml` (gitleaks + branch guard + large-file check).

---

## CI/CD Gates

Every PR and `master` push runs **all five gates** before deploy:
1. `test` — pytest (see README.md's "passing tests" line for the current count — do not hardcode a number here, it will just go stale again)
2. `lint` — flake8
3. `osv_scan` — dependency vulnerability scan
4. `security` — bandit static analysis
5. `secret_scan` — gitleaks full history scan

Deploy to Fly.io only proceeds when all five pass.

---

## AI-Native SDLC Pipeline (Phase Agents)

For a feature (not a trivial one-line fix), this repo has a phase-agent pipeline instead of one
agent doing spec+design+code+review+test end to end in a single undifferentiated pass:

```
Spec  →  Design  →  branch created  →  Code  →  Review (correctness / security / simplification, parallel)  →  Test & Release
```

- **Spec vs. Design — the split that matters most.** Spec decides *what* the feature does and for
  whom (problem, functional requirements, acceptance criteria, non-goals) — no file names, no
  technical approach. Design decides *how*, given a fixed Spec (which existing services/modules
  to reuse, interface/schema shape, version-bump type, architecture/pricing/user-facing impact,
  branch name). If a Spec answer would change depending on which technical approach gets picked,
  it's not actually a Spec question — it belongs in Design.
- **Agents**: `.claude/agents/phase-spec.md`, `phase-design.md`, `phase-code.md`, `phase-review.md`,
  `phase-test.md` — each scoped to one phase, with its own tool access and its own slice of the
  Release Checklist above. Code implements the Design and bumps the version. Review reports
  findings, it does not fix them. Test checks the Code phase's work against the Spec's acceptance
  criteria, triages Review's findings, runs the full checklist, and is the only phase that commits,
  pushes, and opens the PR.
- **Orchestrator**: `.claude/workflows/release-feature.js`, a Workflow script that runs Code (given
  the Spec + Design), then fans the diff out to three parallel Review agents (one per dimension),
  then runs Test & Release with the Spec's acceptance criteria and Review's findings as input.
- **Entry point**: `/release-feature <description>` (`.claude/skills/release-feature/SKILL.md`)
  runs Spec, then Design, itself first (before any branch exists — Design's version-bump decision
  is needed to name the branch), creates the `feature/*` branch per the Git Flow rules above, then
  calls the Workflow.

This does not replace the rules above — it is those rules, split across phases so each one gets a
dedicated agent instead of competing for one agent's attention alongside everything else. It still
ends at an open PR against `develop`; merging is still the user's call (Git Flow rule 5).

**Orchestrator core (v1.83.0).** The service also hosts an operator-only orchestrator
(`src/services/orchestrator_*.py`, `/orchestrator/*`, the Orchestrator block on `/admin`) that
accepts inbound work exactly once (GitHub events via `.github/workflows/orchestrator-intake.yml`,
the weekly price audit's `report-run` job, owner requests), routes it from trigger class and
owner-applied labels only, and holds every run at human checkpoints. The SDLC pipeline is wrapped
as an **owner checkpoint**, not run unattended: the queue shows the exact command
`/release-feature Fix issue #N (orchestrator run orc_...)`, the skill stamps `Orchestrator-Run:`
into the PR footer, and the PR's `opened` event completes the checkpoint and posts one status
comment. The orchestrator never merges, never moves money, never sends anything without a recorded
approval, and cannot change its own budgets, modes or workflow definitions from inside a run.
