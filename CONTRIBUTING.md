# Contributing Guide

## Branching Strategy
We **STRICTLY** follow Git Flow for all development processes. This ensures a clean, predictable workflow and prevents direct commits to protected branches.

### Branch Overview

1. **Master Branch**: The `master` branch always reflects production-ready code. Only merge into master from `develop` or `hotfix` branches.
2. **Develop Branch**: The `develop` branch is the integration branch for ongoing development. All features merge here first.
3. **Feature Branches**: Feature branches should be created off `develop` and follow the naming convention `feature/<feature-name>`.
4. **Release Branches**: When preparing for a new production release, create a branch off `develop` and name it `release/<version>`.
5. **Hotfix Branches**: For critical fixes in production, create a branch off `master` and name it `hotfix/<fix-name>`.

### Git Flow Workflow

#### Creating and Merging Feature Branches

1. **Start a new feature** (ALWAYS branch from `develop`):
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/<feature-name>
   ```

2. **Work on your feature**:
   ```bash
   # Make your changes
   git add .
   git commit -m "Descriptive commit message"
   git push origin feature/<feature-name>
   ```

3. **Merge feature into develop** (NOT master):
   ```bash
   git checkout develop
   git pull origin develop
   git merge --no-ff feature/<feature-name>
   git push origin develop
   ```

4. **Delete feature branch** after merging:
   ```bash
   git branch -d feature/<feature-name>
   git push origin --delete feature/<feature-name>
   ```

#### Creating a Release

1. **Start a release branch** (from `develop`):
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b release/<version>
   ```

2. **Prepare the release** (update version numbers, documentation):
   ```bash
   # Make final adjustments
   git add .
   git commit -m "Prepare release <version>"
   git push origin release/<version>
   ```

3. **Merge release to master**:
   ```bash
   git checkout master
   git pull origin master
   git merge --no-ff release/<version>
   git tag -a v<version> -m "Release version <version>"
   git push origin master --tags
   ```

4. **Merge release back to develop**:
   ```bash
   git checkout develop
   git pull origin develop
   git merge --no-ff release/<version>
   git push origin develop
   ```

5. **Delete release branch**:
   ```bash
   git branch -d release/<version>
   git push origin --delete release/<version>
   ```

#### Hotfix Workflow

1. **Create hotfix branch** (from `master`):
   ```bash
   git checkout master
   git pull origin master
   git checkout -b hotfix/<fix-name>
   ```

2. **Apply the fix**:
   ```bash
   # Make your changes
   git add .
   git commit -m "Fix: description of hotfix"
   git push origin hotfix/<fix-name>
   ```

3. **Merge hotfix to master**:
   ```bash
   git checkout master
   git pull origin master
   git merge --no-ff hotfix/<fix-name>
   git tag -a v<version> -m "Hotfix version <version>"
   git push origin master --tags
   ```

4. **Merge hotfix to develop**:
   ```bash
   git checkout develop
   git pull origin develop
   git merge --no-ff hotfix/<fix-name>
   git push origin develop
   ```

5. **Delete hotfix branch**:
   ```bash
   git branch -d hotfix/<fix-name>
   git push origin --delete hotfix/<fix-name>
   ```

### Important Rules

- ⚠️ **NEVER merge feature branches directly into `master`**
- ⚠️ **NEVER commit directly to `master` or `develop`**
- ⚠️ **NEVER commit secrets, API keys, or sensitive information**
- ✅ **ALWAYS merge features into `develop` first**
- ✅ **ALWAYS use `--no-ff` flag for merges** to preserve branch history
- ✅ **ALWAYS sync `develop` after merging to `master`**
- ✅ **ALWAYS check for secrets before committing** (see Security Checklist below)
- ✅ **Use pull requests** for code review before merging
- ✅ **Include detailed commit messages** describing what and why

### Pre-Commit Security Checklist

Before committing ANY code, verify:

```bash
# 1. Check what files you're about to commit
git status
git diff --cached

# 2. Search for common secret patterns (run these checks!)
grep -r "password" --include="*.py" --include="*.json" --include="*.txt" .
grep -r "api_key" --include="*.py" --include="*.json" --include="*.txt" .
grep -r "secret" --include="*.py" --include="*.json" --include="*.txt" .
grep -r "token" --include="*.py" --include="*.json" --include="*.txt" .

# 3. Ensure .env files are NOT staged
git ls-files | grep -E "\.(env|secret|key|pem|pfx)$"
```

**If you find any secrets:**
1. Remove them immediately
2. Use environment variables instead
3. Add the pattern to `.gitignore`
4. Never commit the secrets

Ensure to follow proper branching practices, including pull requests for merging, and always include detailed commit messages.

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

2. **Store Secrets in Azure Key Vault:**
   - All production secrets are stored in Azure Key Vault
   - Access secrets at runtime, never hard-code them

3. **Use `.env` for Local Development:**
   - Create a `.env` file (already in `.gitignore`)
   - Provide a `.env.example` template with dummy values
   - Document required environment variables in README

4. **Before Every Commit:**
   - Run the pre-commit security checklist (see above)
   - Review `git diff` carefully
   - Check for accidentally staged secret files

### Secret Detection Tools

Consider using these tools to scan for secrets:

```bash
# Install git-secrets (prevents committing secrets)
git secrets --install
git secrets --register-aws

# Scan repository for secrets
git secrets --scan

# Or use gitleaks
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

1. All contributions must undergo static code analysis using the provided CI/CD pipeline
2. Use secure coding practices, avoiding hard-coded credentials
3. Always use environment variables for configuration
4. Follow the principle of least privilege for accessing sensitive resources
5. Keep dependencies updated to patch security vulnerabilities
6. Review the security section in pull request reviews

---

## Privacy Compliance
1. Do not include any Personal Identifiable Information (PII) in logs or outputs.
2. Ensure third-party dependencies comply with GDPR and similar privacy regulations.
3. Periodically review the `privacy_policy.md` file to ensure compliance.

Thank you for contributing to the llm-pricing-mcp-server project!