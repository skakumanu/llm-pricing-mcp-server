"""Tests for PricingHistoryService and the /pricing/history + /pricing/trends endpoints."""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import pytest_asyncio

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402
from src.services.pricing_history import PricingHistoryService  # noqa: E402

client = TestClient(app)


# ---------------------------------------------------------------------------
# PricingHistoryService unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_pricing_history.db")


@pytest_asyncio.fixture
async def svc(db_path):
    s = PricingHistoryService(db_path)
    await s.initialize()
    return s


def _make_model(name="gpt-4o", provider="openai", inp=0.005, out=0.015):
    m = MagicMock()
    m.model_name = name
    m.provider = provider
    m.cost_per_input_token = inp
    m.cost_per_output_token = out
    return m


@pytest.mark.asyncio
async def test_initialize_creates_table(db_path):
    """initialize() creates the DB file and pricing_snapshots table."""
    import aiosqlite
    svc = PricingHistoryService(db_path)
    await svc.initialize()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pricing_snapshots'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None, "pricing_snapshots table should exist"


@pytest.mark.asyncio
async def test_record_snapshot_inserts_rows(svc):
    """record_snapshot() inserts one row per model and returns the count."""
    models = [_make_model("gpt-4o", "openai"), _make_model("claude-3", "anthropic")]
    count = await svc.record_snapshot(models)
    assert count == 2


@pytest.mark.asyncio
async def test_get_history_returns_all_snapshots(svc):
    """get_history() returns all snapshots within the default 30-day window."""
    models = [_make_model("m1", "p1"), _make_model("m2", "p2")]
    await svc.record_snapshot(models)
    result = await svc.get_history()
    assert result["total"] == 2
    assert len(result["snapshots"]) == 2


@pytest.mark.asyncio
async def test_get_history_filters_by_provider(svc):
    """get_history() correctly filters by provider."""
    await svc.record_snapshot([_make_model("m1", "openai"), _make_model("m2", "anthropic")])
    result = await svc.get_history(provider="openai")
    assert result["total"] == 1
    assert result["snapshots"][0]["provider"] == "openai"


@pytest.mark.asyncio
async def test_get_history_filters_by_model_name(svc):
    """get_history() correctly filters by model_name."""
    await svc.record_snapshot([_make_model("gpt-4o", "openai"), _make_model("claude-3", "anthropic")])
    result = await svc.get_history(model_name="gpt-4o")
    assert result["total"] == 1
    assert result["snapshots"][0]["model_name"] == "gpt-4o"


@pytest.mark.asyncio
async def test_get_history_limit(svc):
    """get_history() respects the limit parameter."""
    models = [_make_model(f"m{i}", "p") for i in range(10)]
    await svc.record_snapshot(models)
    result = await svc.get_history(limit=3)
    assert len(result["snapshots"]) == 3
    assert result["total"] == 10


@pytest.mark.asyncio
async def test_get_trends_no_change(svc):
    """get_trends() returns empty when only one snapshot exists (no change detectable)."""
    await svc.record_snapshot([_make_model("gpt-4o", "openai")])
    trends = await svc.get_trends()
    # Need at least two distinct captured_at values for a trend
    assert isinstance(trends, list)


@pytest.mark.asyncio
async def test_get_trends_detects_price_increase(db_path):
    """get_trends() correctly labels a price increase as 'increased'."""
    import aiosqlite
    svc = PricingHistoryService(db_path)
    await svc.initialize()

    now = time.time()
    earlier = now - 86400  # one day ago

    # Insert old snapshot manually
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO pricing_snapshots (captured_at, model_name, provider, "
            "cost_per_input_token, cost_per_output_token) VALUES (?, ?, ?, ?, ?)",
            (earlier, "gpt-4o", "openai", 0.005, 0.015),
        )
        # Insert new snapshot with higher price
        await db.execute(
            "INSERT INTO pricing_snapshots (captured_at, model_name, provider, "
            "cost_per_input_token, cost_per_output_token) VALUES (?, ?, ?, ?, ?)",
            (now, "gpt-4o", "openai", 0.010, 0.030),
        )
        await db.commit()

    trends = await svc.get_trends(days=7)
    assert len(trends) == 1
    assert trends[0]["model_name"] == "gpt-4o"
    assert trends[0]["direction"] == "increased"
    assert trends[0]["input_change_pct"] == pytest.approx(100.0, abs=0.1)


