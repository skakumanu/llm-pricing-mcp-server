"""Conversation history management with per-session turn trimming and optional persistence."""
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


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

    def __init__(self, max_turns: int = 10):
        self._max_turns = max_turns
        self._conversations: Dict[str, ConversationHistory] = {}

    async def get_or_create(
        self, conversation_id: Optional[str] = None
    ) -> Tuple[str, ConversationHistory]:
        """Return (id, history), creating a new conversation if the id is unknown."""
        if conversation_id is None or conversation_id not in self._conversations:
            conversation_id = conversation_id or str(uuid.uuid4())
            self._conversations[conversation_id] = ConversationHistory(
                max_turns=self._max_turns
            )
        return conversation_id, self._conversations[conversation_id]

    async def save(self, conversation_id: str, history: ConversationHistory) -> None:
        """No-op: in-memory store doesn't need explicit persistence."""

    def delete(self, conversation_id: str) -> None:
        self._conversations.pop(conversation_id, None)


class SQLiteConversationStore:
    """Persistent conversation store backed by a SQLite database via aiosqlite.

    Keeps an in-process cache so repeated lookups within the same server
    process don't hit the database on every turn.
    """

    def __init__(self, db_path: str, max_turns: int = 10):
        self._db_path = db_path
        self._max_turns = max_turns
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
            await db.commit()

    async def get_or_create(
        self, conversation_id: Optional[str] = None
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

    async def save(self, conversation_id: str, history: ConversationHistory) -> None:
        """Persist the current state of a conversation to SQLite."""
        import aiosqlite
        messages_json = json.dumps(
            [{"role": t.role, "content": t.content} for t in history._turns]
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO conversations (id, messages, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    messages = excluded.messages,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, messages_json, time.time()),
            )
            await db.commit()

    def delete(self, conversation_id: str) -> None:
        self._cache.pop(conversation_id, None)
