"""MCP Tool: Delete a stored chat conversation session."""
from typing import Any, Dict, Optional

from agent.conversation import get_conversation_store
from mcp.caller import OPERATOR_ONLY, McpCaller, authorize


class DeleteConversationTool:
    """Delete a specific conversation by its ID.

    Returns success=True and deleted=True if the conversation existed and was
    removed.  Returns success=False and deleted=False when the ID is not found.
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
        conversation_id: str = arguments.get("conversation_id", "").strip()
        if not conversation_id:
            return {"success": False, "error": "conversation_id is required"}

        try:
            store = get_conversation_store()
        except (RuntimeError, Exception) as exc:
            return {"success": False, "error": str(exc)}

        try:
            deleted = await store.delete(conversation_id)
        except Exception as exc:
            return {"success": False, "error": f"Failed to delete conversation: {exc}"}

        if deleted:
            return {"success": True, "deleted": True, "conversation_id": conversation_id}
        return {
            "success": False,
            "deleted": False,
            "conversation_id": conversation_id,
            "error": f"Conversation '{conversation_id}' not found",
        }
