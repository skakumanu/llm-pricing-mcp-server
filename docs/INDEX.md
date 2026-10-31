# Documentation Index

Welcome to the LLM Pricing MCP Server documentation! This directory contains comprehensive guides for using, deploying, and contributing to the project.

## Getting Started
- **[README.md](../README.md)** - Project overview, features, and quick start guide (in root)

## User & Developer Guides

### Architecture & Design
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design, architecture diagrams, and component interactions
- **[DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)** - Core design principles (clarity, sustainability, impact)

### Features & Capabilities
- **[LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md)** - Live data fetching architecture, caching strategy, and data sources
- **[BACKWARDS_COMPATIBILITY.md](BACKWARDS_COMPATIBILITY.md)** - API versioning, backwards compatibility guarantees, and migration guides

### Deployment & Operations
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - How production deployment actually works today: CI/CD-driven onto Fly.io
- **[BLUE_GREEN_DEPLOYMENT.md](BLUE_GREEN_DEPLOYMENT.md)** - Health-check, graceful-shutdown and API-versioning endpoints; its blue-green orchestration examples are illustrative only (⚠️ not the active deploy path — production is a single Fly.io app)
- **[DEPLOYMENT_IMPLEMENTATION.md](DEPLOYMENT_IMPLEMENTATION.md)** - Historical implementation summary for the health-check/graceful-shutdown feature (⚠️ not a deployment runbook — see the note at the top of the file)

Actual production deployment is CI/CD-driven via `.github/workflows/ci-cd.yml` (Fly.io + health check on merge to `master`) — see `CLAUDE.md`'s Git Flow section or `DEPLOYMENT.md` for the current process.

### MCP (Model Context Protocol) Integration
- **[MCP_QUICK_START.md](MCP_QUICK_START.md)** - Quick start guide for running the MCP server
- **[MCP_TESTING.md](MCP_TESTING.md)** - How the MCP server is tested: the pytest suite that gates CI, plus local smoke checks
- **[MCP_INTEGRATION.md](MCP_INTEGRATION.md)** - Historical record (2024): the original MCP integration plan; counts and file lists are out of date (see ARCHITECTURE.md for the current system)
- **[CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md)** - Claude Desktop integration guide (local development)
- **[PERPLEXITY_INTEGRATION.md](PERPLEXITY_INTEGRATION.md)** - Perplexity desktop MCP setup + full API endpoint reference
- **[VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)** - VS Code workspace setup and development guide
- **[MCP_VALIDATION_REPORT.md](MCP_VALIDATION_REPORT.md)** - Historical record (Feb 2026): point-in-time validation report of the first MCP implementation; not current behavior

### Development & Contribution
- **[CONTRIBUTING.md](CONTRIBUTING.md)** - Contribution guidelines, Git Flow workflow, and development setup
- **[HOUSEKEEPING.md](HOUSEKEEPING.md)** - Automatic housekeeping checklists for code quality, security, and deployment

## Quick Navigation

### By Role

**For Developers:**
1. Start with [README.md](../README.md) for project overview
2. Read [CONTRIBUTING.md](CONTRIBUTING.md) for development workflow
3. Set up [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md) for optimal development experience
4. Review [ARCHITECTURE.md](ARCHITECTURE.md) for system design
5. Check [MCP_QUICK_START.md](MCP_QUICK_START.md) for MCP server setup
6. Use [MCP_TESTING.md](MCP_TESTING.md) for testing approaches
7. Follow [HOUSEKEEPING.md](HOUSEKEEPING.md) for quality standards

**For DevOps/SRE:**
1. Deploys are CI/CD-driven via `.github/workflows/ci-cd.yml` (Fly.io + health check on merge to `master`) — see `CLAUDE.md`'s Git Flow section or [DEPLOYMENT.md](DEPLOYMENT.md) for the current process
2. [DEPLOYMENT_IMPLEMENTATION.md](DEPLOYMENT_IMPLEMENTATION.md) is a historical implementation record, not a runbook — do not follow it for deployment
3. Use [HOUSEKEEPING.md](HOUSEKEEPING.md) for operational procedures

