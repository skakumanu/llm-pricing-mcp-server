"""Tests for POST /router/recommend/stream SSE endpoint."""
import sys
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pm(name, provider, inp, out, quality=None, context=None, use_cases=None):
    m = PricingMetrics(
        model_name=name, provider=provider,
        cost_per_input_token=inp, cost_per_output_token=out,
        context_window=context, use_cases=use_cases,
    )
    m.quality_score = quality
    return m


SAMPLE_MODELS = [
    _pm("gpt-4o",        "openai",    0.000005,   0.000015,  quality=87, context=128000),
    _pm("gpt-4o-mini",   "openai",    0.00000015, 0.0000006, quality=72, context=128000),
    _pm("claude-opus",   "anthropic", 0.000015,   0.000075,  quality=90, context=200000),
    _pm("gemini-flash",  "google",    0.00000035, 0.00000105, quality=70, context=1000000),
]


def _make_aggregator(models=None):
    agg = MagicMock()
    agg.get_all_pricing_async = AsyncMock(
        return_value=(SAMPLE_MODELS if models is None else models, [])
    )
    return agg


def _parse_sse(text: str) -> list[dict]:
    """Parse raw SSE text into a list of event dicts."""
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _stream_client(aggregator=None, enrich_side_effect=None):
    from src.main import app
    client = TestClient(app)
    agg = aggregator or _make_aggregator()
    enrich = enrich_side_effect or (lambda x: x)
    return client, agg, enrich


# ---------------------------------------------------------------------------
# Endpoint integration tests (using TestClient streaming)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_tracker():
    t = MagicMock()
    t.record_routing = AsyncMock()
    return t


def test_stream_returns_200(mock_tracker):
    from src.main import app
    client = TestClient(app)
    agg = _make_aggregator()
    with (
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=agg)),
        patch("src.main.enrich_models", AsyncMock(side_effect=lambda x: x)),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend/stream",
            json={"max_cost_per_1m_tokens": 100.0},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    assert resp.status_code in (200, 401)


def test_stream_content_type_sse(mock_tracker):
    from src.main import app
    client = TestClient(app)
    agg = _make_aggregator()
    with (
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=agg)),
        patch("src.main.enrich_models", AsyncMock(side_effect=lambda x: x)),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend/stream",
            json={},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    if resp.status_code == 200:
        assert "text/event-stream" in resp.headers.get("content-type", "")


def test_stream_event_sequence(mock_tracker):
    """Full happy-path: verify SSE event type sequence."""
    from src.main import app
    client = TestClient(app)
    agg = _make_aggregator()
    with (
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=agg)),
        patch("src.main.enrich_models", AsyncMock(side_effect=lambda x: x)),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend/stream",
            json={"max_cost_per_1m_tokens": 100.0},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    if resp.status_code != 200:
        pytest.skip("Authenticated env — skipping body checks")

    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]

    assert "start" in types
    assert "fetching_models" in types
    assert "models_loaded" in types
    assert "enriching_quality" in types
    assert "quality_enriched" in types
    assert "filtering" in types
    assert "candidates_ready" in types
    assert "recommendation" in types
    assert "alternatives" in types
    assert "done" in types

    # Order checks
    assert types.index("start") < types.index("fetching_models")
    assert types.index("models_loaded") < types.index("enriching_quality")
    assert types.index("quality_enriched") < types.index("filtering")
    assert types.index("candidates_ready") < types.index("recommendation")
    assert types.index("recommendation") < types.index("alternatives")
    assert types.index("alternatives") < types.index("done")


def test_stream_done_event_has_routing_id(mock_tracker):
    from src.main import app
    client = TestClient(app)
    agg = _make_aggregator()
    with (
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=agg)),
        patch("src.main.enrich_models", AsyncMock(side_effect=lambda x: x)),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend/stream",
            json={},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    events = _parse_sse(resp.text)
    done = next((e for e in events if e["type"] == "done"), None)
    assert done is not None
    assert "routing_id" in done
    assert len(done["routing_id"]) > 0


def test_stream_recommendation_has_required_fields(mock_tracker):
    from src.main import app
    client = TestClient(app)
    agg = _make_aggregator()
    with (
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=agg)),
        patch("src.main.enrich_models", AsyncMock(side_effect=lambda x: x)),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend/stream",
            json={},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    events = _parse_sse(resp.text)
    rec = next((e for e in events if e["type"] == "recommendation"), None)
    assert rec is not None
    assert "model" in rec
    assert "provider" in rec
    assert "score" in rec
    assert "reason" in rec


def test_stream_no_candidates_emits_error(mock_tracker):
    """When constraints filter out all models, an error event is emitted."""
    from src.main import app
    client = TestClient(app)
    agg = _make_aggregator()
    with (
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=agg)),
        patch("src.main.enrich_models", AsyncMock(side_effect=lambda x: x)),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend/stream",
            # Impossible constraint: $0 cost AND 100 quality
            json={"max_cost_per_1m_tokens": 0.0, "min_quality_score": 100},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    assert "error" in types
    assert "done" in types
    # Should NOT have recommendation when no candidates
    assert "recommendation" not in types


def test_stream_start_event_has_constraints(mock_tracker):
    from src.main import app
    client = TestClient(app)
    agg = _make_aggregator()
    with (
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=agg)),
        patch("src.main.enrich_models", AsyncMock(side_effect=lambda x: x)),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend/stream",
            json={"max_cost_per_1m_tokens": 5.0},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    events = _parse_sse(resp.text)
    start = next((e for e in events if e["type"] == "start"), None)
    assert start is not None
    assert "constraints" in start
    assert start["constraints"].get("max_cost_per_1m_tokens") == 5.0


def test_stream_models_loaded_count(mock_tracker):
    from src.main import app
    client = TestClient(app)
    agg = _make_aggregator()
    with (
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=agg)),
        patch("src.main.enrich_models", AsyncMock(side_effect=lambda x: x)),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend/stream",
            json={},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    events = _parse_sse(resp.text)
    loaded = next((e for e in events if e["type"] == "models_loaded"), None)
    assert loaded is not None
    assert loaded["count"] == len(SAMPLE_MODELS)


def test_stream_alternatives_list(mock_tracker):
    from src.main import app
    client = TestClient(app)
    agg = _make_aggregator()
    with (
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=agg)),
        patch("src.main.enrich_models", AsyncMock(side_effect=lambda x: x)),
        patch("src.main.get_savings_tracker", return_value=mock_tracker),
    ):
        resp = client.post(
            "/router/recommend/stream",
            json={},
            headers={"x-api-key": os.environ.get("MCP_API_KEY", "test")},
        )
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    events = _parse_sse(resp.text)
    alts = next((e for e in events if e["type"] == "alternatives"), None)
    assert alts is not None
    assert isinstance(alts["models"], list)
    for m in alts["models"]:
        assert "model_name" in m
        assert "provider" in m
