"""MCP Tool: Get historical pricing snapshots."""
from typing import Any, Dict, Optional

from src.services.pricing_history import get_pricing_history_service


class GetPricingHistoryTool:
    """Tool to query historical pricing snapshots from the local SQLite store."""

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Return historical pricing snapshots, optionally filtered."""
        try:
            svc = get_pricing_history_service()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        model_name: Optional[str] = arguments.get("model_name")
        provider: Optional[str] = arguments.get("provider")
        days: int = max(1, min(int(arguments.get("days", 30)), 365))
        limit: int = max(1, min(int(arguments.get("limit", 50)), 500))

        try:
            result = await svc.get_history(
                model_name=model_name, provider=provider, days=days, limit=limit
            )
            return {"success": True, **result}
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}
