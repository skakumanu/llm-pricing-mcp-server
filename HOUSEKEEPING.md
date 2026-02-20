# Automatic Housekeeping Checklist

This document outlines the automatic housekeeping tasks that should be performed after development work is completed.

## Code Changes Housekeeping

### ✅ After Every Code Modification
- [ ] **Verify no syntax errors** - Check for Python syntax issues before committing
- [ ] **Run relevant tests** - Execute tests for modified modules/features
- [ ] **Check code consistency** - Verify formatting and style compliance
- [ ] **Update related tests** - Add/update assertions for new behavior
- [ ] **Verify imports** - Ensure all imports are correct and used

### ✅ After Functional Changes
- [ ] **Run full test suite** - Execute all tests to catch regressions (109 tests)
- [ ] **Update docstrings** - Ensure documentation matches implementation
- [ ] **Update type hints** - Verify all function signatures are properly typed
- [ ] **Check backwards compatibility** - Verify changes don't break existing APIs

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

### ✅ After Dependency Changes
- [ ] **Update requirements.txt** - Reflect new/changed packages
- [ ] **Update pyproject.toml** - Keep in sync with dependencies
- [ ] **Verify imports work** - Test that packages install and import correctly
- [ ] **Check for security vulnerabilities** - Run vulnerability scanner

## Commit & Push Housekeeping

### ✅ Before Committing
1. **Stage changes** - Use `git add -A` to stage all changes
2. **Review diff** - Use `git diff --cached` to verify changes
3. **Write clear message** - Follow commit message conventions:
   - **Type**: fix:, feat:, docs:, test:, refactor:, chore:, ci:
   - **Scope**: (optional) module or feature name
   - **Subject**: Concise description (present tense)
   - **Body**: Detailed explanation if needed
   - **Example**: `docs: update all version references from 1.5.0 to 1.5.1`

### ✅ After Committing
1. **Verify commit** - Use `git log -1` to confirm commit message and files
2. **Push to repository** - Use `git push origin [branch]`
3. **Check GitHub Actions** - Verify CI/CD pipeline passes
   - CI /test must pass
   - Coverage should be maintained
   - No security warnings introduced

### ✅ For Branch Protection Compliance
- **master branch**: 
  - Cannot push directly
  - Requires PR with CI /test passing
  - All 109 tests must pass
  - Bypassed rule warnings must be resolved

## Quality Gates

### ✅ Testing Standards
- **Test Coverage**: Maintain or improve existing coverage
- **Test Count**: Currently 109 tests passing
- **New Features**: Add corresponding test cases
- **Regressions**: Verify no tests break after changes

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
- **Vulnerability Scanner**: Check for dependency vulnerabilities
- **Secrets**: Never commit API keys or tokens
- **Permissions**: Verify code doesn't introduce permission issues
- **Dependencies**: Keep packages up-to-date

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

## Automation Instructions

When working on any task:

1. **Make code changes** as needed
2. **Run tests** to verify functionality
3. **Update documentation** if behavior changes
4. **Update version references** if not using dynamic versioning
5. **Commit changes** with clear message
6. **Push to repository** and verify CI/CD passes
7. **Verify no regressions** in test suite

These steps ensure code quality, documentation accuracy, and deployment readiness without requiring manual prompts.

---

**Last Updated**: February 19, 2026  
**Version**: 1.5.1  
**Test Status**: ✅ 109/109 passing
