"""Orchestrator store (v1.83.0): runs, steps, events, approvals, action records, spend, settings.

Own SQLite file (``ORCHESTRATOR_DB_PATH``) with the aiosqlite/WAL idiom and the
init_X/get_X singleton pattern of dispatch_receipts.py.

Rules the schema enforces rather than the caller:
- ``orch_runs.dedup_key`` is UNIQUE NOT NULL. Intake does INSERT OR IGNORE then SELECT, so
  two concurrent deliveries of the same event yield exactly one run (FR2).
- ``orch_actions`` is append-only: this module exposes no UPDATE or DELETE for it other
  than the retention purge (``tests/test_orchestrator_store.py`` pins that).
- Retention: rows older than ``run_retention_days`` (365) are deleted (steps go with their
  run, started or not); free-text summary fields are set NULL after ``io_retention_days``
  (90). Purged on initialize() and by the service after every new run it creates.

Nothing here interprets untrusted text: titles/bodies arrive already redacted and
truncated (orchestrator_workflows.summarize_untrusted) and are stored as JSON summaries.
"""
import csv
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)

_RUN_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def new_run_id() -> str:
    return "orc_" + "".join(secrets.choice(_RUN_ID_ALPHABET) for _ in range(12))


def _new_id(prefix: str) -> str:
    return prefix + "_" + "".join(secrets.choice(_RUN_ID_ALPHABET) for _ in range(16))


