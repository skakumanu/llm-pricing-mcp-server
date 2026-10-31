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
# Built at runtime so no secret-shaped literal lands in a commit (gitleaks scans them all).
_OPERATOR_KEY = "-".join(["operator", "test", "value"])
OPERATOR_HEADERS = {"x-api-key": _OPERATOR_KEY}


@pytest.fixture
def operator_key_configured():
    """v1.81.4: /admin/stats and /admin/rate-limits are operator-only."""
    import src.main as main_module
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = _OPERATOR_KEY
    try:
        yield
    finally:
        main_module.settings.mcp_api_key = original


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

@pytest.mark.usefixtures("operator_key_configured")
class TestAdminStatsEndpoint:
    def test_returns_200_with_api_key(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200

    def test_returns_json(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        data = resp.json()
        assert isinstance(data, dict)

    def test_has_version_field(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert "version" in resp.json()

    def test_has_overall_field(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert "overall" in resp.json()

    def test_has_endpoints_field(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert "endpoints" in resp.json()

    def test_has_providers_field(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert "providers" in resp.json()

    def test_has_features_field(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert "features" in resp.json()

    def test_has_deployment_field(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert "deployment" in resp.json()

    def test_overall_has_total_requests(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert "total_requests" in resp.json()["overall"]

    def test_deployment_has_uptime_seconds(self):
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert "uptime_seconds" in resp.json()["deployment"]

    def test_version_matches_package(self):
        import src
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert resp.json()["version"] == src.__version__

    def test_refused_without_api_key(self):
        """v1.81.4: operator-only (was on the public allow-list)."""
        resp = client.get("/admin/stats")
        assert resp.status_code == 401

    def test_customer_service_key_is_refused(self, tmp_path):
        import asyncio
        from unittest.mock import patch
        from src.services.billing_service import BillingService

        svc = BillingService(str(tmp_path / "billing.db"))
        asyncio.run(svc.initialize())
        customer = asyncio.run(svc.create_customer("someone@example.com"))
        with patch("src.main.get_billing_service", return_value=svc):
            resp = client.get("/admin/stats", headers={"x-api-key": customer.api_key})
        assert resp.status_code == 401

    def test_unconfigured_operator_key_fails_closed(self):
        import src.main as main_module
        main_module.settings.mcp_api_key = None  # fixture restores it
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        assert resp.status_code == 503

    def test_uptime_since_is_serialisable(self):
        """uptime_since must be a plain string, not a datetime object."""
        resp = client.get("/admin/stats", headers=OPERATOR_HEADERS)
        uptime_since = resp.json()["overall"].get("uptime_since")
        # If present, must be a JSON string, not an object
        assert uptime_since is None or isinstance(uptime_since, str)


# ── GET /admin/rate-limits ────────────────────────────────────────────────

@pytest.mark.usefixtures("operator_key_configured")
class TestAdminRateLimitsEndpoint:
    def test_returns_200_with_api_key(self):
        resp = client.get("/admin/rate-limits", headers=OPERATOR_HEADERS)
        assert resp.status_code == 200

    def test_returns_json(self):
        resp = client.get("/admin/rate-limits", headers=OPERATOR_HEADERS)
        assert isinstance(resp.json(), dict)

    def test_has_tracked_ips(self):
        resp = client.get("/admin/rate-limits", headers=OPERATOR_HEADERS)
        assert "tracked_ips" in resp.json()

    def test_has_limit_per_minute(self):
        resp = client.get("/admin/rate-limits", headers=OPERATOR_HEADERS)
        assert "limit_per_minute" in resp.json()

    def test_has_top_consumers(self):
        resp = client.get("/admin/rate-limits", headers=OPERATOR_HEADERS)
        assert "top_consumers" in resp.json()

    def test_has_snapshot_at(self):
        resp = client.get("/admin/rate-limits", headers=OPERATOR_HEADERS)
        assert "snapshot_at" in resp.json()

    def test_top_consumers_is_list(self):
        resp = client.get("/admin/rate-limits", headers=OPERATOR_HEADERS)
        assert isinstance(resp.json()["top_consumers"], list)

    def test_limit_per_minute_is_positive(self):
        resp = client.get("/admin/rate-limits", headers=OPERATOR_HEADERS)
        assert resp.json()["limit_per_minute"] > 0

    def test_refused_without_api_key(self):
        """v1.81.4: operator-only (was on the public allow-list)."""
        resp = client.get("/admin/rate-limits")
        assert resp.status_code == 401

    def test_customer_service_key_is_refused(self, tmp_path):
        import asyncio
        from unittest.mock import patch
        from src.services.billing_service import BillingService

        svc = BillingService(str(tmp_path / "billing.db"))
        asyncio.run(svc.initialize())
        customer = asyncio.run(svc.create_customer("someone@example.com"))
        with patch("src.main.get_billing_service", return_value=svc):
            resp = client.get("/admin/rate-limits", headers={"x-api-key": customer.api_key})
        assert resp.status_code == 401

    def test_unconfigured_operator_key_fails_closed(self):
        import src.main as main_module
        main_module.settings.mcp_api_key = None  # fixture restores it
        resp = client.get("/admin/rate-limits", headers=OPERATOR_HEADERS)
        assert resp.status_code == 503


# ── GET /admin/customers (v1.78.1: operator-only) ────────────────────────

class TestAdminCustomersIsOperatorOnly:
    """It lists every customer's email and org_id. A customer service key, which
    public signup hands to anyone, must not be enough to read it."""

    def _with_operator_key(self, fn):
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = _OPERATOR_KEY
            return fn()
        finally:
            main_module.settings.mcp_api_key = original

    def test_customer_service_key_is_refused(self, tmp_path):
        import asyncio
        from unittest.mock import patch
        from src.services.billing_service import BillingService

        svc = BillingService(str(tmp_path / "billing.db"))
        asyncio.run(svc.initialize())
        victim = asyncio.run(svc.create_customer("victim@example.com"))
        attacker = asyncio.run(svc.create_customer("attacker@example.com"))

        def call():
            with patch("src.main.get_billing_service", return_value=svc):
                return client.get("/admin/customers", headers={"x-api-key": attacker.api_key})

        resp = self._with_operator_key(call)
        assert resp.status_code == 401
        assert victim.org_id not in resp.text and "victim@example.com" not in resp.text

    def test_no_key_is_refused(self):
        resp = self._with_operator_key(lambda: client.get("/admin/customers"))
        assert resp.status_code == 401

    def test_operator_key_is_accepted(self):
        resp = self._with_operator_key(
            lambda: client.get("/admin/customers", headers={"x-api-key": _OPERATOR_KEY}))
        assert resp.status_code == 200
        assert "customers" in resp.json()

    def test_unconfigured_operator_key_fails_closed(self):
        import src.main as main_module
        original = main_module.settings.mcp_api_key
        try:
            main_module.settings.mcp_api_key = None
            resp = client.get("/admin/customers", headers=HEADERS)
        finally:
            main_module.settings.mcp_api_key = original
        assert resp.status_code == 503


def test_signup_goes_through_the_rate_limiter():
    """v1.78.1: signup mints keys and reveals whether an email is registered, so it is
    no longer on the full-bypass list; it is anonymous but rate-limited and body-capped."""
    import src.main as main_module
    assert "/billing/signup" not in main_module._unauthenticated_paths
    assert "/billing/signup" in main_module._anonymous_rate_limited_paths


def test_pricing_alert_registration_goes_through_the_rate_limiter():
    """v1.79.2: anonymous POST /pricing/alerts stores a row per call, so it is throttled
    and body-capped; GET/DELETE on the same path are operator-only, and the conversation
    list left the full bypass."""
    import src.main as main_module
    assert "/pricing/alerts" in main_module._anonymous_rate_limited_paths
    assert "/agent/conversations" not in main_module._unauthenticated_paths
    assert "/mcp" in main_module._public_body_capped_paths


# ── v1.81.4: smaller access and shutdown fixes ─────────────────────────────

def test_admin_data_endpoints_are_not_on_the_public_allow_list():
    import src.main as main_module
    for path in ("/admin/stats", "/admin/rate-limits"):
        assert path not in main_module._unauthenticated_paths
        assert path in main_module._sensitive_paths


@pytest.mark.usefixtures("operator_key_configured")
def test_ide_compare_page_is_public_when_operator_key_is_set():
    resp = client.get("/ide-compare/", follow_redirects=True)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_requests_during_shutdown_get_503_not_500():
    from unittest.mock import patch
    import src.main as main_module
    with patch.object(main_module.deployment_manager, "is_shutting_down", return_value=True):
        resp = client.get("/models")
        health = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "Service is shutting down"}
    assert health.status_code == 200


def test_tracking_refusal_gets_503_not_500():
    from unittest.mock import AsyncMock, patch
    import src.main as main_module
    with patch.object(main_module.deployment_manager, "track_request_start",
                      AsyncMock(side_effect=RuntimeError("draining"))):
        resp = client.get("/models")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "draining"}


# ── v1.83.0: Orchestrator block on /admin ──────────────────────────────────

class TestAdminOrchestratorBlock:
    def _html(self):
        return client.get("/admin/", follow_redirects=True).text

    def test_panel_ids_present(self):
        html = self._html()
        for el in ("orch-section", "orch-controls", "orch-budget", "orch-approvals-body", "orch-parked-body",
                   "orch-runs-body", "orch-pause-btn", "orch-mode-controls"):
            assert f'id="{el}"' in html, el

    def test_references_every_orchestrator_data_route(self):
        html = self._html()
        for path in ("/orchestrator/settings", "/orchestrator/budget", "/orchestrator/approvals?status=waiting",
                     "/orchestrator/parked", "/orchestrator/runs?", "/orchestrator/actions?format=csv"):
            assert path in html, path

    def test_offers_every_decision_and_dead_letter_control(self):
        html = self._html()
        for word in ("approve", "reject", "edit_approve", "done_manually", "skip", "resume", "close",
                     "global_pause", "mode_default."):
            assert word in html, word

    def test_orchestrator_paths_are_not_public(self):
        import src.main as main_module
        assert "/orchestrator/" in main_module._OPERATOR_ONLY_PREFIXES
        for path in main_module._unauthenticated_paths | main_module._anonymous_rate_limited_paths:
            assert not path.startswith("/orchestrator"), path
        assert "/orchestrator/" not in str(main_module._sensitive_paths)  # the prefix rule covers it

    def test_shared_nav_unchanged(self):
        html = self._html()
        assert '<a href="/whats-new">What\'s New</a>' in html


@pytest.mark.usefixtures("operator_key_configured")
def test_migration_guide_url_points_at_an_existing_doc():
    resp = client.get("/api/versions", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    prefix = "https://github.com/skakumanu/llm-pricing-mcp-server/blob/master/"
    url = resp.json()["migration_guide_url"]
    assert url.startswith(prefix)
    assert (project_root / url[len(prefix):]).exists()
