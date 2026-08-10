#!/usr/bin/env bash
# Make a new branch's tree exactly match a source ref's tree, built on top of a
# target ref's history.
#
# Why this exists
# ----------------
# This repo's develop/master use squash merges, so their histories diverge even
# when their content is identical — a direct develop-to-master PR sees the full
# diverged history and either explodes into a huge diff or shows a real
# conflict. The workaround used throughout this project's history has been to
# cut a fresh branch from the target (usually master) and overlay the source's
# (usually develop's) files on top, so the resulting PR is a clean diff against
# the target with no ancestry noise.
#
# The overlay step is the part that has bitten this repo twice now
# (v1.55.4's promotion PR #172): `git checkout <source> -- .` only ever ADDS or
# UPDATES paths. A file the source branch deleted — like mcp/server_azure.py in
# v1.55.4 — silently survives on the new branch, because checkout never stages
# a deletion for a path it doesn't find in the source tree. The result looks
# like a clean promotion but quietly resurrects deleted code.
#
# `git read-tree -u --reset <source>` replaces the index AND working tree with
# the source tree exactly — additions, modifications, and deletions all — which
# is what "make this branch look exactly like develop" actually requires.
#
# Usage
# -----
#   scripts/promote_branch_content.sh <new-branch> <target-ref> <source-ref>
#
# Example — promote develop's content onto a fresh branch off master:
#   scripts/promote_branch_content.sh feature/promote-v1.56.0 origin/master origin/develop
#
# The script verifies the result before returning: it diffs the new branch
# against the source ref and refuses to proceed if they differ by even one
# byte. Nothing is pushed or committed here — inspect and commit yourself.

set -euo pipefail

if [ "$#" -ne 3 ]; then
  echo "Usage: $0 <new-branch> <target-ref> <source-ref>" >&2
  echo "Example: $0 feature/promote-v1.56.0 origin/master origin/develop" >&2
  exit 2
fi

NEW_BRANCH="$1"
TARGET_REF="$2"
SOURCE_REF="$3"

if ! git rev-parse --verify --quiet "$TARGET_REF" > /dev/null; then
  echo "ERROR: target ref '$TARGET_REF' not found. Did you fetch it?" >&2
  exit 1
fi
if ! git rev-parse --verify --quiet "$SOURCE_REF" > /dev/null; then
  echo "ERROR: source ref '$SOURCE_REF' not found. Did you fetch it?" >&2
  exit 1
fi

if ! git diff --quiet --cached || ! git diff --quiet; then
  echo "ERROR: working tree has uncommitted changes. Commit, stash, or discard them first." >&2
  exit 1
fi

echo "Creating '$NEW_BRANCH' from '$TARGET_REF'..."
git checkout -B "$NEW_BRANCH" "$TARGET_REF"

echo "Replacing the tree with '$SOURCE_REF' (additions, updates, AND deletions)..."
git read-tree -u --reset "$SOURCE_REF"

echo "Verifying the branch now matches '$SOURCE_REF' exactly..."
if ! git diff --quiet "$SOURCE_REF" --; then
  echo "ERROR: '$NEW_BRANCH' does not match '$SOURCE_REF' after read-tree. Not safe to commit." >&2
  git diff --stat "$SOURCE_REF" --
  exit 1
fi

echo "OK: '$NEW_BRANCH' is an exact content match for '$SOURCE_REF', built on '$TARGET_REF' history."
echo "Nothing has been committed. Review 'git status' / 'git diff --staged', then commit and push."
