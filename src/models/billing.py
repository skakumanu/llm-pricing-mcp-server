"""Pydantic models for the billing / Stripe subscription flow."""
from pydantic import BaseModel, EmailStr
from typing import Optional


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
