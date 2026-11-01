"""Compliance audit report store (v1.88.0).

Reports live in the SAME SQLite file as the orchestrator (``ORCHESTRATOR_DB_PATH``), so the
existing Litestream entry, volume and restore procedure cover them; this module owns two tables
and is their sole writer:

- ``audit_reports``: one row per audit run (status, counts, the data-quality numbers that make the
  regression check a time series, a config echo, the executive summary and where it came from).
- ``audit_checks``: one row per check per report (status, fixed severity, redacted evidence, the
  launch-checklist ids it verifies).

Rules the module enforces rather than the caller:
- The evidence trail is append-only: ``save_report`` adds rows; the only UPDATE
  is ``set_summary`` (the report's own summary text) and the only DELETE is ``purge_expired``
  (retention). ``tests/test_audit_service.py`` pins that no other UPDATE/DELETE statement exists.
- Evidence is redacted (emails, keys, Stripe ids) before it reaches the database, in addition to the
  checks never putting such values there in the first place.
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from src.services.orchestrator_store import _new_id, _open_db  # same alphabet / WAL idiom
from src.services.orchestrator_workflows import redact_untrusted_text

logger = logging.getLogger(__name__)

REPORT_STATUSES = ("pass", "warn", "fail")
CHECK_STATUSES = ("pass", "fail", "warn", "n/a")

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS audit_reports (
        report_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        trigger_class TEXT,
        status TEXT NOT NULL,
        counts_json TEXT,
        numbers_json TEXT,
        config_json TEXT,
        summary_text TEXT,
        summary_source TEXT,
        duration_ms INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_reports_created ON audit_reports (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_audit_reports_run ON audit_reports (run_id)",
    """
    CREATE TABLE IF NOT EXISTS audit_checks (
        row_id TEXT PRIMARY KEY,
        report_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        check_id TEXT NOT NULL,
        status TEXT NOT NULL,
        severity TEXT,
        title TEXT,
        evidence_json TEXT,
        lc_json TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_checks_report_seq ON audit_checks (report_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_audit_checks_id_status ON audit_checks (check_id, status)",
)

_JSON_REPORT = ("counts_json", "numbers_json", "config_json")


def new_report_id() -> str:
    return _new_id("aud")


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def redact_deep(value: Any) -> Any:
    """Redact every string (emails, keys, Stripe ids) in a nested structure."""
    if isinstance(value, str):
        return redact_untrusted_text(value)
    if isinstance(value, dict):
        return {str(k): redact_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_deep(v) for v in value]
    return value


def _decode_report(row: Any) -> Dict[str, Any]:
    d = dict(row)
    for col in _JSON_REPORT:
        raw = d.get(col)
        try:
            d[col[:-5]] = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            d[col[:-5]] = None
        d.pop(col, None)
    return d


def _decode_check(row: Any) -> Dict[str, Any]:
    d = dict(row)
    for col, name in (("evidence_json", "evidence"), ("lc_json", "lc")):
        raw = d.pop(col, None)
        try:
            d[name] = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            d[name] = None
    d["id"] = d.pop("check_id")
    d.pop("row_id", None)
    return d


class AuditStore:
    """Persistence for audit reports. Every method opens a short-lived connection."""

    def __init__(self, db_path: str, retention_days: int = 365) -> None:
        self._db_path = db_path
        self._retention_days = retention_days

    @property
    def db_path(self) -> str:
        return self._db_path

    async def initialize(self) -> None:
        async with _open_db(self._db_path) as db:
            for statement in _SCHEMA:
                await db.execute(statement)
            await db.commit()
        await self.purge_expired()

    # ------------------------------------------------------------------ writes
    async def save_report(self, report: Dict[str, Any], checks: List[Dict[str, Any]]) -> str:
        """Insert one report and all of its check rows in ONE transaction (evidence redacted first).

        A report therefore never exists without its checks, and a retried step cannot leave half of one.
        """
        report_id = report.get("report_id") or new_report_id()
        rows = []
        for seq, c in enumerate(checks, start=1):
            rows.append((_new_id("chk"), report_id, seq, str(c["id"]), str(c["status"]), c.get("severity"),
                         redact_untrusted_text(str(c.get("title") or ""))[:200],
                         _dump(redact_deep(c.get("evidence") or {})), _dump(list(c.get("lc") or []))))
        async with _open_db(self._db_path) as db:
            await db.execute(
                "INSERT INTO audit_reports (report_id, run_id, created_at, trigger_class, status, counts_json, "
                "numbers_json, config_json, summary_text, summary_source, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, report["run_id"], report.get("created_at") or time.time(), report.get("trigger_class"),
                 report["status"], _dump(report.get("counts") or {}), _dump(redact_deep(report.get("numbers") or {})),
                 _dump(redact_deep(report.get("config") or {})), report.get("summary_text"),
                 report.get("summary_source"), report.get("duration_ms")),
            )
            await db.executemany(
                "INSERT INTO audit_checks (row_id, report_id, seq, check_id, status, severity, title, "
                "evidence_json, lc_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            await db.commit()
        return report_id

    async def set_summary(self, report_id: str, text: str, source: str) -> None:
        """The ONE update: the report's own executive summary (the agent's own artefact)."""
        async with _open_db(self._db_path) as db:
            await db.execute("UPDATE audit_reports SET summary_text = ?, summary_source = ? WHERE report_id = ?",
                             (redact_untrusted_text(text)[:4000], source[:100], report_id))
            await db.commit()

    async def purge_expired(self, now: Optional[float] = None) -> Dict[str, int]:
        """Retention: reports older than the window, then check rows whose report is gone."""
        if not self._retention_days or self._retention_days <= 0:
            return {"audit_reports": 0, "audit_checks": 0}
        now = time.time() if now is None else now
        cutoff = now - self._retention_days * 86400
        async with _open_db(self._db_path) as db:
            cur = await db.execute("DELETE FROM audit_reports WHERE created_at < ?", (cutoff,))
            reports = cur.rowcount or 0
            cur = await db.execute("DELETE FROM audit_checks WHERE report_id NOT IN (SELECT report_id FROM audit_reports)")
            checks = cur.rowcount or 0
            await db.commit()
        return {"audit_reports": reports, "audit_checks": checks}

    # ------------------------------------------------------------------ reads
    async def list_reports(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM audit_reports ORDER BY created_at DESC, report_id DESC LIMIT ?",
                                  (limit,)) as cur:
                rows = await cur.fetchall()
        return [_decode_report(r) for r in rows]

    async def get_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        """The report plus its ``checks`` (ordered), or None."""
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM audit_reports WHERE report_id = ?", (report_id,)) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            report = _decode_report(row)
            async with db.execute("SELECT * FROM audit_checks WHERE report_id = ? ORDER BY seq", (report_id,)) as cur:
                checks = await cur.fetchall()
        report["checks"] = [_decode_check(c) for c in checks]
        return report

    async def latest_report(self) -> Optional[Dict[str, Any]]:
        rows = await self.list_reports(limit=1)
        return await self.get_report(rows[0]["report_id"]) if rows else None

    async def get_report_by_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM audit_reports WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                                  (run_id,)) as cur:
                row = await cur.fetchone()
        return _decode_report(row) if row else None

    async def previous_numbers(self, before_ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """The data-quality numbers of the newest report before ``before_ts`` that recorded any."""
        before_ts = time.time() + 1 if before_ts is None else before_ts
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT numbers_json FROM audit_reports WHERE created_at <= ? AND numbers_json IS NOT NULL "
                "AND numbers_json != '{}' ORDER BY created_at DESC LIMIT 1", (before_ts,)
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        try:
            data = json.loads(row["numbers_json"])
        except ValueError:
            return None
        return data if isinstance(data, dict) and data else None

    async def first_seen(self, check_id: str, status: str) -> Optional[float]:
        """When a check was first reported with ``status`` (derived, so no mutable findings table)."""
        async with _open_db(self._db_path) as db:
            async with db.execute(
                "SELECT MIN(r.created_at) FROM audit_checks c JOIN audit_reports r ON r.report_id = c.report_id "
                "WHERE c.check_id = ? AND c.status = ?", (check_id, status)
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row and row[0] is not None else None


_store: Optional[AuditStore] = None


def get_audit_store() -> AuditStore:
    if _store is None:
        raise RuntimeError("AuditStore has not been initialized")
    return _store


async def init_audit_store(db_path: str, retention_days: int = 365) -> AuditStore:
    global _store
    _store = AuditStore(db_path, retention_days=retention_days)
    await _store.initialize()
    return _store


__all__: Tuple[str, ...] = ("AuditStore", "get_audit_store", "init_audit_store", "new_report_id", "redact_deep")
