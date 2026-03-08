"""Background task service for handling long-running requests asynchronously."""
import asyncio
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Coroutine, Dict, Optional

from pydantic import BaseModel, Field

UTC = timezone.utc

# Sentinel used to distinguish "result not yet set" from a legitimate None result.
_UNSET = object()


class JobStatus(str, Enum):
    """Lifecycle status of a background job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobRecord(BaseModel):
    """Record tracking the state of a submitted background job."""

    model_config = {"arbitrary_types_allowed": True}

    job_id: str = Field(..., description="Unique job identifier (UUID)")
    status: JobStatus = Field(..., description="Current job status")
    result: Optional[Any] = Field(None, description="Job result when completed")
    error: Optional[str] = Field(None, description="Error message when failed")
    created_at: datetime = Field(..., description="UTC timestamp when the job was submitted")
    updated_at: datetime = Field(..., description="UTC timestamp of the last status update")


class BackgroundTaskService:
    """Manages async background jobs with in-memory status tracking.

    Jobs are identified by a UUID and progress through the lifecycle:
    PENDING → RUNNING → COMPLETED | FAILED

    Clients submit a coroutine via :meth:`submit_job` and receive a job ID
    they can pass to :meth:`get_job` to poll for the result.
    """

    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def submit_job(self, coro: Coroutine) -> str:
        """Submit a coroutine as a background job.

        The coroutine is scheduled immediately via :func:`asyncio.create_task`.

        Args:
            coro: The awaitable coroutine to run in the background.

        Returns:
            The UUID string that identifies this job.
        """
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        record = JobRecord(
            job_id=job_id,
            status=JobStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        async with self._lock:
            self._jobs[job_id] = record

        asyncio.create_task(self._run_job(job_id, coro))
        return job_id

    async def _run_job(self, job_id: str, coro: Coroutine) -> None:
        """Internal runner: execute *coro* and persist the outcome."""
        await self._update_job(job_id, status=JobStatus.RUNNING)
        try:
            result = await coro
            await self._update_job(job_id, status=JobStatus.COMPLETED, result=result)
        except Exception as exc:  # noqa: BLE001
            await self._update_job(job_id, status=JobStatus.FAILED, error=str(exc))

    async def _update_job(
        self,
        job_id: str,
        *,
        status: Optional[JobStatus] = None,
        result: Any = _UNSET,
        error: Optional[str] = None,
    ) -> None:
        """Atomically update mutable fields on the stored :class:`JobRecord`."""
        async with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            if status is not None:
                record.status = status
            if result is not _UNSET:
                record.result = result
            if error is not None:
                record.error = error
            record.updated_at = datetime.now(UTC)

    async def get_job(self, job_id: str) -> Optional[JobRecord]:
        """Return the current :class:`JobRecord` for *job_id*, or ``None``.

        Args:
            job_id: The UUID returned by :meth:`submit_job`.

        Returns:
            The job record, or ``None`` if the ID is unknown.
        """
        async with self._lock:
            return self._jobs.get(job_id)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_background_task_service: Optional[BackgroundTaskService] = None


def get_background_task_service() -> BackgroundTaskService:
    """Return the module-level singleton :class:`BackgroundTaskService`.

    The instance is created lazily on first call and reused thereafter.
    """
    global _background_task_service
    if _background_task_service is None:
        _background_task_service = BackgroundTaskService()
    return _background_task_service
