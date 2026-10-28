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
    customer = await svc.create_customer("alice@example.com")
    assert customer.email == "alice@example.com"
    assert customer.tier == "free"
    assert len(customer.api_key) > 10
    assert customer.org_id


@pytest.mark.asyncio
async def test_create_customer_new_email(svc):
    customer = await svc.create_customer("nina@example.com")
    assert customer is not None
    assert customer.email == "nina@example.com" and customer.tier == "free"
    assert len(customer.api_key) > 10 and customer.org_id
    assert (await svc.get_customer_by_api_key(customer.api_key)).id == customer.id


@pytest.mark.asyncio
async def test_create_customer_existing_email_returns_none(svc):
    """Public signup uses this: a taken email yields None, never the existing record
    (which carries the service key), and the existing account is left untouched."""
    first = await svc.create_customer("owen@example.com")
    assert await svc.create_customer("owen@example.com") is None
    still = await svc.get_customer_by_api_key(first.api_key)
    assert still is not None and still.id == first.id


@pytest.mark.asyncio
async def test_create_customer_concurrent_same_email_creates_one(svc):
    import asyncio

    results = await asyncio.gather(*(svc.create_customer("pat@example.com") for _ in range(5)))
    created = [r for r in results if r is not None]
    assert len(created) == 1


@pytest.mark.asyncio
async def test_get_by_api_key(svc):
    customer = await svc.create_customer("carol@example.com")
    found = await svc.get_customer_by_api_key(customer.api_key)
    assert found is not None
    assert found.email == "carol@example.com"


@pytest.mark.asyncio
async def test_get_unknown_key_returns_none(svc):
    result = await svc.get_customer_by_api_key("nonexistent-key-xyz")
    assert result is None


@pytest.mark.asyncio
async def test_update_tier(svc):
    customer = await svc.create_customer("dave@example.com")
    assert customer.tier == "free"
    await svc.update_tier(customer.id, "pro", stripe_customer_id="cus_123")
    updated = await svc.get_customer_by_api_key(customer.api_key)
    assert updated.tier == "pro"
    assert updated.stripe_customer_id == "cus_123"


@pytest.mark.asyncio
async def test_get_by_stripe_id(svc):
    customer = await svc.create_customer("frank@example.com")
    await svc.update_tier(customer.id, "pro", stripe_customer_id="cus_abc")
    found = await svc.get_customer_by_stripe_id("cus_abc")
    assert found is not None
    assert found.email == "frank@example.com"
