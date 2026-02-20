"""MCP Tool: Get Performance Metrics"""
from typing import Any, Dict

from src.services.pricing_aggregator import PricingAggregatorService


class GetPerformanceMetricsTool:
    """Tool to get performance metrics (throughput, latency, context window)."""
    
    def __init__(self):
        """Initialize the tool with the pricing service."""
        self.service = PricingAggregatorService()
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the get performance metrics tool.
        
        Args:
            arguments: Tool arguments containing:
                - provider: str (optional) - specific provider to filter by
                - include_cost: bool (optional, default=True) - include cost metrics
        
        Returns:
            Dictionary with performance metrics for models
        """
        try:
            provider_filter = arguments.get("provider")
            include_cost = arguments.get("include_cost", True)
            
            # Fetch pricing data
            if provider_filter:
                pricing_data, provider_statuses = await self.service.get_pricing_by_provider_async(provider_filter)
            else:
                pricing_data, provider_statuses = await self.service.get_all_pricing_async()
            
            # Build performance metrics
            models = []
            best_throughput = None
            best_throughput_value = -1
            lowest_latency = None
            lowest_latency_value = float('inf')
            largest_context = None
            largest_context_value = -1
            best_value = None
            best_value_score = -1
            
            for pricing in pricing_data:
                # Calculate performance score (throughput / cost)
                performance_score = None
                if pricing.throughput and pricing.cost_per_input_token > 0:
                    cost_avg = (pricing.cost_per_input_token + pricing.cost_per_output_token) / 2
                    performance_score = round(pricing.throughput / (cost_avg * 1000), 4)
                
                # Calculate value score (context_window / cost)
                value_score = None
                if pricing.context_window and pricing.cost_per_input_token > 0:
                    cost_avg = (pricing.cost_per_input_token + pricing.cost_per_output_token) / 2
                    value_score = round(pricing.context_window / (cost_avg * 1000), 2)
                
                model_data = {
                    "model_name": pricing.model_name,
                    "provider": pricing.provider,
                    "throughput": pricing.throughput,
                    "latency_ms": pricing.latency_ms,
                    "context_window": pricing.context_window,
                    "performance_score": performance_score,
                    "value_score": value_score,
                }
                
                if include_cost:
                    model_data.update({
                        "cost_per_input_token": pricing.cost_per_input_token,
                        "cost_per_output_token": pricing.cost_per_output_token,
                    })
                
                models.append(model_data)
                
                # Track best metrics
                if pricing.throughput and pricing.throughput > best_throughput_value:
                    best_throughput = pricing.model_name
                    best_throughput_value = pricing.throughput
                
                if pricing.latency_ms and pricing.latency_ms < lowest_latency_value:
                    lowest_latency = pricing.model_name
                    lowest_latency_value = pricing.latency_ms
                
                if pricing.context_window and pricing.context_window > largest_context_value:
                    largest_context = pricing.model_name
                    largest_context_value = pricing.context_window
                
                if value_score and value_score > best_value_score:
                    best_value = pricing.model_name
                    best_value_score = value_score
            
            return {
                "success": True,
                "total_models": len(models),
                "models": models,
                "best_throughput": best_throughput,
                "lowest_latency": lowest_latency,
                "largest_context": largest_context,
                "best_value": best_value,
                "provider_status": [
                    {
                        "provider_name": s.provider_name,
                        "is_available": s.is_available,
                        "models_count": s.models_count,
                    }
                    for s in provider_statuses
                ],
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }
