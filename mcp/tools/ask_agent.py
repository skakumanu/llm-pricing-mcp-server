"""AskAgentTool — MCP tool that delegates to the PricingAgent."""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LENGTH = 10_000   # characters
_MAX_CONVERSATION_ID_LENGTH = 128


class AskAgentTool:
    """MCP tool wrapper around PricingAgent.chat() / run_task()."""

    def __init__(self, pricing_agent=None):
        self._agent = pricing_agent

    def set_agent(self, agent) -> None:
        """Late-bind the PricingAgent (used when the agent is initialized after tool setup)."""
        self._agent = agent

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the ask_agent tool."""
        if self._agent is None:
            return {
                "success": False,
                "error": "PricingAgent is not initialized.",
            }

        message: str = arguments.get("message", "").strip()
        if not message:
            return {
                "success": False,
                "error": "'message' is required and must not be empty.",
            }
        if len(message) > _MAX_MESSAGE_LENGTH:
            return {
                "success": False,
                "error": f"'message' exceeds maximum length of {_MAX_MESSAGE_LENGTH} characters.",
            }

        conversation_id: Optional[str] = arguments.get("conversation_id")
        if conversation_id is not None:
            conversation_id = str(conversation_id)[:_MAX_CONVERSATION_ID_LENGTH]

        autonomous: bool = bool(arguments.get("autonomous", False))

        try:
            if autonomous:
                response = await self._agent.run_task(message)
            else:
                response = await self._agent.chat(message, conversation_id)

            return {
                "success": True,
                "reply": response.reply,
                "conversation_id": response.conversation_id,
                "tool_calls": response.tool_calls,
                "sources": response.sources,
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
