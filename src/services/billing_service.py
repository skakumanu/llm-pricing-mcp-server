"""Billing service — SQLite-backed customer/subscription management."""
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import aiosqlite

_billing_service: Optional["BillingService"] = None

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    api_key TEXT NOT NULL UNIQUE,
    tier TEXT NOT NULL DEFAULT 'free',
    org_id TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""


@dataclass
class CustomerRecord:
    id: str
    email: str
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    api_key: str
    tier: str
    org_id: str
    created_at: float
    updated_at: float


def _row_to_customer(row) -> CustomerRecord:
    return CustomerRecord(
        id=row[0],
        email=row[1],
        stripe_customer_id=row[2],
        stripe_subscription_id=row[3],
        api_key=row[4],
        tier=row[5],
        org_id=row[6],
        created_at=row[7],
        updated_at=row[8],
    )


class BillingService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.commit()

    async def get_or_create_customer(self, email: str) -> CustomerRecord:
        """Idempotent free-tier signup — returns existing customer if email already registered."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT id, email, stripe_customer_id, stripe_subscription_id, "
                "api_key, tier, org_id, created_at, updated_at FROM customers WHERE email = ?",
                (email,),
            ) as cur:
                row = await cur.fetchone()
            if row:
                return _row_to_customer(row)

            now = time.time()
            customer_id = str(uuid.uuid4())
            api_key = secrets.token_urlsafe(32)
            org_id = str(uuid.uuid4())
            await db.execute(
                "INSERT INTO customers "
                "(id, email, stripe_customer_id, stripe_subscription_id, api_key, tier, org_id, created_at, updated_at) "
                "VALUES (?, ?, NULL, NULL, ?, 'free', ?, ?, ?)",
                (customer_id, email, api_key, org_id, now, now),
            )
            await db.commit()
            return CustomerRecord(
                id=customer_id,
                email=email,
                stripe_customer_id=None,
                stripe_subscription_id=None,
                api_key=api_key,
                tier="free",
                org_id=org_id,
                created_at=now,
                updated_at=now,
            )

    async def get_customer_by_api_key(self, api_key: str) -> Optional[CustomerRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT id, email, stripe_customer_id, stripe_subscription_id, "
                "api_key, tier, org_id, created_at, updated_at FROM customers WHERE api_key = ?",
                (api_key,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_customer(row) if row else None

    async def get_customer_by_stripe_id(self, stripe_customer_id: str) -> Optional[CustomerRecord]:
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT id, email, stripe_customer_id, stripe_subscription_id, "
                "api_key, tier, org_id, created_at, updated_at "
                "FROM customers WHERE stripe_customer_id = ?",
                (stripe_customer_id,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_customer(row) if row else None

    async def get_all_customers(self, limit: int = 500) -> list:
        """Return all customers ordered by created_at desc (for admin view)."""
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT id, email, stripe_customer_id, stripe_subscription_id, "
                "api_key, tier, org_id, created_at, updated_at "
                "FROM customers ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_customer(r) for r in rows]

    async def update_tier(
        self,
        customer_id: str,
        tier: str,
        stripe_customer_id: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
    ) -> None:
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            if stripe_customer_id is not None and stripe_subscription_id is not None:
                await db.execute(
                    "UPDATE customers SET tier = ?, stripe_customer_id = ?, "
                    "stripe_subscription_id = ?, updated_at = ? WHERE id = ?",
                    (tier, stripe_customer_id, stripe_subscription_id, now, customer_id),
                )
            elif stripe_customer_id is not None:
                await db.execute(
                    "UPDATE customers SET tier = ?, stripe_customer_id = ?, updated_at = ? WHERE id = ?",
                    (tier, stripe_customer_id, now, customer_id),
                )
            else:
                await db.execute(
                    "UPDATE customers SET tier = ?, updated_at = ? WHERE id = ?",
                    (tier, now, customer_id),
                )
            await db.commit()


async def init_billing_service(db_path: str) -> BillingService:
    global _billing_service
    _billing_service = BillingService(db_path)
    await _billing_service.initialize()
    return _billing_service


def get_billing_service() -> BillingService:
    if _billing_service is None:
        raise RuntimeError("Billing service not initialized")
    return _billing_service
