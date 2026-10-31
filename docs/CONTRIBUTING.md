# Contributing

`CLAUDE.md` is authoritative for git flow and the release checklist; this page is the short version.

## Setup

- Python **3.11** (`runtime.txt`, Dockerfile, CI).
- Every provider key is optional.

```bash
git clone https://github.com/skakumanu/llm-pricing-mcp-server.git
cd llm-pricing-mcp-server
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # optional
uvicorn src.main:app --reload --port 8000
```

Optional local hooks (gitleaks, branch guard, large-file check):

```bash
pip install pre-commit && pre-commit install
```

## Git flow

```
feature/<topic>-v<version>  →  develop (PR)  →  master (PR)  →  deploy
```

- Branch from `develop`; name it `feature/<topic>-v<version>` (e.g. `feature/api-tiers-v1.36.0`).
- Never commit directly to `develop` or `master`; no `release/*` or `hotfix/*` branches.
- Open the PR against `develop`. The maintainer merges.
- `master` = production; every push deploys to Fly.io once CI passes.
- Promotion (maintainer): `scripts/promote_branch_content.sh promote-vX.Y.Z origin/master origin/develop`, then PR to `master`.
- After a `master` push, `sync-develop.yml` opens a PR if `develop` lags.

## Commits

- Conventional commits: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `style`.
- Bump `src/__init__.py`: patch for fixes/docs, minor for features, major for breaking changes.
- Stage files explicitly (`git add <files>`).

## Tests

```bash
pytest tests/ -q                               # full suite; README has the current count
pytest tests/test_version_consistency.py -q    # required before every commit
```

CI gates (`.github/workflows/ci-cd.yml`), all required before deploy:

| Gate | Tool |
|---|---|
| `test` | pytest with coverage |
| `lint` | flake8 (syntax errors + undefined names fail) |
| `osv_scan` | OSV Scanner over `requirements.txt` |
| `security` | bandit |
| `secret_scan` | gitleaks, full history |

## Release checklist

Run the full list in `CLAUDE.md` before every commit. Highlights:
- version bump + version-consistency test
- `docs/ARCHITECTURE.md` update when structure changes
- README test count
- data-quality check for pricing changes
- `/whats-new` entry for user-facing changes

## Secrets

- Never commit keys, tokens, passwords, private keys or `.env` / `.db` files.
- Local config goes in `.env` (gitignored); `.env.example` holds dummy values only.
- An unknown key in `.env` fails startup. When removing a setting, add it to `RETIRED_ENV_KEYS` in `src/config/settings.py`.
- Production secrets: `flyctl secrets set NAME=...` (see [DEPLOYMENT.md](DEPLOYMENT.md)). CI secrets: GitHub Actions secrets.

Before staging, both commands must print nothing:

```bash
git diff --staged | grep -iE \
  "(sk-[a-zA-Z0-9]{20,}|pk-[a-zA-Z0-9]{20,}|whsec_[a-zA-Z0-9]{20,}|api[_-]key\s*=\s*['\"][^'\"]{8,}|password\s*=\s*['\"][^'\"]{4,}|secret\s*=\s*['\"][^'\"]{4,}|AKIA[A-Z0-9]{16})"
git diff --staged --name-only | grep -E "\.(db|env|pem|key|p12|pfx|secret)$"
```

Manual scan: `gitleaks detect --source . --verbose`.

### If a secret is committed

1. Rotate or revoke it immediately.
2. Tell the maintainer.
3. Purge it from history (e.g. [BFG Repo-Cleaner](https://rtyley.github.io/bfg-repo-cleaner/)); force-push needs admin approval.

## Privacy

- No PII in logs or outputs.
- Never log or persist request IPs, provider keys, or prompt/completion text (see [ARCHITECTURE.md](ARCHITECTURE.md)).
