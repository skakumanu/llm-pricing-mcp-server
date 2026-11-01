"""Document writer artefact store (v1.89.0).

Artefacts live in the SAME SQLite file as the orchestrator (``ORCHESTRATOR_DB_PATH``), so the existing
Litestream entry, volume and restore procedure cover them; this module owns one table, ``doc_artifacts``,
and is its sole writer. Artefacts are stored as structured JSON and rendered to HTML/Markdown on download,
so the status banner is always current.

Rules the module enforces rather than the caller:
- Everything stored is scrubbed first (emails, keys, Stripe ids, '@').
- The only UPDATE is ``update`` (a whitelist of the agent's own artefact columns); the only DELETE is
  ``purge_expired`` (retention). ``tests/test_docs_service.py`` pins that no other statement exists.
"""
import json
import time
from typing import Any, Dict, List, Optional

import aiosqlite

from src.services.docs_verify import scrub, scrub_deep
from src.services.orchestrator_store import _new_id, _open_db

STATUSES = ("gathering", "draft", "published", "discarded", "failed")
_JSON_COLUMNS = ("params_json", "manifest_json", "document_json", "llm_json")
UPDATABLE = frozenset({"title", "status", "manifest_json", "document_json", "llm_json", "corrections",
                       "size_bytes", "error", "published_at"})

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS doc_artifacts (
        artifact_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        doc_type TEXT NOT NULL,
        kind TEXT NOT NULL,
        format TEXT NOT NULL,
        title TEXT,
        status TEXT NOT NULL,
        params_json TEXT,
        manifest_json TEXT,
        document_json TEXT,
        llm_json TEXT,
        corrections INTEGER NOT NULL DEFAULT 0,
        size_bytes INTEGER,
        error TEXT,
        expires_at REAL NOT NULL,
        published_at REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_doc_artifacts_created ON doc_artifacts (created_at)",
    "CREATE INDEX IF NOT EXISTS idx_doc_artifacts_run ON doc_artifacts (run_id)",
)


def new_artifact_id() -> str:
    return _new_id("doc")


def _dump(value: Any) -> str:
    return json.dumps(scrub_deep(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _decode(row: Any) -> Dict[str, Any]:
    d = dict(row)
    for col in _JSON_COLUMNS:
        raw = d.pop(col, None)
        try:
            d[col[:-5]] = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            d[col[:-5]] = None
    return d


class DocsStore:
    """Persistence for document artefacts. Every method opens a short-lived connection."""

    def __init__(self, db_path: str, retention_days: int = 30) -> None:
        self._db_path = db_path
        self._retention_days = retention_days

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def retention_days(self) -> int:
        return self._retention_days

    async def initialize(self) -> None:
        async with _open_db(self._db_path) as db:
            for statement in _SCHEMA:
                await db.execute(statement)
            await db.commit()
        await self.purge_expired()

    # ------------------------------------------------------------------ writes
    async def insert_artifact(self, run_id: str, doc_type: str, kind: str, fmt: str, title: str,
                              params: Dict[str, Any], manifest: Dict[str, Any], now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        artifact_id = new_artifact_id()
        async with _open_db(self._db_path) as db:
            await db.execute(
                "INSERT INTO doc_artifacts (artifact_id, run_id, created_at, doc_type, kind, format, title, status, "
                "params_json, manifest_json, corrections, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'gathering', ?, ?, 0, ?)",
                (artifact_id, run_id, now, doc_type, kind, fmt, scrub(title)[:200], _dump(params), _dump(manifest),
                 now + max(1, self._retention_days) * 86400))
            await db.commit()
        return artifact_id

    async def update(self, artifact_id: str, **fields: Any) -> None:
        """The ONE update: the agent's own artefact columns (whitelisted, scrubbed)."""
        bad = set(fields) - UPDATABLE
        if bad:
            raise ValueError("not updatable: " + ", ".join(sorted(bad)))
        if not fields:
            return
        values: List[Any] = []
        for col, value in fields.items():
            if col in _JSON_COLUMNS:
                values.append(None if value is None else _dump(value))
            elif col in ("title", "error", "status"):
                values.append(None if value is None else scrub(str(value))[:300])
            else:
                values.append(value)
        sets = ", ".join(f"{col} = ?" for col in fields)  # column names come from the UPDATABLE whitelist
        async with _open_db(self._db_path) as db:
            await db.execute(f"UPDATE doc_artifacts SET {sets} WHERE artifact_id = ?",  # nosec B608
                             values + [artifact_id])
            await db.commit()

    async def purge_expired(self, now: Optional[float] = None) -> int:
        """Retention: artefacts past their expiry (created + DOCS_ARTIFACT_RETENTION_DAYS)."""
        now = time.time() if now is None else now
        async with _open_db(self._db_path) as db:
            cur = await db.execute("DELETE FROM doc_artifacts WHERE expires_at < ?", (now,))
            n = cur.rowcount or 0
            await db.commit()
        return n

    # ------------------------------------------------------------------ reads
    async def get(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM doc_artifacts WHERE artifact_id = ?", (artifact_id,)) as cur:
                row = await cur.fetchone()
        return _decode(row) if row else None

    async def get_by_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM doc_artifacts WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                                  (run_id,)) as cur:
                row = await cur.fetchone()
        return _decode(row) if row else None

    async def list(self, limit: int = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        # Two static statements (no string-built SQL); the status value is always a bound parameter.
        if status:
            sql = ("SELECT * FROM doc_artifacts WHERE status = ? "
                   "ORDER BY created_at DESC, artifact_id DESC LIMIT ?")
            params: List[Any] = [status, limit]
        else:
            sql = "SELECT * FROM doc_artifacts ORDER BY created_at DESC, artifact_id DESC LIMIT ?"
            params = [limit]
        async with _open_db(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [_decode(r) for r in rows]


_store: Optional[DocsStore] = None


def get_docs_store() -> DocsStore:
    if _store is None:
        raise RuntimeError("DocsStore has not been initialized")
    return _store


async def init_docs_store(db_path: str, retention_days: int = 30) -> DocsStore:
    global _store
    _store = DocsStore(db_path, retention_days=retention_days)
    await _store.initialize()
    return _store
