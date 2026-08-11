"""MCP Tool: List registered budget-spend alert webhooks."""
from typing import Any, Dict

from src.services.budget_alerts import get_budget_alert_service


class ListBudgetAlertsTool:
    """Return all registered budget-spend webhook alerts."""

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            svc = get_budget_alert_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            alerts = await svc.list_alerts()
            return {"success": True, "alerts": alerts, "total": len(alerts)}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
