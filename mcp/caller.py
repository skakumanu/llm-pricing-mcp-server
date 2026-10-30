"""Caller identity for MCP tool execution (v1.79.1, OPERATOR_ONLY in v1.79.2).

An ``McpCaller`` says who is calling a tool: an anonymous user, a customer (a
service API key resolved to an org via the billing service), or the operator (the
global ``MCP_API_KEY``). Transports resolve it — POST /mcp from the connection's
headers; STDIO and the PricingAgent never resolve one and are always anonymous —
and hand it to ``ToolManager.execute_tool``. Tools declare how they treat callers
through a ``caller_access`` class attribute:

- ``PUBLIC`` (default when absent): every caller, unchanged behaviour; the tool
  never sees the caller.
- ``ORG_SCOPED``: a customer acts on, and only on, its own org; the operator keeps
  the unrestricted behaviour; anonymous callers are refused.
- ``OPERATOR_ONLY`` (v1.79.2): only the operator; anonymous and customer callers
  (and therefore the chat agent and STDIO) get a constant ``operator_key_required``
  refusal that reveals nothing about stored records, counts or id validity.
- ``CUSTOMER_ONLY`` (used since v1.80.0 by the MCP ``dispatch`` tool, which needs a
  real org ledger and the org's stored provider keys): only customers allowed;
  anonymous and operator callers are refused (dispatch substitutes HTTP's
  ``dispatch_requires_api_key`` via its ``access_refusal()``), and on STDIO the
  ToolManager returns the tool's ``stdio_refusal()`` before authorising.

This module deliberately imports nothing from ``src.main`` so other transports and
tools can reuse it. A caller never holds a credential: ``from_customer`` copies only
the customer id, org id and tier, so no API key or email can reach a tool result, a
repr, a log record or an exception message.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional

KIND_ANONYMOUS = "anonymous"
KIND_CUSTOMER = "customer"
KIND_OPERATOR = "operator"

PUBLIC = "public"
ORG_SCOPED = "org_scoped"
CUSTOMER_ONLY = "customer_only"
OPERATOR_ONLY = "operator_only"

ORG_MISMATCH_ERROR = "A customer key can only act on its own org; omit org_id or pass your own."
CUSTOMER_KEY_REQUIRED_ERROR = (
    "This tool reads or writes per-org usage data and needs your service API key on the MCP connection."
)

OPERATOR_KEY_REQUIRED_ERROR = (
    "This tool is restricted to the server operator and needs the operator key on the MCP connection."
)

# Shared description suffix for every OPERATOR_ONLY tool (MCP tools/list and the chat
# agent's bound-tool specs), so the auth wording lives in one place.
OPERATOR_ONLY_NOTE = (
    "Operator only: needs the server operator's key (MCP_API_KEY) on the MCP connection "
    "(x-api-key or Authorization: Bearer); any other caller, including the chat agent "
    "and the local STDIO server, gets error_code operator_key_required."
)


@dataclass(frozen=True)
class McpCaller:
    """Who is calling an MCP tool. Never carries a credential."""

    kind: str = KIND_ANONYMOUS
    customer_id: Optional[str] = None
    org_id: Optional[str] = None
    tier: Optional[str] = None

    @property
    def is_anonymous(self) -> bool:
        return self.kind == KIND_ANONYMOUS

    @property
    def is_customer(self) -> bool:
        return self.kind == KIND_CUSTOMER

    @property
    def is_operator(self) -> bool:
        return self.kind == KIND_OPERATOR

    @classmethod
    def anonymous(cls) -> "McpCaller":
        return ANONYMOUS

    @classmethod
    def operator(cls) -> "McpCaller":
        return cls(kind=KIND_OPERATOR)

    @classmethod
    def from_customer(cls, record: Any) -> "McpCaller":
        """Build a customer caller from a billing CustomerRecord (id, org_id, tier only)."""
        return cls(
            kind=KIND_CUSTOMER,
            customer_id=getattr(record, "id", None),
            org_id=getattr(record, "org_id", None),
            tier=getattr(record, "tier", None),
        )


ANONYMOUS = McpCaller()


def customer_key_required() -> Dict[str, Any]:
    """Refusal for an anonymous caller of an org-scoped tool, with setup guidance."""
    return {
        "success": False,
        "error": CUSTOMER_KEY_REQUIRED_ERROR,
        "error_code": "customer_key_required",
        "signup": "/billing/signup",
        "auth": {
            "header": "x-api-key",
            "alternative": "Authorization: Bearer <key>",
            "npx_env": "LLM_PRICING_API_KEY",
            "docs": "/mcp-setup",
        },
    }


def operator_key_required() -> Dict[str, Any]:
    """Refusal for any non-operator caller of an operator-only tool.

    Deliberately constant: identical for anonymous, customer and unknown-key callers
    and for existing and unknown ids — no hints, counts, ids or echoed arguments.
    """
    return {
        "success": False,
        "error": OPERATOR_KEY_REQUIRED_ERROR,
        "error_code": "operator_key_required",
    }


def org_mismatch() -> Dict[str, Any]:
    """Refusal for a customer naming another org. Echoes neither org_id nor the key."""
    return {"success": False, "error": ORG_MISMATCH_ERROR, "error_code": "org_mismatch"}


def authorize(
    caller: Optional[McpCaller], access: str, arguments: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Return a refusal result if ``caller`` may not run a tool with ``access``, else None."""
    if access == PUBLIC:
        return None
    caller = caller or ANONYMOUS
    if access == OPERATOR_ONLY:
        return None if caller.is_operator else operator_key_required()
    if caller.is_customer:
        if not caller.org_id:
            # Defensive: customers.org_id is NOT NULL, but a customer with no org must
            # never fall through to the operator's all-org behaviour.
            return customer_key_required()
        requested = (arguments or {}).get("org_id") if isinstance(arguments, dict) else None
        if requested not in (None, "") and requested != caller.org_id:
            return org_mismatch()
        return None
    if caller.is_operator and access == ORG_SCOPED:
        return None
    # Anonymous for any non-public tool; operator for CUSTOMER_ONLY tools.
    return customer_key_required()


def effective_org_id(caller: Optional[McpCaller], arguments: Dict[str, Any]) -> Optional[str]:
    """Org a tool should act on: the customer's own org, else whatever org_id was passed.

    ``caller=None`` (direct in-process construction, unit tests) and the operator keep
    the pre-v1.79.1 behaviour of honouring ``arguments['org_id']``.
    """
    if caller is not None and caller.is_customer:
        return caller.org_id
    return arguments.get("org_id")
