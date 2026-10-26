"""Every browser page carries the same navigation.

v1.75.1 replaced thirteen hand-copied navs (twelve emoji items, Admin and
Widget in the public bar, two pages with their own variants) with one
canonical block stamped into every page by scripts/sync_nav.py: eight items,
no emoji, Sign in as the call to action, the hamburger from 760px down. The
pages are self-contained static files, so the block is copied rather than
included; these tests compare every copy with the script's constants, and
``sync_nav.py --check`` says the same thing from the command line.
"""
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scripts import sync_nav  # noqa: E402

STATIC = project_root / "static"
PAGES = sync_nav.pages()
NAV_RE = re.compile(r'<nav class="main-nav">.*?</nav>', re.S)
LINK_RE = re.compile(r'<a href="([^"]+)"(?: class="([^"]*)")?>([^<]*)</a>')
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐✅]")


def _html(page: Path) -> str:
    return page.read_text(encoding="utf-8")


def test_every_browser_page_is_covered():
    assert len(PAGES) >= 14
    assert STATIC / "index.html" in PAGES and STATIC / "landing" / "index.html" in PAGES


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(STATIC)))
def test_page_carries_the_canonical_nav_script_and_css(page):
    html = _html(page)
    assert html.count('<nav class="main-nav">') == 1
    assert sync_nav.CANON_NAV + "\n" + sync_nav.CANON_SCRIPT in html, (
        "nav markup or its script differs from scripts/sync_nav.py"
    )
    assert sync_nav.CANON_CSS in html, "nav CSS differs from scripts/sync_nav.py"
    assert html.count("getElementById('navToggle')") == 1, "an old nav script survived"


def test_sync_script_reports_every_page_in_step():
    assert sync_nav.main(["--check"]) == 0


def test_sync_script_is_idempotent():
    for page in PAGES:
        html = _html(page)
        assert sync_nav.render(html) == html


def test_canonical_links_are_the_simplified_set():
    hrefs = [h for h, _ in sync_nav.CANONICAL_LINKS]
    assert hrefs == [
        "/calculator", "/compare", "/trends", "/chat", "/mcp-setup", "/api-docs", "/whats-new", "/billing",
    ]
    assert "/admin" not in hrefs and "/widget" not in hrefs
    for _href, label in sync_nav.CANONICAL_LINKS:
        assert not EMOJI_RE.search(label), f"emoji in nav label {label!r}"
    links = [(h, t) for h, _c, t in LINK_RE.findall(sync_nav.CANON_NAV) if h != "/"]
    assert links == sync_nav.CANONICAL_LINKS
    assert 'href="/billing" class="nav-cta"' in sync_nav.CANON_NAV


def test_toggle_is_accessible_and_collapses_at_the_phone_breakpoint():
    assert 'aria-expanded="false"' in sync_nav.CANON_NAV and 'aria-controls="navLinks"' in sync_nav.CANON_NAV
    assert "setAttribute('aria-expanded'" in sync_nav.CANON_SCRIPT
    assert f"@media(max-width:{sync_nav.MOBILE_BREAKPOINT_PX}px){{" in sync_nav.CANON_CSS
    assert sync_nav.MOBILE_BREAKPOINT_PX >= 760, "eight items do not fit beside the brand below ~760px"


def test_landing_tools_grid_does_not_advertise_admin():
    html = _html(STATIC / "landing" / "index.html")
    assert 'href="/admin" class="tool"' not in html
    assert 'href="/mcp-setup" class="tool"' in html
