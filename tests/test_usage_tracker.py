"""Tests for UsageTrackerService, the /usage endpoints, and the usage MCP tools."""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.services.usage_tracker import UsageTrackerService  # noqa: E402
from mcp.tools.record_usage import RecordUsageTool  # noqa: E402
from mcp.tools.get_usage_summary import GetUsageSummaryTool  # noqa: E402


# ---------------------------------------------------------------------------
# UsageTrackerService unit tests
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_usage.db")


@pytest_asyncio.fixture
async def svc(db_path):
    s = UsageTrackerService(db_path)
    await s.initialize()
    return s


@pytest.mark.asyncio
async def test_initialize_creates_table(db_path):
    import aiosqlite
    svc = UsageTrackerService(db_path)
    await svc.initialize()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='usage_events'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_record_event_inserts_row(svc):
    result = await svc.record_event(
        provider="openai",
        model_name="gpt-4o-mini",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.001,
        org_id="acme",
    )
    assert result["duplicate"] is False

    summary = await svc.get_summary(org_id="acme", days=1)
    assert summary["total_requests"] == 1
    assert summary["total_cost_usd"] == pytest.approx(0.001)
    assert summary["total_input_tokens"] == 1000
    assert summary["total_output_tokens"] == 500


@pytest.mark.asyncio
async def test_duplicate_request_id_is_ignored(svc):
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini",
        input_tokens=100, output_tokens=50, cost_usd=0.01,
        org_id="acme", request_id="req-1",
    )
    result = await svc.record_event(
        provider="openai", model_name="gpt-4o-mini",
        input_tokens=100, output_tokens=50, cost_usd=0.01,
        org_id="acme", request_id="req-1",
    )
    assert result["duplicate"] is True

    summary = await svc.get_summary(org_id="acme", days=1)
    assert summary["total_requests"] == 1


@pytest.mark.asyncio
async def test_same_request_id_different_org_not_deduped(svc):
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini",
        input_tokens=100, output_tokens=50, cost_usd=0.01,
        org_id="org-a", request_id="shared-id",
    )
    result = await svc.record_event(
        provider="openai", model_name="gpt-4o-mini",
        input_tokens=100, output_tokens=50, cost_usd=0.01,
        org_id="org-b", request_id="shared-id",
    )
    assert result["duplicate"] is False


@pytest.mark.asyncio
async def test_org_filter(svc):
    await svc.record_event(
        provider="openai", model_name="m1", input_tokens=10, output_tokens=10,
        cost_usd=1.0, org_id="org-a",
    )
    await svc.record_event(
        provider="anthropic", model_name="m2", input_tokens=10, output_tokens=10,
        cost_usd=2.0, org_id="org-b",
    )

    result_a = await svc.get_summary(org_id="org-a", days=1)
    result_b = await svc.get_summary(org_id="org-b", days=1)
    result_all = await svc.get_summary(days=1)

    assert result_a["total_requests"] == 1
    assert result_a["total_cost_usd"] == pytest.approx(1.0)
    assert result_b["total_requests"] == 1
    assert result_all["total_requests"] == 2


@pytest.mark.asyncio
async def test_days_filter(svc):
    old_ts = time.time() - 40 * 86400
    await svc.record_event(
        provider="openai", model_name="old-model", input_tokens=1, output_tokens=1,
        cost_usd=1.0, occurred_at=old_ts,
    )
    await svc.record_event(
        provider="openai", model_name="recent-model", input_tokens=1, output_tokens=1,
        cost_usd=1.0,
    )

    result_30d = await svc.get_summary(days=30)
    assert result_30d["total_requests"] == 1

    result_60d = await svc.get_summary(days=60)
    assert result_60d["total_requests"] == 2


