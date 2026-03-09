"""Tests for the /calculator static page mount and security middleware behaviour."""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)


class TestCalculatorPage:
    def test_page_returns_200(self):
        resp = client.get("/calculator/")
        assert resp.status_code == 200

    def test_page_content_type_html(self):
        resp = client.get("/calculator/")
        assert "text/html" in resp.headers["content-type"]

    def test_page_no_auth_required(self):
        resp = client.get("/calculator/", headers={})
        assert resp.status_code == 200

    def test_page_contains_pricing_api_call(self):
        resp = client.get("/calculator/")
        assert "/pricing" in resp.text

    def test_page_contains_model_select(self):
        resp = client.get("/calculator/")
        assert "model-select" in resp.text

    def test_page_contains_input_tokens(self):
        resp = client.get("/calculator/")
        assert "input-tokens" in resp.text

    def test_page_contains_output_tokens(self):
        resp = client.get("/calculator/")
        assert "output-tokens" in resp.text

    def test_page_contains_cost_calculator_heading(self):
        resp = client.get("/calculator/")
        assert "Cost Calculator" in resp.text

    def test_page_contains_preset_buttons(self):
        resp = client.get("/calculator/")
        assert "setPreset" in resp.text

    def test_page_contains_comparison_table(self):
        resp = client.get("/calculator/")
        assert "compare-table" in resp.text

    def test_page_contains_scale_section(self):
        resp = client.get("/calculator/")
        assert "scale" in resp.text.lower()

    def test_page_contains_nav_links(self):
        resp = client.get("/calculator/")
        text = resp.text
        assert "/chat" in text
        assert "/trends" in text

    def test_page_contains_sort_function(self):
        resp = client.get("/calculator/")
        assert "sortTable" in resp.text


class TestCalculatorPageSecurityBypass:
    def test_calculator_subpath_bypasses_auth(self):
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            resp = client.get("/calculator/")
            assert resp.status_code == 200
        finally:
            main_module.settings.mcp_api_key = original

    def test_pricing_endpoint_still_requires_auth_when_key_set(self):
        """GET /pricing is NOT in the bypass list — needs auth when key is configured."""
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            resp = client.get("/pricing")  # no key
            assert resp.status_code == 401
        finally:
            main_module.settings.mcp_api_key = original
