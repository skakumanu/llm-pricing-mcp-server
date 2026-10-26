"""The shared browser currency control (v1.76.0).

/calculator, /compare and the landing page embed one identical snippet
(``/* llmp-currency v1 */``). It is copied, not included, so these tests pin
the three copies to each other and to the server's supported set, and check
each page wires it in the way the Spec asks (calculator/compare convert every
displayed amount; the landing carries the selector and note only).
"""
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from scripts import sync_nav  # noqa: E402
from src.main import app  # noqa: E402
from src.services.currency import SUPPORTED_CURRENCIES  # noqa: E402

STATIC = project_root / "static"
PAGES = {
    "calculator": STATIC / "calculator" / "index.html",
    "compare": STATIC / "compare" / "index.html",
    "landing": STATIC / "landing" / "index.html",
}
SNIPPET_RE = re.compile(r"<script>\n/\* llmp-currency v1 \*/.*?</script>", re.S)

client = TestClient(app)


def _snippet(page: Path) -> str:
    matches = SNIPPET_RE.findall(page.read_text(encoding="utf-8"))
    assert len(matches) == 1, f"{page} must embed the snippet exactly once"
    return matches[0]


class TestSharedSnippet:
    def test_three_pages_carry_a_byte_identical_copy(self):
        copies = {name: _snippet(p) for name, p in PAGES.items()}
        assert copies["calculator"] == copies["compare"] == copies["landing"]

    @pytest.mark.parametrize("name", list(PAGES))
    def test_snippet_contract(self, name):
        s = _snippet(PAGES[name])
        for needle in ("llmp.currency", '"/locale"', '"/currency/rates"', "Intl.NumberFormat",
                       "currency-select", "llmp-currency-change", "providers bill in USD", "detected: ",
                       "currency-stale", "(stale)"):
            assert needle in s, f"{name}: snippet lacks {needle!r}"

    def test_snippet_lists_exactly_the_servers_supported_set(self):
        s = _snippet(PAGES["calculator"])
        m = re.search(r"var SUPPORTED = \[([^\]]+)\];", s)
        assert m, "SUPPORTED array missing"
        codes = tuple(re.findall(r'"([A-Z]{3})"', m.group(1)))
        assert codes == SUPPORTED_CURRENCIES

    def test_zero_decimal_currencies_match_server(self):
        s = _snippet(PAGES["calculator"])
        assert "JPY: true, KRW: true" in s

    def test_storage_and_detection_rules(self):
        s = _snippet(PAGES["calculator"])
        # only a visitor's own choice is persisted; detection is re-resolved per load
        assert 'save({ currency: code, source: "user"' in s
        assert 'if (state.source === "user") save(' in s
        assert "localStorage.getItem(STORAGE_KEY)" in s

    def test_control_wraps_on_narrow_screens(self):
        s = _snippet(PAGES["calculator"])
        assert "flex-wrap:wrap" in s and "overflow-wrap:anywhere" in s

    def test_snippet_precedes_page_script_that_uses_it(self):
        for name, page in PAGES.items():
            html = page.read_text(encoding="utf-8")
            assert html.index("/* llmp-currency v1 */") < html.index("LLMPCurrency.renderControl("), name


class TestCalculatorWiring:
    html = PAGES["calculator"].read_text(encoding="utf-8")

    def test_has_control_and_init(self):
        assert 'id="currency-control"' in self.html
        assert "LLMPCurrency.init()" in self.html
        assert 'window.addEventListener("llmp-currency-change", calculate)' in self.html

    def test_every_amount_routes_through_the_formatter(self):
        body = self.html.split("/* llmp-currency v1 */")[1]
        page_script = body.split("<main")[1]
        assert "return LLMPCurrency.format(n);" in page_script
        assert "LLMPCurrency.formatRate(model.cost_per_input_token)" in page_script
        assert "LLMPCurrency.formatRate(model.cost_per_output_token)" in page_script
        assert "\\$${" not in page_script and "`$${" not in page_script, "a hard-coded $ survived"
        assert '"$0.00"' not in page_script

    def test_tokens_per_unit_uses_converted_total(self):
        assert "LLMPCurrency.convert(total)" in self.html
        assert "Tokens per 1 ${LLMPCurrency.currency}" in self.html


class TestCompareWiring:
    html = PAGES["compare"].read_text(encoding="utf-8")

    def test_has_control_and_init(self):
        assert 'id="currency-control"' in self.html
        assert "LLMPCurrency.init()" in self.html
        assert 'window.addEventListener("llmp-currency-change"' in self.html
        assert "renderResults(_compared)" in self.html

    def test_cost_displays_route_through_the_formatter(self):
        page_script = self.html.split("/* llmp-currency v1 */")[1].split('<div class="page">')[1]
        assert "return LLMPCurrency.format(usd);" in page_script
        assert "$${" not in page_script, "a hard-coded $ template fragment survived"
        assert 'id="th-in"' in self.html and "Input ${LLMPCurrency.currency}/1M" in self.html
        assert "Tokens / 1 ${LLMPCurrency.currency}" in self.html
        assert "LLMPCurrency.convert(m.input_usd)" in self.html

    def test_usd_values_stay_usd_in_state(self):
        assert "input_usd:  inp * 1e6" in self.html and "output_usd: out * 1e6" in self.html


class TestLandingWiring:
    html = PAGES["landing"].read_text(encoding="utf-8")

    def test_selector_and_note_only(self):
        assert 'id="currency-control"' in self.html
        assert "LLMPCurrency.renderControl(" in self.html and "LLMPCurrency.init()" in self.html
        # OWNER DECISION: no landing numbers are converted
        page_body = self.html.split("/* llmp-currency v1 */")[1].split("<!-- Hero -->")[1]
        assert "LLMPCurrency.format(" not in page_body and "LLMPCurrency.convert(" not in page_body

    def test_tier_prices_untouched(self):
        assert '<div class="price">$0 <span>/mo</span></div>' in self.html
        assert '<div class="price">$29 <span>/mo</span></div>' in self.html


class TestServedPages:
    @pytest.mark.parametrize("path", ["/calculator/", "/compare/", "/"])
    def test_pages_serve_the_control(self, path):
        resp = client.get(path, follow_redirects=True)
        assert resp.status_code == 200
        assert "currency-control" in resp.text and "/* llmp-currency v1 */" in resp.text

    def test_nav_block_untouched(self):
        assert sync_nav.main(["--check"]) == 0

    def test_browser_endpoints_are_public(self):
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            assert client.get("/locale").status_code == 200
            assert client.get("/currency/rates").status_code == 200
        finally:
            main_module.settings.mcp_api_key = original
