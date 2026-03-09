"""Tests for the /history static page mount and related security middleware behaviour."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)


def _patch_history_svc(snapshots=None, total=None):
    """Patch get_pricing_history_service for /pricing/history endpoint tests."""
    payload = {"snapshots": snapshots or [], "total": total or 0}
    mock_svc = MagicMock()
    mock_svc.get_history = AsyncMock(return_value=payload)
    return patch("src.main.get_pricing_history_service", return_value=mock_svc)


class TestHistoryPage:
    def test_history_page_returns_200(self):
        resp = client.get("/history/")
        assert resp.status_code == 200

    def test_history_page_content_type_html(self):
        resp = client.get("/history/")
        assert "text/html" in resp.headers["content-type"]

    def test_history_page_contains_chartjs(self):
        resp = client.get("/history/")
        assert "chart.js" in resp.text.lower() or "Chart" in resp.text

    def test_history_page_contains_pricing_history_reference(self):
        resp = client.get("/history/")
        assert "/pricing/history" in resp.text

    def test_history_page_no_auth_required(self):
        """The /history page should be accessible without an API key."""
        resp = client.get("/history/", headers={})
        assert resp.status_code == 200


class TestHistoryPageSecurityBypass:
    def test_history_subpath_bypasses_auth(self):
        """All /history/* paths should bypass API key enforcement."""
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            # No x-api-key header — should still get 200 for UI path
            resp = client.get("/history/")
            assert resp.status_code == 200
        finally:
            main_module.settings.mcp_api_key = original

    def test_pricing_history_endpoint_requires_auth_when_key_set(self):
        """GET /pricing/history is NOT in the bypass list — requires auth when key is set."""
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = "secret"
            with _patch_history_svc():
                resp = client.get("/pricing/history")
            assert resp.status_code == 401
        finally:
            main_module.settings.mcp_api_key = original


class TestHistoryApiIntegration:
    def test_pricing_history_api_returns_200(self):
        with _patch_history_svc():
            resp = client.get("/pricing/history")
        assert resp.status_code == 200

    def test_pricing_history_api_returns_snapshots(self):
        snap = {
            "model_name": "gpt-4o", "provider": "openai",
            "cost_per_input_token": 0.005, "cost_per_output_token": 0.015,
            "captured_at": 1700000000.0,
        }
        with _patch_history_svc(snapshots=[snap], total=1):
            resp = client.get("/pricing/history?model_name=gpt-4o")
        data = resp.json()
        assert data["total"] == 1
        assert data["snapshots"][0]["model_name"] == "gpt-4o"
