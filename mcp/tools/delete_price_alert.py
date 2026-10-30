"""MCP Tool: Delete a registered price-change alert webhook."""
from typing import Any, Dict, Optional

from mcp.caller import OPERATOR_ONLY, McpCaller, authorize
from src.services.pricing_alerts import get_pricing_alert_service


class DeletePriceAlertTool:
    """Delete a price-change webhook alert by its ID."""

    # v1.79.2: every subscriber's / visitor's data — ToolManager refuses any caller
    # but the operator (MCP_API_KEY). caller=None (direct construction) is unrestricted.
    caller_access = OPERATOR_ONLY

    async def execute(
        self, arguments: Dict[str, Any], caller: Optional[McpCaller] = None
    ) -> Dict[str, Any]:
        refusal = authorize(caller, self.caller_access, arguments) if caller is not None else None
        if refusal is not None:
            return refusal
        alert_id = arguments.get("alert_id")
        if alert_id is None:
            return {"success": False, "error": "alert_id is required"}

        try:
            alert_id = int(alert_id)
        except (TypeError, ValueError):
            return {"success": False, "error": "alert_id must be an integer"}

        try:
            svc = get_pricing_alert_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            deleted = await svc.delete(alert_id)
            if deleted:
                return {"success": True, "deleted": True, "alert_id": alert_id}
            return {"success": False, "deleted": False, "error": f"Alert {alert_id} not found"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
