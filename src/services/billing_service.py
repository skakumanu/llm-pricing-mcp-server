"""Billing service — SQLite-backed customer/subscription management."""
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _open_db(db_path: str):
    """Open an aiosqlite connection with WAL mode and busy timeout pre-configured."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        yield db

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
    updated_at REAL NOT NULL,
    billing_status TEXT NOT NULL DEFAULT 'active',
    past_due_since REAL
)
"""

# v1.84.0: columns added to an existing customers table (PRAGMA table_info-guarded).
_ADDED_COLUMNS = (
    ("billing_status", "TEXT NOT NULL DEFAULT 'active'"),
    ("past_due_since", "REAL"),
)

# v1.84.0: Stripe event ids seen, so a redelivered webhook event is ignored (AC18).
_CREATE_STRIPE_EVENTS = """
CREATE TABLE IF NOT EXISTS stripe_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    received_at REAL NOT NULL
)
"""

_COLUMNS = (
    "id, email, stripe_customer_id, stripe_subscription_id, api_key, tier, org_id, created_at, updated_at, "
    "billing_status, past_due_since"
)

BILLING_STATUSES = ("active", "past_due")


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
    # v1.84.0 — 'active' | 'past_due'; past_due_since is set when a renewal payment fails.
    billing_status: str = "active"
    past_due_since: Optional[float] = None


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
        billing_status=row[9] if len(row) > 9 and row[9] else "active",
        past_due_since=row[10] if len(row) > 10 else None,
    )


def effective_tier(record: Optional[CustomerRecord], grace_days: int, now: Optional[float] = None) -> str:
    """The tier a customer is served at right now.

    Pure helper (no I/O). The stored ``tier`` changes only through Stripe events; a
    ``past_due`` customer keeps it for ``grace_days`` after ``past_due_since`` and is then
    served as ``free`` until the invoice is paid. Used by the rate-limit middleware, the
    MCP caller resolver, ``/billing/me`` and the support agent's ``get_my_account``.
    """
    if record is None:
        return "free"
    tier = record.tier or "free"
    if getattr(record, "billing_status", "active") != "past_due":
        return tier
    since = getattr(record, "past_due_since", None)
    if since is None:
        return tier
    now = time.time() if now is None else now
    if now - float(since) > max(0, int(grace_days)) * 86400:
        return "free"
    return tier


