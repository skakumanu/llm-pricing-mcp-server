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

### 2. Update README test count

After running tests, update the count in `README.md` if it changed:

```
- N passing tests, CI/CD on every PR (test → lint → bandit → OSV → gitleaks → deploy)
```

Run tests first:
```bash
py -m pytest tests/ -q
```

### 3. Secret scan — MUST PASS before `git add`

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

### 4. Verify `.db` / `.env` are gitignored

```bash
git status --short | grep -E "\.(db|env)$"
```
Should return empty. If `billing.db` or `pricing_history.db` appear untracked, they are already in `.gitignore` — do not `git add` them.

### 5. Commit message format

Use conventional commits:
```
type(scope): short description

- Detail line 1
- Detail line 2

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```
Types: `feat`, `fix`, `style`, `refactor`, `test`, `docs`, `chore`, `ci`

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
1. `test` — pytest (625+ tests)
2. `lint` — flake8
3. `osv_scan` — dependency vulnerability scan
4. `security` — bandit static analysis
5. `secret_scan` — gitleaks full history scan

Deploy to Fly.io only proceeds when all five pass.
