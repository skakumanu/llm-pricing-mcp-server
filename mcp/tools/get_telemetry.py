"""MCP Tool: Get Telemetry Data"""
from typing import Any, Dict

from src.services.telemetry import get_telemetry_service


class GetTelemetryTool:
    """Tool to get MCP server telemetry and usage statistics."""
    
    def __init__(self):
        """Initialize the tool with the telemetry service."""
        self.telemetry = get_telemetry_service()
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the telemetry retrieval tool.
        
        Args:
            arguments: Tool arguments containing:
                - include_details: bool (optional, default: True) - Include detailed stats
                - limit: int (optional, default: 10) - Max items per category
        
        Returns:
            Dictionary with telemetry data
        """
        try:
            include_details = arguments.get("include_details", True)
            limit = arguments.get("limit", 10)
            
            # Validate limit
            if not isinstance(limit, int) or limit < 1 or limit > 50:
                return {
                    "success": False,
                    "error": "limit must be an integer between 1 and 50",
                }
            
            # Get overall statistics
            overall_stats = self.telemetry.get_overall_stats()
            
            result = {
                "success": True,
                "overall_stats": overall_stats,
            }
            
            # Add detailed statistics if requested
            if include_details:
                endpoint_stats = self.telemetry.get_endpoint_stats()
                
                # Filter for MCP endpoints
                mcp_endpoints = [
                    stat for stat in endpoint_stats 
                    if stat.get("path", "").startswith("mcp:")
                ]
                
                # Get feature usage (MCP tools)
                feature_usage = self.telemetry.get_feature_usage()
                mcp_tools = [
                    feat for feat in feature_usage
                    if feat.get("feature_name", "").startswith("mcp_tool:")
                ]
                
                result["mcp_endpoints"] = mcp_endpoints[:limit]
                result["mcp_tools_usage"] = [
                    {
                        "tool_name": feat["feature_name"].replace("mcp_tool:", ""),
                        "usage_count": feat["usage_count"],
                        "last_used": feat["last_used"]
                    }
                    for feat in mcp_tools[:limit]
                ]
                
                # Add general stats
                result["all_endpoints"] = endpoint_stats[:limit]
                result["provider_adoption"] = self.telemetry.get_provider_adoption()[:limit]
                result["top_features"] = feature_usage[:limit]
                result["client_locations"] = self.telemetry.get_client_locations(limit=limit)
                result["top_browsers"] = self.telemetry.get_browser_stats(limit=limit)
            
            return result
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to retrieve telemetry: {str(e)}",
                "error_type": type(e).__name__,
            }
