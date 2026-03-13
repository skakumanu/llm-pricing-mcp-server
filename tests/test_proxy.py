"""Tests for the OpenAI-compatible proxy endpoint POST /v1/chat/completions."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app, _infer_task_from_messages, _format_routing_recommendation  # noqa: E402
from src.services.router import RouterResult  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _pm(name="gpt-4o-mini", provider="openai", inp=0.00000015, out=0.0000006, quality=72):
    m = PricingMetrics(
        model_name=name,
        provider=provider,
        cost_per_input_token=inp,
        cost_per_output_token=out,
    )
    m.quality_score = quality
    return m


def _make_router_result():
    rec = _pm()
    return RouterResult(
        recommended=rec,
        score=12.5,
        reason="gpt-4o-mini; quality=72",
        alternatives=[_pm("gemini-flash", "google", 0.00000035, 0.00000105, quality=70)],
    )


# ---------------------------------------------------------------------------
# _infer_task_from_messages
# ---------------------------------------------------------------------------

def test_infer_task_code():
    msgs = [{"role": "user", "content": "write a Python function to parse JSON"}]
    assert _infer_task_from_messages(msgs) == "code"


def test_infer_task_summarization():
    msgs = [{"role": "user", "content": "summarize this article"}]
    assert _infer_task_from_messages(msgs) == "summarization"


def test_infer_task_analysis():
    msgs = [{"role": "user", "content": "analyze the pros and cons"}]
    assert _infer_task_from_messages(msgs) == "analysis"


def test_infer_task_chat_default():
    msgs = [{"role": "user", "content": "hello there"}]
    assert _infer_task_from_messages(msgs) == "chat"


def test_infer_task_empty_messages():
    assert _infer_task_from_messages([]) == "chat"


# ---------------------------------------------------------------------------
# _format_routing_recommendation
# ---------------------------------------------------------------------------

def test_format_recommendation_with_result():
    result = _make_router_result()
    text = _format_routing_recommendation("gpt-4", result)
    assert "gpt-4o-mini" in text
    assert "gpt-4" in text
    assert "openai" in text


def test_format_recommendation_no_result():
    text = _format_routing_recommendation("gpt-4", None)
    assert "No model found" in text or "gpt-4" in text


# ---------------------------------------------------------------------------
# POST /v1/chat/completions endpoint
# ---------------------------------------------------------------------------

client = TestClient(app)


@pytest.fixture
def mock_router():
    result = _make_router_result()
    mock = MagicMock()
    mock.get_optimal_model = AsyncMock(return_value=result)
    return mock


def test_proxy_returns_200(mock_router):
    with patch("src.main.get_router", return_value=mock_router):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert resp.status_code == 200


def test_proxy_response_shape(mock_router):
    with patch("src.main.get_router", return_value=mock_router):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert "choices" in data
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["choices"][0]["finish_reason"] == "stop"


def test_proxy_routing_metadata(mock_router):
    with patch("src.main.get_router", return_value=mock_router):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    data = resp.json()
    meta = data.get("routing_metadata", {})
    assert meta.get("requested_model") == "gpt-4"
    assert meta.get("actual_forwarding") is False
    assert "recommended_model" in meta


def test_proxy_unauthenticated():
    """Endpoint should be accessible without an API key."""
    mock_router = MagicMock()
    mock_router.get_optimal_model = AsyncMock(return_value=_make_router_result())
    with patch("src.main.get_router", return_value=mock_router):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "hello"}],
            },
            # No x-api-key header
        )
    assert resp.status_code == 200


def test_proxy_infers_code_task(mock_router):
    with patch("src.main.get_router", return_value=mock_router):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "write a Python function"}],
            },
        )
    data = resp.json()
    assert data["routing_metadata"]["task_type_inferred"] == "code"


def test_proxy_router_unavailable_graceful():
    """If router isn't initialized, endpoint should not crash."""
    with patch("src.main.get_router", side_effect=RuntimeError("not initialized")):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "choices" in data
    # No recommendation available; model echoed back
    assert data["routing_metadata"]["recommended_model"] == "gpt-4"


def test_proxy_id_unique(mock_router):
    """Each call should return a unique ID."""
    ids = set()
    with patch("src.main.get_router", return_value=mock_router):
        for _ in range(3):
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
            )
            ids.add(resp.json()["id"])
    assert len(ids) == 3


def test_proxy_includes_usage_field(mock_router):
    with patch("src.main.get_router", return_value=mock_router):
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
        )
    data = resp.json()
    assert "usage" in data
