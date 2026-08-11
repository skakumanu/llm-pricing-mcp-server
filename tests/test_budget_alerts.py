"""Tests for BudgetAlertService, the /usage/alerts endpoints, and the budget alert MCP tools."""
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
from src.services.budget_alerts import BudgetAlertService  # noqa: E402
from mcp.tools.register_budget_alert import RegisterBudgetAlertTool  # noqa: E402
from mcp.tools.list_budget_alerts import ListBudgetAlertsTool  # noqa: E402
from mcp.tools.delete_budget_alert import DeleteBudgetAlertTool  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# BudgetAlertService unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_budget_alerts.db")


@pytest_asyncio.fixture
async def svc(db_path):
    s = BudgetAlertService(db_path)
    await s.initialize()
    return s


def _mock_tracker(total_cost_usd, total_requests=1):
    tracker = MagicMock()
    tracker.get_summary = AsyncMock(return_value={
        "org_id": None, "days": 30, "total_requests": total_requests,
        "total_cost_usd": total_cost_usd, "total_input_tokens": 0, "total_output_tokens": 0,
        "by_model": [], "by_provider": [],
    })
    return tracker


def _patch_httpx_success():
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_client.post = AsyncMock(return_value=mock_resp)
    return patch("src.services.budget_alerts.httpx.AsyncClient", return_value=mock_client)


@pytest.mark.asyncio
async def test_initialize_creates_table(db_path):
    import aiosqlite
    s = BudgetAlertService(db_path)
    await s.initialize()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='budget_alerts'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_register_returns_record(svc):
    record = await svc.register("https://example.com/hook", threshold_usd=100.0)
    assert record["id"] == 1
    assert record["url"] == "https://example.com/hook"
    assert record["threshold_usd"] == 100.0
    assert record["org_id"] is None
    assert record["period_days"] == 30
    assert record["last_fired_at"] is None


@pytest.mark.asyncio
async def test_register_with_org_and_period(svc):
    record = await svc.register(
        "https://example.com/hook", threshold_usd=50.0, org_id="acme", period_days=7,
    )
    assert record["org_id"] == "acme"
    assert record["period_days"] == 7


@pytest.mark.asyncio
async def test_list_alerts_empty(svc):
    assert await svc.list_alerts() == []


@pytest.mark.asyncio
async def test_list_alerts_returns_all(svc):
    await svc.register("https://a.com", 10.0)
    await svc.register("https://b.com", 20.0)
    alerts = await svc.list_alerts()
    assert len(alerts) == 2


@pytest.mark.asyncio
async def test_delete_existing_alert(svc):
    record = await svc.register("https://example.com", 10.0)
    deleted = await svc.delete(record["id"])
    assert deleted is True
    assert await svc.list_alerts() == []


@pytest.mark.asyncio
async def test_delete_nonexistent_alert(svc):
    assert await svc.delete(999) is False


@pytest.mark.asyncio
async def test_check_and_fire_no_alerts(svc):
    count = await svc.check_and_fire(_mock_tracker(1000.0))
    assert count == 0


@pytest.mark.asyncio
async def test_check_and_fire_below_threshold(svc):
    await svc.register("https://example.com/hook", threshold_usd=100.0)
    count = await svc.check_and_fire(_mock_tracker(50.0))
    assert count == 0


@pytest.mark.asyncio
async def test_check_and_fire_above_threshold(svc):
    await svc.register("https://example.com/hook", threshold_usd=100.0)
    with _patch_httpx_success():
        count = await svc.check_and_fire(_mock_tracker(150.0))
    assert count == 1


@pytest.mark.asyncio
async def test_check_and_fire_sets_last_fired_at(svc):
    record = await svc.register("https://example.com/hook", threshold_usd=100.0)
    with _patch_httpx_success():
        await svc.check_and_fire(_mock_tracker(150.0))
    alerts = await svc.list_alerts()
    assert alerts[0]["id"] == record["id"]
    assert alerts[0]["last_fired_at"] is not None


@pytest.mark.asyncio
async def test_check_and_fire_cooldown_prevents_refire(svc):
    """A second check within period_days does not re-fire even though spend is still over threshold."""
    await svc.register("https://example.com/hook", threshold_usd=100.0, period_days=30)
    with _patch_httpx_success():
        first = await svc.check_and_fire(_mock_tracker(150.0))
        second = await svc.check_and_fire(_mock_tracker(160.0))
    assert first == 1
    assert second == 0


