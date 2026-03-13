"""Savings tracker: persist routing decisions and report per-org savings."""
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS routing_savings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id TEXT,
    api_key_tier TEXT,
    requested_at REAL NOT NULL,
    recommended_model TEXT NOT NULL,
    recommended_provider TEXT NOT NULL,
    recommended_cost_per_1m REAL NOT NULL,
    baseline_model TEXT,
    baseline_cost_per_1m REAL,
    savings_per_1m REAL,
    task_type TEXT
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_routing_savings_lookup
ON routing_savings (org_id, requested_at)
"""

_INSERT = """
INSERT INTO routing_savings
    (org_id, api_key_tier, requested_at,
     recommended_model, recommended_provider, recommended_cost_per_1m,
     baseline_model, baseline_cost_per_1m, savings_per_1m, task_type)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_QUERY = """
SELECT id, org_id, api_key_tier, requested_at,
       recommended_model, recommended_provider, recommended_cost_per_1m,
       baseline_model, baseline_cost_per_1m, savings_per_1m, task_type
FROM routing_savings
WHERE requested_at >= ?
{where_extra}
ORDER BY requested_at DESC
LIMIT ?
"""

_TOTAL_QUERY = """
SELECT COUNT(*) FROM routing_savings WHERE requested_at >= ? {where_extra}
"""

_SUM_SAVINGS_QUERY = """
SELECT COALESCE(SUM(savings_per_1m), 0.0) FROM routing_savings
WHERE requested_at >= ? {where_extra}
"""


class SavingsTrackerService:
    """Records routing decisions to SQLite and exposes per-org savings queries."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def initialize(self) -> None:
        """Create the routing_savings table if it doesn't exist."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_INDEX)
            await db.commit()

    async def record_routing(
        self,
        recommended_model: str,
        recommended_provider: str,
        recommended_cost_per_1m: float,
        org_id: Optional[str] = None,
        api_key_tier: Optional[str] = None,
        baseline_model: Optional[str] = None,
        baseline_cost_per_1m: Optional[float] = None,
        task_type: Optional[str] = None,
    ) -> None:
        """Persist a routing decision."""
        import aiosqlite
        savings = None
        if baseline_cost_per_1m is not None:
            savings = max(0.0, baseline_cost_per_1m - recommended_cost_per_1m)

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_INSERT, (
                org_id,
                api_key_tier,
                time.time(),
                recommended_model,
                recommended_provider,
                recommended_cost_per_1m,
                baseline_model,
                baseline_cost_per_1m,
                savings,
                task_type,
            ))
            await db.commit()

    async def get_savings(
        self,
        org_id: Optional[str] = None,
        days: int = 30,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Return routing records within the last `days` days, optionally filtered by org."""
        import aiosqlite
        cutoff = time.time() - days * 86400
        extra_clauses: List[str] = []
        params: List[Any] = [cutoff]

        if org_id:
            extra_clauses.append("AND org_id = ?")
            params.append(org_id)

        where_extra = " ".join(extra_clauses)
        query_sql = _QUERY.format(where_extra=where_extra)
        total_sql = _TOTAL_QUERY.format(where_extra=where_extra)
        sum_sql = _SUM_SAVINGS_QUERY.format(where_extra=where_extra)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(total_sql, params) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(sum_sql, params) as cur:
                total_savings = (await cur.fetchone())[0] or 0.0
            async with db.execute(query_sql, params + [limit]) as cur:
                rows = await cur.fetchall()

        records = [dict(r) for r in rows]
        return {"records": records, "total": total, "total_savings_per_1m": total_savings}


_savings_service: Optional[SavingsTrackerService] = None


def get_savings_tracker() -> SavingsTrackerService:
    """Return the singleton SavingsTrackerService (must call init first)."""
    if _savings_service is None:
        raise RuntimeError("SavingsTrackerService has not been initialized")
    return _savings_service


async def init_savings_tracker(db_path: str) -> SavingsTrackerService:
    """Create, initialize, and register the singleton service."""
    global _savings_service
    _savings_service = SavingsTrackerService(db_path)
    await _savings_service.initialize()
    return _savings_service