**For MCP Client Integrators:**
1. **Perplexity**: Follow [PERPLEXITY_INTEGRATION.md](PERPLEXITY_INTEGRATION.md) for Perplexity desktop MCP setup
2. **Claude Desktop**: Follow [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) for Claude Desktop with local server
3. **VS Code**: Follow [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md) for Copilot + MCP
4. Review [ARCHITECTURE.md](ARCHITECTURE.md) ("MCP Dual Transport") for the current transports and custom clients
5. Use [MCP_TESTING.md](MCP_TESTING.md) to validate your integration
6. [MCP_INTEGRATION.md](MCP_INTEGRATION.md) and [MCP_VALIDATION_REPORT.md](MCP_VALIDATION_REPORT.md) are historical records of the original implementation, not current behavior

**For API Users:**
1. Start with [README.md](../README.md) API Reference section
2. Review [BACKWARDS_COMPATIBILITY.md](BACKWARDS_COMPATIBILITY.md) for version info
3. Check [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md) for data source details
4. See [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md) for design philosophy

### By Topic

**Deployment & Infrastructure:**
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [BLUE_GREEN_DEPLOYMENT.md](BLUE_GREEN_DEPLOYMENT.md)
- [DEPLOYMENT_IMPLEMENTATION.md](DEPLOYMENT_IMPLEMENTATION.md) (⚠️ historical record, not a runbook — see note above)
- [HOUSEKEEPING.md](HOUSEKEEPING.md) - Deployment Housekeeping section

**MCP Protocol & Tools:**
- [MCP_QUICK_START.md](MCP_QUICK_START.md)
- [MCP_INTEGRATION.md](MCP_INTEGRATION.md) (historical)
- [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) - Claude Desktop setup
- [PERPLEXITY_INTEGRATION.md](PERPLEXITY_INTEGRATION.md) - Perplexity desktop setup
- [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)
- [MCP_TESTING.md](MCP_TESTING.md)
- [MCP_VALIDATION_REPORT.md](MCP_VALIDATION_REPORT.md) (historical)

**Architecture & Design:**
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)
- [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md)

**API & Versioning:**
- [BACKWARDS_COMPATIBILITY.md](BACKWARDS_COMPATIBILITY.md)

**Security & Quality:**
- [CONTRIBUTING.md](CONTRIBUTING.md)
- [HOUSEKEEPING.md](HOUSEKEEPING.md)

## Documentation Standards

All documentation in this directory follows these standards:

- **Clear Structure**: Hierarchical headings (H2, H3, H4)
- **Cross-References**: Links to related documentation
- **Code Examples**: Practical examples where applicable
- **Step-by-Step**: Procedures broken into actionable steps
- **Maintenance**: Updated with each major change

## Key Documents Overview

| Document | Purpose | Audience | Size |
|----------|---------|----------|------|
| ARCHITECTURE.md | System design and components | Developers, Architects | ~770 lines |
| BACKWARDS_COMPATIBILITY.md | API versioning and compatibility | API Users, Developers | ~500 lines |
| BLUE_GREEN_DEPLOYMENT.md | Health/shutdown/versioning endpoints; illustrative blue-green examples (not the active deploy path) | DevOps, SRE | ~630 lines |
| CLAUDE_INTEGRATION.md | Claude Desktop integration | MCP Users, Integrators | ~230 lines |
| CONTRIBUTING.md | Development workflow and guidelines | Developers, Contributors | ~270 lines |
| DEPLOYMENT.md | Current Fly.io deployment via CI/CD | DevOps, SRE | ~180 lines |
| DEPLOYMENT_IMPLEMENTATION.md | Historical implementation record (not a runbook) | DevOps, Engineers | ~350 lines |
| DESIGN_PRINCIPLES.md | Core design principles | Architects, Developers | ~20 lines |
| HOUSEKEEPING.md | Quality and operational standards | All Developers | ~1100 lines |
| LIVE_DATA_FETCHING.md | Data fetching architecture | Developers, Architects | ~300 lines |
| MCP_INTEGRATION.md | Historical record: original MCP integration plan (2024) | Developers, Integrators | ~430 lines |
| MCP_QUICK_START.md | Quick start guide | All Users | ~260 lines |
| MCP_TESTING.md | Testing procedures | Developers, QA | ~120 lines |
| MCP_VALIDATION_REPORT.md | Historical record: Feb 2026 validation report | QA, Management | ~580 lines |
| PERPLEXITY_INTEGRATION.md | Perplexity desktop MCP setup + API endpoint reference | MCP Users, Integrators | ~450 lines |
| VS_CODE_INTEGRATION.md | VS Code workspace & development | Developers | ~540 lines |

