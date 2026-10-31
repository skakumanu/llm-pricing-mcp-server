"""In-memory MCP session store for the hosted Streamable-HTTP transport (v1.85.0).

The hosted POST /mcp is stateless per request, so the only way to carry the
``initialize`` clientInfo.name to a later ``tools/call`` is an ``Mcp-Session-Id``
header the client echoes back. This store keeps ONLY that name string (<= 100
chars): never headers, arguments or keys. It is per-process memory, bounded
and TTL'd; it is lost on restart, in which case callers simply get
client_source='none'. Best effort by design.
"""
import secrets
import time
from collections import OrderedDict
from typing import Optional, Tuple

MAX_NAME_LENGTH = 100


class McpSessionStore:
    def __init__(self, max_entries: int = 10_000, ttl_seconds: int = 86_400) -> None:
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._items: "OrderedDict[str, Tuple[Optional[str], float]]" = OrderedDict()

    def create(self, client_name: Optional[str]) -> str:
        name = client_name[:MAX_NAME_LENGTH] if isinstance(client_name, str) and client_name else None
        session_id = secrets.token_urlsafe(24)
        now = time.time()
        self._purge(now)
        while len(self._items) >= self._max_entries:
            self._items.popitem(last=False)
        self._items[session_id] = (name, now)
        return session_id

    def client_name(self, session_id: Optional[str]) -> Optional[str]:
        if not session_id:
            return None
        item = self._items.get(session_id)
        if item is None:
            return None
        name, created = item
        if time.time() - created >= self._ttl:
            del self._items[session_id]
            return None
        self._items.move_to_end(session_id)
        return name

    def _purge(self, now: float) -> None:
        expired = [k for k, (_, t) in self._items.items() if now - t >= self._ttl]
        for k in expired:
            del self._items[k]

    def __len__(self) -> int:
        return len(self._items)
