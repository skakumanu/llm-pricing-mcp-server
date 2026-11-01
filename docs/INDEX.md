# Documentation Index

Project overview and quick start: [README.md](../README.md). Release notes: `/whats-new` (`static/whats-new/index.html`). Version: `src/__init__.py`.

## Docs

| Doc | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Canonical internals: layers, files, endpoints, auth, data stores, CI/CD |
| [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md) | Clarity, sustainability, impact |
| [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md) | How a price gets into the catalogue (stages, oracle, drift) |
| [BACKWARDS_COMPATIBILITY.md](BACKWARDS_COMPATIBILITY.md) | API v1 change policy: additive only |
| [DEPLOYMENT.md](DEPLOYMENT.md) | How a release reaches Fly.io; settings, secrets, operations |
| [FAQ.md](FAQ.md) | Customer-facing FAQ (plans, billing, keys, rate limits, data accuracy, help); source of `/faq` and the support agent's knowledge base |
| [LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md) | Machine-parsed list of what must be true before paid plans are sold (entity, Stripe, backups, legal review, scanners) |
| [legal/](legal/) | Markdown sources of the legal pages (`/terms`, `/privacy`, `/refunds`, `/dpa`, `/subprocessors`) and the security statement (`/security`); templates until lawyer-reviewed |
| [MCP_QUICK_START.md](MCP_QUICK_START.md) | Connect an MCP client; canonical tool reference |
| [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) | Claude Desktop setup |
| [PERPLEXITY_INTEGRATION.md](PERPLEXITY_INTEGRATION.md) | Perplexity desktop setup |
| [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md) | VS Code MCP clients and server development |
| [MCP_TESTING.md](MCP_TESTING.md) | How the MCP server is tested (CI suite + local smoke checks) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, git flow, tests, secrets |
| [HOUSEKEEPING.md](HOUSEKEEPING.md) | Repo upkeep: promotion, weekly audit PRs, dependency hygiene, test-enforced docs rules |
| [Archive](archive/README.md) | Historical records, not maintained (original MCP plan and validation report, Azure blue-green deployment) |

## Common questions

| Question | Go to |
|---|---|
| Set up the project? | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Connect an MCP client? | [MCP_QUICK_START.md](MCP_QUICK_START.md) |
| Plans, billing, keys, how to get help? | [FAQ.md](FAQ.md) (served at `/faq`) |
| How does deploy work? | [DEPLOYMENT.md](DEPLOYMENT.md) (CI/CD on merge to `master`) |
| What must happen before a commit? | `CLAUDE.md` release checklist |
| Where is endpoint X / service Y? | [ARCHITECTURE.md](ARCHITECTURE.md) |
