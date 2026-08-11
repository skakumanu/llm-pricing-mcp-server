"""MCP Tool: Get a summary of actual recorded LLM usage/spend."""
from typing import Any, Dict

from src.services.usage_tracker import get_usage_tracker


class GetUsageSummaryTool:
    """Report total actual spend and per-model/per-provider breakdowns from recorded usage events."""

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        org_id = arguments.get("org_id")
        days = int(arguments.get("days", 30))
        if days <= 0:
            return {"success": False, "error": "days must be greater than 0"}

        try:
            tracker = get_usage_tracker()
        except RuntimeError as exc:
            return {"success": False, "error": str(exc)}

        try:
            result = await tracker.get_summary(org_id=org_id, days=days)
        except Exception as exc:
            return {"success": False, "error": str(exc), "error_type": type(exc).__name__}

        return {"success": True, **result}
