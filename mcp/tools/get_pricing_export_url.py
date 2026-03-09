"""MCP Tool: Generate a pricing history export download URL."""
from typing import Any, Dict, Optional
from urllib.parse import urlencode


class GetPricingExportUrlTool:
    """
    Construct a download URL for the pricing history export endpoint.

    Returns a URL the user can open in their browser to download the
    snapshot history as CSV or JSON.  Does not fetch the file itself.
    """

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        fmt: str = arguments.get("format", "csv").lower()
        if fmt not in ("csv", "json"):
            return {"success": False, "error": "format must be 'csv' or 'json'"}

        model_name: Optional[str] = arguments.get("model_name") or None
        provider: Optional[str] = arguments.get("provider") or None
        days: int = max(1, min(int(arguments.get("days", 30)), 365))
        limit: int = max(1, min(int(arguments.get("limit", 10000)), 100000))

        params: Dict[str, Any] = {"format": fmt, "days": days, "limit": limit}
        if model_name:
            params["model_name"] = model_name
        if provider:
            params["provider"] = provider

        url = "/pricing/history/export?" + urlencode(params)

        description_parts = [f"last {days} days"]
        if model_name:
            description_parts.append(f"model={model_name}")
        if provider:
            description_parts.append(f"provider={provider}")
        description = f"Pricing history export ({', '.join(description_parts)}) as {fmt.upper()}"

        return {
            "success": True,
            "url": url,
            "format": fmt,
            "description": description,
            "note": "Open this URL in a browser or use curl/wget to download the file.",
        }
