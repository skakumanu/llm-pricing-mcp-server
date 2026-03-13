# Claude Code Instructions

## Git Flow — MANDATORY

This repo uses git flow. You MUST follow these rules for every coding session:

### Branch workflow
```
feature/<short-name>  →  develop (PR)  →  master (PR)  →  deploy
```

### Rules
1. **Never commit directly to `develop` or `master`.**
2. **Before writing any code**, create a feature branch:
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b feature/<short-descriptive-name>
   ```
3. Name branches: `feature/<topic>-v<version>` (e.g. `feature/api-tiers-v1.36.0`)
4. Commit on the feature branch, then push and open a PR targeting `develop`.
5. Do not merge PRs yourself — leave that to the user.

### At the start of every session
- Run `git branch` to confirm you are NOT on `develop` or `master`.
- If on `develop` with uncommitted work, immediately branch off before committing.
