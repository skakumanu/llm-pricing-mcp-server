"""Tests for SavingsTrackerService and the /telemetry/savings endpoint."""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.services.savings_tracker import SavingsTrackerService  # noqa: E402


# ---------------------------------------------------------------------------
# SavingsTrackerService unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_savings.db")


@pytest_asyncio.fixture
async def svc(db_path):
    s = SavingsTrackerService(db_path)
    await s.initialize()
    return s


@pytest.mark.asyncio
async def test_initialize_creates_table(db_path):
    import aiosqlite
    svc = SavingsTrackerService(db_path)
    await svc.initialize()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='routing_savings'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_record_routing_inserts_row(svc):
    await svc.record_routing(
        recommended_model="gpt-4o-mini",
        recommended_provider="openai",
        recommended_cost_per_1m=0.375,
        org_id="acme",
        api_key_tier="free",
        baseline_model="gpt-4o",
        baseline_cost_per_1m=10.0,
        task_type="chat",
    )
    result = await svc.get_savings(org_id="acme", days=1)
    assert result["total"] == 1
    assert result["records"][0]["recommended_model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_savings_calculated_correctly(svc):
    await svc.record_routing(
        recommended_model="cheap",
        recommended_provider="groq",
        recommended_cost_per_1m=1.0,
        baseline_model="expensive",
        baseline_cost_per_1m=10.0,
    )
    result = await svc.get_savings(days=1)
    assert result["records"][0]["savings_per_1m"] == pytest.approx(9.0)
    assert result["total_savings_per_1m"] == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_no_savings_when_no_baseline(svc):
    await svc.record_routing(
        recommended_model="gpt-4o-mini",
        recommended_provider="openai",
        recommended_cost_per_1m=0.375,
    )
    result = await svc.get_savings(days=1)
    assert result["records"][0]["savings_per_1m"] is None


@pytest.mark.asyncio
async def test_org_filter(svc):
    await svc.record_routing("m1", "openai", 1.0, org_id="org-a")
    await svc.record_routing("m2", "anthropic", 2.0, org_id="org-b")

    result_a = await svc.get_savings(org_id="org-a", days=1)
    result_b = await svc.get_savings(org_id="org-b", days=1)
    result_all = await svc.get_savings(days=1)

    assert result_a["total"] == 1
    assert result_b["total"] == 1
    assert result_all["total"] == 2


@pytest.mark.asyncio
async def test_days_filter(svc):
    import aiosqlite
    # Insert a record with an old timestamp directly
    old_ts = time.time() - 40 * 86400  # 40 days ago
    async with aiosqlite.connect(svc._db_path) as db:
        await db.execute(
            "INSERT INTO routing_savings "
            "(org_id, api_key_tier, requested_at, recommended_model, recommended_provider, "
            "recommended_cost_per_1m, baseline_model, baseline_cost_per_1m, savings_per_1m, task_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (None, None, old_ts, "old-model", "openai", 1.0, None, None, None, None),
        )
        await db.commit()

    # Recent record
    await svc.record_routing("recent-model", "openai", 1.0)

    result_30d = await svc.get_savings(days=30)
    assert result_30d["total"] == 1  # Only the recent one

    result_60d = await svc.get_savings(days=60)
    assert result_60d["total"] == 2  # Both


@pytest.mark.asyncio
async def test_limit_parameter(svc):
    for i in range(5):
        await svc.record_routing(f"model-{i}", "openai", float(i))
    result = await svc.get_savings(days=1, limit=3)
    assert len(result["records"]) == 3
    assert result["total"] == 5


# ---------------------------------------------------------------------------
# /telemetry/savings endpoint integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_savings_tracker():
    mock = MagicMock()
    mock.get_savings = AsyncMock(return_value={
        "records": [
            {
                "id": 1,
                "org_id": "acme",
                "api_key_tier": "pro",
                "requested_at": time.time(),
                "recommended_model": "gpt-4o-mini",
                "recommended_provider": "openai",
                "recommended_cost_per_1m": 0.375,
                "baseline_model": "gpt-4o",
                "baseline_cost_per_1m": 10.0,
                "savings_per_1m": 9.625,
                "task_type": "chat",
            }
        ],
        "total": 1,
        "total_savings_per_1m": 9.625,
    })
    return mock


def test_telemetry_savings_endpoint(mock_savings_tracker):
    from src.main import app
    client = TestClient(app)
    with patch("src.main.get_savings_tracker", return_value=mock_savings_tracker):
        resp = client.get(
            "/telemetry/savings?org_id=acme&days=7",
            headers={"x-api-key": "test"},
        )
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        data = resp.json()
        assert "records" in data
        assert "total_savings_per_1m" in data


def test_telemetry_savings_no_org_filter(mock_savings_tracker):
    from src.main import app
    client = TestClient(app)
    with patch("src.main.get_savings_tracker", return_value=mock_savings_tracker):
        resp = client.get("/telemetry/savings", headers={"x-api-key": "test"})
    assert resp.status_code in (200, 401)
