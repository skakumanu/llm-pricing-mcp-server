"""MCP Tool: List registered price-change alert webhooks."""
from typing import Any, Dict, Optional

from mcp.caller import OPERATOR_ONLY, McpCaller, authorize
from src.services.pricing_alerts import get_pricing_alert_service


class ListPriceAlertsTool:
    """Return all registered price-change webhook alerts."""

    # v1.79.2: every subscriber's / visitor's data — ToolManager refuses any caller
    # but the operator (MCP_API_KEY). caller=None (direct construction) is unrestricted.
    caller_access = OPERATOR_ONLY

    async def execute(
        self, arguments: Dict[str, Any], caller: Optional[McpCaller] = None
    ) -> Dict[str, Any]:
        refusal = authorize(caller, self.caller_access, arguments) if caller is not None else None
        if refusal is not None:
            return refusal
        try:
            svc = get_pricing_alert_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            alerts = await svc.list_alerts()
            return {"success": True, "alerts": alerts, "total": len(alerts)}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
