"""Tests for async background workers and request-splitting logic."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from src.main import app
from src.services.job_manager import Job, JobManager, JobStatus, get_job_manager
from mcp.tools.compare_costs import split_models_into_chunks, CompareCostsTool

client = TestClient(app)


# ---------------------------------------------------------------------------
# split_models_into_chunks unit tests
# ---------------------------------------------------------------------------


def test_split_models_basic():
    """Splitting an exact multiple of chunk_size produces equal-sized chunks."""
    models = [f"model-{i}" for i in range(6)]
    chunks = split_models_into_chunks(models, chunk_size=2)
    assert len(chunks) == 3
    assert chunks == [["model-0", "model-1"], ["model-2", "model-3"], ["model-4", "model-5"]]


def test_split_models_remainder():
    """Last chunk may be smaller than chunk_size."""
    models = [f"m{i}" for i in range(7)]
    chunks = split_models_into_chunks(models, chunk_size=3)
    assert len(chunks) == 3
    assert chunks[0] == ["m0", "m1", "m2"]
    assert chunks[1] == ["m3", "m4", "m5"]
    assert chunks[2] == ["m6"]


def test_split_models_single_chunk():
    """List smaller than chunk_size returns one chunk."""
    models = ["a", "b"]
    chunks = split_models_into_chunks(models, chunk_size=10)
    assert chunks == [["a", "b"]]


def test_split_models_chunk_size_one():
    """chunk_size=1 produces one chunk per model."""
    models = ["x", "y", "z"]
    chunks = split_models_into_chunks(models, chunk_size=1)
    assert chunks == [["x"], ["y"], ["z"]]


def test_split_models_empty_list():
    """Empty list returns empty result."""
    assert split_models_into_chunks([], chunk_size=5) == []


def test_split_models_invalid_chunk_size():
    """chunk_size < 1 raises ValueError."""
    with pytest.raises(ValueError):
        split_models_into_chunks(["model"], chunk_size=0)


def test_split_models_preserves_order():
    """All models appear exactly once and in original order."""
    models = [f"m{i}" for i in range(20)]
    chunks = split_models_into_chunks(models, chunk_size=6)
    flattened = [m for chunk in chunks for m in chunk]
    assert flattened == models


# ---------------------------------------------------------------------------
# JobManager unit tests
# ---------------------------------------------------------------------------


def test_job_manager_create_job():
    """create_job returns a job with a unique ID and PENDING status."""
    jm = JobManager()
    job = jm.create_job()

    assert isinstance(job, Job)
    assert job.status == JobStatus.PENDING
    assert job.result is None
    assert job.error is None


def test_job_manager_get_job_exists():
    """get_job returns the correct job by ID."""
    jm = JobManager()
    job = jm.create_job()
    retrieved = jm.get_job(job.job_id)
    assert retrieved is job


def test_job_manager_get_job_missing():
    """get_job returns None for unknown job IDs."""
    jm = JobManager()
    assert jm.get_job("non-existent-id") is None


@pytest.mark.asyncio
async def test_job_manager_update_job_completed():
    """update_job correctly transitions a job to COMPLETED with a result."""
    jm = JobManager()
    job = jm.create_job()

    result_payload = {"success": True, "models": []}
    await jm.update_job(job.job_id, JobStatus.COMPLETED, result=result_payload)

    assert job.status == JobStatus.COMPLETED
    assert job.result == result_payload
    assert job.error is None


@pytest.mark.asyncio
async def test_job_manager_update_job_failed():
    """update_job correctly transitions a job to FAILED with an error message."""
    jm = JobManager()
    job = jm.create_job()

    await jm.update_job(job.job_id, JobStatus.FAILED, error="Something went wrong")

    assert job.status == JobStatus.FAILED
    assert job.error == "Something went wrong"
    assert job.result is None


@pytest.mark.asyncio
async def test_job_manager_update_job_running():
    """update_job correctly transitions a job to RUNNING."""
    jm = JobManager()
    job = jm.create_job()

    await jm.update_job(job.job_id, JobStatus.RUNNING)
    assert job.status == JobStatus.RUNNING


@pytest.mark.asyncio
async def test_job_manager_update_nonexistent_job():
    """update_job on an unknown ID is a no-op (does not raise)."""
    jm = JobManager()
    await jm.update_job("ghost-id", JobStatus.COMPLETED, result={})


def test_job_to_dict():
    """Job.to_dict contains all expected keys."""
    job = Job("test-id")
    d = job.to_dict()
    assert d["job_id"] == "test-id"
    assert d["status"] == JobStatus.PENDING
    assert "created_at" in d
    assert "updated_at" in d
    assert d["result"] is None
    assert d["error"] is None


def test_get_job_manager_singleton():
    """get_job_manager returns the same instance on repeated calls."""
    jm1 = get_job_manager()
    jm2 = get_job_manager()
    assert jm1 is jm2


# ---------------------------------------------------------------------------
# /compare endpoint tests
# ---------------------------------------------------------------------------


def test_compare_endpoint_basic():
    """POST /compare returns a valid BatchCostEstimateResponse."""
    pricing_resp = client.get("/pricing")
    assert pricing_resp.status_code == 200
    models = pricing_resp.json()["models"]
    test_models = [m["model_name"] for m in models[:4]]

    response = client.post("/compare", json={
        "model_names": test_models,
        "input_tokens": 1000,
        "output_tokens": 500,
        "chunk_size": 2,
    })
    assert response.status_code == 200
    data = response.json()

    assert "models" in data
    assert "input_tokens" in data
    assert "output_tokens" in data
    assert "cheapest_model" in data
    assert "most_expensive_model" in data
    assert len(data["models"]) == len(test_models)
    assert data["currency"] == "USD"


def test_compare_endpoint_default_chunk_size():
    """POST /compare works without specifying chunk_size."""
    pricing_resp = client.get("/pricing")
    models = pricing_resp.json()["models"]
    test_models = [m["model_name"] for m in models[:3]]

    response = client.post("/compare", json={
        "model_names": test_models,
        "input_tokens": 500,
        "output_tokens": 500,
    })
    assert response.status_code == 200


def test_compare_endpoint_invalid_chunk_size():
    """POST /compare with chunk_size < 1 returns 422."""
    response = client.post("/compare", json={
        "model_names": ["gpt-4"],
        "input_tokens": 100,
        "output_tokens": 100,
        "chunk_size": 0,
    })
    assert response.status_code == 422


def test_compare_endpoint_negative_tokens():
    """POST /compare with negative tokens returns 422."""
    response = client.post("/compare", json={
        "model_names": ["gpt-4"],
        "input_tokens": -1,
        "output_tokens": 100,
    })
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# /background/compare endpoint tests
# ---------------------------------------------------------------------------


def test_background_compare_submit_returns_job_id():
    """POST /background/compare returns a job_id and pending status."""
    pricing_resp = client.get("/pricing")
    models = pricing_resp.json()["models"]
    test_models = [m["model_name"] for m in models[:3]]

    response = client.post("/background/compare", json={
        "model_names": test_models,
        "input_tokens": 1000,
        "output_tokens": 500,
    })
    assert response.status_code == 200
    data = response.json()

    assert "job_id" in data
    assert data["status"] == "pending"
    assert "message" in data
    assert data["job_id"] in data["message"]


def test_background_compare_invalid_input():
    """POST /background/compare with missing required fields returns 422."""
    response = client.post("/background/compare", json={
        "model_names": ["gpt-4"],
        # missing input_tokens and output_tokens
    })
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# /job/{job_id} endpoint tests
# ---------------------------------------------------------------------------


def test_get_job_status_not_found():
    """GET /job/{job_id} returns 404 for unknown IDs."""
    response = client.get("/job/totally-unknown-id")
    assert response.status_code == 404


def test_get_job_status_after_submit():
    """GET /job/{job_id} returns job details immediately after submission."""
    pricing_resp = client.get("/pricing")
    models = pricing_resp.json()["models"]
    test_models = [m["model_name"] for m in models[:2]]

    submit_resp = client.post("/background/compare", json={
        "model_names": test_models,
        "input_tokens": 100,
        "output_tokens": 100,
    })
    assert submit_resp.status_code == 200
    job_id = submit_resp.json()["job_id"]

    status_resp = client.get(f"/job/{job_id}")
    assert status_resp.status_code == 200
    data = status_resp.json()

    assert data["job_id"] == job_id
    assert data["status"] in ("pending", "running", "completed", "failed")
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_job_reaches_completed_state():
    """A background job eventually transitions to completed with a valid result."""
    from src.main import _run_compare_background

    jm = get_job_manager()
    job = jm.create_job()

    pricing_resp = client.get("/pricing")
    models = pricing_resp.json()["models"]
    test_models = [m["model_name"] for m in models[:2]]

    request_data = {
        "model_names": test_models,
        "input_tokens": 500,
        "output_tokens": 500,
        "chunk_size": 5,
    }

    await _run_compare_background(job.job_id, request_data)

    updated_job = jm.get_job(job.job_id)
    assert updated_job.status == JobStatus.COMPLETED
    assert updated_job.result is not None
    assert updated_job.result.get("success") is True
    assert "models" in updated_job.result


@pytest.mark.asyncio
async def test_job_reaches_failed_state_on_error():
    """A background job transitions to failed when the tool raises an error."""
    from src.main import _run_compare_background

    jm = get_job_manager()
    job = jm.create_job()

    request_data = {
        "model_names": ["gpt-4"],
        "input_tokens": 100,
        "output_tokens": 100,
    }

    with patch(
        "mcp.tools.compare_costs.CompareCostsTool.execute",
        new_callable=AsyncMock,
        side_effect=RuntimeError("pricing service down"),
    ):
        await _run_compare_background(job.job_id, request_data)

    updated_job = jm.get_job(job.job_id)
    assert updated_job.status == JobStatus.FAILED
    assert updated_job.error == "pricing service down"
    assert updated_job.result is None
