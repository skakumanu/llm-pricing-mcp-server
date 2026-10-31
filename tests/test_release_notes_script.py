"""Tests for scripts/release_notes_from_whats_new.py.

The deploy job turns a version's What's New entry into GitHub Release notes.
Before this the Releases page sat at v1.11.1 while the product reached v1.82.x,
because nothing in the pipeline tagged or published a Release.
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
SCRIPT = project_root / "scripts" / "release_notes_from_whats_new.py"
WHATS_NEW = project_root / "static" / "whats-new" / "index.html"
# Real HTML tags only; literal text such as `X-Provider-Key-<Provider>` or "<= 32 bytes" is fine.
TAG_RE = r"</?(div|span|ul|ol|li|a|code|strong|em|b|i|br|p|h[1-6])\b[^>]*>"

spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
rn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rn)

SAMPLE = """<div class="container">
  <!-- v2.0.1 -->
  <div class="release latest">
    <div class="release-header">
      <span class="version-badge">v2.0.1</span>
      <span class="release-date">Nov 1, 2026</span>
      <span class="release-tag tag-fix">Fix</span>
    </div>
    <div class="release-body">
      <div class="release-title">Faster <code>compare</code> &amp; safer alerts</div>
      <div class="section-label">Fixed</div>
      <ul class="change-list">
        <li><strong>Compare is faster.</strong> See <a href="/compare">the page</a></li>
        <li>Alerts use <code>https</code> only &mdash; <em>always</em></li>
      </ul>
      <div class="stats-strip">
        <div class="stat-chip">Providers <strong>31 → 30</strong></div>
      </div>
    </div>
  </div>

  <!-- v2.0.0 -->
  <div class="release">
    <div class="release-title">Big one</div>
    <ul class="change-list"><li>Everything</li></ul>
  </div>
</div>
"""


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


class TestExtraction:
    def test_entry_is_bounded_by_markers(self):
        block = rn.extract_entry(SAMPLE, "2.0.1")
        assert "Faster" in block and "Big one" not in block

    def test_last_entry_runs_to_end(self):
        assert "Everything" in rn.extract_entry(SAMPLE, "2.0.0")

    def test_unknown_version_raises(self):
        with pytest.raises(LookupError):
            rn.extract_entry(SAMPLE, "0.0.1")


class TestMarkdown:
    def test_title_tag_date_sections_and_items(self):
        md = rn.entry_to_markdown(rn.extract_entry(SAMPLE, "2.0.1"), "2.0.1")
        assert md.startswith("## Faster `compare` & safer alerts\n")
        assert "_Fix · Nov 1, 2026_" in md
        assert "### Fixed" in md
        assert "- **Compare is faster.** See [the page](https://llm-pricing-api.fly.dev/compare)" in md
        assert "- Alerts use `https` only — *always*" in md
        assert "### By the numbers" in md and "- Providers **31 → 30**" in md
        assert md.rstrip().endswith("Full changelog: https://llm-pricing-api.fly.dev/whats-new")

    def test_no_html_survives(self):
        md = rn.entry_to_markdown(rn.extract_entry(SAMPLE, "2.0.1"), "2.0.1")
        assert not re.search(TAG_RE, md) and "&amp;" not in md

    def test_title_helper(self):
        assert rn.entry_title(rn.extract_entry(SAMPLE, "2.0.1")) == "Faster `compare` & safer alerts"


class TestCli:
    def test_current_version_has_notes(self):
        from src import __version__
        proc = run(__version__)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.startswith("## ")
        assert "Full changelog:" in proc.stdout

    def test_title_flag_and_v_prefix(self):
        from src import __version__
        proc = run(f"v{__version__}", "--title")
        assert proc.returncode == 0
        assert proc.stdout.strip() and "\n" not in proc.stdout.strip()

    def test_missing_version_exits_2(self):
        proc = run("0.0.1")
        assert proc.returncode == 2
        assert "no What's New entry" in proc.stderr

    def test_bad_version_exits_1(self):
        assert run("latest").returncode == 1

    def test_every_marked_entry_renders(self):
        """Every entry on the page must produce notes, so a future release never fails the deploy step."""
        versions = re.findall(r"<!--\s*v(\d+\.\d+\.\d+)\s*-->", WHATS_NEW.read_text(encoding="utf-8"))
        assert versions
        text = WHATS_NEW.read_text(encoding="utf-8")
        for v in versions:
            md = rn.entry_to_markdown(rn.extract_entry(text, v), v)
            assert md.startswith("## "), v
            assert not re.search(TAG_RE, md), v
