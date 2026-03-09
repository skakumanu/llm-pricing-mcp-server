"""Tests for the pricing embed widget: /widget UI and /pricing/public endpoint."""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)
HEADERS = {"x-api-key": "test-key"}


# ── /widget static UI ─────────────────────────────────────────────────────

class TestWidgetUI:
    def test_widget_index_returns_200(self):
        resp = client.get("/widget/", follow_redirects=True)
        assert resp.status_code == 200

    def test_widget_content_type_html(self):
        resp = client.get("/widget/", follow_redirects=True)
        assert "text/html" in resp.headers["content-type"]

    def test_widget_page_title(self):
        resp = client.get("/widget/", follow_redirects=True)
        assert "Pricing Embed Widget" in resp.text

    def test_widget_no_api_key_required(self):
        """Static widget page must be accessible without auth."""
        resp = client.get("/widget/", follow_redirects=True)
        assert resp.status_code == 200

    def test_widget_js_served(self):
        resp = client.get("/widget/widget.js")
        assert resp.status_code == 200

    def test_widget_js_content_type(self):
        resp = client.get("/widget/widget.js")
        assert "javascript" in resp.headers["content-type"]

    def test_widget_js_contains_data_llm_pricing(self):
        resp = client.get("/widget/widget.js")
        assert "data-llm-pricing" in resp.text

    def test_widget_js_contains_pricing_public(self):
        resp = client.get("/widget/widget.js")
        assert "/pricing/public" in resp.text

    def test_widget_js_self_contained(self):
        """No external dependencies — IIFE pattern."""
        resp = client.get("/widget/widget.js")
        assert "(function" in resp.text or "function()" in resp.text

    def test_widget_js_supports_dark_theme(self):
        resp = client.get("/widget/widget.js")
        assert "dark" in resp.text

    def test_widget_js_supports_light_theme(self):
        resp = client.get("/widget/widget.js")
        assert "light" in resp.text

    def test_widget_js_supports_compact_layout(self):
        resp = client.get("/widget/widget.js")
        assert "compact" in resp.text

    def test_widget_js_auto_refresh(self):
        resp = client.get("/widget/widget.js")
        assert "setInterval" in resp.text or "refresh" in resp.text

    def test_widget_demo_has_embed_generator(self):
        resp = client.get("/widget/", follow_redirects=True)
        assert "Embed Code Generator" in resp.text

    def test_widget_demo_has_attribute_table(self):
        resp = client.get("/widget/", follow_redirects=True)
        assert "data-models" in resp.text
        assert "data-theme" in resp.text
        assert "data-layout" in resp.text

    def test_widget_demo_references_public_api(self):
        resp = client.get("/widget/", follow_redirects=True)
        assert "/pricing/public" in resp.text

    def test_widget_demo_has_copy_button(self):
        resp = client.get("/widget/", follow_redirects=True)
        assert "Copy" in resp.text

    def test_widget_demo_has_live_preview_section(self):
        resp = client.get("/widget/", follow_redirects=True)
        assert "preview" in resp.text.lower()

    def test_widget_index_html_exists(self):
        path = project_root / "static" / "widget" / "index.html"
        assert path.exists()

    def test_widget_js_file_exists(self):
        path = project_root / "static" / "widget" / "widget.js"
        assert path.exists()


# ── /pricing/public endpoint ───────────────────────────────────────────────

class TestPricingPublicEndpoint:
    def test_returns_200_no_auth(self):
        """Must work without an API key."""
        resp = client.get("/pricing/public")
        assert resp.status_code == 200

    def test_returns_json(self):
        resp = client.get("/pricing/public")
        data = resp.json()
        assert isinstance(data, dict)

    def test_has_models_key(self):
        resp = client.get("/pricing/public")
        data = resp.json()
        assert "models" in data

    def test_has_total_key(self):
        resp = client.get("/pricing/public")
        data = resp.json()
        assert "total" in data

    def test_has_updated_at_key(self):
        resp = client.get("/pricing/public")
        data = resp.json()
        assert "updated_at" in data

    def test_model_has_required_fields(self):
        resp = client.get("/pricing/public")
        data = resp.json()
        if data["models"]:
            m = data["models"][0]
            assert "model_name" in m
            assert "provider" in m
            assert "input_per_1m_usd" in m
            assert "output_per_1m_usd" in m

    def test_model_prices_are_numeric(self):
        resp = client.get("/pricing/public")
        data = resp.json()
        for m in data["models"]:
            assert isinstance(m["input_per_1m_usd"],  (int, float))
            assert isinstance(m["output_per_1m_usd"], (int, float))

    def test_total_matches_models_count(self):
        resp = client.get("/pricing/public")
        data = resp.json()
        assert data["total"] == len(data["models"])

    def test_models_filter_param(self):
        resp = client.get("/pricing/public?models=gpt-4o")
        data = resp.json()
        # Should only return gpt-4o if it exists; either 0 or 1 matching result
        for m in data["models"]:
            assert m["model_name"].lower() == "gpt-4o"

    def test_provider_filter_param(self):
        resp = client.get("/pricing/public?provider=openai")
        data = resp.json()
        for m in data["models"]:
            assert m["provider"].lower() == "openai"

    def test_no_api_key_header_still_works(self):
        """Explicitly no auth header."""
        resp = client.get("/pricing/public", headers={})
        assert resp.status_code == 200

    def test_context_window_field_present(self):
        resp = client.get("/pricing/public")
        data = resp.json()
        # context_window can be null; key must exist
        if data["models"]:
            assert "context_window" in data["models"][0]

    def test_prices_are_per_1m_usd(self):
        """Sanity: a real GPT-4o should cost roughly $2–$15 per 1M input tokens."""
        resp = client.get("/pricing/public?models=gpt-4o")
        data = resp.json()
        for m in data["models"]:
            if m["model_name"] == "gpt-4o":
                # Should be dollars-scale, not sub-cent micro-token scale
                assert m["input_per_1m_usd"] > 0.01, "Price looks like it's still per-token, not per-1M"
                assert m["input_per_1m_usd"] < 1000, "Price unexpectedly large"
