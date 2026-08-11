"""Usage tracker: persist caller-reported LLM usage events and report actual spend.

Unlike pricing_history.py (which snapshots catalogue prices) or savings_tracker.py
(which records routing recommendations), this records what a caller says they actually
spent — the "actual" counterpart to estimate_cost's hypothetical numbers. Cost is always
computed server-side from current pricing at ingestion time, never trusted from the
caller, so a usage event's cost reflects this server's pricing data, not a self-reported
figure.
"""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _open_db(db_path: str):
    """Open an aiosqlite connection with WAL mode and busy timeout pre-configured."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT,
    recorded_at REAL NOT NULL,
    occurred_at REAL NOT NULL,
    provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    request_id TEXT
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_usage_events_lookup
ON usage_events (org_id, occurred_at)
"""

# request_id is optional (NULL allowed), but when supplied it must be unique per org so
# a duplicate submission (e.g. a retried client call) is a no-op, not a double-count.
# SQLite treats each NULL as distinct under a UNIQUE constraint, so rows without a
# request_id never collide with each other.
_CREATE_DEDUPE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_events_dedupe
ON usage_events (org_id, request_id)
"""

_INSERT = """
INSERT OR IGNORE INTO usage_events
    (org_id, recorded_at, occurred_at, provider, model_name, input_tokens, output_tokens, cost_usd, request_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SUMMARY_TOTALS_QUERY = """
SELECT
    COUNT(*) AS total_requests,
    COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd,
    COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
    COALESCE(SUM(output_tokens), 0) AS total_output_tokens
FROM usage_events
WHERE occurred_at >= ?
{where_extra}
"""

_SUMMARY_BY_MODEL_QUERY = """
SELECT
    model_name,
    provider,
    COUNT(*) AS request_count,
    COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd
FROM usage_events
WHERE occurred_at >= ?
{where_extra}
GROUP BY model_name, provider
ORDER BY total_cost_usd DESC
"""

_SUMMARY_BY_PROVIDER_QUERY = """
SELECT
    provider,
    COUNT(*) AS request_count,
    COALESCE(SUM(cost_usd), 0.0) AS total_cost_usd
FROM usage_events
WHERE occurred_at >= ?
{where_extra}
GROUP BY provider
ORDER BY total_cost_usd DESC
"""


class UsageTrackerService:
    """Records caller-reported usage events to SQLite and reports per-org spend."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        """Create the usage_events table and indexes if they don't exist."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with _open_db(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_INDEX)
            await db.execute(_CREATE_DEDUPE_INDEX)
            await db.commit()

    async def record_event(
        self,
        provider: str,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        org_id: Optional[str] = None,
        occurred_at: Optional[float] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a usage event. Returns whether it was recorded or ignored as a duplicate."""
        occurred = occurred_at if occurred_at is not None else time.time()
        async with _open_db(self._db_path) as db:
            cursor = await db.execute(_INSERT, (
                org_id,
                time.time(),
                occurred,
                provider,
                model_name,
                input_tokens,
                output_tokens,
                cost_usd,
                request_id,
            ))
            await db.commit()
            duplicate = cursor.rowcount == 0 and request_id is not None
        return {"duplicate": duplicate}

    async def get_summary(
        self,
        org_id: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Return total spend and per-model/per-provider breakdowns within the last `days` days."""
        cutoff = time.time() - days * 86400
        extra_clauses: List[str] = []
        params: List[Any] = [cutoff]
        if org_id:
            extra_clauses.append("AND org_id = ?")
            params.append(org_id)
        where_extra = " ".join(extra_clauses)

        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(_SUMMARY_TOTALS_QUERY.format(where_extra=where_extra), params) as cur:
                totals = dict(await cur.fetchone())
            async with db.execute(_SUMMARY_BY_MODEL_QUERY.format(where_extra=where_extra), params) as cur:
                by_model = [dict(r) for r in await cur.fetchall()]
            async with db.execute(_SUMMARY_BY_PROVIDER_QUERY.format(where_extra=where_extra), params) as cur:
                by_provider = [dict(r) for r in await cur.fetchall()]

        return {
            "org_id": org_id,
            "days": days,
            "total_requests": totals["total_requests"],
            "total_cost_usd": round(totals["total_cost_usd"], 6),
            "total_input_tokens": totals["total_input_tokens"],
            "total_output_tokens": totals["total_output_tokens"],
            "by_model": [
                {**row, "total_cost_usd": round(row["total_cost_usd"], 6)}
                for row in by_model
            ],
            "by_provider": [
                {**row, "total_cost_usd": round(row["total_cost_usd"], 6)}
                for row in by_provider
            ],
        }


_usage_tracker: Optional[UsageTrackerService] = None


def get_usage_tracker() -> UsageTrackerService:
    """Return the singleton UsageTrackerService (must call init first)."""
    if _usage_tracker is None:
        raise RuntimeError("UsageTrackerService has not been initialized")
    return _usage_tracker


async def init_usage_tracker(db_path: str) -> UsageTrackerService:
    """Create, initialize, and register the singleton service."""
    global _usage_tracker
    _usage_tracker = UsageTrackerService(db_path)
    await _usage_tracker.initialize()
    return _usage_tracker
