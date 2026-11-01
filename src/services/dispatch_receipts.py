"""Dispatch receipt store (v1.77.0): metadata-only audit rows for the dispatch gateway.

One row per dispatch that reached a provider (success or exhausted failover); pre-call
refusals (auth, budget stop, no candidate) are not rows. Columns are ids, model and
provider names, outcomes, token counts, costs and latencies — never messages,
completion text, provider error bodies, provider keys, IPs or headers. Rows older than
``retention_days`` are purged on initialize() and opportunistically after each insert.
v1.78.0 adds ``streamed`` (the dispatch was requested with ``stream: true``) and
``usage_estimated`` (token counts were counted by the gateway, not reported by the provider).

Same SQLite file and aiosqlite/WAL idiom as usage_tracker.py; same init_X/get_X
singleton pattern as every other service.
"""
import csv
import logging
import time
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _open_db(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


# The column order here is the CSV column order. New columns are appended at the end so
# existing export consumers see their columns unchanged.
#   streamed        (v1.78.0) 1 when the dispatch was requested with `stream: true`
#   usage_estimated (v1.78.0) 1 when the token counts were counted by the gateway rather
#                   than reported by the provider (a stream that carried no usage record)
COLUMNS: List[str] = [
    "dispatch_id", "org_id", "created_at", "request_id", "session_id", "requested_model",
    "objective", "policy_json", "task_type", "chosen_model", "chosen_provider", "outcome",
    "attempts", "attempts_json", "input_tokens", "output_tokens", "cost_usd",
    "estimated_cost_usd", "savings_vs_most_expensive_usd", "savings_vs_requested_usd",
    "latency_total_ms", "latency_provider_ms", "quality_score", "price_as_of",
    "price_source_kind", "shadow_model", "shadow_provider", "shadow_outcome",
    "shadow_cost_usd", "shadow_latency_ms", "live_call", "streamed", "usage_estimated",
]

# Boolean columns stored as 0/1 and returned as bool.
_BOOL_COLUMNS = ("live_call", "streamed", "usage_estimated")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS dispatch_receipts (
    dispatch_id TEXT PRIMARY KEY,
    org_id TEXT,
    created_at REAL NOT NULL,
    request_id TEXT,
    session_id TEXT,
    requested_model TEXT,
    objective TEXT,
    policy_json TEXT,
    task_type TEXT,
    chosen_model TEXT,
    chosen_provider TEXT,
    outcome TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    attempts_json TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    estimated_cost_usd REAL,
    savings_vs_most_expensive_usd REAL,
    savings_vs_requested_usd REAL,
    latency_total_ms REAL,
    latency_provider_ms REAL,
    quality_score REAL,
    price_as_of TEXT,
    price_source_kind TEXT,
    shadow_model TEXT,
    shadow_provider TEXT,
    shadow_outcome TEXT,
    shadow_cost_usd REAL,
    shadow_latency_ms REAL,
    live_call INTEGER NOT NULL DEFAULT 1,
    streamed INTEGER NOT NULL DEFAULT 0,
    usage_estimated INTEGER NOT NULL DEFAULT 0
)
"""

# Columns added after v1.77.0; applied to pre-existing databases on initialize().
_MIGRATIONS = (
    "ALTER TABLE dispatch_receipts ADD COLUMN streamed INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE dispatch_receipts ADD COLUMN usage_estimated INTEGER NOT NULL DEFAULT 0",
)

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_dispatch_receipts_org_created
ON dispatch_receipts (org_id, created_at)
"""

_INSERT = (
    "INSERT OR IGNORE INTO dispatch_receipts (" + ", ".join(COLUMNS) + ") VALUES ("
    + ", ".join("?" for _ in COLUMNS) + ")"
)

_PURGE = "DELETE FROM dispatch_receipts WHERE created_at < ?"

# Leading characters a spreadsheet treats as the start of a formula.
_FORMULA_PREFIXES = frozenset("=+-@\t\r")


class DispatchReceiptStore:
    """Persist and export dispatch receipt metadata, scoped by org."""

    def __init__(self, db_path: str, retention_days: int = 90) -> None:
        self._db_path = db_path
        self._retention_days = retention_days

    async def initialize(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with _open_db(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_INDEX)
            # Migrate v1.77.0 databases (same idiom as usage_tracker.py): a column that already
            # exists makes ALTER TABLE fail, which is the expected steady state.
            for statement in _MIGRATIONS:
                try:
                    await db.execute(statement)
                except aiosqlite.OperationalError:
                    pass  # nosec B110 — column already exists, this is intentional
            await db.commit()
        await self.purge_expired()

    async def record(self, row: Dict[str, Any]) -> None:
        """Insert one receipt row. Unknown keys are ignored; missing ones are NULL."""
        values = []
        for col in COLUMNS:
            v = row.get(col)
            if col == "live_call":
                v = 1 if (v is None or v) else 0
            elif col in _BOOL_COLUMNS:
                v = 1 if v else 0  # NOT NULL DEFAULT 0: None means "no"
            if col == "created_at" and v is None:
                v = time.time()
            if col == "attempts" and v is None:
                v = 0  # NOT NULL: an explicit NULL would make INSERT OR IGNORE drop the row silently
            values.append(v)
        async with _open_db(self._db_path) as db:
            await db.execute(_INSERT, values)
            await db.commit()
        await self.purge_expired()

    async def purge_expired(self) -> int:
        if self._retention_days is None or self._retention_days <= 0:
            return 0
        cutoff = time.time() - self._retention_days * 86400
        async with _open_db(self._db_path) as db:
            cursor = await db.execute(_PURGE, (cutoff,))
            await db.commit()
            return cursor.rowcount or 0

    async def delete_org(self, org_id: str) -> int:
        """v1.87.0 account deletion: erase every receipt of one org."""
        async with _open_db(self._db_path) as db:
            cursor = await db.execute("DELETE FROM dispatch_receipts WHERE org_id = ?", (org_id,))
            await db.commit()
            return cursor.rowcount or 0

    async def list_receipts(
        self,
        org_id: str,
        since: Optional[float] = None,
        until: Optional[float] = None,
        outcome: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Return receipts for exactly one org (callers must always pass the caller's own org)."""
        clauses = ["org_id = ?"]
        params: List[Any] = [org_id]
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= ?")
            params.append(until)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        # COLUMNS and every clause are literals from this module; all values are
        # bound through "?" placeholders, so no caller input reaches the SQL text.
        sql = (
            "SELECT " + ", ".join(COLUMNS) + " FROM dispatch_receipts WHERE " + " AND ".join(clauses)  # nosec B608
            + " ORDER BY created_at DESC LIMIT ?"
        )
        params.append(limit)
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for col in _BOOL_COLUMNS:
                d[col] = bool(d.get(col))
            out.append(d)
        return out

    @staticmethod
    def _csv_cell(value: Any) -> Any:
        """Neutralise spreadsheet formula injection in caller-controlled text cells.

        request_id / session_id / requested_model / policy_json are caller-supplied; a value
        starting with = + - @ or a tab/CR would be evaluated as a formula by Excel/LibreOffice
        when the export is opened. Prefixing a single quote makes it inert text. Numbers are
        left alone (negative savings are legitimately "-0.0001").
        """
        if isinstance(value, str) and value and value[0] in _FORMULA_PREFIXES:
            return "'" + value
        return value

    @staticmethod
    def to_csv(rows: List[Dict[str, Any]]) -> str:
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(COLUMNS)
        for r in rows:
            writer.writerow([DispatchReceiptStore._csv_cell(r.get(c)) for c in COLUMNS])
        return buf.getvalue()


_store: Optional[DispatchReceiptStore] = None


def get_dispatch_receipt_store() -> DispatchReceiptStore:
    if _store is None:
        raise RuntimeError("DispatchReceiptStore has not been initialized")
    return _store


async def init_dispatch_receipt_store(db_path: str, retention_days: int = 90) -> DispatchReceiptStore:
    global _store
    _store = DispatchReceiptStore(db_path, retention_days=retention_days)
    await _store.initialize()
    return _store