@pytest.mark.asyncio
async def test_check_and_fire_refires_after_cooldown_expires(svc):
    record = await svc.register("https://example.com/hook", threshold_usd=100.0, period_days=1)
    # Simulate the alert having already fired long enough ago that the cooldown has expired.
    import aiosqlite
    old_fire_time = time.time() - 2 * 86400
    async with aiosqlite.connect(svc._db_path) as db:
        await db.execute(
            "UPDATE budget_alerts SET last_fired_at = ? WHERE id = ?",
            (old_fire_time, record["id"]),
        )
        await db.commit()
    with _patch_httpx_success():
        count = await svc.check_and_fire(_mock_tracker(150.0))
    assert count == 1


@pytest.mark.asyncio
async def test_check_and_fire_queries_alerts_own_org_and_period(svc):
    """check_and_fire passes each alert's own org_id/period_days to get_summary, not shared values."""
    await svc.register("https://example.com/hook", threshold_usd=10.0, org_id="acme", period_days=7)
    tracker = _mock_tracker(50.0)
    with _patch_httpx_success():
        await svc.check_and_fire(tracker)
    tracker.get_summary.assert_awaited_once_with(org_id="acme", days=7)


@pytest.mark.asyncio
async def test_check_and_fire_webhook_failure_does_not_raise(svc):
    await svc.register("https://bad-url.invalid/hook", threshold_usd=10.0)
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
    with patch("src.services.budget_alerts.httpx.AsyncClient", return_value=mock_client):
        count = await svc.check_and_fire(_mock_tracker(50.0))
    assert count == 0


# ---------------------------------------------------------------------------
# /usage/alerts REST endpoint integration tests
# ---------------------------------------------------------------------------

def _patch_budget_alert_service(alerts=None):
    mock_svc = MagicMock()
    _alerts = alerts or []
    mock_svc.register = AsyncMock(return_value={
        "id": 1, "url": "https://example.com/hook", "org_id": None,
        "threshold_usd": 100.0, "period_days": 30,
        "created_at": 1700000000.0, "last_fired_at": None,
    })
    mock_svc.list_alerts = AsyncMock(return_value=_alerts)
    mock_svc.delete = AsyncMock(return_value=True)
    return patch("src.main.get_budget_alert_service", return_value=mock_svc)


class TestCreateBudgetAlert:
    def test_returns_201(self):
        with _patch_budget_alert_service():
            resp = client.post("/usage/alerts", json={
                "url": "https://example.com/hook", "threshold_usd": 100.0,
            })
        assert resp.status_code == 201

    def test_response_shape(self):
        with _patch_budget_alert_service():
            resp = client.post("/usage/alerts", json={
                "url": "https://example.com/hook", "threshold_usd": 100.0, "org_id": "acme",
            })
        data = resp.json()
        assert data["id"] == 1
        assert data["url"] == "https://example.com/hook"

    def test_missing_url_returns_422(self):
        resp = client.post("/usage/alerts", json={"threshold_usd": 100.0})
        assert resp.status_code == 422

    def test_zero_threshold_returns_422(self):
        resp = client.post("/usage/alerts", json={
            "url": "https://example.com/hook", "threshold_usd": 0,
        })
        assert resp.status_code == 422

    def test_negative_threshold_returns_422(self):
        resp = client.post("/usage/alerts", json={
            "url": "https://example.com/hook", "threshold_usd": -10,
        })
        assert resp.status_code == 422


class TestListBudgetAlerts:
    def test_returns_200_empty(self):
        with _patch_budget_alert_service():
            resp = client.get("/usage/alerts")
        assert resp.status_code == 200
        assert resp.json() == {"alerts": [], "total": 0}

    def test_returns_all_alerts(self):
        stored = [{"id": 1, "url": "https://a.com", "org_id": None, "threshold_usd": 50.0,
                   "period_days": 30, "created_at": 1700000000.0, "last_fired_at": None}]
        with _patch_budget_alert_service(alerts=stored):
            resp = client.get("/usage/alerts")
        data = resp.json()
        assert data["total"] == 1
        assert data["alerts"][0]["url"] == "https://a.com"


