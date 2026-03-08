"""Tests for request-splitting and background task functionality."""
import asyncio
import pytest
from unittest.mock import MagicMock

from mcp.tools.compare_costs import split_into_chunks, _compare_chunk, DEFAULT_CHUNK_SIZE
from src.services.background_tasks import BackgroundTaskService, JobStatus, get_background_task_service


# ---------------------------------------------------------------------------
# split_into_chunks
# ---------------------------------------------------------------------------

class TestSplitIntoChunks:
    """Unit tests for the split_into_chunks helper."""

    def test_even_split(self):
        items = list(range(10))
        chunks = split_into_chunks(items, 5)
        assert len(chunks) == 2
        assert chunks[0] == [0, 1, 2, 3, 4]
        assert chunks[1] == [5, 6, 7, 8, 9]

    def test_uneven_split(self):
        items = list(range(7))
        chunks = split_into_chunks(items, 3)
        assert len(chunks) == 3
        assert chunks[0] == [0, 1, 2]
        assert chunks[1] == [3, 4, 5]
        assert chunks[2] == [6]

    def test_chunk_size_larger_than_list(self):
        items = [1, 2, 3]
        chunks = split_into_chunks(items, 10)
        assert len(chunks) == 1
        assert chunks[0] == [1, 2, 3]

    def test_chunk_size_one(self):
        items = ["a", "b", "c"]
        chunks = split_into_chunks(items, 1)
        assert len(chunks) == 3
        assert chunks == [["a"], ["b"], ["c"]]

    def test_empty_list(self):
        chunks = split_into_chunks([], 5)
        assert chunks == []

    def test_invalid_chunk_size_raises(self):
        with pytest.raises(ValueError, match="chunk_size must be at least 1"):
            split_into_chunks([1, 2, 3], 0)

    def test_default_chunk_size_constant_is_positive(self):
        assert DEFAULT_CHUNK_SIZE == 10

    def test_preserves_order(self):
        items = list(range(20))
        chunks = split_into_chunks(items, 6)
        merged = [item for chunk in chunks for item in chunk]
        assert merged == items


# ---------------------------------------------------------------------------
# _compare_chunk
# ---------------------------------------------------------------------------

class TestCompareChunk:
    """Unit tests for the _compare_chunk async helper."""

    def _make_pricing(self, model_name, provider, input_cost, output_cost):
        m = MagicMock()
        m.model_name = model_name
        m.provider = provider
        m.cost_per_input_token = input_cost
        m.cost_per_output_token = output_cost
        return m

    @pytest.mark.asyncio
    async def test_known_models_are_calculated(self):
        pricing = self._make_pricing("gpt-4", "OpenAI", 0.03, 0.06)
        pricing_map = {"gpt-4": pricing}

        result = await _compare_chunk(["gpt-4"], 1000, 500, pricing_map)

        assert len(result["comparisons"]) == 1
        comp = result["comparisons"][0]
        assert comp["is_available"] is True
        assert comp["model_name"] == "gpt-4"
        assert comp["provider"] == "OpenAI"
        assert comp["input_cost"] == round((0.03 / 1000) * 1000, 6)
        assert comp["output_cost"] == round((0.06 / 1000) * 500, 6)

    @pytest.mark.asyncio
    async def test_unknown_model_is_flagged(self):
        result = await _compare_chunk(["unknown-model"], 1000, 500, {})

        assert len(result["comparisons"]) == 1
        comp = result["comparisons"][0]
        assert comp["is_available"] is False
        assert "not found" in comp["error"]

    @pytest.mark.asyncio
    async def test_costs_tuple_only_for_available_models(self):
        pricing = self._make_pricing("claude-3", "Anthropic", 0.015, 0.075)
        pricing_map = {"claude-3": pricing}

        result = await _compare_chunk(["claude-3", "missing-model"], 100, 100, pricing_map)

        # Only the known model contributes to costs
        assert len(result["costs"]) == 1
        assert result["costs"][0][0] == "claude-3"

    @pytest.mark.asyncio
    async def test_zero_tokens_cost_per_1m_is_zero(self):
        pricing = self._make_pricing("gpt-3.5-turbo", "OpenAI", 0.001, 0.002)
        pricing_map = {"gpt-3.5-turbo": pricing}

        result = await _compare_chunk(["gpt-3.5-turbo"], 0, 0, pricing_map)

        comp = result["comparisons"][0]
        assert comp["cost_per_1m_tokens"] == 0

    @pytest.mark.asyncio
    async def test_lookup_is_case_insensitive(self):
        pricing = self._make_pricing("GPT-4", "OpenAI", 0.03, 0.06)
        pricing_map = {"gpt-4": pricing}

        result = await _compare_chunk(["GPT-4"], 100, 100, pricing_map)
        assert result["comparisons"][0]["is_available"] is True


# ---------------------------------------------------------------------------
# BackgroundTaskService
# ---------------------------------------------------------------------------

