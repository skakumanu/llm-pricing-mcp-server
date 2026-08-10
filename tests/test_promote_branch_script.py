"""Tests for scripts/promote_branch_content.sh against a throwaway git repo.

This script exists because the v1.55.4 promotion (PR #172) silently resurrected
a deleted file: the manual recipe used `git checkout <source> -- .`, which only
ever adds or updates paths — it never stages a deletion for a file the source
branch removed. The bug was only caught by an extra manual `comm -23` check
that isn't guaranteed to happen every time.

These tests build a real, isolated git repo per test (never touching this
repo's actual branches) and prove the script correctly mirrors additions,
modifications, AND deletions, and refuses to proceed when it can't guarantee
an exact match.
"""
import subprocess
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
SCRIPT = project_root / "scripts" / "promote_branch_content.sh"


def run(*args, cwd):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True
    )


def git(*args, cwd, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check
    )


@pytest.fixture
def sandbox(tmp_path):
    """An isolated repo with a 'master'-like and 'develop'-like branch.

    master (target): keep.txt, only_on_master.txt
    develop (source): keep.txt (different content), only_on_develop.txt
    -- only_on_master.txt exists on master but not on develop: the exact shape
       of the bug (mcp/server_azure.py existed on master, deleted on develop).
    """
    repo = tmp_path / "sandbox"
    repo.mkdir()
    git("init", "-q", "-b", "master", cwd=repo)
    git("config", "user.email", "test@example.com", cwd=repo)
    git("config", "user.name", "Test", cwd=repo)

    (repo / "keep.txt").write_text("master version\n")
    (repo / "only_on_master.txt").write_text("should be deleted by promotion\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "master: initial", cwd=repo)

    git("checkout", "-q", "-b", "develop", cwd=repo)
    (repo / "keep.txt").write_text("develop version\n")
    (repo / "only_on_master.txt").unlink()
    (repo / "only_on_develop.txt").write_text("added on develop\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "develop: delete one file, modify one, add one", cwd=repo)

    git("checkout", "-q", "master", cwd=repo)
    return repo


class TestHandlesDeletions:
    """The exact bug this script was written to fix."""

    def test_file_deleted_on_source_is_removed(self, sandbox):
        result = run("promoted", "master", "develop", cwd=sandbox)
        assert result.returncode == 0, result.stderr
        assert not (sandbox / "only_on_master.txt").exists()

    def test_new_branch_matches_source_tree_exactly(self, sandbox):
        run("promoted", "master", "develop", cwd=sandbox)
        diff = git("diff", "develop", "--", cwd=sandbox)
        assert diff.stdout == ""


class TestHandlesAdditionsAndModifications:
    def test_file_only_on_source_is_added(self, sandbox):
        run("promoted", "master", "develop", cwd=sandbox)
        assert (sandbox / "only_on_develop.txt").read_text() == "added on develop\n"

    def test_shared_file_takes_source_content(self, sandbox):
        run("promoted", "master", "develop", cwd=sandbox)
        assert (sandbox / "keep.txt").read_text() == "develop version\n"


class TestBranchAndHistory:
    def test_creates_new_branch_from_target(self, sandbox):
        run("promoted", "master", "develop", cwd=sandbox)
        branch = git("branch", "--show-current", cwd=sandbox).stdout.strip()
        assert branch == "promoted"

    def test_new_branch_history_descends_from_target_not_source(self, sandbox):
        run("promoted", "master", "develop", cwd=sandbox)
        # The new branch's parent commit should be master's tip, proving this
        # promotes CONTENT without inheriting develop's diverged history.
        parent = git("rev-parse", "HEAD", cwd=sandbox).stdout.strip()
        master_tip = git("rev-parse", "master", cwd=sandbox).stdout.strip()
        assert parent == master_tip

    def test_nothing_is_committed(self, sandbox):
        """The script stages the result; committing is left to the caller."""
        run("promoted", "master", "develop", cwd=sandbox)
        status = git("status", "--porcelain", cwd=sandbox).stdout
        assert status.strip() != "", "expected staged changes, found a clean tree"


class TestRefusesUnsafeConditions:
    def test_rejects_missing_target_ref(self, sandbox):
        result = run("promoted", "does-not-exist", "develop", cwd=sandbox)
        assert result.returncode != 0
        assert "not found" in result.stderr

    def test_rejects_missing_source_ref(self, sandbox):
        result = run("promoted", "master", "does-not-exist", cwd=sandbox)
        assert result.returncode != 0
        assert "not found" in result.stderr

    def test_rejects_dirty_working_tree(self, sandbox):
        (sandbox / "keep.txt").write_text("uncommitted local edit\n")
        result = run("promoted", "master", "develop", cwd=sandbox)
        assert result.returncode != 0
        assert "uncommitted" in result.stderr

    def test_wrong_argument_count_shows_usage(self, sandbox):
        result = run("only-one-arg", cwd=sandbox)
        assert result.returncode != 0
        assert "Usage" in result.stderr


class TestIdenticalContentIsANoOp:
    def test_promoting_identical_trees_produces_no_diff(self, sandbox):
        """When target and source already match, the result should too."""
        git("checkout", "-q", "-b", "develop-copy", "develop", cwd=sandbox)
        result = run("promoted", "develop", "develop-copy", cwd=sandbox)
        assert result.returncode == 0, result.stderr
        diff = git("diff", "develop-copy", "--", cwd=sandbox)
        assert diff.stdout == ""