@pytest.mark.asyncio
async def test_summary_breakdowns_by_model_and_provider(svc):
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=10, output_tokens=10,
        cost_usd=1.0,
    )
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=10, output_tokens=10,
        cost_usd=1.0,
    )
    await svc.record_event(
        provider="anthropic", model_name="claude-haiku", input_tokens=10, output_tokens=10,
        cost_usd=0.5,
    )

    summary = await svc.get_summary(days=1)
    by_model = {row["model_name"]: row for row in summary["by_model"]}
    by_provider = {row["provider"]: row for row in summary["by_provider"]}

    assert by_model["gpt-4o-mini"]["request_count"] == 2
    assert by_model["gpt-4o-mini"]["total_cost_usd"] == pytest.approx(2.0)
    assert by_provider["openai"]["total_cost_usd"] == pytest.approx(2.0)
    assert by_provider["anthropic"]["total_cost_usd"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_empty_summary(svc):
    summary = await svc.get_summary(days=30)
    assert summary["total_requests"] == 0
    assert summary["total_cost_usd"] == 0.0
    assert summary["by_model"] == []
    assert summary["by_provider"] == []


# ---------------------------------------------------------------------------
# /usage endpoint integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_usage_tracker():
    mock = MagicMock()
    mock.record_event = AsyncMock(return_value={"duplicate": False})
    mock.get_summary = AsyncMock(return_value={
        "org_id": None,
        "days": 30,
        "total_requests": 1,
        "total_cost_usd": 0.01,
        "total_input_tokens": 100,
        "total_output_tokens": 50,
        "by_model": [],
        "by_provider": [],
    })
    return mock


def test_record_usage_endpoint(mock_usage_tracker):
    from src.main import app
    client = TestClient(app)
    with patch("src.main.get_usage_tracker", return_value=mock_usage_tracker):
        resp = client.post("/usage", json={
            "model_name": "gpt-4o-mini",
            "input_tokens": 1000,
            "output_tokens": 500,
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["recorded"] is True
    assert data["duplicate"] is False
    assert data["model_name"]
    assert data["cost_usd"] >= 0
    mock_usage_tracker.record_event.assert_awaited_once()


def test_record_usage_unknown_model(mock_usage_tracker):
    from src.main import app
    client = TestClient(app)
    with patch("src.main.get_usage_tracker", return_value=mock_usage_tracker):
        resp = client.post("/usage", json={
            "model_name": "definitely-not-a-real-model-xyz",
            "input_tokens": 100,
            "output_tokens": 50,
        })
    assert resp.status_code == 404


def test_record_usage_batch_endpoint(mock_usage_tracker):
    from src.main import app
    client = TestClient(app)
    with patch("src.main.get_usage_tracker", return_value=mock_usage_tracker):
        resp = client.post("/usage/batch", json={
            "events": [
                {"model_name": "gpt-4o-mini", "input_tokens": 100, "output_tokens": 50},
                {"model_name": "not-a-real-model", "input_tokens": 100, "output_tokens": 50},
            ]
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["recorded"] == 1
    assert data["failed"] == 1
    assert len(data["errors"]) == 1


def test_usage_summary_endpoint(mock_usage_tracker):
    from src.main import app
    client = TestClient(app)
    with patch("src.main.get_usage_tracker", return_value=mock_usage_tracker):
        resp = client.get("/usage/summary?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_cost_usd" in data
    assert "by_model" in data


def test_usage_summary_org_filter(mock_usage_tracker):
    from src.main import app
    client = TestClient(app)
    with patch("src.main.get_usage_tracker", return_value=mock_usage_tracker):
        resp = client.get("/usage/summary?org_id=acme&days=7")
    assert resp.status_code == 200
    mock_usage_tracker.get_summary.assert_awaited_once()
    _, kwargs = mock_usage_tracker.get_summary.await_args
    assert kwargs["org_id"] == "acme"


# ---------------------------------------------------------------------------
# RecordUsageTool / GetUsageSummaryTool (MCP) unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_usage_tool_missing_model_name():
    tool = RecordUsageTool()
    result = await tool.execute({"input_tokens": 10, "output_tokens": 10})
    assert result["success"] is False
    assert "model_name" in result["error"]


@pytest.mark.asyncio
async def test_record_usage_tool_negative_tokens():
    tool = RecordUsageTool()
    result = await tool.execute({
        "model_name": "gpt-4o-mini", "input_tokens": -1, "output_tokens": 10,
    })
    assert result["success"] is False


@pytest.mark.asyncio
async def test_record_usage_tool_unknown_model():
    tool = RecordUsageTool()
    result = await tool.execute({
        "model_name": "definitely-not-a-real-model-xyz",
        "input_tokens": 10,
        "output_tokens": 10,
    })
    assert result["success"] is False
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_record_usage_tool_success():
    tool = RecordUsageTool()
    mock_tracker = MagicMock()
    mock_tracker.record_event = AsyncMock(return_value={"duplicate": False})
    with patch("mcp.tools.record_usage.get_usage_tracker", return_value=mock_tracker):
        result = await tool.execute({
            "model_name": "gpt-4o-mini",
            "input_tokens": 1000,
            "output_tokens": 500,
            "org_id": "acme",
        })
    assert result["success"] is True
    assert result["recorded"] is True
    assert result["org_id"] == "acme"
    mock_tracker.record_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_usage_tool_tracker_not_initialized():
    tool = RecordUsageTool()
    with patch("mcp.tools.record_usage.get_usage_tracker", side_effect=RuntimeError("not initialized")):
        result = await tool.execute({
            "model_name": "gpt-4o-mini", "input_tokens": 10, "output_tokens": 10,
        })
    assert result["success"] is False


@pytest.mark.asyncio
async def test_get_usage_summary_tool_success():
    tool = GetUsageSummaryTool()
    mock_tracker = MagicMock()
    mock_tracker.get_summary = AsyncMock(return_value={
        "org_id": "acme", "days": 30, "total_requests": 5, "total_cost_usd": 1.23,
        "total_input_tokens": 500, "total_output_tokens": 250,
        "by_model": [], "by_provider": [],
    })
    with patch("mcp.tools.get_usage_summary.get_usage_tracker", return_value=mock_tracker):
        result = await tool.execute({"org_id": "acme", "days": 30})
    assert result["success"] is True
    assert result["total_requests"] == 5


@pytest.mark.asyncio
async def test_get_usage_summary_tool_invalid_days():
    tool = GetUsageSummaryTool()
    result = await tool.execute({"days": 0})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_get_usage_summary_tool_not_initialized():
    tool = GetUsageSummaryTool()
    with patch("mcp.tools.get_usage_summary.get_usage_tracker", side_effect=RuntimeError("not initialized")):
        result = await tool.execute({})
    assert result["success"] is False
