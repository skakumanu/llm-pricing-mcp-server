"""Legal-pages live gate (v1.86.0).

The legal pages (/terms, /privacy, /refunds, /dpa, /subprocessors) are generated from the
Markdown templates in docs/legal/. They are templates until a lawyer has reviewed them and
the owner has replaced every placeholder. This module is the one place that decides whether
they are *in force*:

    live  =  settings.legal_pages_live  AND  no [PLACEHOLDER] remains in docs/legal/*.md

It fails closed: the operator flag alone is never enough. When the flag is on but
placeholders remain, the pages keep their draft notice, paid checkout stays disabled
(src/services/tiers.py) and a WARNING is logged. tests/test_legal_pages.py also fails the
build if the flag is on by default (settings, fly.toml, .env.example) while a placeholder
remains.

Pure standard library; the caller passes the settings object.
"""
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# The only placeholders the sources may use; the owner replaces them before going live.
LEGAL_PLACEHOLDERS = ("[ENTITY NAME]", "[STATE]", "[CONTACT EMAIL]", "[POSTAL ADDRESS]")
LEGAL_DOCS_DIR = Path(__file__).resolve().parents[2] / "docs" / "legal"

# Shown (server-side, no JavaScript) in place of the page's notice slot while not live.
NOTICE_MARKER = "<!-- legal:notice -->"
DRAFT_NOTICE_HTML = (
    '<div class="legal-draft" role="note"><strong>Draft, not yet in force.</strong> '
    "This is a template awaiting legal review; paid plans cannot be bought until it is in force.</div>"
)

_cache: Optional[Dict[str, List[str]]] = None


def scan_placeholders(docs_dir: Optional[Path] = None) -> Dict[str, List[str]]:
    """Map each legal source file that still contains placeholders to the ones it has."""
    base = Path(docs_dir) if docs_dir is not None else LEGAL_DOCS_DIR
    found: Dict[str, List[str]] = {}
    sources = sorted(base.glob("*.md"))
    if not sources:
        # Fail closed: no sources at all (e.g. docs/ not in the image) is never "clean".
        return {str(base): ["<no legal sources found>"]}
    for path in sources:
        text = path.read_text(encoding="utf-8")
        present = [p for p in LEGAL_PLACEHOLDERS if p in text]
        # Any other [UPPER CASE WORDS] token is also a placeholder the owner must resolve.
        for token in re.findall(r"\[[A-Z][A-Z ]{2,}\](?!\()", text):
            if token not in present:
                present.append(token)
        if present:
            found[path.name] = present
    return found


def refresh(settings: Any = None) -> Dict[str, List[str]]:
    """Rescan the sources, cache the result and warn if the flag is on but placeholders remain."""
    global _cache
    try:
        _cache = scan_placeholders()
    except Exception as exc:  # fail closed: unreadable sources are never "clean"
        logger.error("legal sources unreadable (%s): pages stay draft, checkout disabled", exc)
        _cache = {str(LEGAL_DOCS_DIR): ["<legal sources unreadable>"]}
        return _cache
    if _cache and settings is not None and getattr(settings, "legal_pages_live", False):
        logger.warning(
            "LEGAL_PAGES_LIVE is on but placeholders remain in %s: pages stay draft and "
            "checkout stays disabled", ", ".join(sorted(_cache)))
    return _cache


def placeholders_remaining() -> Dict[str, List[str]]:
    if _cache is None:
        refresh()
    return _cache or {}


def is_live(settings: Any) -> bool:
    """True only when the operator flag is on AND no placeholder remains (fail closed)."""
    if not getattr(settings, "legal_pages_live", False):
        return False
    return not placeholders_remaining()
