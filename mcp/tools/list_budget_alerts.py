"""MCP Tool: List registered budget-spend alert webhooks."""
from typing import Any, Dict, Optional

from mcp.caller import ORG_SCOPED, McpCaller, authorize
from src.services.budget_alerts import get_budget_alert_service


class ListBudgetAlertsTool:
    """Return registered budget-spend webhook alerts: a customer's own org's, or all for the operator."""

    # v1.79.1: per-org data — ToolManager refuses anonymous callers and binds a
    # customer key to its own org. caller=None (direct construction) is unrestricted.
    caller_access = ORG_SCOPED

    async def execute(
        self, arguments: Dict[str, Any], caller: Optional[McpCaller] = None
    ) -> Dict[str, Any]:
        refusal = authorize(caller, self.caller_access, arguments) if caller is not None else None
        if refusal is not None:
            return refusal
        try:
            svc = get_budget_alert_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            if caller is not None and caller.is_customer:
                alerts = await svc.list_alerts(org_id=caller.org_id)
            else:
                alerts = await svc.list_alerts()
            return {"success": True, "alerts": alerts, "total": len(alerts)}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
