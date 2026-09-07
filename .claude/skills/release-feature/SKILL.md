---
name: release-feature
description: Runs a feature request through this repo's AI-native SDLC pipeline — Plan, Code, Review (parallel correctness/security/simplification), Test & Release — ending with an open PR against develop. Use when the user asks to build/ship/release a feature end-to-end, or explicitly invokes /release-feature.
---

# /release-feature

Runs one feature request through this repo's phase-agent pipeline end to end: **Plan → Code → Review → Test & Release**, ending with a pushed branch and an open PR against `develop`. This does not merge the PR — per `CLAUDE.md` rule 5, that decision is left to the user.

Feature request: $ARGUMENTS

If no feature request was given above, ask the user for one before proceeding.

## Steps

### 0. Preflight

- Run `git branch --show-current` and `git status --short`.
- If there are uncommitted changes, stop and tell the user — do not stash or discard their work automatically.
- If currently on `develop` or `master`, that's the expected starting point. If on some other branch, ask the user whether to switch to `develop` first.

### 1. Plan phase

Run the Plan phase as a single subagent (`Agent` tool, `subagent_type: "phase-plan"`, foreground — you need its result before continuing). Give it the feature request verbatim and instruct it to return **only** a JSON object with this exact shape, no prose around it:

```json
{
  "scope": "string",
  "affectedAreas": ["string"],
  "versionBumpType": "patch|minor|major",
  "targetVersion": "string",
  "touchesArchitecture": true,
  "touchesPricing": false,
  "userFacing": true,
  "branchName": "feature/<slug>-v<targetVersion>",
  "openQuestions": ["string"]
}
```

Parse that JSON. If `openQuestions` is non-empty, surface them to the user and ask before proceeding — do not guess on genuine ambiguity.

### 2. Create the branch

Per `CLAUDE.md`'s git-flow (mandatory, not optional):

```bash
git checkout develop
git pull origin develop
git checkout -b <plan.branchName>
```

### 3. Run the Code -> Review -> Test pipeline

Call the `Workflow` tool with `name: "release-feature"` (the script lives at `.claude/workflows/release-feature.js` in this repo) and `args`:

```json
{
  "plan": "<the parsed plan object>",
  "branch": "<plan.branchName>",
  "attribution": {
    "commitTrailer": "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nClaude-Session: <this session's URL if you have one, else omit the line>",
    "prFooter": "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\n<this session's URL if you have one, else omit>"
  }
}
```

This is an explicit, deliberate multi-agent orchestration step — calling `Workflow` here is expected and intended by this skill, not something to second-guess.

### 4. Report back

Once the workflow returns, summarize for the user in a few lines:

- The plan's scope and target version.
- What the Code phase implemented (its `summary`).
- Review findings: how many, and which the Test phase fixed vs. judged as false positives (with reasons).
- Whether the full test suite passed (and the count).
- The PR URL — and remind the user that merging it is their call, not something this pipeline does automatically.
- Any `blocker` the Test phase reported, verbatim, if present — do not paper over a reported blocker with a cheerful summary.

If the workflow result has `release: null` or a `blocker`, treat this as an incomplete run: explain what phase failed and what the user needs to do (e.g. re-run, or fix something by hand), rather than reporting success.
