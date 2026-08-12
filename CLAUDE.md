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

### 9. Update `static/whats-new/index.html` for user-facing releases

Add a new release entry at the **top** of the list (mark it `class="release latest"` and remove
that class from the previous top entry) whenever a commit ships something a customer would
notice: a new endpoint, MCP tool, UI page, or behavior change. Follow the existing entry format
(version badge, date, tag, title, change list, optional stats-strip) — copy an existing entry as
a template rather than inventing new markup.

**Skip this step** for internal-only changes a customer never sees: docs fixes, dead-code
removal, CI/test changes, internal refactors. When in doubt, ask "would someone reading this page
learn something new about the product?" — if no, skip it.

Past entries are a historical record — never rewrite an old entry's content to match current
reality (e.g. don't update an old entry's tool count when the total changes later). Only ever
append new entries; this file is otherwise excluded from `tests/test_tool_registry.py`'s
doc-sync checks for exactly this reason.

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
