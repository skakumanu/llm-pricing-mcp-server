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
    assert summary["estimated_request_count"] == 0
    assert summary["has_estimated_usage"] is False


# ---------------------------------------------------------------------------
# is_estimated (exact-vs-estimated) aggregation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_event_defaults_to_exact(svc):
    """Existing callers that never pass is_estimated keep recording exact events."""
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=10, output_tokens=10,
        cost_usd=1.0,
    )
    summary = await svc.get_summary(days=1)
    assert summary["estimated_request_count"] == 0
    assert summary["has_estimated_usage"] is False


@pytest.mark.asyncio
async def test_summary_reports_estimated_vs_exact_breakdown(svc):
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=10, output_tokens=10,
        cost_usd=1.0, is_estimated=False,
    )
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=20, output_tokens=20,
        cost_usd=2.0, is_estimated=True,
    )
    summary = await svc.get_summary(days=1)
    assert summary["total_requests"] == 2
    assert summary["estimated_request_count"] == 1
    assert summary["has_estimated_usage"] is True


@pytest.mark.asyncio
async def test_session_usage_reports_estimated_vs_exact_breakdown(svc):
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=10, output_tokens=10,
        cost_usd=1.0, session_id="sess-est", is_estimated=False,
    )
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=20, output_tokens=20,
        cost_usd=2.0, session_id="sess-est", is_estimated=True,
    )
    usage = await svc.get_session_usage("sess-est")
    assert usage["total_requests"] == 2
    assert usage["estimated_request_count"] == 1
    assert usage["has_estimated_usage"] is True
    assert usage["by_model"][0]["estimated_request_count"] == 1


@pytest.mark.asyncio
async def test_session_usage_no_data_has_no_estimated_usage(svc):
    usage = await svc.get_session_usage("nonexistent-session-2")
    assert usage["estimated_request_count"] == 0
    assert usage["has_estimated_usage"] is False


# ---------------------------------------------------------------------------
# Session-scoped usage (session_id)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_event_with_session_id(svc):
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=1000, output_tokens=500,
        cost_usd=0.01, session_id="sess-1",
    )
    usage = await svc.get_session_usage("sess-1")
    assert usage["total_requests"] == 1
    assert usage["total_input_tokens"] == 1000
    assert usage["total_output_tokens"] == 500
    assert usage["total_cost_usd"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_get_session_usage_no_data(svc):
    usage = await svc.get_session_usage("nonexistent-session")
    assert usage["total_requests"] == 0
    assert usage["total_cost_usd"] == 0.0
    assert usage["by_model"] == []
    assert usage["first_occurred_at"] is None
    assert usage["last_occurred_at"] is None


@pytest.mark.asyncio
async def test_get_session_usage_single_model(svc):
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=100, output_tokens=50,
        cost_usd=0.005, session_id="sess-single",
    )
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=200, output_tokens=100,
        cost_usd=0.01, session_id="sess-single",
    )
    usage = await svc.get_session_usage("sess-single")
    assert usage["total_requests"] == 2
    assert usage["total_input_tokens"] == 300
    assert usage["total_output_tokens"] == 150
    assert usage["total_cost_usd"] == pytest.approx(0.015)
    assert len(usage["by_model"]) == 1
    assert usage["by_model"][0]["model_name"] == "gpt-4o-mini"
    assert usage["by_model"][0]["request_count"] == 2


@pytest.mark.asyncio
async def test_get_session_usage_multi_model(svc):
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=100, output_tokens=50,
        cost_usd=0.005, session_id="sess-multi",
    )
    await svc.record_event(
        provider="anthropic", model_name="claude-haiku", input_tokens=300, output_tokens=150,
        cost_usd=0.02, session_id="sess-multi",
    )
    usage = await svc.get_session_usage("sess-multi")
    assert usage["total_requests"] == 2
    assert usage["total_input_tokens"] == 400
    assert usage["total_output_tokens"] == 200
    assert usage["total_cost_usd"] == pytest.approx(0.025)
    assert len(usage["by_model"]) == 2
    by_model_names = {row["model_name"] for row in usage["by_model"]}
    assert by_model_names == {"gpt-4o-mini", "claude-haiku"}


