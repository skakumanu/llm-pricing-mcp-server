"""MCP Tool: List registered price-change alert webhooks."""
from typing import Any, Dict

from src.services.pricing_alerts import get_pricing_alert_service


class ListPriceAlertsTool:
    """Return all registered price-change webhook alerts."""

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            svc = get_pricing_alert_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            alerts = await svc.list_alerts()
            return {"success": True, "alerts": alerts, "total": len(alerts)}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