@asynccontextmanager
async def _open_db(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


RUN_COLUMNS: List[str] = [
    "run_id", "workflow_id", "trigger_class", "dedup_key", "mode", "status", "result", "public_summary",
    "requester", "source_ref", "instruction_like", "created_at", "updated_at", "finished_at", "step_count",
    "max_steps", "deadline_at", "cost_usd", "parked_reason", "provenance_json", "summary_json", "owner_note",
]
STEP_COLUMNS: List[str] = [
    "step_id", "run_id", "seq", "name", "kind", "agent", "status", "attempt", "max_attempts", "started_at",
    "finished_at", "timeout_s", "outcome", "artifact_hash", "external_ref", "io_summary_json", "cost_usd",
    "error_code",
]
EVENT_COLUMNS: List[str] = [
    "event_id", "run_id", "dedup_key", "trigger_class", "event_type", "received_at", "actor", "actor_is_owner",
    "delivery_ref", "duplicate", "outcome", "instruction_like", "payload_summary_json",
]
APPROVAL_COLUMNS: List[str] = [
    "approval_id", "run_id", "step_id", "kind", "status", "artifact_hash", "artifact_summary_json", "recipient",
    "requested_at", "decided_at", "decided_by", "decision_ref", "decision_note", "edited_artifact_hash",
]
ACTION_COLUMNS: List[str] = [
    "action_id", "run_id", "step_id", "actor", "at", "action", "target", "trigger", "outcome", "mode", "executor",
    "artifact_hash", "provenance_json",
]
SPEND_COLUMNS: List[str] = [
    "entry_id", "run_id", "step_id", "workflow_id", "agent", "model", "at", "period", "tokens_in", "tokens_out",
    "cost_usd",
]

_JSON_COLUMNS = frozenset({
    "provenance_json", "summary_json", "io_summary_json", "payload_summary_json", "artifact_summary_json",
})
_BOOL_COLUMNS = frozenset({"instruction_like", "actor_is_owner", "duplicate"})

# Free-text/JSON fields nulled after io_retention_days (FR16 retention; spec Q7).
_IO_FIELDS = {
    "orch_runs": ("summary_json", "owner_note"),
    "orch_steps": ("io_summary_json",),
    "orch_events": ("payload_summary_json",),
    "orch_approvals": ("artifact_summary_json", "decision_note"),
}
_TIME_COLUMN = {
    "orch_runs": "created_at", "orch_steps": "started_at", "orch_events": "received_at",
    "orch_approvals": "requested_at", "orch_actions": "at", "orch_spend": "at",
}

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS orch_runs (
        run_id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL,
        trigger_class TEXT NOT NULL,
        dedup_key TEXT NOT NULL UNIQUE,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        result TEXT,
        public_summary TEXT,
        requester TEXT,
        source_ref TEXT,
        instruction_like INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        finished_at REAL,
        step_count INTEGER NOT NULL DEFAULT 0,
        max_steps INTEGER NOT NULL,
        deadline_at REAL,
        cost_usd REAL NOT NULL DEFAULT 0,
        parked_reason TEXT,
        provenance_json TEXT,
        summary_json TEXT,
        owner_note TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orch_runs_status_created ON orch_runs (status, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_orch_runs_workflow_created ON orch_runs (workflow_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS orch_steps (
        step_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        agent TEXT NOT NULL,
        status TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        started_at REAL,
        finished_at REAL,
        timeout_s INTEGER NOT NULL DEFAULT 30,
        outcome TEXT,
        artifact_hash TEXT,
        external_ref TEXT,
        io_summary_json TEXT,
        cost_usd REAL NOT NULL DEFAULT 0,
        error_code TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orch_steps_run_seq ON orch_steps (run_id, seq)",
    """
    CREATE TABLE IF NOT EXISTS orch_events (
        event_id TEXT PRIMARY KEY,
        run_id TEXT,
        dedup_key TEXT NOT NULL,
        trigger_class TEXT NOT NULL,
        event_type TEXT NOT NULL,
        received_at REAL NOT NULL,
        actor TEXT,
        actor_is_owner INTEGER NOT NULL DEFAULT 0,
        delivery_ref TEXT,
        duplicate INTEGER NOT NULL DEFAULT 0,
        outcome TEXT NOT NULL,
        instruction_like INTEGER NOT NULL DEFAULT 0,
        payload_summary_json TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orch_events_run ON orch_events (run_id, received_at)",
    """
    CREATE TABLE IF NOT EXISTS orch_approvals (
        approval_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        artifact_hash TEXT NOT NULL,
        artifact_summary_json TEXT,
        recipient TEXT,
        requested_at REAL NOT NULL,
        decided_at REAL,
        decided_by TEXT,
        decision_ref TEXT,
        decision_note TEXT,
        edited_artifact_hash TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orch_approvals_status ON orch_approvals (status, requested_at)",
    "CREATE INDEX IF NOT EXISTS idx_orch_approvals_binding "
    "ON orch_approvals (run_id, step_id, artifact_hash, recipient)",
    """
    CREATE TABLE IF NOT EXISTS orch_actions (
        action_id TEXT PRIMARY KEY,
        run_id TEXT,
        step_id TEXT,
        actor TEXT NOT NULL,
        at REAL NOT NULL,
        action TEXT NOT NULL,
        target TEXT,
        trigger TEXT,
        outcome TEXT NOT NULL,
        mode TEXT,
        executor TEXT,
        artifact_hash TEXT,
        provenance_json TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orch_actions_run_at ON orch_actions (run_id, at)",
    "CREATE INDEX IF NOT EXISTS idx_orch_actions_at ON orch_actions (at)",
    """
    CREATE TABLE IF NOT EXISTS orch_spend (
        entry_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        step_id TEXT,
        workflow_id TEXT NOT NULL,
        agent TEXT,
        model TEXT,
        at REAL NOT NULL,
        period TEXT NOT NULL,
        tokens_in INTEGER,
        tokens_out INTEGER,
        cost_usd REAL NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_orch_spend_period_workflow ON orch_spend (period, workflow_id)",
    """
    CREATE TABLE IF NOT EXISTS orch_settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at REAL NOT NULL,
        updated_by TEXT
    )
    """,
)

# Leading characters a spreadsheet treats as the start of a formula (dispatch_receipts idiom).
_FORMULA_PREFIXES = frozenset("=+-@\t\r")


def _insert_sql(table: str, columns: List[str], or_ignore: bool = False) -> str:
    verb = "INSERT OR IGNORE" if or_ignore else "INSERT"
    marks = ", ".join("?" for _ in columns)
    return f"{verb} INTO {table} (" + ", ".join(columns) + f") VALUES ({marks})"  # nosec B608


def _encode(col: str, value: Any) -> Any:
    if col in _JSON_COLUMNS and value is not None and not isinstance(value, str):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    if col in _BOOL_COLUMNS:
        return 1 if value else 0
    return value


def _decode_row(row: Any) -> Dict[str, Any]:
    d = dict(row)
    for col in list(d):
        if col in _JSON_COLUMNS and isinstance(d[col], str):
            try:
                d[col] = json.loads(d[col])
            except ValueError:
                d[col] = None
        elif col in _BOOL_COLUMNS:
            d[col] = bool(d[col])
    return d


class OrchestratorStore:
    """Persistence for the orchestrator core. Every method opens a short-lived connection."""

    def __init__(self, db_path: str, run_retention_days: int = 365, io_retention_days: int = 90) -> None:
        self._db_path = db_path
        self._run_retention_days = run_retention_days
        self._io_retention_days = io_retention_days

    @property
    def db_path(self) -> str:
        return self._db_path

    async def initialize(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with _open_db(self._db_path) as db:
            for statement in _SCHEMA:
                await db.execute(statement)
            await db.commit()
        await self.purge_expired()

    # ------------------------------------------------------------------ retention
    async def purge_expired(self, now: Optional[float] = None) -> Dict[str, int]:
        now = time.time() if now is None else now
        counts: Dict[str, int] = {}
        async with _open_db(self._db_path) as db:
            if self._run_retention_days and self._run_retention_days > 0:
                cutoff = now - self._run_retention_days * 86400
                for table, col in _TIME_COLUMN.items():
                    # Table and column names are literals from this module.
                    cur = await db.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff,))  # nosec B608
                    counts[table] = cur.rowcount or 0
                # Steps that never started have started_at NULL (never < cutoff): remove the ones
                # whose run is gone so a purged run leaves no orphaned step rows behind.
                cur = await db.execute(
                    "DELETE FROM orch_steps WHERE run_id NOT IN (SELECT run_id FROM orch_runs)"
                )
                counts["orch_steps"] += cur.rowcount or 0
            if self._io_retention_days and self._io_retention_days > 0:
                io_cutoff = now - self._io_retention_days * 86400
                for table, fields in _IO_FIELDS.items():
                    col = _TIME_COLUMN[table]
                    sets = ", ".join(f"{f} = NULL" for f in fields)
                    where = " OR ".join(f"{f} IS NOT NULL" for f in fields)
                    cur = await db.execute(
                        f"UPDATE {table} SET {sets} WHERE {col} < ? AND ({where})", (io_cutoff,)  # nosec B608
                    )
                    counts[table + ":io_nulled"] = cur.rowcount or 0
            await db.commit()
        return counts

    # ------------------------------------------------------------------ runs
    async def create_run_or_attach(self, run: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """INSERT OR IGNORE on dedup_key, then SELECT. Returns (run_row, created)."""
        values = [_encode(c, run.get(c)) for c in RUN_COLUMNS]
        async with _open_db(self._db_path) as db:
            cur = await db.execute(_insert_sql("orch_runs", RUN_COLUMNS, or_ignore=True), values)
            created = (cur.rowcount or 0) == 1
            await db.commit()
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM orch_runs WHERE dedup_key = ?", (run["dedup_key"],)) as c2:
                row = await c2.fetchone()
        return _decode_row(row), created

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM orch_runs WHERE run_id = ?", (run_id,)) as cur:
                row = await cur.fetchone()
        return _decode_row(row) if row else None

    async def get_run_by_dedup_key(self, dedup_key: str) -> Optional[Dict[str, Any]]:
        """v1.84.0: the run an intake with this key would attach to (dedup_key is UNIQUE)."""
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM orch_runs WHERE dedup_key = ?", (dedup_key,)) as cur:
                row = await cur.fetchone()
        return _decode_row(row) if row else None

    async def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = [c for c in fields if c in RUN_COLUMNS and c != "run_id"]
        if not cols:
            return
        fields.setdefault("updated_at", time.time())
        if "updated_at" not in cols:
            cols.append("updated_at")
        sets = ", ".join(f"{c} = ?" for c in cols)
        params = [_encode(c, fields[c]) for c in cols] + [run_id]
        async with _open_db(self._db_path) as db:
            await db.execute(f"UPDATE orch_runs SET {sets} WHERE run_id = ?", params)  # nosec B608
            await db.commit()

    async def list_runs(self, status: Optional[str] = None, workflow_id: Optional[str] = None,
                        trigger_class: Optional[str] = None, since: Optional[float] = None,
                        until: Optional[float] = None, limit: int = 200) -> List[Dict[str, Any]]:
        clauses, params = ["1=1"], []  # type: List[str], List[Any]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if workflow_id:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if trigger_class:
            clauses.append("trigger_class = ?")
            params.append(trigger_class)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= ?")
            params.append(until)
        params.append(max(1, min(int(limit), 10_000)))
        sql = ("SELECT * FROM orch_runs WHERE " + " AND ".join(clauses)  # nosec B608
               + " ORDER BY created_at DESC LIMIT ?")
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [_decode_row(r) for r in rows]

    async def list_runs_by_status(self, statuses: Tuple[str, ...]) -> List[Dict[str, Any]]:
        marks = ", ".join("?" for _ in statuses)
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM orch_runs WHERE status IN ({marks}) ORDER BY created_at", list(statuses)  # nosec B608
            ) as cur:
                rows = await cur.fetchall()
        return [_decode_row(r) for r in rows]

    # ------------------------------------------------------------------ steps
    async def insert_steps(self, steps: List[Dict[str, Any]]) -> None:
        async with _open_db(self._db_path) as db:
            for s in steps:
                await db.execute(_insert_sql("orch_steps", STEP_COLUMNS), [_encode(c, s.get(c)) for c in STEP_COLUMNS])
            await db.commit()

    async def get_steps(self, run_id: str) -> List[Dict[str, Any]]:
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM orch_steps WHERE run_id = ? ORDER BY seq", (run_id,)) as cur:
                rows = await cur.fetchall()
        return [_decode_row(r) for r in rows]

    async def get_step(self, step_id: str) -> Optional[Dict[str, Any]]:
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM orch_steps WHERE step_id = ?", (step_id,)) as cur:
                row = await cur.fetchone()
        return _decode_row(row) if row else None

    async def update_step(self, step_id: str, **fields: Any) -> None:
        cols = [c for c in fields if c in STEP_COLUMNS and c != "step_id"]
        if not cols:
            return
        sets = ", ".join(f"{c} = ?" for c in cols)
        params = [_encode(c, fields[c]) for c in cols] + [step_id]
        async with _open_db(self._db_path) as db:
            await db.execute(f"UPDATE orch_steps SET {sets} WHERE step_id = ?", params)  # nosec B608
            await db.commit()

    # ------------------------------------------------------------------ events
    async def insert_event(self, event: Dict[str, Any]) -> str:
        event = dict(event)
        event.setdefault("event_id", _new_id("evt"))
        event.setdefault("received_at", time.time())
        async with _open_db(self._db_path) as db:
            await db.execute(_insert_sql("orch_events", EVENT_COLUMNS),
                             [_encode(c, event.get(c)) for c in EVENT_COLUMNS])
            await db.commit()
        return event["event_id"]

    async def list_events(self, run_id: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if run_id is None:
                sql, params = "SELECT * FROM orch_events ORDER BY received_at DESC LIMIT ?", [limit]
            else:
                sql = "SELECT * FROM orch_events WHERE run_id = ? ORDER BY received_at LIMIT ?"
                params = [run_id, limit]
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [_decode_row(r) for r in rows]

    # ------------------------------------------------------------------ approvals
    async def insert_approval(self, approval: Dict[str, Any]) -> str:
        approval = dict(approval)
        approval.setdefault("approval_id", _new_id("apr"))
        approval.setdefault("requested_at", time.time())
        approval.setdefault("status", "waiting")
        async with _open_db(self._db_path) as db:
            await db.execute(_insert_sql("orch_approvals", APPROVAL_COLUMNS),
                             [_encode(c, approval.get(c)) for c in APPROVAL_COLUMNS])
            await db.commit()
        return approval["approval_id"]

    async def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM orch_approvals WHERE approval_id = ?", (approval_id,)) as cur:
                row = await cur.fetchone()
        return _decode_row(row) if row else None

    async def find_approval(self, run_id: str, step_id: str, artifact_hash: str,
                            recipient: Optional[str]) -> Optional[Dict[str, Any]]:
        """The approval bound to exactly this (run, step, artifact, recipient), newest first."""
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM orch_approvals WHERE run_id = ? AND step_id = ? AND artifact_hash = ? "
                "AND COALESCE(recipient, '') = COALESCE(?, '') ORDER BY requested_at DESC LIMIT 1",
                (run_id, step_id, artifact_hash, recipient),
            ) as cur:
                row = await cur.fetchone()
        return _decode_row(row) if row else None

    async def update_approval(self, approval_id: str, **fields: Any) -> None:
        cols = [c for c in fields if c in APPROVAL_COLUMNS and c != "approval_id"]
        if not cols:
            return
        sets = ", ".join(f"{c} = ?" for c in cols)
        params = [_encode(c, fields[c]) for c in cols] + [approval_id]
        async with _open_db(self._db_path) as db:
            await db.execute(f"UPDATE orch_approvals SET {sets} WHERE approval_id = ?", params)  # nosec B608
            await db.commit()

    async def list_approvals(self, status: Optional[str] = "waiting", run_id: Optional[str] = None,
                             limit: int = 500) -> List[Dict[str, Any]]:
        clauses, params = ["1=1"], []  # type: List[str], List[Any]
        if status:
            clauses.append("status = ?")
            params.append(status)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        params.append(limit)
        sql = ("SELECT * FROM orch_approvals WHERE " + " AND ".join(clauses)  # nosec B608
               + " ORDER BY requested_at DESC LIMIT ?")
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [_decode_row(r) for r in rows]

    # ------------------------------------------------------------------ action records (append-only)
    async def append_action(self, action: Dict[str, Any]) -> str:
        action = dict(action)
        action.setdefault("action_id", _new_id("act"))
        action.setdefault("at", time.time())
        action.setdefault("actor", "orchestrator")
        async with _open_db(self._db_path) as db:
            await db.execute(_insert_sql("orch_actions", ACTION_COLUMNS),
                             [_encode(c, action.get(c)) for c in ACTION_COLUMNS])
            await db.commit()
        return action["action_id"]

    async def list_actions(self, run_id: Optional[str] = None, since: Optional[float] = None,
                           limit: int = 500) -> List[Dict[str, Any]]:
        clauses, params = ["1=1"], []  # type: List[str], List[Any]
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if since is not None:
            clauses.append("at >= ?")
            params.append(since)
        params.append(max(1, min(int(limit), 10_000)))
        sql = ("SELECT * FROM orch_actions WHERE " + " AND ".join(clauses)  # nosec B608
               + " ORDER BY at ASC LIMIT ?")
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [_decode_row(r) for r in rows]

    async def count_actions(self, run_id: Optional[str], action: str) -> int:
        async with _open_db(self._db_path) as db:
            if run_id is None:
                sql, params = "SELECT COUNT(*) FROM orch_actions WHERE action = ?", [action]
            else:
                sql, params = "SELECT COUNT(*) FROM orch_actions WHERE run_id = ? AND action = ?", [run_id, action]
            async with db.execute(sql, params) as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ spend
    async def record_spend(self, entry: Dict[str, Any]) -> str:
        entry = dict(entry)
        entry.setdefault("entry_id", _new_id("spd"))
        entry.setdefault("at", time.time())
        entry.setdefault("period", time.strftime("%Y-%m", time.gmtime(entry["at"])))
        async with _open_db(self._db_path) as db:
            await db.execute(_insert_sql("orch_spend", SPEND_COLUMNS),
                             [_encode(c, entry.get(c)) for c in SPEND_COLUMNS])
            await db.commit()
        return entry["entry_id"]

    async def spend_total(self, period: Optional[str] = None, workflow_id: Optional[str] = None,
                          run_id: Optional[str] = None) -> float:
        clauses, params = ["1=1"], []  # type: List[str], List[Any]
        if period:
            clauses.append("period = ?")
            params.append(period)
        if workflow_id:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM orch_spend WHERE " + " AND ".join(clauses)  # nosec B608
        async with _open_db(self._db_path) as db:
            async with db.execute(sql, params) as cur:
                row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def spend_by_workflow(self, period: str) -> Dict[str, float]:
        async with _open_db(self._db_path) as db:
            async with db.execute(
                "SELECT workflow_id, COALESCE(SUM(cost_usd), 0) FROM orch_spend WHERE period = ? GROUP BY workflow_id",
                (period,),
            ) as cur:
                rows = await cur.fetchall()
        return {r[0]: float(r[1]) for r in rows}

    # ------------------------------------------------------------------ settings
    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with _open_db(self._db_path) as db:
            async with db.execute("SELECT value_json FROM orch_settings WHERE key = ?", (key,)) as cur:
                row = await cur.fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except ValueError:
            return default

    async def set_setting(self, key: str, value: Any, updated_by: str = "operator") -> None:
        async with _open_db(self._db_path) as db:
            await db.execute(
                "INSERT INTO orch_settings (key, value_json, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (key, json.dumps(value, sort_keys=True), time.time(), updated_by),
            )
            await db.commit()

    async def all_settings(self) -> Dict[str, Any]:
        async with _open_db(self._db_path) as db:
            async with db.execute("SELECT key, value_json FROM orch_settings ORDER BY key") as cur:
                rows = await cur.fetchall()
        out: Dict[str, Any] = {}
        for k, v in rows:
            try:
                out[k] = json.loads(v)
            except ValueError:
                continue
        return out

    # ------------------------------------------------------------------ export
    @staticmethod
    def _csv_cell(value: Any) -> Any:
        """Neutralise spreadsheet formula injection in text cells (dispatch_receipts idiom)."""
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        if isinstance(value, str) and value and value[0] in _FORMULA_PREFIXES:
            return "'" + value
        return value

    @staticmethod
    def to_csv(rows: List[Dict[str, Any]], columns: List[str]) -> str:
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        for r in rows:
            writer.writerow([OrchestratorStore._csv_cell(r.get(c)) for c in columns])
        return buf.getvalue()


_store: Optional[OrchestratorStore] = None


def get_orchestrator_store() -> OrchestratorStore:
    if _store is None:
        raise RuntimeError("OrchestratorStore has not been initialized")
    return _store


async def init_orchestrator_store(db_path: str, run_retention_days: int = 365,
                                  io_retention_days: int = 90) -> OrchestratorStore:
    global _store
    _store = OrchestratorStore(db_path, run_retention_days=run_retention_days, io_retention_days=io_retention_days)
    await _store.initialize()
    return _store
