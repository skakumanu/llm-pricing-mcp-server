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
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Standard deployment procedures and best practices
- **[BLUE_GREEN_DEPLOYMENT.md](BLUE_GREEN_DEPLOYMENT.md)** - Zero-downtime blue-green deployment strategy and implementation
- **[DEPLOYMENT_IMPLEMENTATION.md](DEPLOYMENT_IMPLEMENTATION.md)** - Detailed implementation guide for deployment automation
- **[MCP_BLUE_GREEN_DEPLOYMENT.md](MCP_BLUE_GREEN_DEPLOYMENT.md)** - MCP-specific blue-green deployment with automated validation

### MCP (Model Context Protocol) Integration
- **[MCP_QUICK_START.md](MCP_QUICK_START.md)** - Quick start guide for running the MCP server
- **[MCP_TESTING.md](MCP_TESTING.md)** - Comprehensive testing guide with all test scripts
- **[MCP_INTEGRATION.md](MCP_INTEGRATION.md)** - Architecture and integration patterns
- **[CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md)** - Claude Desktop integration guide (local development)
- **[MCP_AZURE_CLIENT_SETUP.md](MCP_AZURE_CLIENT_SETUP.md)** - End-user guide for connecting to Azure-hosted server
- **[VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)** - VS Code workspace setup and development guide
- **[MCP_PRODUCTION_CHECKLIST.md](MCP_PRODUCTION_CHECKLIST.md)** - Pre-deployment checklist and procedures
- **[MCP_MONITORING_GUIDE.md](MCP_MONITORING_GUIDE.md)** - Production monitoring and observability
- **[MCP_VALIDATION_REPORT.md](MCP_VALIDATION_REPORT.md)** - Validation results and test reports

### Development & Contribution
- **[CONTRIBUTING.md](CONTRIBUTING.md)** - Contribution guidelines, Git Flow workflow, and development setup
- **[HOUSEKEEPING.md](HOUSEKEEPING.md)** - Automatic housekeeping checklists for code quality, security, and deployment
- **[SECURITY_AUDIT.md](SECURITY_AUDIT.md)** - Security audit findings and remediation
- **[NEXT_STEPS.md](NEXT_STEPS.md)** - Roadmap and future enhancements

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
1. Begin with [DEPLOYMENT.md](DEPLOYMENT.md) for overview
2. Study [MCP_BLUE_GREEN_DEPLOYMENT.md](MCP_BLUE_GREEN_DEPLOYMENT.md) for MCP deployment strategy
3. Reference [DEPLOYMENT_IMPLEMENTATION.md](DEPLOYMENT_IMPLEMENTATION.md) for setup
4. Review [MCP_PRODUCTION_CHECKLIST.md](MCP_PRODUCTION_CHECKLIST.md) before deploying
5. Configure [MCP_MONITORING_GUIDE.md](MCP_MONITORING_GUIDE.md) for production
6. Use [HOUSEKEEPING.md](HOUSEKEEPING.md) for operational procedures

**For MCP Client Integrators:**
1. **Azure Users (Recommended)**: Follow [MCP_AZURE_CLIENT_SETUP.md](MCP_AZURE_CLIENT_SETUP.md) to connect to the cloud server
2. **Local Development**: Follow [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) for Claude Desktop with local server
3. Review [MCP_INTEGRATION.md](MCP_INTEGRATION.md) for architecture and custom clients
4. Use [MCP_TESTING.md](MCP_TESTING.md) to validate your integration
5. Check [MCP_VALIDATION_REPORT.md](MCP_VALIDATION_REPORT.md) for expected behavior

**For API Users:**
1. Start with [README.md](../README.md) API Documentation section
2. Review [BACKWARDS_COMPATIBILITY.md](BACKWARDS_COMPATIBILITY.md) for version info
3. Check [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md) for data source details
4. See [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md) for design philosophy

### By Topic

**Deployment & Infrastructure:**
- [DEPLOYMENT.md](DEPLOYMENT.md)
- [BLUE_GREEN_DEPLOYMENT.md](BLUE_GREEN_DEPLOYMENT.md)
- [MCP_BLUE_GREEN_DEPLOYMENT.md](MCP_BLUE_GREEN_DEPLOYMENT.md)
- [DEPLOYMENT_IMPLEMENTATION.md](DEPLOYMENT_IMPLEMENTATION.md)
- [MCP_PRODUCTION_CHECKLIST.md](MCP_PRODUCTION_CHECKLIST.md)
- [HOUSEKEEPING.md](HOUSEKEEPING.md) - Blue-Green Deployment section

