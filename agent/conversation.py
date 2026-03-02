"""Conversation history management with per-session turn trimming."""
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

    def get_or_create(
        self, conversation_id: Optional[str] = None
    ) -> Tuple[str, ConversationHistory]:
        """Return (id, history), creating a new conversation if the id is unknown."""
        if conversation_id is None or conversation_id not in self._conversations:
            conversation_id = conversation_id or str(uuid.uuid4())
            self._conversations[conversation_id] = ConversationHistory(
                max_turns=self._max_turns
            )
        return conversation_id, self._conversations[conversation_id]

    def delete(self, conversation_id: str) -> None:
        self._conversations.pop(conversation_id, None)
