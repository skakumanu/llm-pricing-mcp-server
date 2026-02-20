# Git Flow Correction - MCP Server Implementation

## Problem
The MCP server implementation was created on `master` branch, violating Git Flow requirements per HOUSEKEEPING.md.

## Solution: Follow Git Flow Correctly

Run the following commands **in order** in PowerShell:

---

## Step 1: Switch to Develop and Update

```powershell
cd c:\Users\skaku\OneDrive\Documents\GitHub\llm-pricing-mcp-server
.\.venv\Scripts\Activate.ps1

# Switch to develop and get latest
git checkout develop
git pull origin develop
```

**Expected Output:**
```
Switched to branch 'develop'
Already up to date.
```

---

## Step 2: Create Feature Branch

```powershell
# Create feature branch from develop
git checkout -b feature/mcp-server

# Verify you're on the right branch
git branch --show-current
```

**Expected Output:**
```
feature/mcp-server
```

---

## Step 3: Stage MCP Files (Force-Add since they might be gitignored)

```powershell
# Add all MCP files
git add -f mcp/
git add -f docs/MCP_*.md
git add -f FILE_INVENTORY.md
git add -f MCP_SETUP_COMPLETE.md
git add -f .vscode/launch.json

# Verify staged changes
git status
```

**Expected Output:**
```
On branch feature/mcp-server
Changes to be committed:
  (new file): mcp/__init__.py
  (new file): mcp/server.py
  (new file): mcp/schema_generator.py
  ... and 35 more files
```

---

## Step 4: Create Commits Following Branch Plan

### Commit 1: Infrastructure
```powershell
git add mcp/__init__.py mcp/schema_generator.py
git commit -m "chore: create MCP package structure and schema generator"
```

### Commit 2: Core Server
```powershell
git add mcp/server.py mcp/tools/tool_manager.py
git add mcp/sessions/
git add mcp/utils/
git add mcp/tools/__init__.py
git commit -m "feat: implement JSON-RPC 2.0 MCP server with STDIO support"
```

### Commit 3: Tool Implementations
```powershell
git add mcp/tools/get_all_pricing.py
git add mcp/tools/estimate_cost.py
git add mcp/tools/compare_costs.py
git add mcp/tools/get_performance_metrics.py
git add mcp/tools/get_use_cases.py
git commit -m "feat: implement 5 MCP tools for pricing operations"
```

### Commit 4: Tool Manifests
```powershell
git add mcp/tools/manifests/
git commit -m "docs: add tool manifests with examples and input schemas"
```

### Commit 5: Generated Schemas
```powershell
git add mcp/schemas/
git commit -m "build: auto-generate JSON schemas from Pydantic models"
```

### Commit 6: Documentation
```powershell
git add docs/MCP_INTEGRATION.md
git add docs/MCP_TESTING.md
git add docs/MCP_QUICK_START.md
git add docs/MCP_VALIDATION_REPORT.md
git commit -m "docs: add comprehensive MCP server documentation and guides"
```

### Commit 7: Additional Documentation
```powershell
git add FILE_INVENTORY.md
git add MCP_SETUP_COMPLETE.md
git commit -m "docs: add setup and file inventory documentation"
```

### Commit 8: VS Code Configuration
```powershell
git add .vscode/launch.json
git commit -m "config: add VS Code launch configurations for MCP server"
```

---

## Step 5: Review All Commits

```powershell
# See all commits on your feature branch
git log --oneline feature/mcp-server -10

# See what changed compared to develop
git diff develop..feature/mcp-server --stat
```

---

## Step 6: Push Feature Branch

```powershell
# Push to remote
git push -u origin feature/mcp-server

# Verify
git branch -vv
```

**Expected Output:**
```
  develop                        abcd123 [origin/develop] commit message
  feature/mcp-server             xyz1234 [origin/feature/mcp-server] commit message
* feature/mcp-server             xyz1234 [origin/feature/mcp-server] commit message
```

---

## Step 7: Create Pull Request to Develop

**Via GitHub Web**:
1. Go to https://github.com/skakumanu/llm-pricing-mcp-server
2. Click "Compare & Pull Request" for `feature/mcp-server`
3. Set:
   - **Base**: `develop` (NOT master)
   - **Compare**: `feature/mcp-server`
   - **Title**: `feat: Add MCP Server as Parallel Interface to FastAPI`
   - **Description**: See below

