"""Tests for the /compare static UI endpoint."""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)
HEADERS = {"x-api-key": "test-key"}


class TestCompareUIMount:
    """Static file serving for /compare."""

    def test_compare_returns_200(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert resp.status_code == 200

    def test_compare_content_type_html(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "text/html" in resp.headers["content-type"]

    def test_compare_contains_model_comparison_title(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "Model Comparison" in resp.text

    def test_compare_contains_selector_bar(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "slot-row" in resp.text

    def test_compare_contains_volume_tabs(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "volume-tab" in resp.text

    def test_compare_contains_compare_grid(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "compare-grid" in resp.text

    def test_compare_contains_cost_table(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "cost-table" in resp.text

    def test_compare_contains_bar_chart(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "bar-chart" in resp.text

    def test_compare_nav_links_present(self):
        resp = client.get("/compare/", follow_redirects=True)
        html = resp.text
        assert "/chat" in html
        assert "/calculator" in html
        assert "/compare" in html

    def test_compare_no_api_key_required(self):
        """Static UI should be accessible without an API key."""
        resp = client.get("/compare/", follow_redirects=True)
        assert resp.status_code == 200

    def test_compare_references_pricing_api(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "/pricing" in resp.text

    def test_compare_references_performance_api(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "/pricing/performance" in resp.text

    def test_compare_references_use_cases_api(self):
        resp = client.get("/compare/", follow_redirects=True)
        assert "/pricing/use-cases" in resp.text

    def test_compare_index_html_exists(self):
        html_path = project_root / "static" / "compare" / "index.html"
        assert html_path.exists()

    def test_compare_index_html_has_volume_tokens(self):
        html_path = project_root / "static" / "compare" / "index.html"
        content = html_path.read_text(encoding="utf-8")
        assert "1000" in content
        assert "10000" in content
        assert "100000" in content
        assert "1000000" in content

    def test_compare_index_has_bar_chart_section(self):
        html_path = project_root / "static" / "compare" / "index.html"
        content = html_path.read_text(encoding="utf-8")
        assert "bar-chart-section" in content

    def test_compare_index_has_use_case_rendering(self):
        html_path = project_root / "static" / "compare" / "index.html"
        content = html_path.read_text(encoding="utf-8")
        assert "use-case" in content

    def test_compare_index_has_performance_metrics(self):
        html_path = project_root / "static" / "compare" / "index.html"
        content = html_path.read_text(encoding="utf-8")
        assert "throughput" in content.lower() or "Throughput" in content

    def test_compare_index_has_context_window(self):
        html_path = project_root / "static" / "compare" / "index.html"
        content = html_path.read_text(encoding="utf-8")
        assert "context_window" in content or "Context Window" in content

    def test_compare_index_has_latency(self):
        html_path = project_root / "static" / "compare" / "index.html"
        content = html_path.read_text(encoding="utf-8")
        assert "latency" in content.lower()
