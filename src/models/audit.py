"""Request model for the compliance audit routes (v1.88.0)."""
from typing import Optional

from pydantic import BaseModel, Field


class AuditRunIn(BaseModel):
    """POST /audit/run — the owner starts an on-demand audit."""

    request_id: Optional[str] = Field(
        None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$",
        description="Optional idempotency id; omitted = a fresh id, so every click runs a new audit. "
                    "The same id attaches to the existing run.")
