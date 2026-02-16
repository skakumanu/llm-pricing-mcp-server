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
- ✅ **ALWAYS merge features into `develop` first**
- ✅ **ALWAYS use `--no-ff` flag for merges** to preserve branch history
- ✅ **ALWAYS sync `develop` after merging to `master`**
- ✅ **Use pull requests** for code review before merging
- ✅ **Include detailed commit messages** describing what and why

Ensure to follow proper branching practices, including pull requests for merging, and always include detailed commit messages.

---

## Security Compliance
1. All secrets are stored in Azure Key Vault. Ensure that no secrets are hard-coded or included in the repository.
2. All contributions must undergo static code analysis using the provided CI/CD pipeline.
3. Use secure coding practices, avoiding hard-coded credentials, and ensure proper use of environment variables.
4. Follow the principle of least privilege for accessing sensitive resources.

---

## Privacy Compliance
1. Do not include any Personal Identifiable Information (PII) in logs or outputs.
2. Ensure third-party dependencies comply with GDPR and similar privacy regulations.
3. Periodically review the `privacy_policy.md` file to ensure compliance.

Thank you for contributing to the llm-pricing-mcp-server project!