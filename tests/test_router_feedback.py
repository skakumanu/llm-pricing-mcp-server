"""Tests for router feedback loop: routing_id, POST /router/feedback, acceptance_rate."""
import sys
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.services.savings_tracker import SavingsTrackerService  # noqa: E402
from src.models.pricing import RouterFeedbackRequest, RouterFeedbackResponse, RouterResponse  # noqa: E402


# ---------------------------------------------------------------------------
# SavingsTrackerService — routing_id and feedback methods
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test_feedback.db")


@pytest_asyncio.fixture
async def svc(db_path):
    s = SavingsTrackerService(db_path)
    await s.initialize()
    return s


@pytest.mark.asyncio
async def test_initialize_creates_feedback_table(db_path):
    import aiosqlite
    svc = SavingsTrackerService(db_path)
    await svc.initialize()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='routing_feedback'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_record_routing_stores_routing_id(svc):
    rid = "test-routing-id-123"
    await svc.record_routing(
        recommended_model="gpt-4o-mini",
        recommended_provider="openai",
        recommended_cost_per_1m=0.375,
        routing_id=rid,
    )
    import aiosqlite
    async with aiosqlite.connect(svc._db_path) as db:
        async with db.execute(
            "SELECT routing_id FROM routing_savings WHERE routing_id = ?", (rid,)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == rid


@pytest.mark.asyncio
async def test_record_feedback_stores_row(svc):
    rid = "feedback-test-456"
    await svc.record_routing("m1", "openai", 1.0, routing_id=rid)
    await svc.record_feedback(rid, was_used=True)

    import aiosqlite
    async with aiosqlite.connect(svc._db_path) as db:
        async with db.execute(
            "SELECT was_used FROM routing_feedback WHERE routing_id = ?", (rid,)
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1


@pytest.mark.asyncio
async def test_record_feedback_duplicate_ignored(svc):
    rid = "dup-test-789"
    await svc.record_routing("m1", "openai", 1.0, routing_id=rid)
    await svc.record_feedback(rid, was_used=True)
    await svc.record_feedback(rid, was_used=False)  # duplicate — ignored

    import aiosqlite
    async with aiosqlite.connect(svc._db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM routing_feedback WHERE routing_id = ?", (rid,)
        ) as cur:
            count = (await cur.fetchone())[0]
    assert count == 1


@pytest.mark.asyncio
async def test_acceptance_rate_all_used(svc):
    for i in range(3):
        rid = f"rid-{i}"
        await svc.record_routing(f"m{i}", "openai", 1.0, routing_id=rid)
        await svc.record_feedback(rid, was_used=True)

    rate = await svc.get_acceptance_rate(days=1)
    assert rate == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_acceptance_rate_none_used(svc):
    for i in range(2):
        rid = f"notused-{i}"
        await svc.record_routing(f"m{i}", "openai", 1.0, routing_id=rid)
        await svc.record_feedback(rid, was_used=False)

    rate = await svc.get_acceptance_rate(days=1)
    assert rate == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_acceptance_rate_none_when_no_feedback(svc):
    await svc.record_routing("m1", "openai", 1.0, routing_id="no-fb")
    rate = await svc.get_acceptance_rate(days=1)
    assert rate is None


@pytest.mark.asyncio
async def test_get_savings_includes_acceptance_rate(svc):
    rid = "savings-ar-test"
    await svc.record_routing("m1", "openai", 1.0, routing_id=rid)
    await svc.record_feedback(rid, was_used=True)

    result = await svc.get_savings(days=1)
    assert "acceptance_rate" in result
    assert result["acceptance_rate"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_record_feedback_stores_notes(svc):
    rid = "notes-test"
    await svc.record_routing("m1", "openai", 1.0, routing_id=rid)
    await svc.record_feedback(rid, was_used=False, actual_model_used="gpt-4o", notes="too slow")

    import aiosqlite
    async with aiosqlite.connect(svc._db_path) as db:
        async with db.execute(
            "SELECT actual_model_used, notes FROM routing_feedback WHERE routing_id = ?", (rid,)
        ) as cur:
            row = await cur.fetchone()
    assert row[0] == "gpt-4o"
    assert row[1] == "too slow"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

def test_router_response_has_routing_id():
    from src.models.pricing import PricingMetrics
    pm = PricingMetrics(
        model_name="gpt-4o-mini", provider="openai",
        cost_per_input_token=0.00000015, cost_per_output_token=0.0000006,
    )
    resp = RouterResponse(recommended=pm, score=100.0, reason="test", routing_id="abc-123")
    assert resp.routing_id == "abc-123"


def test_router_feedback_request_model():
    req = RouterFeedbackRequest(routing_id="abc", was_used=True)
    assert req.routing_id == "abc"
    assert req.was_used is True
    assert req.actual_model_used is None
    assert req.notes is None


def test_router_feedback_response_model():
    resp = RouterFeedbackResponse(routing_id="abc", recorded=True)
    assert resp.recorded is True


# ---------------------------------------------------------------------------
# POST /router/feedback endpoint integration tests
# ---------------------------------------------------------------------------

def _client():
    from src.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def patch_tracker_for_feedback():
    mock_tracker = MagicMock()
    mock_tracker.record_feedback = AsyncMock()
    with patch("src.main.get_savings_tracker", return_value=mock_tracker):
        yield mock_tracker


def test_router_feedback_endpoint_success(patch_tracker_for_feedback):
    client = _client()
    resp = client.post(
        "/router/feedback",
        json={"routing_id": "some-uuid", "was_used": True},
        headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
    )
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        data = resp.json()
        assert data["routing_id"] == "some-uuid"
        assert data["recorded"] is True


def test_router_feedback_with_notes(patch_tracker_for_feedback):
    client = _client()
    resp = client.post(
        "/router/feedback",
        json={"routing_id": "uuid-2", "was_used": False, "notes": "too expensive"},
        headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
    )
    assert resp.status_code in (200, 401)


def test_router_feedback_missing_routing_id(patch_tracker_for_feedback):
    client = _client()
    resp = client.post(
        "/router/feedback",
        json={"was_used": True},
        headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
    )
    assert resp.status_code in (401, 422)


def test_router_recommend_returns_routing_id():
    """Verify /router/recommend response includes routing_id field."""
    from src.services.router import RouterResult
    from src.models.pricing import PricingMetrics

    pm = PricingMetrics(
        model_name="gpt-4o-mini", provider="openai",
        cost_per_input_token=0.00000015, cost_per_output_token=0.0000006,
    )
    mock_result = RouterResult(recommended=pm, score=192.0, reason="test", alternatives=[])
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=mock_result)
    mock_tracker = MagicMock()
    mock_tracker.record_routing = AsyncMock()

    client = _client()
    with (
        patch("src.main.get_router", return_value=mock_router),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend",
            json={"max_cost_per_1m_tokens": 5.0},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    assert resp.status_code in (200, 401)
    if resp.status_code == 200:
        data = resp.json()
        assert "routing_id" in data
        assert len(data["routing_id"]) > 0
