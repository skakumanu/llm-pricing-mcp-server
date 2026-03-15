"""Tests for the admin dashboard UI and API endpoints."""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)
HEADERS = {"x-api-key": "test-key"}


# ── /admin static UI ──────────────────────────────────────────────────────

class TestAdminUI:
    def test_admin_index_returns_200(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert resp.status_code == 200

    def test_admin_content_type_html(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "text/html" in resp.headers["content-type"]

    def test_admin_page_title(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "Admin Dashboard" in resp.text

    def test_admin_no_api_key_required_for_static(self):
        """Static HTML served without auth; data APIs are separate."""
        resp = client.get("/admin/", follow_redirects=True)
        assert resp.status_code == 200

    def test_admin_has_stat_row(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "stat-row" in resp.text

    def test_admin_has_endpoint_table(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "endpoint-table" in resp.text or "endpoint-body" in resp.text

    def test_admin_has_provider_grid(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "provider-grid" in resp.text

    def test_admin_has_rate_limit_table(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "rl-body" in resp.text or "rate" in resp.text.lower()

    def test_admin_has_feature_usage_table(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "feature" in resp.text.lower()

    def test_admin_has_webhook_section(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "Webhook" in resp.text or "alert" in resp.text.lower()

    def test_admin_has_conversations_section(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "Conversation" in resp.text or "conv" in resp.text.lower()

    def test_admin_references_admin_stats_api(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "/admin/stats" in resp.text

    def test_admin_references_rate_limits_api(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "/admin/rate-limits" in resp.text

    def test_admin_has_auto_refresh_control(self):
        resp = client.get("/admin/", follow_redirects=True)
        assert "refresh" in resp.text.lower()

    def test_admin_nav_links_present(self):
        resp = client.get("/admin/", follow_redirects=True)
        html = resp.text
        assert "/chat" in html
        assert "/compare" in html
        assert "/admin" in html

    def test_admin_index_html_file_exists(self):
        path = project_root / "static" / "admin" / "index.html"
        assert path.exists()


# ── GET /admin/stats ──────────────────────────────────────────────────────

class TestAdminStatsEndpoint:
    def test_returns_200_with_api_key(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        assert resp.status_code == 200

    def test_returns_json(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        data = resp.json()
        assert isinstance(data, dict)

    def test_has_version_field(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        assert "version" in resp.json()

    def test_has_overall_field(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        assert "overall" in resp.json()

    def test_has_endpoints_field(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        assert "endpoints" in resp.json()

    def test_has_providers_field(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        assert "providers" in resp.json()

    def test_has_features_field(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        assert "features" in resp.json()

    def test_has_deployment_field(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        assert "deployment" in resp.json()

    def test_overall_has_total_requests(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        assert "total_requests" in resp.json()["overall"]

    def test_deployment_has_uptime_seconds(self):
        resp = client.get("/admin/stats", headers=HEADERS)
        assert "uptime_seconds" in resp.json()["deployment"]

    def test_version_matches_package(self):
        import src
        resp = client.get("/admin/stats", headers=HEADERS)
        assert resp.json()["version"] == src.__version__

    def test_accessible_without_api_key(self):
        """/admin/stats is public read-only — no auth required."""
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            resp = client.get("/admin/stats")
            assert resp.status_code == 200
        finally:
            main_module.settings.mcp_api_key = original

    def test_uptime_since_is_serialisable(self):
        """uptime_since must be a plain string, not a datetime object."""
        resp = client.get("/admin/stats", headers=HEADERS)
        uptime_since = resp.json()["overall"].get("uptime_since")
        # If present, must be a JSON string, not an object
        assert uptime_since is None or isinstance(uptime_since, str)


# ── GET /admin/rate-limits ────────────────────────────────────────────────

class TestAdminRateLimitsEndpoint:
    def test_returns_200_with_api_key(self):
        resp = client.get("/admin/rate-limits", headers=HEADERS)
        assert resp.status_code == 200

    def test_returns_json(self):
        resp = client.get("/admin/rate-limits", headers=HEADERS)
        assert isinstance(resp.json(), dict)

    def test_has_tracked_ips(self):
        resp = client.get("/admin/rate-limits", headers=HEADERS)
        assert "tracked_ips" in resp.json()

    def test_has_limit_per_minute(self):
        resp = client.get("/admin/rate-limits", headers=HEADERS)
        assert "limit_per_minute" in resp.json()

    def test_has_top_consumers(self):
        resp = client.get("/admin/rate-limits", headers=HEADERS)
        assert "top_consumers" in resp.json()

    def test_has_snapshot_at(self):
        resp = client.get("/admin/rate-limits", headers=HEADERS)
        assert "snapshot_at" in resp.json()

    def test_top_consumers_is_list(self):
        resp = client.get("/admin/rate-limits", headers=HEADERS)
        assert isinstance(resp.json()["top_consumers"], list)

    def test_limit_per_minute_is_positive(self):
        resp = client.get("/admin/rate-limits", headers=HEADERS)
        assert resp.json()["limit_per_minute"] > 0

    def test_accessible_without_api_key(self):
        """/admin/rate-limits is public read-only — no auth required."""
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            resp = client.get("/admin/rate-limits")
            assert resp.status_code == 200
        finally:
            main_module.settings.mcp_api_key = original
