"""MCP Tool: Estimate Cost for a Single Model"""
from typing import Any, Dict, Optional

from src.services.pricing_aggregator import PricingAggregatorService


class EstimateCostTool:
    """Tool to estimate cost for a single LLM model."""
    
    def __init__(self):
        """Initialize the tool with the pricing service."""
        self.service = PricingAggregatorService()
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the cost estimation tool.
        
        Args:
            arguments: Tool arguments containing:
                - model_name: str (required)
                - input_tokens: int (required, >= 0)
                - output_tokens: int (required, >= 0)
        
        Returns:
            Dictionary with cost estimation results
        """
        try:
            model_name = arguments.get("model_name")
            input_tokens = arguments.get("input_tokens")
            output_tokens = arguments.get("output_tokens")
            
            # Validate arguments
            if not model_name:
                return {
                    "success": False,
                    "error": "model_name is required",
                }
            
            if input_tokens is None or output_tokens is None:
                return {
                    "success": False,
                    "error": "input_tokens and output_tokens are required",
                }
            
            if input_tokens < 0 or output_tokens < 0:
                return {
                    "success": False,
                    "error": "input_tokens and output_tokens must be non-negative",
                }
            
            # Find pricing for the model
            pricing = await self.service.find_model_pricing(model_name)
            
            if not pricing:
                return {
                    "success": False,
                    "error": f"Model '{model_name}' not found",
                    "available_models_hint": "Use get_all_pricing tool to see available models",
                }
            
            # Calculate costs
            input_cost = (pricing.cost_per_input_token / 1000) * input_tokens
            output_cost = (pricing.cost_per_output_token / 1000) * output_tokens
            total_cost = input_cost + output_cost
            
            return {
                "success": True,
                "model_name": pricing.model_name,
                "provider": pricing.provider,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "input_cost": round(input_cost, 6),
                "output_cost": round(output_cost, 6),
                "total_cost": round(total_cost, 6),
                "currency": pricing.currency,
                "breakdown": {
                    "cost_per_input_token": pricing.cost_per_input_token,
                    "cost_per_output_token": pricing.cost_per_output_token,
                },
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }
