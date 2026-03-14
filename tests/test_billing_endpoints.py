"""Integration tests for billing endpoints."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.services.billing_service import BillingService, CustomerRecord  # noqa: E402
import time  # noqa: E402


def _make_customer(**kwargs):
    defaults = dict(
        id="cust-123",
        email="test@example.com",
        stripe_customer_id=None,
        stripe_subscription_id=None,
        api_key="test-api-key-abc",
        tier="free",
        org_id="org-abc-123",
        created_at=time.time(),
        updated_at=time.time(),
    )
    defaults.update(kwargs)
    return CustomerRecord(**defaults)


def _client():
    from src.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /billing/signup
# ---------------------------------------------------------------------------

def test_signup_success():
    customer = _make_customer(email="user@example.com")
    mock_svc = MagicMock()
    mock_svc.get_or_create_customer = AsyncMock(return_value=customer)

    with patch("src.main.get_billing_service", return_value=mock_svc):
        client = _client()
        resp = client.post("/billing/signup", json={"email": "user@example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert "api_key" in data
    assert data["tier"] == "free"
    assert "org_id" in data
    assert "message" in data


def test_signup_invalid_email():
    client = _client()
    resp = client.post("/billing/signup", json={"email": "not-an-email"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /billing/me
# ---------------------------------------------------------------------------

def test_billing_me_unauthenticated():
    client = _client()
    # No x-api-key header — should get 401 when mcp_api_key is not configured
    # (billing service will not find the key, and no global key → 401 only if
    #  mcp_api_key is set; otherwise middleware logs warning and proceeds)
    # We mock the billing service to return None for the key lookup
    mock_svc = MagicMock()
    mock_svc.get_customer_by_api_key = AsyncMock(return_value=None)
    with patch("src.main.get_billing_service", return_value=mock_svc):
        with patch("src.config.settings.settings") as mock_settings:
            mock_settings.mcp_api_key = "global-key"
            mock_settings.mcp_api_key_header = "x-api-key"
            mock_settings.rate_limit_per_minute = 10000
            mock_settings.rate_limit_free = 30
            mock_settings.rate_limit_pro = 120
            mock_settings.rate_limit_enterprise = 600
            mock_settings.max_body_bytes = 1_000_000
            resp = client.get("/billing/me")
    # Without key or with wrong key → 401
    assert resp.status_code == 401


def test_billing_me_with_mock_customer():
    customer = _make_customer()
    mock_svc = MagicMock()
    mock_svc.get_customer_by_api_key = AsyncMock(return_value=customer)

    mock_tracker = MagicMock()
    mock_tracker.get_savings = AsyncMock(return_value={
        "total": 5,
        "total_savings_per_1m": 1.23,
        "acceptance_rate": 0.8,
        "records": [],
    })

    with patch("src.main.get_billing_service", return_value=mock_svc):
        with patch("src.main.get_savings_tracker", return_value=mock_tracker):
            client = _client()
            resp = client.get("/billing/me", headers={"x-api-key": "test-api-key-abc"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "test@example.com"
    assert data["tier"] == "free"
    assert data["router_calls_30d"] == 5
    assert data["api_key_preview"] == "test-api" + "..."


# ---------------------------------------------------------------------------
# POST /billing/checkout
# ---------------------------------------------------------------------------

def test_checkout_no_stripe_key():
    customer = _make_customer()
    mock_svc = MagicMock()
    mock_svc.get_customer_by_api_key = AsyncMock(return_value=customer)

    with patch("src.main.get_billing_service", return_value=mock_svc):
        with patch("src.config.settings.settings.stripe_secret_key", None):
            client = _client()
            resp = client.post(
                "/billing/checkout",
                json={"tier": "pro"},
                headers={"x-api-key": "test-api-key-abc"},
            )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /billing/webhook
# ---------------------------------------------------------------------------

def test_webhook_bad_signature():
    with patch("src.config.settings.settings.stripe_webhook_secret", "whsec_test"):
        with patch("src.config.settings.settings.stripe_secret_key", "sk_test"):
            import stripe
            with patch.object(stripe.Webhook, "construct_event", side_effect=stripe.error.SignatureVerificationError("bad sig", "sig")):
                client = _client()
                resp = client.post(
                    "/billing/webhook",
                    content=b'{"type":"test"}',
                    headers={"stripe-signature": "bad"},
                )
    assert resp.status_code == 400


def test_webhook_subscription_deleted():
    customer = _make_customer(email="webhook@example.com", stripe_customer_id="cus_del_test", tier="pro")

    fake_event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_del_test"}},
    }

    mock_billing = MagicMock()
    mock_billing.get_customer_by_stripe_id = AsyncMock(return_value=customer)
    mock_billing.update_tier = AsyncMock()

    import stripe
    with patch("src.main.settings") as ms:
        ms.stripe_webhook_secret = "whsec_test"
        ms.stripe_secret_key = "sk_test"
        ms.stripe_price_id_pro = None
        ms.stripe_price_id_enterprise = None
        ms.mcp_api_key = None
        ms.mcp_api_key_header = "x-api-key"
        ms.rate_limit_per_minute = 10000
        ms.rate_limit_free = 30
        ms.rate_limit_pro = 120
        ms.rate_limit_enterprise = 600
        ms.max_body_bytes = 1_000_000
        with patch.object(stripe.Webhook, "construct_event", return_value=fake_event):
            with patch("src.main.get_billing_service", return_value=mock_billing):
                client = _client()
                resp = client.post(
                    "/billing/webhook",
                    content=b'{}',
                    headers={"stripe-signature": "t=1,v1=abc"},
                )
    assert resp.status_code == 200
    mock_billing.update_tier.assert_called_once_with(customer.id, "free")
