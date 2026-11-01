#!/usr/bin/env python3
"""Render docs/FAQ.md into static/faq/index.html (v1.84.0).

docs/FAQ.md is the one customer-facing FAQ: the support agent indexes it, the browser page
is generated from it, and tests/test_support_faq.py pins its prices and limits to
src/services/tiers.py and the settings defaults. This script converts the constrained
markdown the FAQ uses (headings, paragraphs, bullet lists, one table, inline links / code /
bold) with the standard library only and stamps it between the markers in the page template,
which carries the canonical nav from scripts/sync_nav.py.

    python scripts/sync_faq.py            # rewrite static/faq/index.html
    python scripts/sync_faq.py --check    # exit 1 if the page is out of step with FAQ.md
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import sync_nav  # noqa: E402

FAQ_MD = PROJECT_ROOT / "docs" / "FAQ.md"
FAQ_HTML = PROJECT_ROOT / "static" / "faq" / "index.html"

START = "<!-- faq:start (generated from docs/FAQ.md by scripts/sync_faq.py; do not edit by hand) -->"
END = "<!-- faq:end -->"

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ORDERED = re.compile(r"^(\d+)\. (.*)$")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    """Escape, then apply inline code, bold and links (only relative or https links)."""
    out = html.escape(text, quote=False)
    out = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)

    def _link(m: re.Match) -> str:
        href = m.group(2)
        if not (href.startswith("/") or href.startswith("https://")):
            return m.group(0)
        return f'<a href="{href}">{m.group(1)}</a>'
    return _LINK.sub(_link, out)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render_markdown(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    in_list = False       # inside <ul>
    in_olist = False      # inside <ol>

    def close_list() -> None:
        nonlocal in_list, in_olist
        if in_list:
            out.append("</ul>")
            in_list = False
        if in_olist:
            out.append("</ol>")
            in_olist = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            close_list()
            i += 1
            continue
        if stripped.startswith("# "):
            close_list()
            out.append(f"<h1>{_inline(stripped[2:])}</h1>")
        elif stripped.startswith("## "):
            close_list()
            title = stripped[3:]
            out.append(f'<h2 id="{_slug(title)}">{_inline(title)}</h2>')
        elif stripped.startswith("### "):
            close_list()
            title = stripped[4:]
            out.append(f'<h3 id="{_slug(title)}">{_inline(title)}</h3>')
        elif (m_ord := _ORDERED.match(stripped)):
            if in_list:
                out.append("</ul>")
                in_list = False
            if not in_olist:
                out.append("<ol>")
                in_olist = True
            out.append(f"<li>{_inline(m_ord.group(2))}</li>")
        elif stripped.startswith("- "):
            if in_olist:
                out.append("</ol>")
                in_olist = False
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
        elif stripped.startswith("|"):
            close_list()
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            header, body = rows[0], [r for r in rows[2:]]  # rows[1] is the |---| separator
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in header) + "</tr></thead>")
            out.append("<tbody>" + "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>")
            continue
        else:
            close_list()
            out.append(f"<p>{_inline(stripped)}</p>")
        i += 1
    close_list()
    return "\n".join(out)


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FAQ — LLM Pricing API</title>
  <style>
    :root {{
      --bg: #0f1117;
      --surface: #1a1d27;
      --surface2: #22263a;
      --border: #2e3347;
      --accent: #7c6af7;
      --text: #e2e8f0;
      --muted: #8892a4;
      --success: #48bb78;
      --warn: #ed8936;
      --danger: #f56565;
      --radius: 10px;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; line-height: 1.6; }}
{nav_css}
    /* ── FAQ page ── */
    .container {{ max-width: 860px; margin: 0 auto; padding: 40px 24px 80px; }}
    .faq h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
    .faq h2 {{ font-size: 18px; font-weight: 600; margin: 32px 0 10px; padding-top: 8px; border-top: 1px solid var(--border); }}
    .faq p {{ color: var(--muted); margin: 0 0 10px; }}
    .faq h1 + p {{ font-size: 15px; }}
    .faq ul, .faq ol {{ margin: 0 0 12px 1.2rem; color: var(--text); }}
    .faq li {{ margin: 4px 0; overflow-wrap: anywhere; }}
    .faq a {{ color: var(--accent); text-decoration: none; }}
    .faq a:hover {{ text-decoration: underline; }}
    .faq code {{ background: var(--surface2); border: 1px solid var(--border); border-radius: 4px; padding: 0 .35em; font-size: .9em; }}
    .faq table {{ border-collapse: collapse; width: 100%; margin: 10px 0 14px; font-size: 13px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }}
    .faq th, .faq td {{ border-bottom: 1px solid var(--border); padding: 8px 10px; text-align: left; vertical-align: top; }}
    .faq th {{ background: var(--surface2); font-weight: 600; }}
    .faq-help {{ margin-top: 36px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 20px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
    .faq-help a.btn {{ background: var(--accent); color: #fff; padding: 8px 14px; border-radius: 7px; font-weight: 600; text-decoration: none; }}
    .faq-help a.btn.secondary {{ background: transparent; border: 1px solid var(--border); color: var(--muted); }}
    .faq-help a.btn.secondary:hover {{ border-color: var(--accent); color: var(--accent); }}
  </style>
</head>
<body>
{nav}
{script}

<div class="container">
  <div class="faq">
{start}
{body}
{end}
  </div>
  <div class="faq-help">
    <span style="color:var(--muted)">Still stuck?</span>
    <a class="btn" href="/chat?mode=support">Ask the support assistant</a>
    <a class="btn secondary" href="/billing">Billing dashboard</a>
    <a class="btn secondary" href="/mcp-setup">MCP setup</a>
    <a class="btn secondary" href="/api-docs">API reference</a>
  </div>
</div>

</body>
</html>
"""


def render_page(md: str) -> str:
    return PAGE_TEMPLATE.format(
        nav_css=sync_nav.CANON_CSS.rstrip("\n"),
        nav=sync_nav.CANON_NAV,
        script=sync_nav.CANON_SCRIPT,
        start=START,
        body=render_markdown(md),
        end=END,
    )


def main(argv: list[str]) -> int:
    check = "--check" in argv
    expected = render_page(FAQ_MD.read_text(encoding="utf-8"))
    current = FAQ_HTML.read_text(encoding="utf-8") if FAQ_HTML.exists() else None
    if current == expected:
        print("static/faq/index.html in step with docs/FAQ.md")
        return 0
    if check:
        print("static/faq/index.html is out of step with docs/FAQ.md (run scripts/sync_faq.py)")
        return 1
    FAQ_HTML.parent.mkdir(parents=True, exist_ok=True)
    FAQ_HTML.write_text(expected, encoding="utf-8")
    print("static/faq/index.html rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
