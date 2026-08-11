"""MCP Tool: Register a budget-spend alert webhook."""
from typing import Any, Dict, Optional

from src.services.budget_alerts import get_budget_alert_service


class RegisterBudgetAlertTool:
    """Register a webhook URL to be called when actual recorded spend crosses a threshold."""

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        url: Optional[str] = arguments.get("url")
        if not url:
            return {"success": False, "error": "url is required"}

        threshold_usd = arguments.get("threshold_usd")
        if threshold_usd is None:
            return {"success": False, "error": "threshold_usd is required"}
        threshold_usd = float(threshold_usd)
        if threshold_usd <= 0:
            return {"success": False, "error": "threshold_usd must be greater than 0"}

        period_days = int(arguments.get("period_days", 30))
        if period_days <= 0:
            return {"success": False, "error": "period_days must be greater than 0"}

        org_id: Optional[str] = arguments.get("org_id") or None

        try:
            svc = get_budget_alert_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            record = await svc.register(
                url=url,
                threshold_usd=threshold_usd,
                org_id=org_id,
                period_days=period_days,
            )
            return {"success": True, **record}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
