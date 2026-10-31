"""Pydantic models for the billing / Stripe subscription flow."""
from pydantic import BaseModel, EmailStr, Field
from typing import Any, Dict, List, Optional


class SignupRequest(BaseModel):
    email: EmailStr


class SignupResponse(BaseModel):
    api_key: str
    org_id: str
    tier: str
    message: str


class CheckoutRequest(BaseModel):
    tier: str  # "pro" | "enterprise"


class CheckoutResponse(BaseModel):
    checkout_url: str


class BillingPortalResponse(BaseModel):
    portal_url: str


class CustomerDashboard(BaseModel):
    email: str
    tier: str
    org_id: str
    api_key_preview: str        # first 8 chars + "..."
    router_calls_30d: int
    savings_per_1m_30d: float
    acceptance_rate: Optional[float]
    # v1.84.0 — payment state and the tier actually served (free once past_due exceeds the grace period)
    effective_tier: str = "free"
    billing_status: str = "active"
    past_due_since: Optional[float] = None
    rate_limit_per_minute: int = 0


class BillingConfigResponse(BaseModel):
    """v1.84.0 — GET /billing/config: what this server can sell and the tier catalogue."""
    checkout_enabled: bool
    portal_enabled: bool
    past_due_grace_days: int
    tiers: List[Dict[str, Any]]


class KeyRotateResponse(BaseModel):
    """v1.84.0 — the new key, shown exactly once."""
    api_key: str
    org_id: str
    message: str


class SupportChatRequest(BaseModel):
    """Request body for POST /support/chat (v1.84.0)."""
    message: str = Field(..., min_length=1, max_length=10_000,
                         description="Support question (max 10,000 characters)")
    conversation_id: Optional[str] = Field(
        None, max_length=64,
        description="Server-minted id ('sup-' + uuid4) from a previous reply, to continue the conversation"
    )


class SupportChatResponse(BaseModel):
    reply: str
    conversation_id: str
    identified: bool
    tier: Optional[str] = None
    escalation: Optional[Dict[str, Any]] = None   # {reference, public_summary}
    fallback: Optional[str] = None                # spend_cap | support_disabled | ledger_unavailable
    sources: List[str] = []


class EscalationStatusRequest(BaseModel):
    """POST /support/escalations/status — the reference travels in the body, never the URL."""
    reference: str = Field(..., min_length=1, max_length=40)
