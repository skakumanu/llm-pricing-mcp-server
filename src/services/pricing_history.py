"""Pricing history service: snapshot live prices periodically and expose trends."""
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pricing_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at REAL NOT NULL,
    model_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    cost_per_input_token REAL NOT NULL,
    cost_per_output_token REAL NOT NULL
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_snapshots_lookup
ON pricing_snapshots (provider, model_name, captured_at)
"""

_INSERT = """
INSERT INTO pricing_snapshots
    (captured_at, model_name, provider, cost_per_input_token, cost_per_output_token)
VALUES (?, ?, ?, ?, ?)
"""

_HISTORY_QUERY = """
SELECT model_name, provider, cost_per_input_token, cost_per_output_token, captured_at
FROM pricing_snapshots
WHERE captured_at >= ?
{where_extra}
ORDER BY captured_at DESC
LIMIT ?
"""

_TRENDS_QUERY = """
SELECT
    o.model_name,
    o.provider,
    o.cost_per_input_token  AS first_input,
    o.cost_per_output_token AS first_output,
    n.cost_per_input_token  AS last_input,
    n.cost_per_output_token AS last_output,
    o.captured_at           AS first_seen,
    n.captured_at           AS last_seen
FROM pricing_snapshots o
JOIN pricing_snapshots n
  ON o.model_name = n.model_name AND o.provider = n.provider
WHERE o.captured_at = (
    SELECT MIN(captured_at) FROM pricing_snapshots
    WHERE model_name = o.model_name AND provider = o.provider AND captured_at >= :cutoff
)
AND n.captured_at = (
    SELECT MAX(captured_at) FROM pricing_snapshots
    WHERE model_name = n.model_name AND provider = n.provider
)
AND o.captured_at < n.captured_at
ORDER BY ABS(
    (n.cost_per_input_token - o.cost_per_input_token) / MAX(o.cost_per_input_token, 1e-12)
) DESC
LIMIT :limit
"""

_TOTAL_QUERY = """
SELECT COUNT(*) FROM pricing_snapshots WHERE captured_at >= ? {where_extra}
"""


class PricingHistoryService:
    """Records periodic snapshots of live pricing and exposes history/trend queries."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def initialize(self) -> None:
        """Create the snapshots table and index if they don't exist."""
        import aiosqlite
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_INDEX)
            await db.commit()

    async def record_snapshot(self, models) -> int:
        """Persist a snapshot of current pricing. Returns the number of rows inserted."""
        import aiosqlite
        now = time.time()
        rows = [
            (now, m.model_name, m.provider, m.cost_per_input_token, m.cost_per_output_token)
            for m in models
        ]
        async with aiosqlite.connect(self._db_path) as db:
            await db.executemany(_INSERT, rows)
            await db.commit()
        logger.info("Pricing snapshot: %d models recorded", len(rows))
        return len(rows)

    async def get_history(
        self,
        model_name: Optional[str] = None,
        provider: Optional[str] = None,
        days: int = 30,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Return snapshots within the last `days` days, optionally filtered."""
        import aiosqlite
        cutoff = time.time() - days * 86400
        extra_clauses, params = [], [cutoff]

        if provider:
            extra_clauses.append("AND provider = ?")
            params.append(provider)
        if model_name:
            extra_clauses.append("AND model_name = ?")
            params.append(model_name)

        where_extra = " ".join(extra_clauses)
        history_sql = _HISTORY_QUERY.format(where_extra=where_extra)
        total_sql = _TOTAL_QUERY.format(where_extra=where_extra)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(total_sql, params) as cur:
                total = (await cur.fetchone())[0]
            async with db.execute(history_sql, params + [limit]) as cur:
                rows = await cur.fetchall()

        snapshots = [dict(r) for r in rows]
        return {"snapshots": snapshots, "total": total}

    async def get_trends(
        self, days: int = 30, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return models with the largest price change over the last `days` days."""
        import aiosqlite
        cutoff = time.time() - days * 86400

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(_TRENDS_QUERY, {"cutoff": cutoff, "limit": limit}) as cur:
                rows = await cur.fetchall()

        trends = []
        for r in rows:
            first_in = r["first_input"]
            last_in = r["last_input"]
            first_out = r["first_output"]
            last_out = r["last_output"]

            def pct(old, new):
                if old == 0:
                    return 0.0
                return round((new - old) / old * 100, 2)

            in_chg = pct(first_in, last_in)
            out_chg = pct(first_out, last_out)

            if abs(in_chg) < 0.1 and abs(out_chg) < 0.1:
                direction = "unchanged"
            elif (in_chg + out_chg) / 2 < 0:
                direction = "decreased"
            else:
                direction = "increased"

            trends.append({
                "model_name": r["model_name"],
                "provider": r["provider"],
                "input_change_pct": in_chg,
                "output_change_pct": out_chg,
                "direction": direction,
                "first_seen": r["first_seen"],
                "last_seen": r["last_seen"],
                "first_input": first_in,
                "last_input": last_in,
                "first_output": first_out,
                "last_output": last_out,
            })
        return trends


_history_service: Optional[PricingHistoryService] = None


def get_pricing_history_service() -> PricingHistoryService:
    """Return the singleton PricingHistoryService (must call initialize() first)."""
    if _history_service is None:
        raise RuntimeError("PricingHistoryService has not been initialized")
    return _history_service


async def init_pricing_history_service(db_path: str) -> PricingHistoryService:
    """Create, initialize, and register the singleton service."""
    global _history_service
    _history_service = PricingHistoryService(db_path)
    await _history_service.initialize()
    return _history_service
