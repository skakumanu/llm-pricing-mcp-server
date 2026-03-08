"""Tests for PricingAlertService and the /pricing/alerts endpoints."""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402
from src.services.pricing_alerts import PricingAlertService  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# PricingAlertService unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_alerts.db")


@pytest_asyncio.fixture
async def svc(db_path):
    s = PricingAlertService(db_path)
    await s.initialize()
    return s


@pytest.mark.asyncio
async def test_initialize_creates_table(db_path):
    import aiosqlite
    s = PricingAlertService(db_path)
    await s.initialize()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pricing_alerts'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_register_returns_record(svc):
    record = await svc.register("https://example.com/hook", threshold_pct=5.0)
    assert record["id"] == 1
    assert record["url"] == "https://example.com/hook"
    assert record["threshold_pct"] == 5.0
    assert record["provider"] is None
    assert record["model_name"] is None


@pytest.mark.asyncio
async def test_register_with_filters(svc):
    record = await svc.register(
        "https://example.com/hook", threshold_pct=10.0,
        provider="openai", model_name="gpt-4o"
    )
    assert record["provider"] == "openai"
    assert record["model_name"] == "gpt-4o"


@pytest.mark.asyncio
async def test_list_alerts_empty(svc):
    assert await svc.list_alerts() == []


@pytest.mark.asyncio
async def test_list_alerts_returns_all(svc):
    await svc.register("https://a.com", 5.0)
    await svc.register("https://b.com", 10.0)
    alerts = await svc.list_alerts()
    assert len(alerts) == 2


@pytest.mark.asyncio
async def test_delete_existing_alert(svc):
    record = await svc.register("https://example.com", 5.0)
    deleted = await svc.delete(record["id"])
    assert deleted is True
    assert await svc.list_alerts() == []


@pytest.mark.asyncio
async def test_delete_nonexistent_alert(svc):
    deleted = await svc.delete(999)
    assert deleted is False


@pytest.mark.asyncio
async def test_check_and_fire_no_alerts(svc):
    trends = [{"model_name": "gpt-4o", "provider": "openai", "input_change_pct": 50.0,
               "output_change_pct": 50.0, "direction": "increased",
               "first_seen": 0.0, "last_seen": 1.0}]
    count = await svc.check_and_fire(trends)
    assert count == 0


@pytest.mark.asyncio
async def test_check_and_fire_below_threshold(svc):
    await svc.register("https://example.com/hook", threshold_pct=20.0)
    trends = [{"model_name": "gpt-4o", "provider": "openai", "input_change_pct": 5.0,
               "output_change_pct": 3.0, "direction": "increased",
               "first_seen": 0.0, "last_seen": 1.0}]
    count = await svc.check_and_fire(trends)
    assert count == 0


