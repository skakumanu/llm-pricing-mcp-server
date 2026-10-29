"""MCP Tool: Delete a registered budget-spend alert webhook."""
from typing import Any, Dict, Optional

from mcp.caller import ORG_SCOPED, McpCaller, authorize
from src.services.budget_alerts import get_budget_alert_service


class DeleteBudgetAlertTool:
    """Delete a budget-spend webhook alert by its ID.

    A customer caller can only delete its own org's alerts; another org's alert id
    reads as "not found" (BudgetAlertService.delete's org filter).
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
        alert_id = arguments.get("alert_id")
        if alert_id is None:
            return {"success": False, "error": "alert_id is required"}

        try:
            alert_id = int(alert_id)
        except (TypeError, ValueError):
            return {"success": False, "error": "alert_id must be an integer"}

        try:
            svc = get_budget_alert_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            if caller is not None and caller.is_customer:
                deleted = await svc.delete(alert_id, org_id=caller.org_id)
            else:
                deleted = await svc.delete(alert_id)
            if deleted:
                return {"success": True, "deleted": True, "alert_id": alert_id}
            return {"success": False, "deleted": False, "error": f"Alert {alert_id} not found"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
