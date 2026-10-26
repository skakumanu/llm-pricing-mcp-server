#!/usr/bin/env python3
"""Keep the navigation identical on every browser page.

The pages under static/ are self-contained HTML files, so the nav cannot be
included from one place at serve time; instead this script holds the single
canonical nav (markup + toggle script + CSS) and stamps it into every page.
tests/test_nav_consistency.py compares each page against the same constants,
so a hand edit to one page fails CI until this script is run again.

To change the nav: edit CANONICAL_LINKS / CANON_CSS below, run

    python scripts/sync_nav.py            # rewrite every page
    python scripts/sync_nav.py --check    # exit 1 if any page is out of step

and commit the pages together with this file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC = PROJECT_ROOT / "static"

CANONICAL_LINKS = [
    ("/calculator", "Calculator"),
    ("/compare", "Compare"),
    ("/trends", "Trends"),
    ("/chat", "Chat"),
    ("/mcp-setup", "MCP"),
    ("/api-docs", "API"),
    ("/whats-new", "What's New"),
    ("/billing", "Sign in"),
]
CTA_HREF = "/billing"
MOBILE_BREAKPOINT_PX = 760

CANON_CSS = f"""\
    /* ── Shared nav (identical on every page; scripts/sync_nav.py, tests/test_nav_consistency.py) ── */
    .main-nav{{display:flex;align-items:center;background:#0f1117;padding:0 1.25rem;height:52px;border-bottom:1px solid #23263a;position:sticky;top:0;z-index:200;gap:.5rem;flex-shrink:0}}
    .nav-brand{{font-weight:700;color:#7c6af7;text-decoration:none;font-size:1rem;white-space:nowrap;flex-shrink:0}}
    .nav-spacer{{flex:1}}
    .nav-links{{display:flex;align-items:center;gap:.125rem}}
    .nav-links a{{color:#8b93a5;text-decoration:none;padding:.35rem .6rem;border-radius:6px;font-size:.8rem;white-space:nowrap;transition:all .15s;border:none}}
    .nav-links a:hover{{background:#1a1d2e;color:#e5e7eb}}
    .nav-links a.active{{background:#1a1d2e;color:#7c6af7;font-weight:600}}
    .nav-links a.nav-cta{{margin-left:.35rem;border:1px solid #7c6af7;color:#7c6af7}}
    .nav-links a.nav-cta:hover{{background:#7c6af7;color:#fff}}
    .nav-toggle{{display:none;background:none;border:1px solid #2a2d3a;color:#9ca3af;font-size:1.1rem;cursor:pointer;padding:.25rem .5rem;border-radius:6px;line-height:1;flex-shrink:0}}
    @media(max-width:{MOBILE_BREAKPOINT_PX}px){{
      .nav-toggle{{display:block}}
      .nav-links{{display:none;position:absolute;top:52px;left:0;right:0;flex-direction:column;align-items:stretch;background:#0f1117;border-bottom:1px solid #23263a;padding:.5rem 1rem;gap:.25rem;z-index:199}}
      .nav-links.open{{display:flex}}
      .nav-links a{{padding:.6rem .75rem;font-size:.875rem}}
      .nav-links a.nav-cta{{margin-left:0;text-align:center}}
    }}
"""


def _link(href: str, label: str) -> str:
    cls = ' class="nav-cta"' if href == CTA_HREF else ""
    return f'    <a href="{href}"{cls}>{label}</a>'


CANON_NAV = (
    '<nav class="main-nav">\n'
    '  <a href="/" class="nav-brand">⚡ LLM Pricing</a>\n'
    '  <span class="nav-spacer"></span>\n'
    '  <div class="nav-links" id="navLinks">\n'
    + "\n".join(_link(h, t) for h, t in CANONICAL_LINKS) + "\n"
    '  </div>\n'
    '  <button class="nav-toggle" id="navToggle" aria-label="Menu" aria-expanded="false" aria-controls="navLinks">☰</button>\n'
    '</nav>'
)

CANON_SCRIPT = """\
<script>
(function(){
  var t=document.getElementById('navToggle'),l=document.getElementById('navLinks');
  if(!t||!l)return;
  t.onclick=function(){var o=l.classList.toggle('open');t.setAttribute('aria-expanded',o?'true':'false');};
  var p=location.pathname.replace(/\\/$/,'')||'/';
  l.querySelectorAll('a').forEach(function(a){
    var h=a.getAttribute('href');
    if(h===p||h===p+'/')a.classList.add('active');
    a.addEventListener('click',function(){l.classList.remove('open');t.setAttribute('aria-expanded','false');});
  });
})();
</script>"""

# The nav markup immediately followed by its script, as stamped by this file.
NAV_AND_SCRIPT_RE = re.compile(
    r'<nav class="main-nav">.*?</nav>\n<script>\n\(function\(\)\{\n  var t=document\.getElementById\(\'navToggle\'\).*?\}\)\(\);\n</script>',
    re.S,
)
# The CSS block from its heading comment to the closing brace of the mobile media query.
CSS_RE = re.compile(
    r"^[ \t]*/\* ── Shared nav[^\n]*\*/[ \t]*\n[ \t]*\.main-nav\{.*?@media\(max-width:\d+px\)\{.*?^[ \t]*\}[ \t]*\n",
    re.S | re.M,
)


def pages() -> list[Path]:
    return [STATIC / "index.html", *sorted(STATIC.glob("*/index.html"))]


def render(html: str) -> str:
    """Return *html* with the canonical nav, script and CSS in place of its current ones."""
    if not NAV_AND_SCRIPT_RE.search(html):
        raise ValueError("no canonical nav+script block found (first-time pages need the v1.75.1 migration)")
    if not CSS_RE.search(html):
        raise ValueError("no shared nav CSS block found")
    html = NAV_AND_SCRIPT_RE.sub(lambda _m: CANON_NAV + "\n" + CANON_SCRIPT, html, count=1)
    html = CSS_RE.sub(lambda _m: CANON_CSS, html, count=1)
    return html


def main(argv: list[str]) -> int:
    check = "--check" in argv
    out_of_step = []
    for page in pages():
        html = page.read_text(encoding="utf-8")
        try:
            new = render(html)
        except ValueError as exc:
            print(f"{page.relative_to(PROJECT_ROOT)}: {exc}")
            out_of_step.append(page)
            continue
        if new != html:
            out_of_step.append(page)
            if not check:
                page.write_text(new, encoding="utf-8")
                print(f"{page.relative_to(PROJECT_ROOT)}: updated")
            else:
                print(f"{page.relative_to(PROJECT_ROOT)}: out of step")
    if check:
        print("all pages in step" if not out_of_step else f"{len(out_of_step)} page(s) out of step")
        return 1 if out_of_step else 0
    print(f"{len(out_of_step)} page(s) rewritten, {len(pages()) - len(out_of_step)} already current")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