class TestDeleteBudgetAlert:
    def test_returns_204_on_success(self):
        with _patch_budget_alert_service():
            resp = client.delete("/usage/alerts/1")
        assert resp.status_code == 204

    def test_returns_404_when_not_found(self):
        with _patch_budget_alert_service() as mock_patch:
            mock_patch.return_value.delete = AsyncMock(return_value=False)
            resp = client.delete("/usage/alerts/999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RegisterBudgetAlertTool / ListBudgetAlertsTool / DeleteBudgetAlertTool (MCP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_budget_alert_tool_missing_url():
    tool = RegisterBudgetAlertTool()
    result = await tool.execute({"threshold_usd": 100.0})
    assert result["success"] is False
    assert "url" in result["error"]


@pytest.mark.asyncio
async def test_register_budget_alert_tool_missing_threshold():
    tool = RegisterBudgetAlertTool()
    result = await tool.execute({"url": "https://example.com/hook"})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_register_budget_alert_tool_zero_threshold():
    tool = RegisterBudgetAlertTool()
    result = await tool.execute({"url": "https://example.com/hook", "threshold_usd": 0})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_register_budget_alert_tool_success():
    tool = RegisterBudgetAlertTool()
    mock_svc = MagicMock()
    mock_svc.register = AsyncMock(return_value={
        "id": 1, "url": "https://example.com/hook", "org_id": "acme",
        "threshold_usd": 100.0, "period_days": 30,
        "created_at": 1700000000.0, "last_fired_at": None,
    })
    with patch("mcp.tools.register_budget_alert.get_budget_alert_service", return_value=mock_svc):
        result = await tool.execute({
            "url": "https://example.com/hook", "threshold_usd": 100.0, "org_id": "acme",
        })
    assert result["success"] is True
    assert result["id"] == 1
    mock_svc.register.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_budget_alerts_tool_success():
    tool = ListBudgetAlertsTool()
    mock_svc = MagicMock()
    mock_svc.list_alerts = AsyncMock(return_value=[{"id": 1, "url": "https://a.com"}])
    with patch("mcp.tools.list_budget_alerts.get_budget_alert_service", return_value=mock_svc):
        result = await tool.execute({})
    assert result["success"] is True
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_delete_budget_alert_tool_missing_id():
    tool = DeleteBudgetAlertTool()
    result = await tool.execute({})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_delete_budget_alert_tool_success():
    tool = DeleteBudgetAlertTool()
    mock_svc = MagicMock()
    mock_svc.delete = AsyncMock(return_value=True)
    with patch("mcp.tools.delete_budget_alert.get_budget_alert_service", return_value=mock_svc):
        result = await tool.execute({"alert_id": 1})
    assert result["success"] is True
    assert result["deleted"] is True


@pytest.mark.asyncio
async def test_delete_budget_alert_tool_not_found():
    tool = DeleteBudgetAlertTool()
    mock_svc = MagicMock()
    mock_svc.delete = AsyncMock(return_value=False)
    with patch("mcp.tools.delete_budget_alert.get_budget_alert_service", return_value=mock_svc):
        result = await tool.execute({"alert_id": 999})
    assert result["success"] is False
    assert result["deleted"] is False


# ---------------------------------------------------------------------------
# /usage triggers a budget-alert check
# ---------------------------------------------------------------------------

def test_record_usage_triggers_budget_alert_check():
    mock_usage_tracker = MagicMock()
    mock_usage_tracker.record_event = AsyncMock(return_value={"duplicate": False})
    mock_budget_svc = MagicMock()
    mock_budget_svc.check_and_fire = AsyncMock(return_value=0)

    with patch("src.main.get_usage_tracker", return_value=mock_usage_tracker), \
         patch("src.main.get_budget_alert_service", return_value=mock_budget_svc):
        resp = client.post("/usage", json={
            "model_name": "gpt-4o-mini", "input_tokens": 100, "output_tokens": 50,
        })
    assert resp.status_code == 200
    mock_budget_svc.check_and_fire.assert_awaited_once()


def test_record_usage_budget_alert_failure_does_not_break_response():
    """If the budget-alert check itself errors, /usage still returns successfully."""
    mock_usage_tracker = MagicMock()
    mock_usage_tracker.record_event = AsyncMock(return_value={"duplicate": False})
    mock_budget_svc = MagicMock()
    mock_budget_svc.check_and_fire = AsyncMock(side_effect=Exception("boom"))

    with patch("src.main.get_usage_tracker", return_value=mock_usage_tracker), \
         patch("src.main.get_budget_alert_service", return_value=mock_budget_svc):
        resp = client.post("/usage", json={
            "model_name": "gpt-4o-mini", "input_tokens": 100, "output_tokens": 50,
        })
    assert resp.status_code == 200
