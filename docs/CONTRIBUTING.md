# Contributing Guide

## Development Setup

The service runs on **Python 3.11** (`runtime.txt`, `Dockerfile`'s `python:3.11-slim`, and the CI jobs all pin 3.11).

```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
cd llm-pricing-mcp-server
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt                     # includes pytest, pytest-asyncio, pytest-cov
cp .env.example .env                                # optional; every provider key is optional
uvicorn src.main:app --reload --port 8000
```

Optional local hooks (gitleaks, no commits to `develop`/`master`, large-file check — see `.pre-commit-config.yaml`):

```bash
pip install pre-commit
pre-commit install
```

## Testing

```bash
pytest tests/ -q                                     # full suite; README.md has the current passing-test count
pytest tests/test_version_consistency.py -q          # required before every commit (see CLAUDE.md)
```

CI (`.github/workflows/ci-cd.yml`) runs on every PR to `develop` or `master` and on pushes to `master`. All five gates must pass before the Fly.io deploy job runs:

1. `test` — `pytest tests/ -v --cov=src --cov-report=term-missing`
2. `lint` — `flake8 src tests --select=E9,F63,F7,F82` (syntax errors and undefined names fail the build; a second, full flake8 pass with `--max-line-length=127` is report-only)
3. `osv_scan` — Google OSV Scanner over `requirements.txt`
4. `security` — `bandit -r src -q`
5. `secret_scan` — gitleaks over the full git history

---

## Branching Strategy
We **STRICTLY** follow Git Flow for all development processes. `CLAUDE.md`'s "Git Flow — MANDATORY" section is the authoritative source; this is a summary.

```
feature/<short-name>  →  develop (PR)  →  master (PR)  →  deploy
```

### Branch Overview

1. **Master Branch**: `master` always reflects production. Every push to `master` deploys to Fly.io once CI passes. It only receives promotion PRs from `develop` (see below).
2. **Develop Branch**: `develop` is the integration branch. All changes merge here first, by pull request.
3. **Feature Branches**: Every change — feature, bug fix, docs, tests, urgent fix — uses a `feature/*` branch created off `develop`, named `feature/<topic>-v<version>` (e.g. `feature/api-tiers-v1.36.0`).

There are no `release/*` or `hotfix/*` branches: urgent fixes take the same `feature/*` → `develop` → `master` path.

### Git Flow Workflow

1. **Start a new feature** (ALWAYS branch from `develop`):
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/<topic>-v<version>
   ```

2. **Work on your feature** — bump `src/__init__.py` per CLAUDE.md's Release Checklist, use conventional commit messages, and stage files explicitly:
   ```bash
   git add <files>
   git commit -m "feat(scope): short description"
   git push -u origin feature/<topic>-v<version>
   ```

3. **Open a pull request targeting `develop`** (NOT master). Do not merge it yourself; the maintainer merges after review and green CI.

4. **Promote `develop` to `master`** (maintainer). `develop` and `master` are both squash-merge targets, so their histories diverge; `scripts/promote_branch_content.sh` builds a clean promotion branch whose tree matches `develop` exactly:
   ```bash
   git fetch origin master develop
   scripts/promote_branch_content.sh promote-vX.Y.Z origin/master origin/develop
   git commit -m "chore: promote develop to master (vX.Y.Z)"
   git push -u origin promote-vX.Y.Z
   # Open a PR: promote-vX.Y.Z → master
   ```
   On every push to `master`, `.github/workflows/sync-develop.yml` opens a `ci/sync-develop-*` PR into `develop` if `develop` is missing any `master` commits.

### Important Rules

- ⚠️ **NEVER merge feature branches directly into `master`**
- ⚠️ **NEVER commit directly to `master` or `develop`**
- ⚠️ **NEVER commit secrets, API keys, or sensitive information**
- ✅ **ALWAYS branch from `develop` and open the PR against `develop`**
- ✅ **ALWAYS run the Release Checklist in `CLAUDE.md` before committing** (version bump, tests, secret scan)
- ✅ **ALWAYS check for secrets before committing** (see Security Checklist below)
- ✅ **Use pull requests** for code review before merging
- ✅ **Include detailed commit messages** describing what and why (conventional commits: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `style`)

### Pre-Commit Security Checklist

Before committing ANY code, verify:

```bash
# 1. Check what files you're about to commit
git status
git diff --cached

# 2. Scan the staged diff for secret-shaped strings (same pattern as CLAUDE.md's checklist)
git diff --staged | grep -iE \
  "(sk-[a-zA-Z0-9]{20,}|pk-[a-zA-Z0-9]{20,}|whsec_[a-zA-Z0-9]{20,}|api[_-]key\s*=\s*['\"][^'\"]{8,}|password\s*=\s*['\"][^'\"]{4,}|secret\s*=\s*['\"][^'\"]{4,}|AKIA[A-Z0-9]{16})"

# 3. Ensure no .env / .db / key files are staged (must print nothing)
git diff --staged --name-only | grep -E "\.(db|env|pem|key|p12|pfx|secret)$"
```

**If you find any secrets:**
1. Remove them immediately
2. Use environment variables instead
3. Add the pattern to `.gitignore`
4. Never commit the secrets

---

## Security Compliance

### Critical Security Rules

⚠️ **NEVER commit secrets or sensitive information to the repository!**

### What NOT to Commit

**Secrets and Credentials:**
- API keys (OpenAI, Anthropic, Azure, etc.)
- Passwords or passphrases
- Access tokens or bearer tokens
- Database connection strings with credentials
- Private keys (`.pem`, `.key`, `.pfx` files)
- SSH keys
- OAuth client secrets
- Encryption keys
- Azure storage account keys
- Service principal credentials

**Configuration Files with Secrets:**
- `.env` files (use `.env.example` instead)
- `secrets.json` or `config.local.json`
- Any file containing hard-coded credentials

**Personal Information:**
- Email addresses (except in documentation)
- Phone numbers
- Personal identifiable information (PII)

### How to Handle Secrets Properly

1. **Use Environment Variables:**
   ```python
   import os
   api_key = os.getenv("OPENAI_API_KEY")
   ```

2. **Store Production Secrets as Fly.io Secrets:**
   - Production runs on Fly.io; secrets are set with `flyctl secrets set NAME=...` (never in `fly.toml`, never committed) — see [DEPLOYMENT.md](DEPLOYMENT.md)
   - CI credentials (e.g. `FLY_API_TOKEN`) are GitHub Actions secrets
   - Access secrets at runtime via environment variables, never hard-code them

3. **Use `.env` for Local Development:**
   - Copy `.env.example` to `.env` (`.env` is already in `.gitignore`)
   - Keep `.env.example` limited to dummy values
   - Document required environment variables in README

4. **Before Every Commit:**
   - Run the pre-commit security checklist (see above)
   - Review `git diff` carefully
   - Check for accidentally staged secret files

### Secret Detection Tools

gitleaks is the scanner this repo uses: CI's `secret_scan` job runs it over the full history, and the optional pre-commit hook runs it locally. To scan by hand:

```bash
gitleaks detect --source . --verbose
```

### If You Accidentally Commit a Secret

1. **Immediately rotate/revoke the secret** (API key, password, etc.)
2. Contact the repository maintainer
3. Remove the secret from git history using:
   ```bash
   git filter-branch --force --index-filter \
     "git rm --cached --ignore-unmatch <file-with-secret>" \
     --prune-empty --tag-name-filter cat -- --all
   ```
   Or use [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/)
4. Force push (requires admin approval)
5. Notify the security team

### Additional Security Practices

1. All contributions must pass the CI/CD gates (bandit static analysis, OSV dependency scan, gitleaks)
2. Use secure coding practices, avoiding hard-coded credentials
3. Always use environment variables for configuration
4. Follow the principle of least privilege for accessing sensitive resources
5. Keep dependencies updated to patch security vulnerabilities
6. Review the security section in pull request reviews

---

## Privacy Compliance
1. Do not include any Personal Identifiable Information (PII) in logs or outputs.
2. Ensure third-party dependencies comply with GDPR and similar privacy regulations.
3. Never log or persist request IPs, provider keys, or prompt/completion text (see the privacy rules in `docs/ARCHITECTURE.md`).

Thank you for contributing to the llm-pricing-mcp-server project!