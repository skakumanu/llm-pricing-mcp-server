"""MCP Tool: Get Use Cases for Models"""
from typing import Any, Dict

from src.services.pricing_aggregator import PricingAggregatorService


class GetUseCasesTool:
    """Tool to get recommended use cases for LLM models."""
    
    def __init__(self):
        """Initialize the tool with the pricing service."""
        self.service = PricingAggregatorService()
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the get use cases tool.
        
        Args:
            arguments: Tool arguments containing:
                - provider: str (optional) - specific provider to filter by
        
        Returns:
            Dictionary with use case information for models
        """
        try:
            provider_filter = arguments.get("provider")
            
            # Fetch pricing data
            if provider_filter:
                pricing_data, _ = await self.service.get_pricing_by_provider_async(provider_filter)
            else:
                pricing_data, _ = await self.service.get_all_pricing_async()
            
            # Build use case information
            models = []
            all_providers = set()
            
            for pricing in pricing_data:
                all_providers.add(pricing.provider)
                
                # Determine cost tier
                input_cost = pricing.cost_per_input_token
                if input_cost < 0.001:
                    cost_tier = "low"
                elif input_cost < 0.01:
                    cost_tier = "medium"
                else:
                    cost_tier = "high"
                
                model_data = {
                    "model_name": pricing.model_name,
                    "provider": pricing.provider,
                    "best_for": pricing.best_for or "General purpose LLM",
                    "use_cases": pricing.use_cases or [],
                    "strengths": pricing.strengths or [],
                    "context_window": pricing.context_window,
                    "cost_tier": cost_tier,
                    "cost_per_input_token": pricing.cost_per_input_token,
                }
                
                models.append(model_data)
            
            return {
                "success": True,
                "total_models": len(models),
                "models": models,
                "providers": sorted(list(all_providers)),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }
