"""Conversation history management with per-session turn trimming and optional persistence."""
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union


@dataclass
class Turn:
    """A single message in a conversation."""
    role: str   # "user" | "assistant"
    content: str


class ConversationHistory:
    """Ordered list of conversation turns, capped at max_turns pairs."""

    def __init__(self, max_turns: int = 10):
        # max_turns = max user+assistant pairs to retain
        self._max_messages = max_turns * 2
        self._turns: List[Turn] = []

    def add(self, role: str, content: str) -> None:
        """Append a turn and trim to the configured maximum."""
        self._turns.append(Turn(role=role, content=content))
        if len(self._turns) > self._max_messages:
            self._turns = self._turns[-self._max_messages:]

    def to_messages(self) -> List[Dict[str, Any]]:
        """Convert history to a list of role/content dicts for LLM APIs."""
        return [{"role": t.role, "content": t.content} for t in self._turns]

    def clear(self) -> None:
        self._turns.clear()

    def __len__(self) -> int:
        return len(self._turns)


class ConversationStore:
    """In-memory registry of named ConversationHistory objects."""

    def __init__(self, max_turns: int = 10, retention_days: int = 0):
        self._max_turns = max_turns
        self._retention_days = retention_days
        self._conversations: Dict[str, ConversationHistory] = {}
        # v1.87.0: org attribution and last-update time, parallel to the SQLite store's columns.
        self._org: Dict[str, str] = {}
        self._updated: Dict[str, float] = {}

    async def get_or_create(
        self, conversation_id: Optional[str] = None, org_id: Optional[str] = None
    ) -> Tuple[str, ConversationHistory]:
        """Return (id, history), creating a new conversation if the id is unknown."""
        if conversation_id is None or conversation_id not in self._conversations:
            conversation_id = conversation_id or str(uuid.uuid4())
            self._conversations[conversation_id] = ConversationHistory(
                max_turns=self._max_turns
            )
            self._updated[conversation_id] = time.time()
        if org_id:
            self._org.setdefault(conversation_id, org_id)
        return conversation_id, self._conversations[conversation_id]

    async def save(self, conversation_id: str, history: ConversationHistory,
                   org_id: Optional[str] = None) -> None:
        """Record attribution and recency; a later anonymous save never detaches the org."""
        if conversation_id not in self._conversations:
            self._conversations[conversation_id] = history
        if org_id:
            self._org.setdefault(conversation_id, org_id)  # first owner wins
        self._updated[conversation_id] = time.time()
        await self.purge_expired()

    async def delete(self, conversation_id: str) -> bool:
        """Remove a conversation. Returns True if it existed."""
        existed = conversation_id in self._conversations
        self._conversations.pop(conversation_id, None)
        self._org.pop(conversation_id, None)
        self._updated.pop(conversation_id, None)
        return existed

    async def owner_of(self, conversation_id: str) -> Optional[str]:
        """The org a conversation is attributed to (None when anonymous or unknown)."""
        return self._org.get(conversation_id)

    async def list_for_org(self, org_id: str) -> List[Dict[str, Any]]:
        """Every conversation attributed to ``org_id`` (account export)."""
        return [
            {"id": cid, "updated_at": self._updated.get(cid, 0.0),
             "messages": self._conversations[cid].to_messages()}
            for cid, owner in self._org.items() if owner == org_id and cid in self._conversations
        ]

    async def delete_for_org(self, org_id: str) -> int:
        """Erase every conversation attributed to ``org_id`` (account deletion)."""
        ids = [cid for cid, owner in self._org.items() if owner == org_id]
        for cid in ids:
            await self.delete(cid)
        return len(ids)

    async def purge_expired(self) -> int:
        """Drop conversations not updated within the retention window (<= 0 disables)."""
        if self._retention_days <= 0:
            return 0
        cutoff = time.time() - self._retention_days * 86400
        stale = [cid for cid, ts in self._updated.items() if ts < cutoff]
        for cid in stale:
            await self.delete(cid)
        return len(stale)

    async def list_conversations(self) -> List[Dict[str, Any]]:
        """Return metadata for all in-memory conversations, newest first."""
        result = []
        for conv_id, history in self._conversations.items():
            turns = history.to_messages()
            preview: Optional[str] = None
            for msg in reversed(turns):
                if msg["role"] == "user":
                    content = msg["content"]
                    preview = content[:120] + ("…" if len(content) > 120 else "")
                    break
            result.append({
                "id": conv_id,
                "updated_at": 0.0,
                "turn_count": len(turns),
                "preview": preview,
            })
        return result


class SQLiteConversationStore:
    """Persistent conversation store backed by a SQLite database via aiosqlite.

    Keeps an in-process cache so repeated lookups within the same server
    process don't hit the database on every turn.
    """

    def __init__(self, db_path: str, max_turns: int = 10, retention_days: int = 0):
        self._db_path = db_path
        self._max_turns = max_turns
        self._retention_days = retention_days
        self._cache: Dict[str, ConversationHistory] = {}

    async def initialize(self) -> None:
        """Create the conversations table if it doesn't exist."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    messages TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            # v1.87.0: nullable org attribution (anonymous conversations stay NULL).
            async with db.execute("PRAGMA table_info(conversations)") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            if "org_id" not in existing:
                await db.execute("ALTER TABLE conversations ADD COLUMN org_id TEXT")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversations_org ON conversations (org_id, updated_at)"
            )
            await db.commit()

    async def get_or_create(
        self, conversation_id: Optional[str] = None, org_id: Optional[str] = None
    ) -> Tuple[str, ConversationHistory]:
        """Load conversation from cache or DB, creating a new one if unknown."""
        import aiosqlite
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())

        if conversation_id in self._cache:
            return conversation_id, self._cache[conversation_id]

        history = ConversationHistory(max_turns=self._max_turns)
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT messages FROM conversations WHERE id = ?", (conversation_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    for turn in json.loads(row[0]):
                        history._turns.append(Turn(role=turn["role"], content=turn["content"]))

        self._cache[conversation_id] = history
        return conversation_id, history

    async def save(self, conversation_id: str, history: ConversationHistory,
                   org_id: Optional[str] = None) -> None:
        """Persist the current state of a conversation to SQLite.

        ``org_id`` attributes the conversation to an organisation; a later save without one
        (an anonymous turn) never detaches it, and a different org never takes it over (first owner wins).
        """
        import aiosqlite
        messages_json = json.dumps(
            [{"role": t.role, "content": t.content} for t in history._turns]
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO conversations (id, messages, updated_at, org_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    messages = excluded.messages,
                    updated_at = excluded.updated_at,
                    org_id = COALESCE(conversations.org_id, excluded.org_id)
                """,
                (conversation_id, messages_json, time.time(), org_id),
            )
            await db.commit()
        await self.purge_expired()

    async def owner_of(self, conversation_id: str) -> Optional[str]:
        """The org a conversation is attributed to (None when anonymous or unknown)."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute("SELECT org_id FROM conversations WHERE id = ?", (conversation_id,)) as cur:
                row = await cur.fetchone()
        return row[0] if row else None

    async def list_for_org(self, org_id: str) -> List[Dict[str, Any]]:
        """Every conversation attributed to ``org_id`` (account export)."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(
                "SELECT id, messages, updated_at FROM conversations WHERE org_id = ? ORDER BY updated_at",
                (org_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [{"id": r[0], "updated_at": r[2], "messages": json.loads(r[1])} for r in rows]

    async def delete_for_org(self, org_id: str) -> int:
        """Erase every conversation attributed to ``org_id`` (account deletion)."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute("SELECT id FROM conversations WHERE org_id = ?", (org_id,)) as cur:
                ids = [r[0] for r in await cur.fetchall()]
            await db.execute("DELETE FROM conversations WHERE org_id = ?", (org_id,))
            await db.commit()
        for cid in ids:
            self._cache.pop(cid, None)
        return len(ids)

    async def purge_expired(self) -> int:
        """Delete conversations not updated within the retention window (<= 0 disables)."""
        if self._retention_days <= 0:
            return 0
        import aiosqlite
        cutoff = time.time() - self._retention_days * 86400
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute("SELECT id FROM conversations WHERE updated_at < ?", (cutoff,)) as cur:
                ids = [r[0] for r in await cur.fetchall()]
            if ids:
                await db.execute("DELETE FROM conversations WHERE updated_at < ?", (cutoff,))
                await db.commit()
        for cid in ids:
            self._cache.pop(cid, None)
        return len(ids)

    async def delete(self, conversation_id: str) -> bool:
        """Remove a conversation from cache and DB. Returns True if it existed."""
        import aiosqlite
        self._cache.pop(conversation_id, None)
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM conversations WHERE id = ?", (conversation_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_conversations(self) -> List[Dict[str, Any]]:
        """Return metadata for all stored conversations, newest first."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, messages, updated_at FROM conversations ORDER BY updated_at DESC"
            ) as cur:
                rows = await cur.fetchall()

        result = []
        for row in rows:
            messages = json.loads(row["messages"])
            preview: Optional[str] = None
            for msg in reversed(messages):
                if msg["role"] == "user":
                    content = msg["content"]
                    preview = content[:120] + ("…" if len(content) > 120 else "")
                    break
            result.append({
                "id": row["id"],
                "updated_at": row["updated_at"],
                "turn_count": len(messages),
                "preview": preview,
            })
        return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_conversation_store: Optional[Union[ConversationStore, "SQLiteConversationStore"]] = None


async def init_conversation_store(
    db_path: Optional[str] = None, max_turns: int = 10, retention_days: int = 0
) -> None:
    """Create and initialize the module-level conversation store singleton.

    Call this once at application startup before the agent is initialized.
    If ``db_path`` is None the store is in-memory (conversations lost on restart).
    ``retention_days`` > 0 purges conversations not updated within that window, here at
    startup and after every save (v1.87.0).
    """
    global _conversation_store
    if db_path:
        store = SQLiteConversationStore(db_path=db_path, max_turns=max_turns, retention_days=retention_days)
        await store.initialize()
        await store.purge_expired()
        _conversation_store = store
    else:
        _conversation_store = ConversationStore(max_turns=max_turns, retention_days=retention_days)


def get_conversation_store() -> Union[ConversationStore, "SQLiteConversationStore"]:
    """Return the module-level conversation store singleton.

    Raises ``RuntimeError`` if ``init_conversation_store`` has not been called.
    """
    if _conversation_store is None:
        raise RuntimeError(
            "Conversation store has not been initialized. "
            "Call init_conversation_store() first."
        )
    return _conversation_store
