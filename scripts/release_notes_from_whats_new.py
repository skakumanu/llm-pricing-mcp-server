#!/usr/bin/env python3
"""Print one release's What's New entry as Markdown, for GitHub Release notes.

static/whats-new/index.html is the product's release record (append-only, one
``<!-- vX.Y.Z -->`` marker per entry). The deploy job calls this after the
post-deploy health check confirms the version, and feeds the output to
``gh release create`` so the GitHub Releases page stops drifting from reality
(it sat at v1.11.1 while the product reached v1.82.x).

Usage:
    python scripts/release_notes_from_whats_new.py 1.82.2            # Markdown body
    python scripts/release_notes_from_whats_new.py v1.82.2 --title   # title line only

Exit codes: 0 ok, 1 usage, 2 version has no What's New entry.
"""
import argparse
import html
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = PROJECT_ROOT / "static" / "whats-new" / "index.html"
SITE = "https://llm-pricing-api.fly.dev"

_MARKER_RE = re.compile(r"<!--\s*v(\d+\.\d+\.\d+)\s*-->")


def extract_entry(text: str, version: str) -> str:
    """Return the raw HTML between this version's marker and the next marker."""
    start = None
    for m in _MARKER_RE.finditer(text):
        if start is not None:
            return text[start:m.start()]
        if m.group(1) == version:
            start = m.end()
    if start is None:
        raise LookupError(version)
    return text[start:]


def _inline(fragment: str) -> str:
    """HTML inline markup -> Markdown (code, strong, links); strip the rest."""
    s = fragment
    s = re.sub(r"<code>(.*?)</code>", r"`\1`", s, flags=re.S)
    s = re.sub(r"<strong>(.*?)</strong>", r"**\1**", s, flags=re.S)
    s = re.sub(r"<em>(.*?)</em>", r"*\1*", s, flags=re.S)

    def link(m):
        href, label = m.group(1), m.group(2)
        if href.startswith("/"):
            href = SITE + href
        return f"[{label}]({href})"

    s = re.sub(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', link, s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _first(pattern: str, block: str) -> str:
    m = re.search(pattern, block, flags=re.S)
    return _inline(m.group(1)) if m else ""


def entry_title(block: str) -> str:
    return _first(r'class="release-title">(.*?)</div>', block)


def entry_to_markdown(block: str, version: str) -> str:
    title = entry_title(block)
    date = _first(r'class="release-date">(.*?)</span>', block)
    tag = _first(r'class="release-tag[^"]*">(.*?)</span>', block)

    lines = [f"## {title}" if title else f"## v{version}", ""]
    meta = " · ".join(x for x in (tag, date) if x)
    if meta:
        lines += [f"_{meta}_", ""]

    # Walk section labels and change lists in document order.
    for kind, body in _sections(block):
        if kind == "label":
            lines += [f"### {body}", ""]
        else:
            lines += [f"- {item}" for item in body] + [""]

    # Optional stats strip: <div class="stat-chip">Label <strong>value</strong></div>
    stats = re.findall(r'class="stat-chip">(.*?)</div>', block, flags=re.S)
    if stats:
        lines += ["### By the numbers", ""]
        lines += [f"- {_inline(chip)}" for chip in stats] + [""]

    lines += [f"Full changelog: {SITE}/whats-new"]
    return "\n".join(lines).rstrip() + "\n"


def _sections(block: str):
    """Yield ('label', text) and ('items', [text, ...]) in document order."""
    token_re = re.compile(
        r'class="section-label">(?P<label>.*?)</div>|<ul class="change-list">(?P<list>.*?)</ul>',
        flags=re.S,
    )
    for m in token_re.finditer(block):
        if m.group("label") is not None:
            yield "label", _inline(m.group("label"))
        else:
            items = re.findall(r"<li>(.*?)</li>", m.group("list"), flags=re.S)
            yield "items", [_inline(i) for i in items if _inline(i)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version", help="e.g. 1.82.2 or v1.82.2")
    ap.add_argument("--file", type=Path, default=DEFAULT_FILE)
    ap.add_argument("--title", action="store_true", help="print only the entry title")
    args = ap.parse_args(argv)

    version = args.version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        print(f"not a semver version: {args.version}", file=sys.stderr)
        return 1
    text = args.file.read_text(encoding="utf-8")
    try:
        block = extract_entry(text, version)
    except LookupError:
        print(f"no What's New entry for v{version} in {args.file}", file=sys.stderr)
        return 2
    if args.title:
        print(entry_title(block) or f"v{version}")
    else:
        sys.stdout.write(entry_to_markdown(block, version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
