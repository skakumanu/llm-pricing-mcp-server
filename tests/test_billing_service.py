"""Unit tests for BillingService."""
import sys
from pathlib import Path

import pytest
import pytest_asyncio

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.billing_service import BillingService  # noqa: E402


@pytest_asyncio.fixture
async def svc(tmp_path):
    s = BillingService(str(tmp_path / "billing.db"))
    await s.initialize()
    return s


@pytest.mark.asyncio
async def test_create_customer(svc):
    customer = await svc.get_or_create_customer("alice@example.com")
    assert customer.email == "alice@example.com"
    assert customer.tier == "free"
    assert len(customer.api_key) > 10
    assert customer.org_id


@pytest.mark.asyncio
async def test_idempotent_signup(svc):
    c1 = await svc.get_or_create_customer("bob@example.com")
    c2 = await svc.get_or_create_customer("bob@example.com")
    assert c1.api_key == c2.api_key
    assert c1.id == c2.id
    assert c1.org_id == c2.org_id


@pytest.mark.asyncio
async def test_get_by_api_key(svc):
    customer = await svc.get_or_create_customer("carol@example.com")
    found = await svc.get_customer_by_api_key(customer.api_key)
    assert found is not None
    assert found.email == "carol@example.com"


@pytest.mark.asyncio
async def test_get_unknown_key_returns_none(svc):
    result = await svc.get_customer_by_api_key("nonexistent-key-xyz")
    assert result is None


@pytest.mark.asyncio
async def test_update_tier(svc):
    customer = await svc.get_or_create_customer("dave@example.com")
    assert customer.tier == "free"
    await svc.update_tier(customer.id, "pro", stripe_customer_id="cus_123")
    updated = await svc.get_customer_by_api_key(customer.api_key)
    assert updated.tier == "pro"
    assert updated.stripe_customer_id == "cus_123"


@pytest.mark.asyncio
async def test_duplicate_email_same_key(svc):
    """Repeated signups with the same email return identical keys."""
    first = await svc.get_or_create_customer("eve@example.com")
    second = await svc.get_or_create_customer("eve@example.com")
    assert first.api_key == second.api_key


@pytest.mark.asyncio
async def test_get_by_stripe_id(svc):
    customer = await svc.get_or_create_customer("frank@example.com")
    await svc.update_tier(customer.id, "pro", stripe_customer_id="cus_abc")
    found = await svc.get_customer_by_stripe_id("cus_abc")
    assert found is not None
    assert found.email == "frank@example.com"
