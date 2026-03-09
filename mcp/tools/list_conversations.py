"""MCP Tool: List stored chat conversation sessions."""
from typing import Any, Dict

from agent.conversation import get_conversation_store


class ListConversationsTool:
    """Return a summary of all stored chat sessions, newest first.

    Each entry includes the conversation ID, last-updated timestamp,
    turn count, and a 120-character preview of the most recent user message.
    """

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        limit: int = max(1, min(int(arguments.get("limit", 20)), 100))
        try:
            store = get_conversation_store()
        except (RuntimeError, Exception) as exc:
            return {"success": False, "error": str(exc)}

        try:
            conversations = await store.list_conversations()
        except Exception as exc:
            return {"success": False, "error": f"Failed to list conversations: {exc}"}

        conversations = conversations[:limit]
        return {
            "success": True,
            "conversations": conversations,
            "total": len(conversations),
        }
