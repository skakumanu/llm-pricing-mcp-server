"""Request models for the orchestrator core (v1.83.0).

Bodies are bounded because the service stores redacted summaries of them. Free text
(``data.title`` / ``data.body`` / ``data.message``) is accepted here only to be redacted,
truncated and flagged by orchestrator_workflows.summarize_untrusted; it never reaches
routing. Secrets are never modelled: there is no field for a key or token anywhere.
"""
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

TriggerClass = Literal["customer_message", "github_event", "schedule", "owner_request"]
Decision = Literal["approve", "reject", "edit_approve", "done_manually", "skip"]


class EventSource(BaseModel):
    kind: str = Field(..., max_length=50, description="e.g. github, actions, schedule")
    ref: Optional[str] = Field(None, max_length=200, description="opaque reference, e.g. a run URL")


class OrchestratorEventIn(BaseModel):
    """POST /orchestrator/events — one inbound item from a trigger class."""

    trigger_class: TriggerClass
    event_type: str = Field(..., max_length=100, description="e.g. issues.labeled, pull_request.opened, push.master")
    dedup_key: str = Field(..., min_length=4, max_length=220,
                           description="Idempotency key: gh:issue:<n>:accepted, gh:pr:<m>:opened, gh:master:<sha>, "
                                       "schedule:price-audit:<ISO week>")
    actor: Optional[str] = Field(None, max_length=100, description="Sender login (GitHub) or 'schedule'")
    actor_is_owner: bool = Field(False, description="Asserted by the executor from sender.login == repository_owner")
    delivery_ref: Optional[str] = Field(None, max_length=200, description="Delivery / workflow-run id, for the log")
    source: Optional[EventSource] = None
    data: Dict[str, Any] = Field(default_factory=dict,
                                 description="Structured fields (issue_number, labels, label, pr_number, pr_url, sha, "
                                             "jobs, orchestrator_run_id) plus untrusted title/body carried as data")


class OwnerRunIn(BaseModel):
    """POST /orchestrator/runs — the owner starts a workflow directly."""

    workflow_id: str = Field(..., max_length=60)
    request_id: str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$",
                            description="Owner-chosen idempotency id; the same id attaches to the existing run")
    inputs: Dict[str, Any] = Field(default_factory=dict)
    mode: Optional[Literal["standard", "supervised", "dry_run"]] = Field(
        None, description="May only add supervision over the workflow default")


class ProvenanceEcho(BaseModel):
    run_id: str = Field(..., max_length=40)
    action_id: str = Field(..., max_length=60)


class ActionResultIn(BaseModel):
    """POST /orchestrator/runs/{run_id}/actions/{action_id}/result — executor reports back."""

    outcome: Literal["success", "failed"]
    external_ref: Optional[str] = Field(None, max_length=300, description="e.g. the comment URL")
    artifact_hash: Optional[str] = Field(None, max_length=64, description="Must echo the dispatched artifact hash")
    provenance_echo: Optional[ProvenanceEcho] = None
    error_code: Optional[str] = Field(None, max_length=60)


class ApprovalDecisionIn(BaseModel):
    """POST /orchestrator/approvals/{approval_id}."""

    decision: Decision
    note: Optional[str] = Field(None, max_length=1000)
    artifact: Optional[Dict[str, Any]] = Field(None, description="edit_approve: the edited artifact ({body})")
    artifact_hash: Optional[str] = Field(None, max_length=64, description="Optional: the hash you are approving")


class RunCloseIn(BaseModel):
    note: Optional[str] = Field(None, max_length=1000)
    result: Literal["deliberately_not_done", "failed"] = "deliberately_not_done"


class SettingsIn(BaseModel):
    """PUT /orchestrator/settings — owner control surface, outside any run."""

    changes: Dict[str, Any] = Field(..., min_length=1, max_length=40)
