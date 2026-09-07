"""Tests for the recommendation cache wired into POST /router/recommend.

POST /router/recommend/stream is explicitly out of scope for caching and is
covered separately (it must keep calling the router directly, uncached).
"""
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.services.recommendation_cache import RecommendationCache  # noqa: E402
from src.services.router import RouterResult  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402


def _pm(name, provider="openai", inp=0.000005, out=0.000015):
    m = PricingMetrics(
        model_name=name, provider=provider,
        cost_per_input_token=inp, cost_per_output_token=out,
    )
    m.quality_score = 80
    return m


_HEADERS = {"x-api-key": os.environ.get("MCP_API_KEY", "test")}


@pytest.fixture
def fresh_cache():
    """Give /router/recommend a clean, isolated cache for the test's duration."""
    with patch("src.main._router_recommend_cache", RecommendationCache()) as c:
        yield c


@pytest.fixture
def mock_router_and_tracker():
    """Patch get_router()/get_savings_tracker() with a mock whose
    get_optimal_model() we can assert the call-count of, to prove cache hits
    skip recomputation entirely."""
    result = RouterResult(
        recommended=_pm("gpt-4o-mini"),
        score=42.0,
        reason="gpt-4o-mini (openai); quality_value_score=42.00",
        alternatives=[_pm("gemini-flash", provider="google")],
    )
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=result)

    mock_tracker = MagicMock()
    mock_tracker.record_routing = AsyncMock()

    with (
        patch("src.main.get_router", return_value=mock_router),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        mock_router.tracker = mock_tracker  # convenience: reach the tracker mock via the router mock
        yield mock_router


def _client():
    from src.main import app
    return TestClient(app)


def test_repeated_request_within_ttl_served_from_cache(fresh_cache, mock_router_and_tracker):
    client = _client()
    body = {"max_cost_per_1m_tokens": 5.0, "min_quality_score": 70}

    resp1 = client.post("/router/recommend", json=body, headers=_HEADERS)
    if resp1.status_code == 401:
        pytest.skip("MCP_API_KEY is configured in this environment; auth blocks the request")
    resp2 = client.post("/router/recommend", json=body, headers=_HEADERS)

    assert resp1.status_code == 200, resp1.text
    assert resp2.status_code == 200, resp2.text

    body1, body2 = resp1.json(), resp2.json()
    assert body1["cached"] is False
    assert body2["cached"] is True
    assert body2["recommended"] == body1["recommended"]
    assert body2["score"] == body1["score"]
    assert body2["reason"] == body1["reason"]
    assert body2["alternatives"] == body1["alternatives"]

    # The routing computation itself only ran once.
    assert mock_router_and_tracker.get_optimal_model.call_count == 1


def test_repeated_request_after_ttl_is_recomputed(fresh_cache, mock_router_and_tracker):
    client = _client()
    body = {"max_cost_per_1m_tokens": 5.0, "min_quality_score": 70}

    resp1 = client.post("/router/recommend", json=body, headers=_HEADERS)
    if resp1.status_code == 401:
        pytest.skip("MCP_API_KEY is configured in this environment; auth blocks the request")
    assert resp1.status_code == 200, resp1.text

    # Force the cached entry to look stale (past the ~5 minute TTL).
    for entry in fresh_cache._entries.values():
        entry.created_at = time.time() - 301

    resp2 = client.post("/router/recommend", json=body, headers=_HEADERS)
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["cached"] is False
    assert mock_router_and_tracker.get_optimal_model.call_count == 2


def test_different_requests_not_cross_cached(fresh_cache, mock_router_and_tracker):
    client = _client()
    resp1 = client.post(
        "/router/recommend", json={"max_cost_per_1m_tokens": 5.0}, headers=_HEADERS
    )
    if resp1.status_code == 401:
        pytest.skip("MCP_API_KEY is configured in this environment; auth blocks the request")
    resp2 = client.post(
        "/router/recommend", json={"max_cost_per_1m_tokens": 10.0}, headers=_HEADERS
    )

    assert resp1.json()["cached"] is False
    assert resp2.json()["cached"] is False
    assert mock_router_and_tracker.get_optimal_model.call_count == 2


def test_cached_field_present_on_fresh_response(fresh_cache, mock_router_and_tracker):
    """No prior test-suite behaviour breaks: callers who never repeat a
    request just see cached=False on every response."""
    client = _client()
    resp = client.post("/router/recommend", json={}, headers=_HEADERS)
    if resp.status_code == 401:
        pytest.skip("MCP_API_KEY is configured in this environment; auth blocks the request")
    assert resp.status_code == 200
    assert resp.json()["cached"] is False


def test_savings_tracking_and_telemetry_run_on_every_call_including_cache_hits(
    fresh_cache, mock_router_and_tracker
):
    """Caching must not skip billing/usage-tracking side effects (non-goal:
    caching does not change rate limiting/quota/billing behaviour)."""
    client = _client()
    body = {"max_cost_per_1m_tokens": 5.0}

    resp1 = client.post("/router/recommend", json=body, headers=_HEADERS)
    if resp1.status_code == 401:
        pytest.skip("MCP_API_KEY is configured in this environment; auth blocks the request")
    resp2 = client.post("/router/recommend", json=body, headers=_HEADERS)

    assert resp1.json()["cached"] is False
    assert resp2.json()["cached"] is True
    # routing computation itself is skipped on the cache-hit call...
    assert mock_router_and_tracker.get_optimal_model.call_count == 1
    # ...but record_routing (savings tracking) still runs on both calls, and
    # each response still gets its own fresh routing_id (billing/telemetry
    # behaviour is unaffected by caching per the Spec's non-goals).
    assert mock_router_and_tracker.tracker.record_routing.call_count == 2
    assert resp1.json()["routing_id"] != resp2.json()["routing_id"]
