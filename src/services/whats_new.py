"""Parse the What's New release record (static/whats-new/index.html).

The parser moved here from scripts/release_notes_from_whats_new.py in v1.89.0 (the document writer runs
in the production image, which has no scripts/); the script is now a CLI shim that re-exports these
names, so tests/test_release_notes_script.py and the deploy job keep working unchanged.
"""
import html
import re
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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


def list_versions(text: str) -> List[str]:
    """Every version marker in document order (newest first in the file)."""
    return [m.group(1) for m in _MARKER_RE.finditer(text)]


def version_key(version: str):
    return tuple(int(p) for p in version.split("."))


def entry_date(block: str) -> str:
    return _first(r'class="release-date">(.*?)</span>', block)


def entry_tag(block: str) -> str:
    return _first(r'class="release-tag[^"]*">(.*?)</span>', block)


def entry_sections(block: str):
    """[(label, [items...])] in document order; items before any label get label ''."""
    out = []
    label = ""
    for kind, body in _sections(block):
        if kind == "label":
            label = body
        else:
            out.append((label, body))
    return out


def entry_stats(block: str) -> List[str]:
    return [_inline(c) for c in re.findall(r'class="stat-chip">(.*?)</div>', block, flags=re.S)]


def read_file(path: Optional[Path] = None) -> str:
    return (path or DEFAULT_FILE).read_text(encoding="utf-8")