@pytest.mark.asyncio
async def test_check_and_fire_above_threshold(svc):
    await svc.register("https://example.com/hook", threshold_pct=10.0)
    trends = [{"model_name": "gpt-4o", "provider": "openai", "input_change_pct": 100.0,
               "output_change_pct": 100.0, "direction": "increased",
               "first_seen": 0.0, "last_seen": 1.0}]
    import httpx
    with patch("src.services.pricing_alerts.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client
        count = await svc.check_and_fire(trends)
    assert count == 1


@pytest.mark.asyncio
async def test_check_and_fire_provider_filter_mismatch(svc):
    await svc.register("https://example.com/hook", threshold_pct=5.0, provider="anthropic")
    trends = [{"model_name": "gpt-4o", "provider": "openai", "input_change_pct": 100.0,
               "output_change_pct": 100.0, "direction": "increased",
               "first_seen": 0.0, "last_seen": 1.0}]
    count = await svc.check_and_fire(trends)
    assert count == 0


@pytest.mark.asyncio
async def test_check_and_fire_model_filter_match(svc):
    await svc.register("https://example.com/hook", threshold_pct=5.0, model_name="gpt-4o")
    trends = [{"model_name": "gpt-4o", "provider": "openai", "input_change_pct": 50.0,
               "output_change_pct": 50.0, "direction": "increased",
               "first_seen": 0.0, "last_seen": 1.0}]
    import httpx
    with patch("src.services.pricing_alerts.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client
        count = await svc.check_and_fire(trends)
    assert count == 1


@pytest.mark.asyncio
async def test_check_and_fire_webhook_failure_does_not_raise(svc):
    """Webhook delivery errors are caught and logged; method returns 0."""
    await svc.register("https://bad-url.invalid/hook", threshold_pct=5.0)
    trends = [{"model_name": "gpt-4o", "provider": "openai", "input_change_pct": 50.0,
               "output_change_pct": 50.0, "direction": "increased",
               "first_seen": 0.0, "last_seen": 1.0}]
    with patch("src.services.pricing_alerts.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
        mock_client_cls.return_value = mock_client
        count = await svc.check_and_fire(trends)
    assert count == 0


# ---------------------------------------------------------------------------
# REST endpoint integration tests
# ---------------------------------------------------------------------------

def _patch_alert_service(alerts=None):
    mock_svc = MagicMock()
    _alerts = alerts or []
    mock_svc.register = AsyncMock(return_value={
        "id": 1, "url": "https://example.com/hook",
        "threshold_pct": 5.0, "provider": None, "model_name": None,
        "created_at": 1700000000.0,
    })
    mock_svc.list_alerts = AsyncMock(return_value=_alerts)
    mock_svc.delete = AsyncMock(return_value=True)
    return patch("src.main.get_pricing_alert_service", return_value=mock_svc)


class TestCreatePricingAlert:
    def test_returns_201(self):
        with _patch_alert_service():
            resp = client.post("/pricing/alerts", json={"url": "https://example.com/hook"})
        assert resp.status_code == 201

    def test_response_shape(self):
        with _patch_alert_service():
            resp = client.post("/pricing/alerts", json={
                "url": "https://example.com/hook",
                "threshold_pct": 10.0,
                "provider": "openai",
            })
        data = resp.json()
        assert data["id"] == 1
        assert data["url"] == "https://example.com/hook"

    def test_missing_url_returns_422(self):
        resp = client.post("/pricing/alerts", json={"threshold_pct": 5.0})
        assert resp.status_code == 422

    def test_zero_threshold_returns_422(self):
        resp = client.post("/pricing/alerts", json={
            "url": "https://example.com/hook", "threshold_pct": 0
        })
        assert resp.status_code == 422

    def test_negative_threshold_returns_422(self):
        resp = client.post("/pricing/alerts", json={
            "url": "https://example.com/hook", "threshold_pct": -5
        })
        assert resp.status_code == 422


class TestListPricingAlerts:
    def test_returns_200_empty(self):
        with _patch_alert_service():
            resp = client.get("/pricing/alerts")
        assert resp.status_code == 200
        assert resp.json() == {"alerts": [], "total": 0}

    def test_returns_all_alerts(self):
        stored = [{"id": 1, "url": "https://a.com", "threshold_pct": 5.0,
                   "provider": None, "model_name": None, "created_at": 1700000000.0}]
        with _patch_alert_service(alerts=stored):
            resp = client.get("/pricing/alerts")
        data = resp.json()
        assert data["total"] == 1
        assert data["alerts"][0]["url"] == "https://a.com"


class TestDeletePricingAlert:
    def test_returns_204_on_success(self):
        with _patch_alert_service():
            resp = client.delete("/pricing/alerts/1")
        assert resp.status_code == 204

    def test_returns_404_when_not_found(self):
        with _patch_alert_service() as mock_patch:
            mock_patch.return_value.delete = AsyncMock(return_value=False)
            resp = client.delete("/pricing/alerts/999")
        assert resp.status_code == 404