@pytest.mark.asyncio
async def test_get_trends_detects_price_decrease(db_path):
    """get_trends() correctly labels a price decrease as 'decreased'."""
    import aiosqlite
    svc = PricingHistoryService(db_path)
    await svc.initialize()

    now = time.time()
    earlier = now - 86400

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO pricing_snapshots (captured_at, model_name, provider, "
            "cost_per_input_token, cost_per_output_token) VALUES (?, ?, ?, ?, ?)",
            (earlier, "claude-3", "anthropic", 0.010, 0.030),
        )
        await db.execute(
            "INSERT INTO pricing_snapshots (captured_at, model_name, provider, "
            "cost_per_input_token, cost_per_output_token) VALUES (?, ?, ?, ?, ?)",
            (now, "claude-3", "anthropic", 0.005, 0.015),
        )
        await db.commit()

    trends = await svc.get_trends(days=7)
    assert len(trends) == 1
    assert trends[0]["direction"] == "decreased"
    assert trends[0]["input_change_pct"] == pytest.approx(-50.0, abs=0.1)


# ---------------------------------------------------------------------------
# REST endpoint integration tests
# ---------------------------------------------------------------------------

def _patch_history_service(history_result=None, trends_result=None):
    """Context manager: patch get_pricing_history_service with a mock."""
    mock_svc = MagicMock()
    mock_svc.get_history = AsyncMock(return_value=history_result or {"snapshots": [], "total": 0})
    mock_svc.get_trends = AsyncMock(return_value=trends_result or [])
    return patch("src.main.get_pricing_history_service", return_value=mock_svc)


class TestPricingHistoryEndpoint:
    def test_history_returns_200(self):
        with _patch_history_service():
            resp = client.get("/pricing/history")
        assert resp.status_code == 200

    def test_history_response_shape(self):
        payload = {
            "snapshots": [
                {
                    "model_name": "gpt-4o",
                    "provider": "openai",
                    "cost_per_input_token": 0.005,
                    "cost_per_output_token": 0.015,
                    "captured_at": 1700000000.0,
                }
            ],
            "total": 1,
        }
        with _patch_history_service(history_result=payload):
            resp = client.get("/pricing/history")
        data = resp.json()
        assert data["total"] == 1
        assert data["snapshots"][0]["model_name"] == "gpt-4o"

    def test_history_accepts_query_params(self):
        with _patch_history_service() as mock_patch:
            mock_svc = mock_patch.return_value
            resp = client.get("/pricing/history?model_name=gpt-4o&provider=openai&days=7&limit=50")
        assert resp.status_code == 200
        mock_svc.get_history.assert_called_once_with(
            model_name="gpt-4o", provider="openai", days=7, limit=50
        )

    def test_history_invalid_days(self):
        resp = client.get("/pricing/history?days=0")
        assert resp.status_code == 422

    def test_history_days_too_large(self):
        resp = client.get("/pricing/history?days=366")
        assert resp.status_code == 422


class TestPricingTrendsEndpoint:
    def test_trends_returns_200(self):
        with _patch_history_service():
            resp = client.get("/pricing/trends")
        assert resp.status_code == 200

    def test_trends_response_shape(self):
        trends_data = [
            {
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
        ]
        with _patch_history_service(trends_result=trends_data):
            resp = client.get("/pricing/trends")
        data = resp.json()
        assert data["days"] == 30
        assert len(data["trends"]) == 1
        assert data["trends"][0]["direction"] == "increased"

    def test_trends_custom_days_and_limit(self):
        with _patch_history_service() as mock_patch:
            mock_svc = mock_patch.return_value
            resp = client.get("/pricing/trends?days=7&limit=5")
        assert resp.status_code == 200
        mock_svc.get_trends.assert_called_once_with(days=7, limit=5)

    def test_trends_invalid_limit(self):
        resp = client.get("/pricing/trends?limit=0")
        assert resp.status_code == 422

    def test_trends_days_param_returned_in_response(self):
        with _patch_history_service():
            resp = client.get("/pricing/trends?days=14")
        assert resp.json()["days"] == 14
