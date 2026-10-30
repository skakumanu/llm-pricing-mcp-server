"""MCP Tool: List stored chat conversation sessions."""
from typing import Any, Dict, Optional

from agent.conversation import get_conversation_store
from mcp.caller import OPERATOR_ONLY, McpCaller, authorize


class ListConversationsTool:
    """Return a summary of all stored chat sessions, newest first.

    Each entry includes the conversation ID, last-updated timestamp,
    turn count, and a 120-character preview of the most recent user message.
    """

    # v1.79.2: every subscriber's / visitor's data — ToolManager refuses any caller
    # but the operator (MCP_API_KEY). caller=None (direct construction) is unrestricted.
    caller_access = OPERATOR_ONLY

    async def execute(
        self, arguments: Dict[str, Any], caller: Optional[McpCaller] = None
    ) -> Dict[str, Any]:
        refusal = authorize(caller, self.caller_access, arguments) if caller is not None else None
        if refusal is not None:
            return refusal
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
