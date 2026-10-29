"""MCP Tool: Get a summary of actual recorded LLM usage/spend."""
from typing import Any, Dict, Optional

from mcp.caller import ORG_SCOPED, McpCaller, authorize, effective_org_id
from src.services.usage_tracker import get_usage_tracker


class GetUsageSummaryTool:
    """Report total actual spend and per-model/per-provider breakdowns from recorded usage events.

    A customer caller only ever sees its own org's summary — never the all-org aggregate
    that an omitted org_id yields for the operator.
    """

    # v1.79.1: per-org data — ToolManager refuses anonymous callers and binds a
    # customer key to its own org. caller=None (direct construction) is unrestricted.
    caller_access = ORG_SCOPED

    async def execute(
        self, arguments: Dict[str, Any], caller: Optional[McpCaller] = None
    ) -> Dict[str, Any]:
        refusal = authorize(caller, self.caller_access, arguments) if caller is not None else None
        if refusal is not None:
            return refusal
        org_id = effective_org_id(caller, arguments)
        days = int(arguments.get("days", 30))
        if days <= 0:
            return {"success": False, "error": "days must be greater than 0"}

        try:
            tracker = get_usage_tracker()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            result = await tracker.get_summary(org_id=org_id, days=days)
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}

        return {"success": True, **result}
