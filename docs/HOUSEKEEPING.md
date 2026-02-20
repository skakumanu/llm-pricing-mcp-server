# Automatic Housekeeping Checklist

This document outlines the automatic housekeeping tasks that should be performed after development work is completed. These checks ensure code quality, security, documentation accuracy, and a consistent user experience without requiring manual prompts.

---

## 🚨 MANDATORY: Git Flow Requirements

**Every single change to this repository MUST follow Git Flow.** This is not optional. Violations block deployments and break the CI/CD pipeline.

### ✅ Before You Commit: Ask Yourself These Questions

**✓ YES - Continue if:**
- [ ] Am I on a feature/*, hotfix/*, or release/* branch?
- [ ] Did I create this branch from the correct base (develop for features, master for hotfixes)?
- [ ] Is the branch name descriptive and follows the pattern (feature/*, hotfix/*, release/*)?
- [ ] Did I check `git status` to see my current branch?
- [ ] Did I run tests locally and they all pass?
- [ ] Did I review my changes with `git diff --cached`?

**✗ NO - STOP and create proper branch if:**
- [ ] Am I on `master` or `develop` branch trying to commit feature code? **→ WRONG!**
- [ ] Did I make changes directly on master? **→ WRONG!**
- [ ] Did I plan to push directly to develop? **→ WRONG!**
- [ ] Is this my first commit on master without a PR? **→ WRONG!**

### 🔀 Branch Decision Tree

```
What are you working on?

├─ New Feature (code, docs, tests for new functionality)
│  ├─ Base: develop
│  ├─ Branch: feature/description-of-feature
│  ├─ PR to: develop
│  └─ Example: feature/file-organization-standards
│
├─ Bug Fix in Production (urgent, breaking production)
│  ├─ Base: master
│  ├─ Branch: hotfix/brief-description
│  ├─ PR to: master AND develop
│  └─ Example: hotfix/deployment-slot-issue
│
├─ Release Preparation (version bump, final testing)
│  ├─ Base: develop
│  ├─ Branch: release/vX.X.X
│  ├─ PR to: master
│  └─ Example: release/v1.5.2
│
├─ Documentation Only (no code changes)
│  ├─ Base: develop
│  ├─ Branch: feature/docs/description
│  ├─ PR to: develop
│  └─ Example: feature/docs/git-flow-guidelines
│
└─ Test Updates Only (no production code changes)
   ├─ Base: develop
   ├─ Branch: feature/tests/description
   ├─ PR to: develop
   └─ Example: feature/tests/add-security-validations
```

### ❌ What NOT To Do (These Violations Will Block Your PR)

| ❌ VIOLATION | ✅ CORRECT APPROACH |
|---|---|
| Direct commit to `master` | Create `feature/*` from `develop`, PR to `develop`, then release to `master` |
| Direct commit to `develop` | Create `feature/*` from `develop`, PR to `develop` |
| Push directly to `master` without PR | All commits to `master` must be via PR from `release/*` or `hotfix/*` |
| `feature/` branch from `master` | Feature branches ALWAYS come from `develop` |
| `hotfix/` branch from `develop` | Hotfixes come from `master` only (for production issues) |
| Multiple feature changes in one branch | Keep branches focused on one feature; split large changes |
| Commit without tests | Every code commit must include related tests |
| Commit with hardcoded secrets | Scan with `git diff --cached` before committing |

### ✅ Quick Pre-Commit Checklist

Before EVERY commit, run this checklist:

```bash
# 1. Verify you're on the right branch
git status
# Should show: feature/*, hotfix/*, release/*, develop, or master

# 2. Verify your branch is based on the right parent
git log --oneline --graph --all | head -20
# Check that your branch stems from develop or master appropriately

# 3. Review ALL staged changes
git diff --cached
# Verify: no secrets, no debug code, correct files included

# 4. Run tests
pytest tests/ -q --tb=no
# All 109 tests must pass

# 5. Only THEN commit
git commit -m "type(scope): description"

# 6. Push to YOUR feature branch (not master/develop)
git push origin feature/your-name
```

### 📋 Standard Git Flow Workflows

#### ✅ Correct: Adding a New Feature

```bash
# Step 1: Start from develop
git checkout develop
git pull origin develop

# Step 2: Create feature branch
git checkout -b feature/add-new-feature

# Step 3: Make changes, test locally
# ... make code changes ...
pytest tests/ -q --tb=no          # All tests pass ✓
git diff --cached                 # No secrets ✓

# Step 4: Commit with clear message
git add .
git commit -m "feat(module): add new feature implementation"

# Step 5: Push feature branch
git push origin feature/add-new-feature

# Step 6: Create PR on GitHub
# - Base: develop
# - Compare: feature/add-new-feature
# - Add description of what changed and why

# Step 7: After PR approved and CI passes
# - GitHub will show "Merge pull request" button
# - Click to merge (DO NOT push directly)
# - Delete remote branch

# Step 8: Clean up locally
git checkout develop
git pull origin develop
git branch -d feature/add-new-feature
```

#### ✅ Correct: Production Hotfix

```bash
# Step 1: Start from master (production)
git checkout master
git pull origin master

# Step 2: Create hotfix branch
git checkout -b hotfix/fix-production-issue

# Step 3: Make fix, test thoroughly
# ... make fix ...
pytest tests/ -q --tb=no          # All tests pass ✓

# Step 4: Commit hotfix
git add .
git commit -m "fix(deployment): correct production issue"

# Step 5: Push hotfix branch
git push origin hotfix/fix-production-issue

# Step 6: Create TWO PRs
# - PR #1: hotfix → master (for production release)
# - PR #2: hotfix → develop (to merge fix into development)

# Both must pass CI/CD before merging
```

#### ❌ WRONG: Direct Commit to Master

```bash
# This will FAIL:
git checkout master
git commit -m "quick fix"          # ❌ BLOCKED - Cannot commit to master
git push origin master             # ❌ BLOCKED - Branch protection

# Instead, do this:
git checkout -b hotfix/quick-fix   # ✓ Create branch first
git commit -m "fix: quick fix"     # ✓ Commit to feature branch
git push origin hotfix/quick-fix   # ✓ Push to feature branch
# Then create PR and merge properly
```

#### ❌ WRONG: Documentation Changed on Master

```bash
# This will FAIL:
git checkout master
# ... update docs/HOUSEKEEPING.md ...
git commit -m "docs: update housekeeping"        # ❌ BLOCKED
git push origin master                           # ❌ BLOCKED

# Instead, do this:
git checkout develop                  # ✓ Start from develop
git pull origin develop
git checkout -b feature/docs/update-housekeeping # ✓ Feature branch
# ... update docs/HOUSEKEEPING.md ...
git commit -m "docs: update housekeeping"        # ✓ Commit to feature
git push origin feature/docs/update-housekeeping # ✓ Push to feature
# Then create PR to develop
```

### 🔍 How to Check If You're on the Right Branch

```bash
# See current branch (highlighted with *)
git branch

# See full branch history with base
git log --graph --oneline --all | head -30

# See which branch you're currently on
git rev-parse --abbrev-ref HEAD
# Output: develop, feature/*, hotfix/*, release/*, or master

# See all remote branches
git branch -r
```

### ⚠️ Branch Protection Rules Enforced

| Branch | Protection |
|---|---|
| `master` | ✓ PR required ✓ All tests must pass ✓ CI /test must pass ✓ Cannot push directly |
| `develop` | ✓ PR required from feature branches ✓ All tests must pass ✓ Cannot commit directly |
| `feature/*` | No protection - but PR required for merge to develop |
| `hotfix/*` | No protection - but PR to both master AND develop |
| `release/*` | No protection - but PR to master for final release |

**Attempting to push directly to `master` will be blocked by GitHub branch protection.**

---

## Code Changes Housekeeping

### ✅ After Every Code Modification
- [ ] **Verify no syntax errors** - Check for Python syntax issues before committing
- [ ] **Run relevant tests** - Execute tests for modified modules/features
- [ ] **Check code consistency** - Verify formatting and style compliance
- [ ] **Update related tests** - Add/update assertions for new behavior
- [ ] **Verify imports** - Ensure all imports are correct and used
- [ ] **Validate input/output** - Ensure all inputs are validated and outputs are sanitized
  - Check for injection vulnerabilities (SQL, command, code)
  - Validate data types and ranges
  - Sanitize user-supplied data before database/output
  - Verify error messages don't expose sensitive information
- [ ] **Scan for hardcoded secrets** - Check for API keys, passwords, tokens
  - Use `git diff` to review before committing
  - Search for patterns: `password`, `api_key`, `token`, `secret`, `credentials`
  - Verify environment variables are used instead

### ✅ After Functional Changes
- [ ] **Run full test suite** - Execute all tests to catch regressions (109 tests minimum)
- [ ] **Verify test coverage** - Ensure coverage ≥ 90% for modified code
- [ ] **Update docstrings** - Ensure documentation matches implementation
- [ ] **Update type hints** - Verify all function signatures are properly typed
- [ ] **Check backwards compatibility** - Verify changes don't break existing APIs
- [ ] **Review error handling** - Verify all error paths are tested
- [ ] **Test edge cases** - Validate boundary conditions and unusual inputs

### ✅ After Version/Release Changes
- [ ] **Update `src/__init__.py`** - Single source of truth for `__version__`
- [ ] **Verify dynamic imports** - Settings properly import version from src
- [ ] **Update test assertions** - Version references in test files
- [ ] **Update README.md** - All API examples and documentation
- [ ] **Update BACKWARDS_COMPATIBILITY.md** - Version timeline and examples
- [ ] **Update package.json** - Match version in metadata
- [ ] **Update deployment docs** - Docker tags and examples
  - BLUE_GREEN_DEPLOYMENT.md
  - DEPLOYMENT_IMPLEMENTATION.md

### ✅ After Documentation Changes
- [ ] **Update version references** - Consistency across all .md files
- [ ] **Verify code examples** - Test that examples in docs still work
- [ ] **Check for broken links** - Ensure relative paths are correct
- [ ] **Update table of contents** - If adding new sections
- [ ] **Review related files** - Check if other docs need updates
- [ ] **Keep docs well organized** - Verify proper structure and hierarchy
  - Consistent heading levels (H1 → H2 → H3)
  - Logical grouping of related content
  - Cross-references between related topics
  - Clear navigation (links to related docs)
- [ ] **Commit documentation changes** - Separate docs commits from code commits
  - Commit message pattern: `docs: brief description of changes`
  - Include what was added/updated/fixed
  - Reference related code commits if applicable

### ✅ After Dependency Changes
- [ ] **Update requirements.txt** - Reflect new/changed packages
- [ ] **Update pyproject.toml** - Keep in sync with dependencies
- [ ] **Verify imports work** - Test that packages install and import correctly
- [ ] **Check for security vulnerabilities** - Run vulnerability scanner

## File & Directory Organization

Maintaining well-organized files and directories is essential for code maintainability, collaboration, and scalability.

### ✅ Directory Structure Standards

**Root Directory Organization:**
```
llm-pricing-mcp-server/
├── .github/              # GitHub workflows and CI/CD
├── .azure/               # Azure configuration files
├── docs/                 # All project documentation
├── scripts/              # Deployment and utility scripts
├── src/                  # Source code (all Python)
├── tests/                # Test files
├── redirect-app/         # Redirect/proxy application
├── .env.example          # Example environment variables
├── .gitignore            # Git ignore rules
├── .dockerignore         # Docker ignore rules
├── Dockerfile            # Docker container definition
├── README.md             # Project overview (root only)
├── LICENSE               # License text
├── requirements.txt      # Python dependencies
├── pyproject.toml        # Project metadata and config
├── Procfile              # Heroku/Cloud deployment
└── [other config files]
```

**Rules:**
- [ ] Root directory kept clean (only essential files)
- [ ] All code in `src/` directory
- [ ] All tests in `tests/` directory
- [ ] All documentation in `docs/` directory
- [ ] Scripts in `scripts/` directory
- [ ] No loose Python files in root
- [ ] No scattered configuration files

### ✅ Source Code Organization (`src/`)

**Expected Structure:**
```
src/
├── __init__.py           # Package initialization with __version__
├── main.py               # FastAPI application entry point
├── config/               # Configuration management
│   ├── __init__.py
│   └── settings.py       # Settings and environment config
├── models/               # Data models (Pydantic)
│   ├── __init__.py
│   ├── pricing.py        # Pricing data models
│   └── deployment.py     # Deployment models
├── services/             # Business logic services
│   ├── __init__.py
│   ├── base_provider.py         # Abstract base provider
│   ├── [provider]_pricing.py    # Provider implementations
│   ├── pricing_aggregator.py    # Aggregate multiple providers
│   ├── data_fetcher.py          # Data fetching utilities
│   ├── data_sources.py          # Data source definitions
│   ├── deployment.py            # Deployment management
│   ├── geolocation.py           # Geolocation service
│   ├── telemetry.py             # Telemetry tracking
│   └── [other services]
└── __pycache__/          # Python cache (gitignored)
```

**Naming Conventions:**
- [ ] Modules use `snake_case` (lowercase with underscores)
- [ ] Classes use `PascalCase` (uppercase first letter)
- [ ] Constants use `UPPER_CASE` (all uppercase)
- [ ] Functions use `snake_case` (lowercase with underscores)
- [ ] Private functions/vars start with `_` (underscore)
- [ ] Protected functions/vars start with `_` (underscore)
- [ ] Dunder methods: `__init__`, `__str__`, etc.

**Module Organization:**
- [ ] One main class per file (when possible)
- [ ] Related functions grouped together
- [ ] Imports organized: stdlib → third-party → local
- [ ] Each module has clear docstring
- [ ] Each class has clear docstring
- [ ] Each function has docstring with params and returns

### ✅ Test File Organization (`tests/`)

**Expected Structure:**
```
tests/
├── __init__.py
├── conftest.py           # Pytest configuration and fixtures
├── test_api.py           # API endpoint tests
├── test_async_pricing.py # Async functionality tests
├── test_deployment.py    # Deployment feature tests
├── test_geolocation.py   # Geolocation service tests
├── test_models.py        # Data model tests
├── test_security.py      # Security/auth tests
├── test_services.py      # Service layer tests
└── __pycache__/          # Python cache (gitignored)
```

**Naming Conventions:**
- [ ] Test files start with `test_` prefix
- [ ] Test functions start with `test_` prefix
- [ ] Test classes start with `Test` prefix
- [ ] Test fixtures in `conftest.py` or test file
- [ ] Descriptive test names (e.g., `test_deployment_metadata_endpoint_returns_version`)
- [ ] One test suite per module being tested
- [ ] Related tests grouped in test classes

**Organization Rules:**
- [ ] One test file per source module
- [ ] Test file name matches source module name
- [ ] Tests kept in sync with source code changes
- [ ] Test coverage ≥ 90% for all modules
- [ ] No skipped tests without documented reason
- [ ] Fixtures for common test data/setup

### ✅ Documentation Organization (`docs/`)

**Expected Structure:**
```
docs/
├── INDEX.md                           # Documentation table of contents
├── ARCHITECTURE.md                    # System architecture
├── DESIGN_PRINCIPLES.md               # Design philosophy
├── LIVE_DATA_FETCHING.md              # Data fetching architecture
├── BACKWARDS_COMPATIBILITY.md         # API versioning
├── DEPLOYMENT.md                      # Deployment overview
├── BLUE_GREEN_DEPLOYMENT.md           # Zero-downtime deployment
├── DEPLOYMENT_IMPLEMENTATION.md       # Implementation details
├── CONTRIBUTING.md                    # Contribution guidelines
└── HOUSEKEEPING.md                    # This file
```

**Documentation Standards:**
- [ ] All .md files in `docs/` folder only
- [ ] README.md in root (project overview only)
- [ ] Each doc has clear H1 title
- [ ] H2 sections for major topics
- [ ] H3 subsections for details
- [ ] Table of contents for long documents
- [ ] Cross-references using relative links
- [ ] Code examples in fenced blocks with language
- [ ] Updated date at bottom of each doc
- [ ] INDEX.md provides navigation

**Link Format in docs/:**
- [ ] Relative links between docs (no `/docs/`)
- [ ] Links to README: `[README](../README.md)`
- [ ] Links to same folder: `[FILE.md](FILE.md)`
- [ ] External links with full URLs

### ✅ Scripts Organization (`scripts/`)

**Expected Structure:**
```
scripts/
├── blue_green_deployment.sh  # Bash deployment script
├── blue_green_deployment.ps1 # PowerShell script
├── [utility scripts]
└── README.md                 # Scripts documentation
```

**Naming Conventions:**
- [ ] Descriptive script names
- [ ] Extensions match language (`.sh`, `.ps1`, `.py`)
- [ ] Executable permissions set (`chmod +x`)
- [ ] Shebang line at top (e.g., `#!/bin/bash`)
- [ ] Comments explaining what script does
- [ ] Error handling and exit codes
- [ ] Help text/usage examples

### ✅ Configuration File Organization

**Root Level Config Files:**
- `pyproject.toml` - Python project config (primary)
- `requirements.txt` - Python dependencies
- `.env.example` - Example environment variables
- `.gitignore` - Git ignore rules
- `.dockerignore` - Docker ignore rules
- `Dockerfile` - Container definition
- `LICENSE` - License file

**Rules:**
- [ ] Only essential config files in root
- [ ] All sensitive configs in `.env` (not in repo)
- [ ] Example configs with `.example` suffix
- [ ] Document config requirements in README
- [ ] Use environment variables for secrets
- [ ] Azure Key Vault for production secrets
- [ ] Never commit `.env` files

### ✅ Git Repository Organization

**Git Files/Folders:**
```
.git/               # Git repository (auto-created, never touch)
.github/            # GitHub-specific configs
.gitignore          # Ignore rules
.gitattributes      # File handling rules
```

**Rules:**
- [ ] `.gitignore` excludes all secrets/credentials
- [ ] `.gitignore` excludes build artifacts
- [ ] `.gitignore` excludes cache/temp files
- [ ] `.gitignore` excludes environment files
- [ ] `.gitattributes` handles line endings
- [ ] Meaningful `.git` history (linear on master)
- [ ] Commit messages follow conventions
- [ ] No large files in git (> 100MB)
- [ ] Binary files properly marked

### ✅ Keeping Files Organized (Ongoing)

**Weekly Organization Review:**
- [ ] Check for loose files outside proper directories
- [ ] Verify no cache/temp files committed
- [ ] Review test file coverage completeness
- [ ] Check documentation for outdated links
- [ ] Scan for duplicated code/files

**Monthly Organization Audit:**
- [ ] Review directory structure alignment
- [ ] Check for orphaned or unused files
- [ ] Verify naming conventions followed
- [ ] Scan for inconsistent file organization
- [ ] Update documentation organization if needed

**When Adding New Files:**
- [ ] Determine correct directory/module
- [ ] Follow naming conventions
- [ ] Add to proper test suite
- [ ] Update related documentation
- [ ] Update INDEX.md if adding docs
- [ ] Commit as single logical change

**When Refactoring/Reorganizing:**
- [ ] Use `git mv` to preserve history
- [ ] Update all internal references
- [ ] Update all relative paths
- [ ] Update documentation links
- [ ] Single refactoring-only commit
- [ ] Mark as refactoring in commit message

### ✅ File Organization Checklist (Pre-Commit)

- [ ] New Python files in `src/` or `tests/`
- [ ] New tests in `tests/` folder
- [ ] New docs in `docs/` folder
- [ ] Scripts in `scripts/` folder
- [ ] No lost files in root directory
- [ ] Naming conventions followed
- [ ] Internal references updated
- [ ] GitHub-specific files in `.github/`
- [ ] Azure-specific files in `.azure/`
- [ ] No duplicate files
- [ ] No orphaned files

## Git Flow Workflow

### ✅ Branch Strategy (Git Flow)
This project follows Git Flow branching model:

**Branch Types:**
- **master**: Production-ready code, tagged with versions
  - Protected branch (PR required)
  - CI /test must pass
  - All 109 tests must pass
  - Only receives merges from release branches or hotfixes
  
- **develop**: Integration branch for features
  - Base branch for feature development
  - Pre-release testing branch
  - Auto-synced with all merged feature branches
  - Should be in deployable state
  
- **feature/*** : Individual feature branches
  - Created from: `develop`
  - Merged back to: `develop` (via PR)
  - Naming: `feature/description-of-feature`
  - Example: `feature/security-baseline`, `feature/groq-integration`
  
- **release/*** : Pre-release preparation
  - Created from: `develop`
  - Merged to: `master` and `develop`
  - Naming: `release/v1.X.X`
  - For version bumping and final testing
  
- **hotfix/*** : Production fixes
  - Created from: `master`
  - Merged to: `master` and `develop`
  - Naming: `hotfix/brief-description`
  - For urgent production fixes

### ✅ Feature Development Workflow

1. **Create feature branch from develop**
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/your-feature-name
   ```

2. **Make commits regularly**
   - Small, focused commits
   - Clear commit messages
   - Include tests with code changes

3. **Push feature branch**
   ```bash
   git push origin feature/your-feature-name
   ```

4. **Create Pull Request**
   - Base: `develop`
   - PR title: Clear description of changes
   - PR description: What changed, why, testing instructions
   - Enable auto-delete after merge

5. **CI/CD Validation**
   - All tests must pass
   - Code coverage must be ≥ 90%
   - No security vulnerabilities
   - All 109 tests passing

6. **Merge to develop**
   - Merge via GitHub UI (creates merge commit)
   - Delete feature branch after merge
   - Auto-sync keeps develop synchronized

7. **Delete local feature branch**
   ```bash
   git checkout develop
   git pull origin develop
   git branch -d feature/your-feature-name
   ```

### ✅ Before Every Commit

1. **Scan for secrets**
   ```bash
   git diff HEAD --name-only | xargs grep -l "password\|api_key\|token\|secret\|credentials\|AUTH"
   ```
   - Never commit: API keys, passwords, tokens, secrets
   - Never commit: AWS keys, Azure connection strings
   - Never commit: Database credentials, SSH keys
   - Use environment variables and `.env` files instead

2. **Review changes**
   - Use `git diff --cached` to verify all changes
   - Check for debug code (`print()`, `console.log()`)
   - Verify no sensitive data in comments

3. **Verify syntax**
   - Run Python syntax check
   - Verify imports are correct

4. **Update related files**
   - Tests for new functionality
   - Documentation for changed behavior
   - Version file if releasing

### ✅ Commit Message Format

Follow conventional commits:
```
type(scope): subject

body

footer
```

**Types:**
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `test:` Test additions/updates
- `refactor:` Code restructuring (no behavior change)
- `perf:` Performance improvements
- `chore:` Maintenance, dependencies
- `ci:` CI/CD workflow changes
- `sec:` Security fixes or hardening

**Examples:**
- `feat(pricing): add Groq AI provider integration`
- `fix(deployment): correct blue-green slot swapping logic`
- `docs: update README with new API endpoints`
- `test(security): add CSRF token validation tests`
- `sec: sanitize user input to prevent XSS attacks`

### ✅ After Committing

1. **Verify commit** 
   ```bash
   git log -1 --stat
   ```
   - Confirm correct files changed
   - Verify commit message is clear

2. **Push to repository**
   - Use `git push origin [branch-name]`
   - Never use `--force` on develop/master

3. **For feature branches: Create PR**
   - Link related issues
   - Add helpful context for reviewers
   - Tag code reviewers if applicable

4. **Monitor CI/CD**
   - Check GitHub Actions status
   - Fix any failing tests immediately
   - Address security scan findings

### ✅ For Branch Protection Compliance

- **master branch**: 
  - Cannot push directly (protected)
  - Requires PR with all checks passing
  - All 109 tests must pass
  - Code review required (if configured)
  - CI /test status check required

- **develop branch**:
  - Main integration branch
  - PR required from feature branches
  - All tests must pass
  - Auto-synced with master (via workflow)

## Commit & Push Housekeeping

### ✅ Before Committing
1. **Stage changes responsibly** - Use `git add` selectively, not blindly
   - Don't use `git add -A` without reviewing what's included
   - Use `git add -p` for interactive staging
   - Always review with `git diff --cached`
2. **Review diff** - Use `git diff --cached` to verify changes
3. **Write clear message** - Follow commit message conventions (see Git Flow section)

### ✅ After Committing
1. **Verify commit** - Use `git log -1` to confirm commit message and files
2. **Push to appropriate branch** - Respect branch strategy
   - Never push to master directly
   - Always push feature branches
   - Let PRs handle merges
3. **Check GitHub Actions** - Verify CI/CD pipeline passes
   - CI /test must pass
   - Coverage must be ≥ 90%
   - No security vulnerabilities introduced
   - All 109 tests passing at minimum

## Quality Gates

### ✅ Testing Standards
- **Test Coverage**: Minimum 90% for new/modified code
- **Test Count**: Currently 109 tests passing (maintain or improve)
- **All Tests**: Run full suite before any commit
- **New Features**: Add corresponding test cases (achieving ≥ 90% coverage for the feature)
- **Regressions**: Verify no tests break after changes
- **Key Test Breakdown**:
  - 96 functional/integration tests
  - 13 security tests
  - Coverage measured with pytest-cov

### ✅ Security Standards: OWASP Coverage

**Critical Security Controls (OWASP Top 10):**

1. **A01:2021 – Broken Access Control**
   - [ ] Verify authentication is required for protected endpoints
   - [ ] Validate user permissions for resource access
   - [ ] Test role-based access control (RBAC)
   - [ ] Verify API key validation (`x-api-key` header)

2. **A02:2021 – Cryptographic Failures**
   - [ ] Use HTTPS only (enforced in production)
   - [ ] Verify secrets stored in Azure Key Vault, not code
   - [ ] Check API keys never logged or exposed
   - [ ] Validate sensitive data encrypted at rest and in transit

3. **A03:2021 – Injection**
   - [ ] Input validation on all user inputs
   - [ ] Parameterized queries (if using database)
   - [ ] No code execution from user input
   - [ ] Sanitize all outputs to prevent injection
   - [ ] Validate JSON/XML schemas

4. **A04:2021 – Insecure Design**
   - [ ] Rate limiting implemented (prevent brute force)
   - [ ] Error handling doesn't expose system details
   - [ ] Timeout protection on long-running operations
   - [ ] Grace shutdown with connection draining

5. **A05:2021 – Security Misconfiguration**
   - [ ] Security headers properly set
   - [ ] CORS configured to allow only trusted origins
   - [ ] Default credentials changed
   - [ ] Debug mode disabled in production

6. **A06:2021 – Vulnerable and Outdated Components**
   - [ ] All dependencies scanned for vulnerabilities
   - [ ] Dependency updates applied regularly
   - [ ] `pip check` passes with no vulnerabilities
   - [ ] No deprecated/EOL packages used

7. **A07:2021 – Identification & Authentication Failures**
   - [ ] Password requirements enforced (if applicable)
   - [ ] Session management secure
   - [ ] Multi-factor authentication considered
   - [ ] API key rotation policy in place

8. **A08:2021 – Software & Data Integrity Failures**
   - [ ] CI/CD pipeline secure
   - [ ] Code reviews required before merge
   - [ ] Infrastructure as Code versioned in git
   - [ ] Signed commits enabled (recommended)

9. **A09:2021 – Logging & Monitoring Failures**
   - [ ] All security events logged
   - [ ] Sensitive data never logged
   - [ ] Monitoring alerts on suspicious activity
   - [ ] Log retention policy in place

10. **A10:2021 – Server-Side Request Forgery (SSRF)**
    - [ ] Validate all external HTTP requests
    - [ ] Whitelist approved external endpoints
    - [ ] No raw user input in requests
    - [ ] Disable dangerous protocols (file://, gopher://)

### ✅ Input/Output Validation

**Input Validation (All User Inputs):**
- [ ] Type checking: Verify expected data types
- [ ] Range checking: Validate numeric bounds
- [ ] Format validation: Check email, URL, phone formats
- [ ] Length limits: Prevent buffer overflows
- [ ] Whitelist validation: Only allow known good values
- [ ] Reject null bytes and control characters
- [ ] No double encoding accepted

**Output Encoding (All Responses):**
- [ ] JSON responses properly formatted
- [ ] HTML entities escaped if returning HTML
- [ ] No sensitive data in error responses
- [ ] Stack traces never exposed to clients
- [ ] Version information limited in responses
- [ ] Consistent error message format

**API Validation:**
- [ ] Request headers validated
- [ ] Request body schemas validated
- [ ] Query parameters sanitized
- [ ] Path parameters validated
- [ ] HTTP method restrictions enforced
- [ ] Content-Type validation

### ✅ User Experience Standards

**Keep it Simple:**
- [ ] API endpoints are intuitive and consistent
- [ ] Error messages are clear and actionable
- [ ] Documentation is easy to follow
- [ ] Examples are practical and working
- [ ] No unnecessary complexity

**API Design:**
- [ ] RESTful principles followed
- [ ] Consistent endpoint naming (kebab-case or snake_case)
- [ ] Consistent response format
- [ ] Consistent error response format
- [ ] Pagination implemented where needed
- [ ] Filtering/sorting when applicable

**Documentation for Users:**
- [ ] Quick start guide available
- [ ] Common use cases documented
- [ ] Troubleshooting section included
- [ ] Code examples in multiple languages (if applicable)
- [ ] Changelog clear about breaking changes
- [ ] Migration guides for version upgrades

**Monitoring User Experience:**
- [ ] Response times tracked
- [ ] Error rates monitored
- [ ] User feedback channels available
- [ ] Support documentation accessible
- [ ] Status page for service health

### ✅ Version Management
- **Single Source**: Version defined only in `src/__init__.py`
- **Dynamic Import**: Settings imports from src.__version__
- **No Hardcoding**: Never hardcode version elsewhere
- **Semantic Versioning**:
  - MAJOR.MINOR.PATCH
  - Major: Breaking changes
  - Minor: New features (backwards compatible)
  - Patch: Bug fixes only

### ✅ Security Checks
- **Vulnerability Scanner**: Check for dependency vulnerabilities regularly
  - Run `pip check` before commits
  - Check GitHub Dependabot alerts
  - Review security advisories
  
- **Secrets Management**: 
  - Never commit API keys, passwords, tokens
  - Never commit credentials in any form
  - Use Azure Key Vault for secrets
  - Use environment variables for configuration
  - Verify .gitignore excludes sensitive files
  
- **Code Review**: 
  - All commits reviewed before merge
  - Security checks included in review
  - Test coverage verified
  
- **Permissions**: 
  - Verify code doesn't over-privilege users
  - Validate authenticated/authorized actions
  - Check for privilege escalation paths
  
- **Dependencies**: 
  - Keep packages up-to-date
  - Remove unused dependencies
  - Verify licenses are compatible

## Blue-Green Deployment Housekeeping

Blue-green deployment is the **mandatory deployment strategy** for this project. Two identical production environments (blue and green) ensure zero-downtime deployments with instant rollback capability.

### ✅ Pre-Deployment Checklist

Before ANY deployment to production:

1. **Code Readiness**
   - [ ] All 109 tests passing on merge commit
   - [ ] Test coverage ≥ 90% for new code
   - [ ] CI /test status check passed on GitHub Actions
   - [ ] No security vulnerabilities in dependencies
   - [ ] No hardcoded secrets or sensitive data
   - [ ] Code review approved (if required)

2. **Version Management**
   - [ ] Version bumped in `src/__init__.py`
   - [ ] CHANGELOG.md updated with release notes
   - [ ] Breaking changes clearly documented
   - [ ] Migration guide prepared (if needed)
   - [ ] Version tag created in Git (v1.X.X format)

3. **Deployment Artifacts**
   - [ ] Docker image built and tagged: `myregistry/llm-pricing:v1.X.X`
   - [ ] Image pushed to container registry
   - [ ] Image scanned for vulnerabilities
   - [ ] Deployment manifest reviewed and valid
   - [ ] Environment variables confirmed (no hardcoded values)

4. **Health Check Configuration**
   - [ ] Health endpoint `/health` verified working
   - [ ] Health check timeout set appropriately (≥ 5 seconds)
   - [ ] Health check interval configured (≥ 30 seconds)
   - [ ] Liveness and readiness probes configured
   - [ ] Expected HTTP 200 response verified

5. **Database/Migrations (if applicable)**
   - [ ] All database migrations completed on current environment
   - [ ] Rollback plan documented
   - [ ] No breaking schema changes in patch versions
   - [ ] Backwards compatibility verified

6. **Documentation & Communication**
   - [ ] Deployment runbook reviewed
   - [ ] Rollback plan documented
   - [ ] Status page updated (if customer-facing)
   - [ ] Team notified of deployment window
   - [ ] Stakeholders briefed on changes

### ✅ Deployment Process

**Azure App Service Blue-Green Deployment:**

1. **Identify Current Active Slot**
   ```bash
   # Check which slot is production (CURRENT)
   az webapp deployment slot list \
     --resource-group llm-pricing-rg-westus2 \
     --name llm-pricing-mcp \
     --query "[].{Name:name, Status:deploymentStatus}"
   ```
   - [ ] Note which slot is active (blue or green)
   - [ ] Idle slot will be deployment target

2. **Deploy to Idle Slot**
   - [ ] Idle slot identified
   - [ ] New Docker image pushed to idle slot
   - [ ] Deployment started via GitHub Actions or Azure CLI
   - [ ] Deployment progress monitored in logs
   - [ ] No interruption to active slot traffic

3. **Run Health Checks on Idle Slot**
   ```bash
   # Access idle slot endpoint (e.g., green slot)
   curl -X GET \
     https://llm-pricing-mcp-green.azurewebsites.net/health \
     -H "Content-Type: application/json"
   
   # Expected response:
   # {
   #   "status": "healthy",
   #   "service": "LLM Pricing MCP Server",
   #   "version": "1.X.X"
   # }
   ```
   - [ ] Health endpoint returns 200 OK
   - [ ] Version matches deployed version
   - [ ] All dependent services healthy
   - [ ] No errors in application logs
   - [ ] Performance metrics acceptable

4. **Smoke Tests on Idle Slot**
   - [ ] API endpoints responding correctly
   - [ ] Invalid requests rejected properly
   - [ ] Authentication/authorization working
   - [ ] Rate limiting functional
   - [ ] Error handling correct
   - [ ] Database connectivity (if applicable)
   - [ ] External service integrations working

5. **Monitor Staging Slot (30+ minutes)**
   - [ ] Error rates normal (0 errors expected)
   - [ ] Response times acceptable (< 200ms p95)
   - [ ] Memory usage stable
   - [ ] CPU usage normal
   - [ ] No memory leaks detected
   - [ ] Connection pools stable

6. **Perform Slot Swap**
   ```bash
   # Swap slots using Azure CLI
   az webapp deployment slot swap \
     --resource-group llm-pricing-rg-westus2 \
     --name llm-pricing-mcp \
     --slot green
   
   # Expected: Instant traffic shift from blue → green
   # Old active slot (blue) becomes new idle slot
   # New active slot (green) receives production traffic
   ```
   - [ ] Swap command executed
   - [ ] Swap completed successfully
   - [ ] No errors during swap
   - [ ] Time noted for reference

7. **Verify Swap Success (Immediate)**
   - [ ] New active slot responding to requests
   - [ ] Health endpoint returns 200
   - [ ] Version correct in responses
   - [ ] No 503 Service Unavailable errors
   - [ ] No connection timeouts

### ✅ Post-Deployment Verification

**Immediate (First 5 minutes):**
- [ ] Production traffic flowing to new slot
- [ ] Error rates at 0%
- [ ] Response times normal
- [ ] No unusual logs
- [ ] Monitoring alerts not triggered

```bash
# Verify current active slot
az webapp deployment slot list \
  --resource-group llm-pricing-rg-westus2 \
  --name llm-pricing-mcp \
  --query "[?deploymentStatus=='Current'].{Name:name, Version:siteConfig.appSettings}"
```

**Short-term (First hour):**
- [ ] Monitor error logs continuously
- [ ] Track key metrics (latency, throughput, errors)
- [ ] Review telemetry data
- [ ] Check for exception patterns
- [ ] Verify auth/security measures
- [ ] Monitor for performance degradation

**Ongoing (24 hours):**
- [ ] Error rates remain at baseline
- [ ] Response times stable
- [ ] Resource usage normal
- [ ] No memory leaks
- [ ] Scheduled tasks running correctly
- [ ] Cron jobs executing properly
- [ ] Database queries performing normally

### ✅ Idle Slot Management

**What to do with the now-idle slot (previous production):**

1. **Keep Ready for Instant Rollback**
   - [ ] Do NOT delete or deprovision idle slot
   - [ ] Keep idle slot in "warm" state (running)
   - [ ] Maintain for minimum 24 hours
   - [ ] Update status page if necessary

2. **Rollback Window**
   - [ ] Rollback available for 24 hours
   - [ ] Slot swap takes < 1 minute
   - [ ] No code changes needed for rollback
   - [ ] Team aware of rollback procedure

3. **After Observation Period (24 hours)**
   - [ ] Confirm no issues detected
   - [ ] All metrics within expected range
   - [ ] No post-deployment bug reports
   - [ ] Only then, can idle slot be cleaned up
   - [ ] Run cleanup: `az webapp config appsettings update ...`

### ✅ Rollback Procedures

**Immediate Rollback (if issues detected):**

```bash
# Slot swap back to previous version
az webapp deployment slot swap \
  --resource-group llm-pricing-rg-westus2 \
  --name llm-pricing-mcp \
  --slot blue  # or green, whichever is idle
```

**When to Trigger Rollback:**
- [ ] Error rate exceeds 1%
- [ ] Response time exceeds SLA (p95 > 1 second)
- [ ] Critical functionality broken
- [ ] Data corruption detected
- [ ] Security vulnerability discovered
- [ ] Dependency service unavailable
- [ ] Database connection errors
- [ ] Auth/authorization failures

**Rollback Verification:**
- [ ] Bring back previous active slot (< 1 minute)
- [ ] Verify health endpoint immediately
- [ ] Confirm version reverted
- [ ] Monitor for stability
- [ ] Document reason for rollback
- [ ] Create incident report
- [ ] Schedule post-mortem

### ✅ Deployment CI/CD Integration

**GitHub Actions Workflow Enforcement:**

In `.github/workflows/ci-cd.yml`:
- [ ] CI /test required before deployment
- [ ] All 109 tests must pass
- [ ] Coverage check passed
- [ ] Security scan passed
- [ ] Docker image built successfully
- [ ] Image pushed to registry
- [ ] Deployment to idle slot automated
- [ ] Health checks run automatically
- [ ] Alerts triggered on failure
- [ ] Slack notification sent post-deployment

**Azure Deployment Slots Configuration:**
- [ ] Production slot (blue or green) set as default
- [ ] Staging slot for pre-swap testing
- [ ] Auto-swap disabled (manual control)
- [ ] Swap warnings documented
- [ ] Slot connection strings verified
- [ ] Slot app settings verified

### ✅ Deployment Documentation

**Keep Updated in Repository:**
- [ ] `BLUE_GREEN_DEPLOYMENT.md` - Strategy & architecture
- [ ] `DEPLOYMENT_IMPLEMENTATION.md` - Implementation details
- [ ] `DEPLOYMENT.md` - Standard procedures
- [ ] `scripts/blue_green_deployment.sh` - Bash script
- [ ] `scripts/blue_green_deployment.ps1` - PowerShell script
- [ ] `.github/workflows/ci-cd.yml` - Workflow definition

**Runbooks for Each Scenario:**
- [ ] Standard deployment (feature/fix)
- [ ] Hotfix deployment (urgent production fix)
- [ ] Zero-migration deployment (schema change)
- [ ] Rollback procedure
- [ ] Emergency recovery

### ✅ Monitoring & Alerts

**Deployment Notifications:**
- [ ] Slack channel: `#deployments`
- [ ] Email notification to team
- [ ] Discord webhook configured
- [ ] Status page updated
- [ ] Deployment logged to central system

**Critical Alerts (trigger automatic investigation):**
- [ ] Error rate > 1%
- [ ] Response time spike detected
- [ ] Health check failures
- [ ] Slot swap failures
- [ ] Database connection issues
- [ ] Memory leak detected
- [ ] CPU utilization spike

**Metrics to Track:**
```
- Requests per second
- Error rate (%)
- Response time (p50, p95, p99)
- Memory usage (%)
- CPU usage (%)
- Active connections
- Request queue depth
- Failed health checks
```

### ✅ Version Control for Deployments

**Git Tags for Releases:**
```bash
# Tag the released commit
git tag -a v1.X.X -m "Release version 1.X.X"
git push origin v1.X.X

# Format: v{MAJOR}.{MINOR}.{PATCH}
# Example: v1.5.1, v1.6.0, v2.0.0
```
- [ ] Tag created after successful deployment
- [ ] Tag pushed to repository
- [ ] Release notes attached to tag
- [ ] Deployment commit referenced

### ✅ Post-Deployment Checklist (24-48 Hours)

1. **System Stability**
   - [ ] No errors in logs (24-hour period)
   - [ ] Performance metrics stable
   - [ ] All scheduled jobs executed
   - [ ] No data corruption issues
   - [ ] User feedback positive (if customer-facing)

2. **Compliance & Security**
   - [ ] No security alerts triggered
   - [ ] No unauthorized access attempts
   - [ ] Audit logs clean
   - [ ] Compliance requirements met
   - [ ] Data privacy verified

3. **Cleanup & Documentation**
   - [ ] Idle slot can be cleaned if needed
   - [ ] Deployment documentation updated
   - [ ] Lessons learned documented
   - [ ] Performance baseline updated
   - [ ] Next deployment planned

## Current Project State (as of Feb 19, 2026)

**Production Version**: 1.5.1 (dynamically managed from `src/__init__.py`)

**Test Suite**: 109 tests passing
- 96 original tests
- 13 security tests

**Key Files with Version Info**:
- `src/__init__.py` - ✅ Single source of truth
- `src/config/settings.py` - ✅ Dynamically imports
- `src/main.py` - ✅ Uses settings.app_version
- `package.json` - ✅ Updated
- `README.md` - ✅ Updated
- `BACKWARDS_COMPATIBILITY.md` - ✅ Updated
- `BLUE_GREEN_DEPLOYMENT.md` - ✅ Updated
- `DEPLOYMENT_IMPLEMENTATION.md` - ✅ Updated
- Test files - ✅ Updated assertions

**Deployment Pipeline**:
- GitHub Actions CI/CD: Fully functional
- Blue-green deployment: Configured and tested
- Azure App Service: Production environment
- Branch protection: Enabled on master

## Pre-Commit Checklist

### ✅ Security Scanning Before Any Commit

**Secrets Detection:**
```bash
# Search for common secret patterns
git diff HEAD --name-only | xargs grep -E "password|api_key|token|secret|credentials|AUTH|PASSWORD|API_KEY"

# Check for AWS/Azure credentials
git diff HEAD --name-only | xargs grep -E "AKIA|aws_secret|azure.*secret|subscription.*key"
```

**Code Review Checklist:**
- [ ] No `print()` debug statements left in code
- [ ] No commented-out code blocks (cleanup or remove)
- [ ] No TODO/FIXME without issue reference
- [ ] No hardcoded configuration values
- [ ] No API keys or secrets in code
- [ ] No large test data files committed
- [ ] No IDE/editor config files committed

**Test and Coverage Check:**
```bash
# Run full test suite
python -m pytest tests/ -v

# Check coverage
python -m pytest --cov=src tests/

# Verify ≥ 90% coverage on modified code
```

**Dependency Check:**
```bash
# Check for vulnerabilities
pip check

# Check for unused imports
python -m pylint src/
```

### ✅ Post-Push Verification

1. **GitHub Actions Results**
   - CI /test workflow passes
   - All 109 tests pass
   - Coverage ≥ 90%
   - No security warnings
   
2. **PR Status (if applicable)**
   - All CI checks green
   - Code review complete
   - Ready for merge approval

3. **Documentation Sync**
   - README.md updated if behavior changed
   - API docs updated for new endpoints
   - Version references updated (if release)
   - Changelog entry added (if release)

## Automation Instructions

When working on any task, follow this workflow:

### For New Features:
1. Create feature branch: `git checkout -b feature/description develop`
2. Make code changes with tests
3. Verify all syntax: Run tests
4. Verify test coverage ≥ 90% for your code
5. Update documentation if needed
6. Verify no secrets/hardcoded values
7. Commit with clear message: `feat(scope): description`
8. Push to origin: `git push origin feature/description`
9. Create PR to develop branch
10. Wait for CI checks to pass
11. Merge via GitHub (auto-deletes branch)
12. Pull latest develop

### For Bug Fixes:
1. Create feature branch: `git checkout -b fix/description develop`
2. Make code changes with regression tests
3. Verify all 109 tests still pass
4. Update documentation if needed
5. Verify no new security issues
6. Commit with clear message: `fix(scope): description`
7. Push and create PR to develop
8. Merge after CI passes

### For Documentation Updates:
1. Update .md files on develop branch
2. Verify broken links fixed
3. Verify code examples still work
4. Commit: `docs: description of updates`
5. Push and create PR to develop

### For Releases (Version Bump):
1. Create release branch: `git checkout -b release/v1.X.X develop`
2. Update `src/__init__.py` with new version
3. Verify all version references updated
4. Run full test suite (all 109 passing)
5. Update CHANGELOG.md with release notes
6. Commit: `chore: bump version to 1.X.X`
7. Push and create PR to master
8. After merge, create git tag: `git tag -a v1.X.X`
9. Also merge back to develop: PR from master to develop

### For Security Patches (Hotfixes):
1. Create hotfix branch: `git checkout -b hotfix/description master`
2. Make minimal security fix
3. Add tests verifying the fix
4. Run full test suite
5. Commit: `sec: description of security fix`
6. Push and create PR to master
7. After merge to master, create git tag
8. Merge to develop as well

### Ongoing Maintenance:
1. **Weekly**: Check for dependency updates
2. **Monthly**: Scan for security vulnerabilities
3. **Monthly**: Review test coverage report
4. **Quarterly**: Review and update documentation
5. **Per commit**: Follow all checks in Pre-Commit Checklist

These steps ensure code quality, security, documentation accuracy, and deployment readiness automatically without requiring manual prompts.

---

## Pre-Push Validation Commands

Run these before pushing any changes:

```bash
# Full validation script
echo "Running syntax check..."
python -m py_compile src/**/*.py