**PR Description:**
```markdown
## Summary
Implements Model Context Protocol (MCP) server as a parallel interface to the existing FastAPI application. The MCP server runs independently via JSON-RPC 2.0 over STDIO with zero impact on FastAPI endpoints.

## Changes
- ✅ 5 production-ready MCP tools
- ✅ 15 auto-generated JSON schemas
- ✅ Tool manifests with examples
- ✅ Comprehensive documentation (1,500+ lines)
- ✅ VS Code launch configurations
- ✅ Zero breaking changes to FastAPI
- ✅ MCP Security Minimum Bar compliant

## What This Enables
- Query pricing via JSON-RPC 2.0 protocol
- Pricing estimation and comparison
- Performance metrics retrieval
- Use case recommendations
- Integration with Claude and other MCP clients

## Related Documentation
- [MCP Integration](docs/MCP_INTEGRATION.md) - Full architecture and git flow
- [MCP Testing](docs/MCP_TESTING.md) - Testing procedures
- [MCP Quick Start](docs/MCP_QUICK_START.md) - Getting started
- [Validation Report](docs/MCP_VALIDATION_REPORT.md) - Complete audit trail

## Testing
See `docs/MCP_TESTING.md` for comprehensive test instructions.

## Deployment
Server can be run standalone: `python mcp/server.py`
Clients communicate via JSON-RPC 2.0 over STDIO.

Fixes #[issue-number] (if applicable)
```

---

## Step 8: After PR Review & Approval

Once the PR is approved and merged into `develop`:

```powershell
# Switch back to develop
git checkout develop
git pull origin develop

# Verify MCP files are now in develop
git show develop:mcp/server.py  # Shows first 50 lines
```

---

## Later: When Ready to Release to Master

```powershell
# Create release branch
git checkout develop
git pull
git checkout -b release/v1.6.0  # Use appropriate version

# Make version bump if needed, then:
git push -u origin release/v1.6.0

# Create PR from release/v1.6.0 to master
# After merge to master, also merge back to develop
```

---

## Verification Checklist ✅

- [ ] Currently on `feature/mcp-server` branch
- [ ] All MCP files staged with `git add -f`
- [ ] 8 commits made with proper messages
- [ ] Commits are in order (infrastructure → core → tools → configs → docs)
- [ ] `git log --oneline` shows 8 new commits
- [ ] `git push -u origin feature/mcp-server` succeeded
- [ ] PR created to `develop` (NOT `master`)
- [ ] PR title and description complete
- [ ] PR shows "Able to merge" status
- [ ] CI/CD checks pass (if enabled)

---

## If You Make a Mistake

### Need to undo commit?
```powershell
git reset --soft HEAD~1  # Undo last commit, keep changes staged
git reset --hard HEAD~1  # Undo last commit, discard changes
```

### Need to change branch?
```powershell
git checkout develop  # Go back to develop
git branch -D feature/mcp-server  # Delete feature branch locally
git push origin --delete feature/mcp-server  # Delete from remote
git checkout -b feature/mcp-server  # Start over
```

### Need to update branch from develop?
```powershell
git fetch origin develop
git rebase origin/develop
git push -u origin feature/mcp-server  # Force push after rebase
```

---

## Mandatory Checks Before Pushing

```powershell
# 1. Verify you're on feature branch
git status
# Should show: "On branch feature/mcp-server"

# 2. Verify branch is based on develop
git merge-base --is-ancestor develop feature/mcp-server
# Should print nothing (means yes)

# 3. Verify no secrets in changes
git diff develop..feature/mcp-server | Select-String -Pattern "password|secret|key|token|api"
# Should return nothing

# 4. Run tests
pytest tests/ -q --tb=short
# All tests must pass

# 5. Only then push
git push -u origin feature/mcp-server
```

---

**Status**: This guide provides the exact steps to follow Git Flow. Execute commands in order and verify each step. Once PR is merged to `develop`, it will eventually be released to `master` via a `release/*` branch, as per HOUSEKEEPING.md section 4.4.