## Finding Information

**Common Questions:**

- **How do I set up the project?** → [README.md](../README.md#development) and [CONTRIBUTING.md](CONTRIBUTING.md)
- **How do I configure VS Code for development?** → [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)
- **How do I run the MCP server?** → [MCP_QUICK_START.md](MCP_QUICK_START.md)
- **How do I test the MCP server?** → [MCP_TESTING.md](MCP_TESTING.md)
- **How do I integrate with Claude Desktop?** → [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md)
- **How do I deploy?** → CI/CD-driven via `.github/workflows/ci-cd.yml` on merge to `master` (Fly.io + health check); see [DEPLOYMENT.md](DEPLOYMENT.md)
- **How do I contribute?** → [CONTRIBUTING.md](CONTRIBUTING.md)
- **What are the system design principles?** → [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)
- **How does the architecture work?** → [ARCHITECTURE.md](ARCHITECTURE.md)
- **What quality standards apply?** → [HOUSEKEEPING.md](HOUSEKEEPING.md)
- **How does live data fetching work?** → [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md)

## Latest Updates

Current release notes live on the `/whats-new` page (`static/whats-new/index.html`); the list below is the early history only and is not kept current.

- **March 2026** - v1.33.0: Switched default LLM backend to GPT-4o-mini (20× cheaper, 2× faster); both OpenAI and Anthropic fully supported via env vars
- **March 2026** - v1.32.0: Friendly error message for exhausted AI provider credits
- **March 2026** - v1.31.0: Fixed `BadRequestError` from Anthropic — empty `required: []` stripped from tool schemas
- **March 2026** - v1.30.0: Fixed 401 Unauthorized on `/chat`; removed hardcoded API key from frontend
- **March 2026** - v1.29.0: Reduced agent latency; pre-warm agent at startup to eliminate cold start
- **March 2026** - v1.28.0: Admin dashboard at `/admin` with real-time stats and rate-limit monitoring
- **February 2026** - v1.27.0: Pricing embed widget and public API (`/pricing/public`)
- **February 2026** - v1.26.0: Model comparison UI at `/compare`
- **February 2026** - v1.25.0: HMAC-SHA256 webhook payload signing
- **February 2026** - v1.24.0: Cost calculator UI at `/calculator`
- **February 2026** - v1.20.0–v1.23.0: Conversation management API + UI + agent tools
- **February 2026** - v1.14.0–v1.19.0: Historical pricing, alert webhooks, trends UI, export
- **February 2026** - v1.12.0–v1.13.1: SSE streaming, token streaming, SQLite conversation persistence
- **February 20, 2026** - Added VS Code workspace configuration and integration guide
- **February 20, 2026** - Completed MCP blue-green deployment with v1.6.0 production deployment
- **February 19, 2026** - Created documentation index and reorganized docs into dedicated folder

---

**Last Updated**: October 2026
**Documentation Version**: 1.4
**Project Version**: see `src/__init__.py` (single source of truth)