@pytest.mark.asyncio
async def test_session_usage_is_scoped_to_session_not_org_or_time(svc):
    """Session analysis must not leak an unrelated time window or org-wide aggregate."""
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=10, output_tokens=10,
        cost_usd=1.0, org_id="acme", session_id="sess-a",
    )
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=999, output_tokens=999,
        cost_usd=99.0, org_id="acme", session_id="sess-b",
    )
    usage_a = await svc.get_session_usage("sess-a")
    assert usage_a["total_requests"] == 1
    assert usage_a["total_cost_usd"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_get_session_usage_org_id_scoping_prevents_cross_tenant_read(svc):
    """A session_id recorded under one org must not be readable by supplying a
    different org_id — closes the cross-tenant leak an attacker could otherwise
    exploit by guessing/observing another org's session_id."""
    await svc.record_event(
        provider="openai", model_name="gpt-4o-mini", input_tokens=10, output_tokens=10,
        cost_usd=1.0, org_id="org-victim", session_id="shared-session-id",
    )

    # The victim's own org can read it back.
    own_org_view = await svc.get_session_usage("shared-session-id", org_id="org-victim")
    assert own_org_view["total_requests"] == 1

    # A different (attacking) org querying the *same* session_id gets nothing back.
    other_org_view = await svc.get_session_usage("shared-session-id", org_id="org-attacker")
    assert other_org_view["total_requests"] == 0

    # Omitting org_id entirely preserves the old (unscoped) behavior.
    unscoped_view = await svc.get_session_usage("shared-session-id")
    assert unscoped_view["total_requests"] == 1


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
    mock.get_session_usage = AsyncMock(return_value={
        "session_id": "sess-1",
        "total_requests": 0,
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "first_occurred_at": None,
        "last_occurred_at": None,
        "by_model": [],
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


def test_record_usage_endpoint_with_session_id(mock_usage_tracker):
    from src.main import app
    client = TestClient(app)
    with patch("src.main.get_usage_tracker", return_value=mock_usage_tracker):
        resp = client.post("/usage", json={
            "model_name": "gpt-4o-mini",
            "input_tokens": 1000,
            "output_tokens": 500,
            "session_id": "sess-abc",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "sess-abc"
    _, kwargs = mock_usage_tracker.record_event.await_args
    assert kwargs["session_id"] == "sess-abc"


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


def test_usage_summary_endpoint_reports_estimated_breakdown():
    """GET /usage/summary surfaces the org-wide exact-vs-estimated breakdown (AC9)."""
    from src.main import app
    client = TestClient(app)
    mock_tracker = MagicMock()
    mock_tracker.get_summary = AsyncMock(return_value={
        "org_id": None,
        "days": 30,
        "total_requests": 3,
        "total_cost_usd": 0.03,
        "total_input_tokens": 300,
        "total_output_tokens": 150,
        "by_model": [],
        "by_provider": [],
        "estimated_request_count": 1,
        "has_estimated_usage": True,
    })
    with patch("src.main.get_usage_tracker", return_value=mock_tracker):
        resp = client.get("/usage/summary?days=30")
    assert resp.status_code == 200
    data = resp.json()
    assert data["estimated_request_count"] == 1
    assert data["has_estimated_usage"] is True


def test_usage_summary_endpoint_defaults_when_no_estimated_usage(mock_usage_tracker):
    """Orgs/periods with no estimated usage see 0/false, not a missing field (AC9)."""
    from src.main import app
    client = TestClient(app)
    with patch("src.main.get_usage_tracker", return_value=mock_usage_tracker):
        resp = client.get("/usage/summary?days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert data["estimated_request_count"] == 0
    assert data["has_estimated_usage"] is False


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
# GET /usage/session/{session_id} endpoint integration tests
# ---------------------------------------------------------------------------

def _pricing_metrics(model_name, provider, cost_in, cost_out):
    from src.models.pricing import PricingMetrics
    return PricingMetrics(
        model_name=model_name, provider=provider,
        cost_per_input_token=cost_in, cost_per_output_token=cost_out,
    )


def test_usage_session_endpoint_no_data():
    from src.main import app
    client = TestClient(app)
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value={
        "session_id": "empty-session",
        "total_requests": 0,
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "first_occurred_at": None,
        "last_occurred_at": None,
        "by_model": [],
    })
    with patch("src.main.get_usage_tracker", return_value=mock_tracker):
        resp = client.get("/usage/session/empty-session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["has_data"] is False
    assert data.get("recommendation") is None


def test_usage_session_endpoint_single_model_recommends():
    from src.main import app
    from src.services.router import RouterResult

    client = TestClient(app)
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value={
        "session_id": "sess-1",
        "total_requests": 10,
        "total_cost_usd": 1.0,
        "total_input_tokens": 100_000,
        "total_output_tokens": 50_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 2000.0,
        "by_model": [
            {
                "model_name": "gpt-4o", "provider": "openai", "request_count": 10,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 1.0,
            }
        ],
    })
    cheaper = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006)
    mock_result = RouterResult(recommended=cheaper, score=100.0, reason="test", alternatives=[])
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with (
        patch("src.main.get_usage_tracker", return_value=mock_tracker),
        patch("src.main.get_router", return_value=mock_router),
    ):
        resp = client.get("/usage/session/sess-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["has_data"] is True
    assert data["total_requests"] == 10
    assert data["total_input_tokens"] == 100_000
    assert data["total_output_tokens"] == 50_000
    assert len(data["by_model"]) == 1
    rec = data["recommendation"]
    assert rec["recommended_model"] == "gpt-4o-mini"
    assert rec["is_optimal"] is False
    assert "sess-1" in rec["rationale"]
    assert "10 request" in rec["rationale"]


def test_usage_session_endpoint_is_optimal_when_already_using_recommended():
    from src.main import app
    from src.services.router import RouterResult

    client = TestClient(app)
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value={
        "session_id": "sess-optimal",
        "total_requests": 5,
        "total_cost_usd": 0.5,
        "total_input_tokens": 50_000,
        "total_output_tokens": 25_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 1500.0,
        "by_model": [
            {
                "model_name": "gpt-4o-mini", "provider": "openai", "request_count": 5,
                "input_tokens": 50_000, "output_tokens": 25_000, "cost_usd": 0.5,
            }
        ],
    })
    same_model = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006)
    mock_result = RouterResult(recommended=same_model, score=100.0, reason="test", alternatives=[])
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with (
        patch("src.main.get_usage_tracker", return_value=mock_tracker),
        patch("src.main.get_router", return_value=mock_router),
    ):
        resp = client.get("/usage/session/sess-optimal")
    assert resp.status_code == 200
    data = resp.json()
    rec = data["recommendation"]
    assert rec["is_optimal"] is True
    assert rec["savings_usd"] == 0.0


def test_usage_session_endpoint_multi_model_breakdown():
    from src.main import app
    from src.services.router import RouterResult

    client = TestClient(app)
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value={
        "session_id": "sess-multi",
        "total_requests": 4,
        "total_cost_usd": 2.0,
        "total_input_tokens": 200_000,
        "total_output_tokens": 100_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 3000.0,
        "by_model": [
            {
                "model_name": "gpt-4o", "provider": "openai", "request_count": 2,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 1.5,
            },
            {
                "model_name": "claude-haiku", "provider": "anthropic", "request_count": 2,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 0.5,
            },
        ],
    })
    recommended = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006)
    mock_result = RouterResult(recommended=recommended, score=100.0, reason="test", alternatives=[])
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with (
        patch("src.main.get_usage_tracker", return_value=mock_tracker),
        patch("src.main.get_router", return_value=mock_router),
    ):
        resp = client.get("/usage/session/sess-multi")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["by_model"]) == 2
    names = {row["model_name"] for row in data["by_model"]}
    assert names == {"gpt-4o", "claude-haiku"}
    # Multi-model session can never be "is_optimal" (no single model to match against)
    assert data["recommendation"]["is_optimal"] is False


