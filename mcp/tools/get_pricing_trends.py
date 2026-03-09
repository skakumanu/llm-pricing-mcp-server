"""MCP Tool: Get pricing trend analysis."""
from typing import Any, Dict

from src.services.pricing_history import get_pricing_history_service


class GetPricingTrendsTool:
    """Tool to surface models with the largest price changes over a time window."""

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Return models ranked by absolute percentage price change."""
        try:
            svc = get_pricing_history_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        days: int = max(1, min(int(arguments.get("days", 30)), 365))
        limit: int = max(1, min(int(arguments.get("limit", 20)), 100))

        try:
            trends = await svc.get_trends(days=days, limit=limit)
            return {"success": True, "trends": trends, "days": days, "total": len(trends)}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
