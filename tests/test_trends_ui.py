"""Tests for the /trends static page mount and related security middleware behaviour."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)

_TREND = {
    "model_name": "gpt-4o",
    "provider": "openai",
    "input_change_pct": 100.0,
    "output_change_pct": 100.0,
    "direction": "increased",
    "first_seen": 1700000000.0,
    "last_seen": 1700086400.0,
    "first_input": 0.005,
    "last_input": 0.010,
    "first_output": 0.015,
    "last_output": 0.030,
}


def _patch_trends_svc(trends=None):
    mock_svc = MagicMock()
    mock_svc.get_trends = AsyncMock(return_value=trends if trends is not None else [_TREND])
    return patch("src.main.get_pricing_history_service", return_value=mock_svc)


class TestTrendsPage:
    def test_trends_page_returns_200(self):
        resp = client.get("/trends/")
        assert resp.status_code == 200

    def test_trends_page_content_type_html(self):
        resp = client.get("/trends/")
        assert "text/html" in resp.headers["content-type"]

    def test_trends_page_no_auth_required(self):
        resp = client.get("/trends/", headers={})
        assert resp.status_code == 200

    def test_trends_page_contains_api_call(self):
        resp = client.get("/trends/")
        assert "/pricing/trends" in resp.text

    def test_trends_page_contains_leaderboard_heading(self):
        resp = client.get("/trends/")
        assert "leaderboard" in resp.text.lower() or "Trends" in resp.text

    def test_trends_page_has_nav_links_to_history_and_chat(self):
        resp = client.get("/trends/")
        assert "/history" in resp.text
        assert "/chat" in resp.text

    def test_trends_page_has_direction_badges(self):
        resp = client.get("/trends/")
        # Badge labels exist in the JS template
        assert "badge-up" in resp.text or "Up" in resp.text
        assert "badge-down" in resp.text or "Down" in resp.text

    def test_trends_page_has_export_csv_button(self):
        resp = client.get("/trends/")
        assert "Export CSV" in resp.text or "exportCsv" in resp.text


class TestTrendsSecurityBypass:
    def test_trends_path_bypasses_auth(self):
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            resp = client.get("/trends/")
            assert resp.status_code == 200
        finally:
            main_module.settings.mcp_api_key = original

    def test_pricing_trends_api_requires_auth_when_key_set(self):
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            with _patch_trends_svc():
                resp = client.get("/pricing/trends")
            assert resp.status_code == 401
        finally:
            main_module.settings.mcp_api_key = original


class TestTrendsApiIntegration:
    def test_pricing_trends_returns_200(self):
        with _patch_trends_svc():
            resp = client.get("/pricing/trends")
        assert resp.status_code == 200

    def test_pricing_trends_response_shape(self):
        with _patch_trends_svc([_TREND]):
            resp = client.get("/pricing/trends")
        data = resp.json()
        assert "trends" in data
        assert "days" in data
        assert data["days"] == 30  # default

    def test_pricing_trends_custom_days(self):
        with _patch_trends_svc() as mock_patch:
            mock_svc = mock_patch.return_value
            resp = client.get("/pricing/trends?days=7&limit=10")
        assert resp.status_code == 200
        mock_svc.get_trends.assert_called_once_with(days=7, limit=10)

    def test_pricing_trends_trend_fields(self):
        with _patch_trends_svc([_TREND]):
            resp = client.get("/pricing/trends")
        trend = resp.json()["trends"][0]
        assert trend["model_name"] == "gpt-4o"
        assert trend["direction"] == "increased"
        assert trend["input_change_pct"] == 100.0

    def test_pricing_trends_empty_list(self):
        with _patch_trends_svc([]):
            resp = client.get("/pricing/trends")
        data = resp.json()
        assert data["trends"] == []
        assert data["days"] == 30