def test_usage_session_endpoint_reports_estimated_breakdown():
    """GET /usage/session/{session_id} surfaces the session's exact-vs-estimated breakdown (AC9)."""
    from src.main import app
    from src.services.router import RouterResult

    client = TestClient(app)
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value={
        "session_id": "sess-est",
        "total_requests": 2,
        "total_cost_usd": 0.02,
        "total_input_tokens": 200,
        "total_output_tokens": 100,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 2000.0,
        "estimated_request_count": 1,
        "has_estimated_usage": True,
        "by_model": [
            {
                "model_name": "gpt-4o-mini", "provider": "openai", "request_count": 2,
                "input_tokens": 200, "output_tokens": 100, "cost_usd": 0.02,
                "estimated_request_count": 1,
            }
        ],
    })
    same_model = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006)
    mock_result = RouterResult(recommended=same_model, score=100.0, reason="test", alternatives=[])
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with (
        patch("src.main.get_usage_tracker", return_value=mock_tracker),
        patch("src.main.get_router", return_value=mock_router),
    ):
        resp = client.get("/usage/session/sess-est")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_estimated_usage"] is True
    assert data["estimated_request_count"] == 1
    assert data["by_model"][0]["estimated_request_count"] == 1


def test_usage_session_endpoint_quality_tradeoff_caveat_regression_case():
    """Live-verified regression, same scenario as the MCP tool's equivalent test: a
    session that used gpt-4o-mini with 100k input / 50k output tokens, recommended to
    @cf/mistral/mistral-7b-instruct-v0.1 with a ~$0.045 savings figure, must surface an
    identical quality_tradeoff caveat via GET /usage/session/{session_id}."""
    from src.main import app
    from src.services.router import RouterResult

    client = TestClient(app)
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value={
        "session_id": "sess-regression",
        "total_requests": 1,
        "total_cost_usd": 0.045,
        "total_input_tokens": 100_000,
        "total_output_tokens": 50_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 1000.0,
        "by_model": [
            {
                "model_name": "gpt-4o-mini", "provider": "openai", "request_count": 1,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 0.045,
            }
        ],
    })
    cheaper_lower_quality = _pricing_metrics(
        "@cf/mistral/mistral-7b-instruct-v0.1", "cloudflare", 0.0000000022, 0.0000000022,
    )
    cheaper_lower_quality.quality_score = 58.0
    mock_result = RouterResult(recommended=cheaper_lower_quality, score=100.0, reason="test", alternatives=[])
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with (
        patch("src.main.get_usage_tracker", return_value=mock_tracker),
        patch("src.main.get_router", return_value=mock_router),
    ):
        resp = client.get("/usage/session/sess-regression")
    assert resp.status_code == 200
    data = resp.json()
    rec = data["recommendation"]
    assert rec["recommended_model"] == "@cf/mistral/mistral-7b-instruct-v0.1"
    assert rec["is_optimal"] is False
    quality_caveats = [c for c in rec.get("caveats", []) if c["type"] == "quality_tradeoff"]
    assert len(quality_caveats) == 1
    assert quality_caveats[0]["quality_score"] == 58.0
    assert quality_caveats[0]["quality_threshold"] == 75.0
    assert "quality_score of 58.0" in quality_caveats[0]["message"]


