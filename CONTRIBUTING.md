# Contributing Guide

## Branching Strategy
We strictly follow Git Flow for all development processes. Here is the breakdown of the branching model:

1. **Master Branch**: The `master` branch always reflects production-ready code.
2. **Develop Branch**: The `develop` branch is used for integrating features. It reflects the latest delivered development changes.
3. **Feature Branches**: Feature branches should be created off `develop` and follow the naming convention `feature/<feature-name>`.
4. **Release Branches**: When preparing for a new production release, create a branch off `develop` and name it `release/<version>`.
5. **Hotfix Branches**: For critical fixes in production, create a branch off `master` and name it `hotfix/<fix-name>`.

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