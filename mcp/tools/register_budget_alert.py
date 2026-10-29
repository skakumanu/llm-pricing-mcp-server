"""MCP Tool: Register a budget-spend alert webhook."""
from typing import Any, Dict, Optional

from mcp.caller import ORG_SCOPED, McpCaller, authorize, effective_org_id
from src.services.budget_alerts import get_budget_alert_service


class RegisterBudgetAlertTool:
    """Register a webhook URL to be called when actual recorded spend crosses a threshold.

    Never sets hard_stop (that stays HTTP-only, POST /usage/alerts with a customer key);
    a customer caller registers only under its own org.
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

        org_id: Optional[str] = effective_org_id(caller, arguments) or None

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