def test_usage_session_endpoint_no_caveat_when_quality_at_or_above_threshold():
    """No caveats key at all (per SessionRecommendation's Optional field convention) when
    the recommended model's quality_score is at/above the threshold."""
    from src.main import app
    from src.services.router import RouterResult

    client = TestClient(app)
    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value={
        "session_id": "sess-1",
        "total_requests": 10,
        "total_cost_usd": 1.0,
        "total_input_tokens": 100_000,
        "total_output_tokens": 50_000,
        "first_occurred_at": 1000.0,
        "last_occurred_at": 2000.0,
        "by_model": [
            {
                "model_name": "gpt-4o", "provider": "openai", "request_count": 10,
                "input_tokens": 100_000, "output_tokens": 50_000, "cost_usd": 1.0,
            }
        ],
    })
    high_quality = _pricing_metrics("gpt-4o-mini", "openai", 0.00000015, 0.0000006)
    high_quality.quality_score = 90.0
    mock_result = RouterResult(recommended=high_quality, score=100.0, reason="test", alternatives=[])
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)

    with (
        patch("src.main.get_usage_tracker", return_value=mock_tracker),
        patch("src.main.get_router", return_value=mock_router),
    ):
        resp = client.get("/usage/session/sess-1")
    assert resp.status_code == 200
    rec = resp.json()["recommendation"]
    assert rec.get("caveats") is None


def test_usage_session_endpoint_scopes_to_authenticated_customers_org():
    """An authenticated caller's own org_id (from their billing API key) must be used to
    scope the session lookup — a customer cannot see another org's session data just by
    supplying/guessing that org's session_id."""
    from src.main import app
    from src.services.billing_service import CustomerRecord
    import time as _time

    customer = CustomerRecord(
        id="cust-1", email="a@example.com", stripe_customer_id=None,
        stripe_subscription_id=None, api_key="cust-1-key", tier="free",
        org_id="org-a", created_at=_time.time(), updated_at=_time.time(),
    )
    mock_billing = MagicMock()
    mock_billing.get_customer_by_api_key = AsyncMock(return_value=customer)

    mock_tracker = MagicMock()
    mock_tracker.get_session_usage = AsyncMock(return_value={
        "session_id": "sess-1",
        "total_requests": 0,
        "total_cost_usd": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "first_occurred_at": None,
        "last_occurred_at": None,
        "by_model": [],
    })

    client = TestClient(app)
    with (
        patch("src.main.get_billing_service", return_value=mock_billing),
        patch("src.main.get_usage_tracker", return_value=mock_tracker),
    ):
        resp = client.get(
            "/usage/session/sess-1",
            headers={"x-api-key": "cust-1-key", "X-Organization-Id": "org-attacker-supplied"},
        )
    assert resp.status_code == 200
    mock_tracker.get_session_usage.assert_awaited_once()
    _, kwargs = mock_tracker.get_session_usage.await_args
    # The authenticated customer's real org_id wins, regardless of any self-reported header.
    assert kwargs["org_id"] == "org-a"


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
async def test_record_usage_tool_with_session_id():
    tool = RecordUsageTool()
    mock_tracker = MagicMock()
    mock_tracker.record_event = AsyncMock(return_value={"duplicate": False})
    with patch("mcp.tools.record_usage.get_usage_tracker", return_value=mock_tracker):
        result = await tool.execute({
            "model_name": "gpt-4o-mini",
            "input_tokens": 1000,
            "output_tokens": 500,
            "session_id": "sess-xyz",
        })
    assert result["success"] is True
    assert result["session_id"] == "sess-xyz"
    _, kwargs = mock_tracker.record_event.await_args
    assert kwargs["session_id"] == "sess-xyz"


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
