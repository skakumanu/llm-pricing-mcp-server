"""MCP tool: support_chat (v1.84.0) — the identity-aware customer support assistant.

A thin adapter over ``agent.support_agent.SupportAgent.chat``. ``caller_access`` is
``IDENTITY_AWARE``: every caller may use it and the ToolManager hands the resolved
``McpCaller`` through, so a customer connected with its service key gets answers about its
own account while anonymous callers get general answers. STDIO has no connection
identity and refuses with ``support_requires_hosted_mcp`` (mirrors ``dispatch``).

Never echoes the message, a key or an exception message; the agent redacts secrets itself.
"""
from contextvars import ContextVar
from typing import Any, Dict, Optional

from mcp.caller import ANONYMOUS, IDENTITY_AWARE, McpCaller

_MAX_MESSAGE_LENGTH = 10_000
_MAX_CONVERSATION_ID_LENGTH = 64

# The hosted /mcp handler sets the connection's client address here for the duration of one
# tools/call so anonymous MCP callers get the same per-address daily spend bucket as /support/chat.
# It is server-set only (never a tool argument), read once, and never stored or logged.
client_address: ContextVar[Optional[str]] = ContextVar("support_chat_client_address", default=None)

STDIO_REFUSAL_MESSAGE = (
    "support_chat runs only on the hosted MCP endpoint, where your service key identifies your account. "
    "Connect to https://llm-pricing-api.fly.dev/mcp (optionally with your service key as x-api-key or "
    "Authorization: Bearer), or use `npx -y llm-pricing-mcp` with LLM_PRICING_API_KEY set. "
    "Without a key the assistant answers general questions only."
)


class SupportChatTool:
    """Ask the support assistant about plans, billing, keys, rate limits and how-to."""

    caller_access = IDENTITY_AWARE
    redact_errors = True

    def __init__(self, support_agent=None):
        self._agent = support_agent

    def set_agent(self, agent) -> None:
        self._agent = agent

    @staticmethod
    def stdio_refusal() -> Dict[str, Any]:
        return {
            "success": False,
            "error": "support_requires_hosted_mcp",
            "message": STDIO_REFUSAL_MESSAGE,
            "hosted_endpoint": "/mcp",
            "npx_env": "LLM_PRICING_API_KEY",
            "faq": "/faq",
            "billing": "/billing",
        }

    async def execute(self, arguments: Dict[str, Any], caller: Optional[McpCaller] = None) -> Dict[str, Any]:
        if self._agent is None:
            return {"success": False, "error": "support_unavailable",
                    "message": "The support assistant is not initialized on this server.", "faq": "/faq"}
        if not isinstance(arguments, dict):
            arguments = {}
        message = str(arguments.get("message") or "").strip()
        if not message:
            return {"success": False, "error": "'message' is required and must not be empty."}
        if len(message) > _MAX_MESSAGE_LENGTH:
            return {"success": False,
                    "error": f"'message' exceeds maximum length of {_MAX_MESSAGE_LENGTH} characters."}
        conversation_id = arguments.get("conversation_id")
        if conversation_id is not None:
            conversation_id = str(conversation_id)[:_MAX_CONVERSATION_ID_LENGTH]
        try:
            response = await self._agent.chat(message, conversation_id, caller=caller or ANONYMOUS,
                                              client_ip=client_address.get(), trigger="mcp:support_chat")
        except ValueError:
            return {"success": False, "error": "invalid_conversation_id",
                    "message": "conversation_id must be one returned by a previous support_chat reply."}
        except Exception as exc:  # never str(exc): it could quote the message
            return {"success": False, "error": "support_internal_error", "error_type": type(exc).__name__}
        return {
            "success": True,
            "reply": response.reply,
            "conversation_id": response.conversation_id,
            "identified": response.identified,
            "tier": response.tier,
            "escalation": response.escalation,
            "fallback": response.fallback,
            "sources": response.sources,
        }
