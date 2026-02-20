# Automatic Housekeeping Checklist

This document outlines the automatic housekeeping tasks that should be performed after development work is completed. These checks ensure code quality, security, documentation accuracy, and a consistent user experience without requiring manual prompts.

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
