#!/usr/bin/env python3
"""Render docs/legal/*.md into the legal pages under static/ (v1.86.0).

Same idea as scripts/sync_faq.py (it reuses its Markdown renderer): the Markdown in
docs/legal/ is the source, the HTML under static/<page>/index.html is generated, and
``--check`` fails when they differ. Each page carries a "Last updated" line taken from the
source's ``**Last updated:** YYYY-MM-DD`` line and a ``<!-- legal:notice -->`` slot that
src/main.py replaces with the draft notice while the pages are not in force
(src/services/legal_pages.py). The /security statement has the slot but is never a draft.

    python scripts/sync_legal.py                 # rewrite the six generated pages
    python scripts/sync_legal.py --check         # exit 1 if any page is out of step
    python scripts/sync_legal.py --placeholders  # exit 0 only if no [PLACEHOLDER] remains
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import sync_faq, sync_nav  # noqa: E402
from src.services import legal_pages  # noqa: E402

LEGAL_DIR = PROJECT_ROOT / "docs" / "legal"
STATIC = PROJECT_ROOT / "static"

# (source markdown, static folder, route, title, gated by the live flag)
PAGES = [
    ("TERMS.md", "terms", "/terms", "Terms of Service", True),
    ("PRIVACY.md", "privacy", "/privacy", "Privacy Policy", True),
    ("REFUNDS.md", "refunds", "/refunds", "Refund Policy", True),
    ("DPA.md", "dpa", "/dpa", "Data Processing Addendum", True),
    ("SUBPROCESSORS.md", "subprocessors", "/subprocessors", "Subprocessors", True),
    ("SECURITY.md", "security", "/security", "Security", False),
]

START = "<!-- legal:start (generated from docs/legal/ by scripts/sync_legal.py; do not edit by hand) -->"
END = "<!-- legal:end -->"
NOTICE_MARKER = legal_pages.NOTICE_MARKER

_UPDATED = re.compile(r"^\*\*Last updated:\*\*\s*(\d{4}-\d{2}-\d{2})\s*$", re.M)

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — LLM Pricing API</title>
  <style>
    :root {{
      --bg: #0f1117;
      --surface: #1a1d27;
      --surface2: #22263a;
      --border: #2e3347;
      --accent: #7c6af7;
      --text: #e2e8f0;
      --muted: #8892a4;
      --warn: #ed8936;
      --radius: 10px;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; line-height: 1.6; }}
{nav_css}
    /* ── Legal page ── */
    .container {{ max-width: 860px; margin: 0 auto; padding: 40px 24px 80px; }}
    .legal h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
    .legal .updated {{ color: var(--muted); font-size: 13px; margin-bottom: 18px; }}
    .legal h2 {{ font-size: 18px; font-weight: 600; margin: 32px 0 10px; padding-top: 8px; border-top: 1px solid var(--border); }}
    .legal h3 {{ font-size: 15px; font-weight: 600; margin: 20px 0 8px; }}
    .legal p {{ color: var(--muted); margin: 0 0 10px; }}
    .legal ul, .legal ol {{ margin: 0 0 12px 1.2rem; color: var(--text); }}
    .legal li {{ margin: 4px 0; overflow-wrap: anywhere; }}
    .legal a {{ color: var(--accent); text-decoration: none; }}
    .legal a:hover {{ text-decoration: underline; }}
    .legal code {{ background: var(--surface2); border: 1px solid var(--border); border-radius: 4px; padding: 0 .35em; font-size: .9em; }}
    .legal table {{ border-collapse: collapse; width: 100%; margin: 10px 0 14px; font-size: 13px; background: var(--surface); border: 1px solid var(--border); }}
    .legal th, .legal td {{ border-bottom: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; }}
    .legal th {{ background: var(--surface2); font-weight: 600; }}
    .legal-draft {{ border: 1px solid var(--warn); color: var(--warn); background: var(--surface); border-radius: var(--radius); padding: 12px 16px; margin-bottom: 18px; }}
    .legal-links {{ margin-top: 36px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; font-size: 13px; color: var(--muted); }}
    .legal-links a {{ color: var(--accent); text-decoration: none; margin-right: 12px; }}
  </style>
</head>
<body>
{nav}
{script}

<div class="container">
  <div class="legal">
{notice}
<p class="updated">Last updated {updated}</p>
{start}
{body}
{end}
  </div>
  <div class="legal-links">
    <span>Policies:</span>
{links}
    <a href="/faq">FAQ</a>
    <a href="/billing">Billing</a>
  </div>
</div>

</body>
</html>
"""


def split_source(md: str) -> tuple[str, str]:
    """Return (last-updated date, markdown body without the date line)."""
    m = _UPDATED.search(md)
    if not m:
        raise ValueError("source has no '**Last updated:** YYYY-MM-DD' line")
    return m.group(1), _UPDATED.sub("", md, count=1)


def render_page(md: str, title: str) -> str:
    updated, body_md = split_source(md)
    links = "\n".join(f'    <a href="{route}">{html.escape(t)}</a>' for _, _, route, t, _ in PAGES)
    return PAGE_TEMPLATE.format(
        title=html.escape(title),
        nav_css=sync_nav.CANON_CSS.rstrip("\n"),
        nav=sync_nav.CANON_NAV,
        script=sync_nav.CANON_SCRIPT,
        notice=NOTICE_MARKER,
        updated=updated,
        start=START,
        body=sync_faq.render_markdown(body_md),
        links=links,
        end=END,
    )


def main(argv: list[str]) -> int:
    if "--placeholders" in argv:
        remaining = legal_pages.scan_placeholders(LEGAL_DIR)
        for name, found in remaining.items():
            print(f"{name}: {', '.join(found)}")
        print("no placeholders remain" if not remaining else "placeholders remain")
        return 1 if remaining else 0
    check = "--check" in argv
    out_of_step = []
    for src_name, folder, _route, title, _gated in PAGES:
        expected = render_page((LEGAL_DIR / src_name).read_text(encoding="utf-8"), title)
        target = STATIC / folder / "index.html"
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == expected:
            continue
        out_of_step.append(folder)
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(expected, encoding="utf-8")
            print(f"static/{folder}/index.html rewritten")
    if out_of_step and check:
        print(f"out of step with docs/legal/ (run scripts/sync_legal.py): {', '.join(out_of_step)}")
        return 1
    if not out_of_step:
        print("legal pages in step with docs/legal/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
