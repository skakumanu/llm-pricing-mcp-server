# Documentation Index

Welcome to the LLM Pricing MCP Server documentation! This directory contains comprehensive guides for using, deploying, and contributing to the project.

## Getting Started
- **[README.md](../README.md)** - Project overview, features, and quick start guide (in root)

## User & Developer Guides

### Architecture & Design
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System design, architecture diagrams, and component interactions
- **[DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)** - Core design principles and architectural decisions

### Features & Capabilities
- **[LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md)** - Live data fetching architecture, caching strategy, and data sources
- **[BACKWARDS_COMPATIBILITY.md](BACKWARDS_COMPATIBILITY.md)** - API versioning, backwards compatibility guarantees, and migration guides

### Deployment & Operations
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - How production deployment actually works today: CI/CD-driven onto Fly.io
- **[BLUE_GREEN_DEPLOYMENT.md](BLUE_GREEN_DEPLOYMENT.md)** - Zero-downtime blue-green deployment strategy and implementation
- **[DEPLOYMENT_IMPLEMENTATION.md](DEPLOYMENT_IMPLEMENTATION.md)** - Historical implementation summary for the health-check/graceful-shutdown feature (⚠️ not a deployment runbook — see the note at the top of the file)

Actual production deployment is CI/CD-driven via `.github/workflows/ci-cd.yml` (Fly.io + health check on merge to `master`) — see `CLAUDE.md`'s Git Flow section or `DEPLOYMENT.md` for the current process.

### MCP (Model Context Protocol) Integration
- **[MCP_QUICK_START.md](MCP_QUICK_START.md)** - Quick start guide for running the MCP server
- **[MCP_TESTING.md](MCP_TESTING.md)** - Comprehensive testing guide with all test scripts
- **[MCP_INTEGRATION.md](MCP_INTEGRATION.md)** - Architecture and integration patterns
- **[CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md)** - Claude Desktop integration guide (local development)
- **[PERPLEXITY_INTEGRATION.md](PERPLEXITY_INTEGRATION.md)** - Perplexity desktop MCP setup + full API endpoint reference
- **[VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)** - VS Code workspace setup and development guide
- **[MCP_VALIDATION_REPORT.md](MCP_VALIDATION_REPORT.md)** - Validation results and test reports

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
4. Review [MCP_INTEGRATION.md](MCP_INTEGRATION.md) for architecture and custom clients
5. Use [MCP_TESTING.md](MCP_TESTING.md) to validate your integration
6. Check [MCP_VALIDATION_REPORT.md](MCP_VALIDATION_REPORT.md) for expected behavior

**For API Users:**
1. Start with [README.md](../README.md) API Documentation section
2. Review [BACKWARDS_COMPATIBILITY.md](BACKWARDS_COMPATIBILITY.md) for version info
3. Check [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md) for data source details
4. See [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md) for design philosophy

### By Topic

**Deployment & Infrastructure:**
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [BLUE_GREEN_DEPLOYMENT.md](BLUE_GREEN_DEPLOYMENT.md)
- [DEPLOYMENT_IMPLEMENTATION.md](DEPLOYMENT_IMPLEMENTATION.md) (⚠️ historical record, not a runbook — see note above)
- [HOUSEKEEPING.md](HOUSEKEEPING.md) - Blue-Green Deployment section

**MCP Protocol & Tools:**
- [MCP_QUICK_START.md](MCP_QUICK_START.md)
- [MCP_INTEGRATION.md](MCP_INTEGRATION.md)
- [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) - Claude Desktop setup
- [PERPLEXITY_INTEGRATION.md](PERPLEXITY_INTEGRATION.md) - Perplexity desktop setup
- [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)
- [MCP_TESTING.md](MCP_TESTING.md)
- [MCP_VALIDATION_REPORT.md](MCP_VALIDATION_REPORT.md)

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
| ARCHITECTURE.md | System design and components | Developers, Architects | ~400 lines |
| BACKWARDS_COMPATIBILITY.md | API versioning and compatibility | API Users, Developers | ~500 lines |
| BLUE_GREEN_DEPLOYMENT.md | Zero-downtime deployment strategy | DevOps, SRE | ~605 lines |
| CLAUDE_INTEGRATION.md | Claude Desktop integration | MCP Users, Integrators | ~900 lines |
| CONTRIBUTING.md | Development workflow and guidelines | Developers, Contributors | ~200 lines |
| DEPLOYMENT.md | Deployment procedures and best practices | DevOps, SRE | ~200 lines |
| DEPLOYMENT_IMPLEMENTATION.md | Detailed deployment implementation | DevOps, Engineers | ~300 lines |
| DESIGN_PRINCIPLES.md | Core architectural principles | Architects, Developers | ~100 lines |
| HOUSEKEEPING.md | Quality and operational standards | All Developers | ~980 lines |
| LIVE_DATA_FETCHING.md | Data fetching architecture | Developers, Architects | ~260 lines |
| MCP_INTEGRATION.md | MCP protocol integration | Developers, Integrators | ~300 lines |
| MCP_QUICK_START.md | Quick start guide | All Users | ~353 lines |
| MCP_TESTING.md | Testing procedures | Developers, QA | ~458 lines |
| MCP_VALIDATION_REPORT.md | Test results and validation | QA, Management | ~150 lines |
| VS_CODE_INTEGRATION.md | VS Code workspace & development | Developers | ~600 lines |

## Finding Information

**Common Questions:**

- **How do I set up the project?** → [README.md](../README.md#installation)
- **How do I configure VS Code for development?** → [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)
- **How do I run the MCP server?** → [MCP_QUICK_START.md](MCP_QUICK_START.md)
- **How do I test the MCP server?** → [MCP_TESTING.md](MCP_TESTING.md)
- **How do I integrate with Claude Desktop?** → [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md)
- **How do I deploy?** → CI/CD-driven via `.github/workflows/ci-cd.yml` on merge to `master` (Fly.io + health check); `DEPLOYMENT.md` predates this and describes the retired Azure target
- **How do I contribute?** → [CONTRIBUTING.md](CONTRIBUTING.md)
- **What are the system design principles?** → [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)
- **How does the architecture work?** → [ARCHITECTURE.md](ARCHITECTURE.md)
- **What quality standards apply?** → [HOUSEKEEPING.md](HOUSEKEEPING.md)
- **How does live data fetching work?** → [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md)

## Latest Updates

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

**Last Updated**: March 2026
**Documentation Version**: 1.3
**Project Version**: 1.33.0