class BillingService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        # Ensure the parent directory exists (important for Fly.io volume mounts)
        db_path = Path(self._db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Initializing billing DB at %s", self._db_path)
        async with _open_db(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            # Older databases: add the v1.84.0 columns only when they are missing.
            async with db.execute("PRAGMA table_info(customers)") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            for name, decl in _ADDED_COLUMNS:
                if name not in existing:
                    await db.execute(f"ALTER TABLE customers ADD COLUMN {name} {decl}")  # nosec B608 — literals
            await db.execute(_CREATE_STRIPE_EVENTS)
            await db.commit()

    async def create_customer(self, email: str) -> Optional[CustomerRecord]:
        """Create a free-tier customer for a new email, or return None if it is taken.

        Public signup uses this. There is deliberately no "get or create": an
        existing customer's record carries its service key and signup is
        unauthenticated, so handing it back would give anyone who knows an email
        address that org's key (the pre-v1.78.1 behaviour). A concurrent signup
        that loses the UNIQUE(email) race also yields None.
        """
        now = time.time()
        customer_id = str(uuid.uuid4())
        api_key = secrets.token_urlsafe(32)
        org_id = str(uuid.uuid4())
        async with _open_db(self._db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO customers "
                    "(id, email, stripe_customer_id, stripe_subscription_id, api_key, tier, org_id, "
                    "created_at, updated_at, billing_status, past_due_since) "
                    "VALUES (?, ?, NULL, NULL, ?, 'free', ?, ?, ?, 'active', NULL)",
                    (customer_id, email, api_key, org_id, now, now),
                )
                await db.commit()
            except aiosqlite.IntegrityError:
                return None
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

    async def _one(self, where: str, value: str) -> Optional[CustomerRecord]:
        async with _open_db(self._db_path) as db:
            async with db.execute(
                f"SELECT {_COLUMNS} FROM customers WHERE {where} = ?", (value,)  # nosec B608 — column literals
            ) as cur:
                row = await cur.fetchone()
        return _row_to_customer(row) if row else None

    async def get_customer_by_api_key(self, api_key: str) -> Optional[CustomerRecord]:
        return await self._one("api_key", api_key)

    async def get_customer_by_stripe_id(self, stripe_customer_id: str) -> Optional[CustomerRecord]:
        return await self._one("stripe_customer_id", stripe_customer_id)

    async def get_customer_by_id(self, customer_id: str) -> Optional[CustomerRecord]:
        """v1.84.0: lookup by primary key (the support agent resolves the caller's own record)."""
        return await self._one("id", customer_id)

    async def get_all_customers(self, limit: int = 500) -> list:
        """Return all customers ordered by created_at desc (for admin view)."""
        async with _open_db(self._db_path) as db:
            async with db.execute(
                f"SELECT {_COLUMNS} FROM customers ORDER BY created_at DESC LIMIT ?",  # nosec B608
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
        billing_status: Optional[str] = None,
    ) -> None:
        """Set the stored tier (Stripe events only). ``billing_status`` optionally resets the
        payment state at the same time ('active' clears past_due_since)."""
        now = time.time()
        sets = ["tier = ?", "updated_at = ?"]
        params: list = [tier, now]
        if stripe_customer_id is not None:
            sets.append("stripe_customer_id = ?")
            params.append(stripe_customer_id)
        if stripe_subscription_id is not None:
            sets.append("stripe_subscription_id = ?")
            params.append(stripe_subscription_id)
        if billing_status is not None:
            if billing_status not in BILLING_STATUSES:
                raise ValueError(f"unknown billing_status {billing_status!r}")
            sets.append("billing_status = ?")
            params.append(billing_status)
            if billing_status == "active":
                sets.append("past_due_since = NULL")
        params.append(customer_id)
        async with _open_db(self._db_path) as db:
            await db.execute(
                "UPDATE customers SET " + ", ".join(sets) + " WHERE id = ?", params  # nosec B608 — literals
            )
            await db.commit()

    async def set_billing_status(self, customer_id: str, status: str,
                                 past_due_since: Optional[float] = None) -> None:
        """v1.84.0: 'past_due' records when the renewal failed; 'active' clears it."""
        if status not in BILLING_STATUSES:
            raise ValueError(f"unknown billing_status {status!r}")
        now = time.time()
        since = None if status == "active" else (past_due_since if past_due_since is not None else now)
        async with _open_db(self._db_path) as db:
            await db.execute(
                "UPDATE customers SET billing_status = ?, past_due_since = ?, updated_at = ? WHERE id = ?",
                (status, since, now, customer_id),
            )
            await db.commit()

    async def rotate_api_key(self, customer_id: str) -> Optional[str]:
        """v1.84.0: replace the service key. The old key stops working at once. Returns the new
        key (shown once by the caller) or None when the customer does not exist."""
        new_key = secrets.token_urlsafe(32)
        now = time.time()
        async with _open_db(self._db_path) as db:
            cur = await db.execute(
                "UPDATE customers SET api_key = ?, updated_at = ? WHERE id = ?", (new_key, now, customer_id)
            )
            await db.commit()
            if (cur.rowcount or 0) != 1:
                return None
        return new_key

    async def record_stripe_event(self, event_id: str, event_type: str) -> bool:
        """v1.84.0: INSERT OR IGNORE the Stripe event id. False = already seen (duplicate)."""
        async with _open_db(self._db_path) as db:
            cur = await db.execute(
                "INSERT OR IGNORE INTO stripe_events (event_id, event_type, received_at) VALUES (?, ?, ?)",
                (str(event_id), str(event_type)[:100], time.time()),
            )
            await db.commit()
            return (cur.rowcount or 0) == 1

    async def forget_stripe_event(self, event_id: str) -> None:
        """v1.84.0: drop a recorded event id after applying it failed, so Stripe's retry is processed."""
        async with _open_db(self._db_path) as db:
            await db.execute("DELETE FROM stripe_events WHERE event_id = ?", (str(event_id),))
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
