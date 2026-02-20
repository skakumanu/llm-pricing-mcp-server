"""MCP Tool: Get All Pricing Data"""
import asyncio
import json
from typing import Any, Dict

from src.services.pricing_aggregator import PricingAggregatorService


class GetAllPricingTool:
    """Tool to fetch pricing data from all providers."""
    
    def __init__(self):
        """Initialize the tool with the pricing service."""
        self.service = PricingAggregatorService()
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the get all pricing tool.
        
        Args:
            arguments: Tool arguments (typically empty for this tool)
            
        Returns:
            Dictionary with pricing data and provider status
        """
        try:
            # Fetch pricing data asynchronously
            pricing_data, provider_statuses = await self.service.get_all_pricing_async()
            
            # Convert to JSON-serializable format
            return {
                "success": True,
                "total_models": len(pricing_data),
                "models": [self._serialize_pricing(p) for p in pricing_data],
                "providers": [self._serialize_status(s) for s in provider_statuses],
                "timestamp": str(pricing_data[0].last_updated) if pricing_data else None,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }
    
    @staticmethod
    def _serialize_pricing(pricing) -> Dict[str, Any]:
        """Convert PricingMetrics to JSON-serializable dict."""
        return {
            "model_name": pricing.model_name,
            "provider": pricing.provider,
            "cost_per_input_token": pricing.cost_per_input_token,
            "cost_per_output_token": pricing.cost_per_output_token,
            "throughput": pricing.throughput,
            "latency_ms": pricing.latency_ms,
            "context_window": pricing.context_window,
            "currency": pricing.currency,
            "unit": pricing.unit,
            "use_cases": pricing.use_cases,
            "strengths": pricing.strengths,
            "best_for": pricing.best_for,
            "cost_at_10k_tokens": {
                "input_cost": pricing.cost_at_10k_tokens.input_cost,
                "output_cost": pricing.cost_at_10k_tokens.output_cost,
                "total_cost": pricing.cost_at_10k_tokens.total_cost,
            },
            "cost_at_100k_tokens": {
                "input_cost": pricing.cost_at_100k_tokens.input_cost,
                "output_cost": pricing.cost_at_100k_tokens.output_cost,
                "total_cost": pricing.cost_at_100k_tokens.total_cost,
            },
            "cost_at_1m_tokens": {
                "input_cost": pricing.cost_at_1m_tokens.input_cost,
                "output_cost": pricing.cost_at_1m_tokens.output_cost,
                "total_cost": pricing.cost_at_1m_tokens.total_cost,
            },
            "estimated_time_1m_tokens": pricing.estimated_time_1m_tokens,
        }
    
    @staticmethod
    def _serialize_status(status) -> Dict[str, Any]:
        """Convert ProviderStatusInfo to JSON-serializable dict."""
        return {
            "provider_name": status.provider_name,
            "is_available": status.is_available,
            "error_message": status.error_message,
            "models_count": status.models_count,
        }