**MCP Protocol & Tools:**
- [MCP_QUICK_START.md](MCP_QUICK_START.md)
- [MCP_INTEGRATION.md](MCP_INTEGRATION.md)
- [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md) - Local development setup
- [MCP_AZURE_CLIENT_SETUP.md](MCP_AZURE_CLIENT_SETUP.md) - Cloud server connection (recommended for end users)
- [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)
- [MCP_TESTING.md](MCP_TESTING.md)
- [MCP_MONITORING_GUIDE.md](MCP_MONITORING_GUIDE.md)
- [MCP_VALIDATION_REPORT.md](MCP_VALIDATION_REPORT.md)

**Architecture & Design:**
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)
- [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md)

**API & Versioning:**
- [BACKWARDS_COMPATIBILITY.md](BACKWARDS_COMPATIBILITY.md)

**Security & Quality:**
- [SECURITY_AUDIT.md](SECURITY_AUDIT.md)
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
| MCP_BLUE_GREEN_DEPLOYMENT.md | MCP zero-downtime deployment | DevOps, SRE | ~544 lines |
| MCP_INTEGRATION.md | MCP protocol integration | Developers, Integrators | ~300 lines |
| MCP_MONITORING_GUIDE.md | Production monitoring | DevOps, SRE | ~350 lines |
| MCP_PRODUCTION_CHECKLIST.md | Pre-deployment checklist | DevOps, QA | ~200 lines |
| MCP_QUICK_START.md | Quick start guide | All Users | ~353 lines |
| MCP_TESTING.md | Testing procedures | Developers, QA | ~458 lines |
| MCP_VALIDATION_REPORT.md | Test results and validation | QA, Management | ~150 lines |
| VS_CODE_INTEGRATION.md | VS Code workspace & development | Developers | ~600 lines |
| SECURITY_AUDIT.md | Security findings | Security, Architects | ~250 lines |
| NEXT_STEPS.md | Roadmap and future work | Management, Architects | ~100 lines |

## Finding Information

**Common Questions:**

- **How do I set up the project?** → [README.md](../README.md#installation)
- **How do I configure VS Code for development?** → [VS_CODE_INTEGRATION.md](VS_CODE_INTEGRATION.md)
- **How do I run the MCP server?** → [MCP_QUICK_START.md](MCP_QUICK_START.md)
- **How do I test the MCP server?** → [MCP_TESTING.md](MCP_TESTING.md)
- **How do I integrate with Claude Desktop?** → [CLAUDE_INTEGRATION.md](CLAUDE_INTEGRATION.md)
- **How do I deploy?** → [DEPLOYMENT.md](DEPLOYMENT.md)
- **How do I maintain zero-downtime?** → [MCP_BLUE_GREEN_DEPLOYMENT.md](MCP_BLUE_GREEN_DEPLOYMENT.md)
- **How do I monitor production?** → [MCP_MONITORING_GUIDE.md](MCP_MONITORING_GUIDE.md)
- **How do I contribute?** → [CONTRIBUTING.md](CONTRIBUTING.md)
- **What are the system design principles?** → [DESIGN_PRINCIPLES.md](DESIGN_PRINCIPLES.md)
- **How does the architecture work?** → [ARCHITECTURE.md](ARCHITECTURE.md)
- **What quality standards apply?** → [HOUSEKEEPING.md](HOUSEKEEPING.md)
- **How does live data fetching work?** → [LIVE_DATA_FETCHING.md](LIVE_DATA_FETCHING.md)

## Latest Updates

- **February 20, 2026** - Added VS Code workspace configuration and integration guide
- **February 20, 2026** - Added MCP testing scripts (quick_validate.py, validate_mcp_client.py, mcp_blue_green_deploy.py)
- **February 20, 2026** - Completed MCP blue-green deployment with v1.6.0 production deployment
- **February 20, 2026** - Updated documentation index with all MCP guides
- **February 19, 2026** - Created documentation index and reorganized docs into dedicated folder
- **February 19, 2026** - Added comprehensive blue-green deployment housekeeping procedures
- **February 19, 2026** - Enhanced housekeeping with security and quality standards

---

**Last Updated**: February 20, 2026  
**Documentation Version**: 1.2  
**Project Version**: 1.7.0