class TestBackgroundTaskService:
    """Unit tests for the BackgroundTaskService."""

    @pytest.mark.asyncio
    async def test_submit_job_returns_string_id(self):
        svc = BackgroundTaskService()

        async def noop():
            return {"ok": True}

        job_id = await svc.submit_job(noop())
        assert isinstance(job_id, str)
        assert len(job_id) > 0

    @pytest.mark.asyncio
    async def test_submitted_job_is_initially_pending_or_running(self):
        svc = BackgroundTaskService()

        async def slow_task():
            await asyncio.sleep(0.05)
            return {"done": True}

        job_id = await svc.submit_job(slow_task())
        record = await svc.get_job(job_id)
        assert record is not None
        assert record.status in (JobStatus.PENDING, JobStatus.RUNNING)

    @pytest.mark.asyncio
    async def test_completed_job_stores_result(self):
        svc = BackgroundTaskService()

        async def fast_task():
            return {"value": 42}

        job_id = await svc.submit_job(fast_task())
        # Allow the task to run
        await asyncio.sleep(0.1)

        record = await svc.get_job(job_id)
        assert record is not None
        assert record.status == JobStatus.COMPLETED
        assert record.result == {"value": 42}
        assert record.error is None

    @pytest.mark.asyncio
    async def test_failed_job_stores_error(self):
        svc = BackgroundTaskService()

        async def failing_task():
            raise ValueError("something went wrong")

        job_id = await svc.submit_job(failing_task())
        await asyncio.sleep(0.1)

        record = await svc.get_job(job_id)
        assert record is not None
        assert record.status == JobStatus.FAILED
        assert "something went wrong" in record.error
        assert record.result is None

    @pytest.mark.asyncio
    async def test_get_job_returns_none_for_unknown_id(self):
        svc = BackgroundTaskService()
        record = await svc.get_job("nonexistent-id")
        assert record is None

    @pytest.mark.asyncio
    async def test_multiple_jobs_are_independent(self):
        svc = BackgroundTaskService()

        async def task_a():
            return "result_a"

        async def task_b():
            return "result_b"

        id_a = await svc.submit_job(task_a())
        id_b = await svc.submit_job(task_b())
        await asyncio.sleep(0.1)

        rec_a = await svc.get_job(id_a)
        rec_b = await svc.get_job(id_b)

        assert rec_a.result == "result_a"
        assert rec_b.result == "result_b"

    @pytest.mark.asyncio
    async def test_updated_at_advances_after_completion(self):
        svc = BackgroundTaskService()

        async def task():
            return "done"

        job_id = await svc.submit_job(task())
        initial_record = await svc.get_job(job_id)
        initial_updated = initial_record.updated_at

        await asyncio.sleep(0.1)

        final_record = await svc.get_job(job_id)
        assert final_record.updated_at >= initial_updated

    def test_get_background_task_service_returns_singleton(self):
        svc1 = get_background_task_service()
        svc2 = get_background_task_service()
        assert svc1 is svc2


# ---------------------------------------------------------------------------
# /compare and /background/compare and /job/{job_id} endpoints
# ---------------------------------------------------------------------------

class TestCompareEndpoints:
    """Integration-style tests for the new FastAPI endpoints."""

    def setup_method(self):
        from fastapi.testclient import TestClient
        from src.main import app
        self.client = TestClient(app)

    def test_compare_endpoint_returns_200(self):
        response = self.client.post(
            "/compare",
            json={"model_names": ["gpt-4"], "input_tokens": 1000, "output_tokens": 500},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "models" in data
        assert "cheapest_model" in data
        assert "currency" in data

    def test_compare_endpoint_handles_unknown_model(self):
        response = self.client.post(
            "/compare",
            json={"model_names": ["totally-nonexistent-xyz"], "input_tokens": 100, "output_tokens": 100},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["models"][0]["is_available"] is False

    def test_compare_endpoint_multiple_models_and_chunking(self):
        """Verify chunked processing merges results correctly."""
        model_names = ["gpt-4", "gpt-3.5-turbo"]
        response = self.client.post(
            "/compare",
            json={"model_names": model_names, "input_tokens": 1000, "output_tokens": 500, "chunk_size": 1},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["models"]) == 2

    def test_compare_endpoint_returns_cost_range(self):
        model_names = ["gpt-4", "gpt-3.5-turbo"]
        response = self.client.post(
            "/compare",
            json={"model_names": model_names, "input_tokens": 1000, "output_tokens": 500},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["cost_range"] is not None
        assert "min" in data["cost_range"]
        assert "max" in data["cost_range"]

    def test_background_compare_returns_202_with_job_id(self):
        response = self.client.post(
            "/background/compare",
            json={"model_names": ["gpt-4"], "input_tokens": 1000, "output_tokens": 500},
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert len(data["job_id"]) > 0
        assert data["status"] in ("pending", "running")

    def test_get_job_status_404_for_unknown_id(self):
        response = self.client.get("/job/nonexistent-job-id")
        assert response.status_code == 404

    def test_get_job_status_returns_job_record(self):
        submit_response = self.client.post(
            "/background/compare",
            json={"model_names": ["gpt-4"], "input_tokens": 500, "output_tokens": 500},
        )
        assert submit_response.status_code == 202
        job_id = submit_response.json()["job_id"]

        poll_response = self.client.get(f"/job/{job_id}")
        assert poll_response.status_code == 200
        data = poll_response.json()
        assert data["job_id"] == job_id
        assert "status" in data
