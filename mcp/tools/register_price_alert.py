"""MCP Tool: Register a price-change alert webhook."""
from typing import Any, Dict, Optional

from src.services.pricing_alerts import get_pricing_alert_service


class RegisterPriceAlertTool:
    """Register a webhook URL to be called when a model's price changes beyond a threshold."""

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        url: Optional[str] = arguments.get("url")
        if not url:
            return {"success": False, "error": "url is required"}

        threshold_pct: float = float(arguments.get("threshold_pct", 5.0))
        if threshold_pct <= 0:
            return {"success": False, "error": "threshold_pct must be greater than 0"}

        provider: Optional[str] = arguments.get("provider") or None
        model_name: Optional[str] = arguments.get("model_name") or None

        try:
            svc = get_pricing_alert_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            record = await svc.register(
                url=url,
                threshold_pct=threshold_pct,
                provider=provider,
                model_name=model_name,
            )
            return {"success": True, **record}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