echo "Running tests..."
python -m pytest tests/ -v

echo "Checking coverage..."
python -m pytest --cov=src tests/

echo "Checking for vulnerabilities..."
pip check

echo "Checking for secrets..."
git diff HEAD --name-only | xargs grep -E "password|api_key|token|secret" || echo "✓ No secrets found"

echo "Verifying commit..."
git log -1 --stat
```

---

## Current Project State (as of Feb 19, 2026)

**Production Version**: 1.5.1 (dynamically managed from `src/__init__.py`)

**Test Suite**: 109 tests passing
- 96 original tests
- 13 security tests
- Target coverage: ≥ 90%

**Key Files with Version Info**:
- `src/__init__.py` - ✅ Single source of truth
- `src/config/settings.py` - ✅ Dynamically imports
- `src/main.py` - ✅ Uses settings.app_version
- `package.json` - ✅ Updated
- `README.md` - ✅ Updated and well-organized
- `BACKWARDS_COMPATIBILITY.md` - ✅ Updated
- `BLUE_GREEN_DEPLOYMENT.md` - ✅ Updated
- `DEPLOYMENT_IMPLEMENTATION.md` - ✅ Updated
- Test files - ✅ Updated assertions

**Deployment Pipeline**:
- GitHub Actions CI/CD: Fully functional
- Blue-green deployment: Configured and tested
- Azure App Service: Production environment
- Branch protection: Enabled on master and develop
- Git Flow: Implemented with feature/release/hotfix branches

**Security Status**:
- ✅ No hardcoded secrets
- ✅ All credentials in Azure Key Vault
- ✅ CORS properly configured
- ✅ Rate limiting enabled
- ✅ Input/output validation on all endpoints
- ✅ OWASP Top 10 controls implemented
- ✅ Security tests: 13 passing
- ✅ Dependency vulnerabilities tracked
