"""Background job manager for long-running async tasks."""
import uuid
import asyncio
from enum import Enum
from datetime import datetime, timezone
from typing import Any, Dict, Optional

UTC = timezone.utc


class JobStatus(str, Enum):
    """Possible states for a background job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Job:
    """Represents a single background job with status and result tracking."""

    def __init__(self, job_id: str):
        self.job_id: str = job_id
        self.status: JobStatus = JobStatus.PENDING
        self.result: Optional[Any] = None
        self.error: Optional[str] = None
        self.created_at: datetime = datetime.now(UTC)
        self.updated_at: datetime = datetime.now(UTC)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the job to a plain dictionary."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class JobManager:
    """In-memory store and coordinator for background jobs."""

    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create the asyncio.Lock on first async use."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def create_job(self) -> Job:
        """Create a new job with a unique ID and return it."""
        job_id = str(uuid.uuid4())
        job = Job(job_id)
        self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        """Return the job with the given ID, or None if not found."""
        return self._jobs.get(job_id)

    async def update_job(
        self,
        job_id: str,
        status: JobStatus,
        result: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update the status, result, and error for an existing job."""
        async with self._get_lock():
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = status
                job.result = result
                job.error = error
                job.updated_at = datetime.now(UTC)


# Module-level singleton used by the FastAPI application.
_job_manager: Optional[JobManager] = None


def get_job_manager() -> JobManager:
    """Return the application-wide JobManager singleton."""
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager()
    return _job_manager
