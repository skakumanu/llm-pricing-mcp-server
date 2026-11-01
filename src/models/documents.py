"""Request model for the document writer routes (v1.89.0)."""
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class DocGenerateIn(BaseModel):
    """POST /documents/generate: the owner asks for one document from the closed catalogue."""

    doc_type: Literal["release_notes_brief", "pricing_comparison_deck", "pricing_one_pager",
                      "internal_status_report"] = Field(..., description="One of the four catalogue types")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific parameters (see GET /documents/types); omitted ones fall back to the "
                    "defaults, which the document discloses")
    format: Literal["html", "markdown", "both"] = Field("both", description="Formats the artefact is meant for")
    data_only: Optional[bool] = Field(
        None, description="true = templated prose and zero model calls; omitted = DOCS_DATA_ONLY_DEFAULT")
    premium: bool = Field(False, description="Draft the narrative with the premium model (DOCS_PREMIUM_MODEL)")
    request_id: Optional[str] = Field(
        None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$",
        description="Optional idempotency id; omitted = a fresh id, so every request makes a new document")
