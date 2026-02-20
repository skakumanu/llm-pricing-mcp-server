"""MCP Tool: Compare Costs for Multiple Models"""
from typing import Any, Dict, List

from src.services.pricing_aggregator import PricingAggregatorService


class CompareCostsTool:
    """Tool to compare costs across multiple LLM models."""
    
    def __init__(self):
        """Initialize the tool with the pricing service."""
        self.service = PricingAggregatorService()
    
    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the cost comparison tool.
        
        Args:
            arguments: Tool arguments containing:
                - model_names: List[str] (required)
                - input_tokens: int (required, >= 0)
                - output_tokens: int (required, >= 0)
        
        Returns:
            Dictionary with cost comparison results
        """
        try:
            model_names = arguments.get("model_names")
            input_tokens = arguments.get("input_tokens")
            output_tokens = arguments.get("output_tokens")
            
            # Validate arguments
            if not model_names or not isinstance(model_names, list):
                return {
                    "success": False,
                    "error": "model_names must be a non-empty list",
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
            
            # Fetch all pricing to find models
            all_pricing, _ = await self.service.get_all_pricing_async()
            
            # Create a map for easy lookup
            pricing_map = {
                p.model_name.lower(): p for p in all_pricing
            }
            
            # Estimate costs for each model
            comparisons = []
            costs = []
            
            for model_name in model_names:
                pricing = pricing_map.get(model_name.lower())
                
                if not pricing:
                    comparisons.append({
                        "model_name": model_name,
                        "is_available": False,
                        "error": f"Model '{model_name}' not found",
                    })
                    continue
                
                # Calculate costs
                input_cost = (pricing.cost_per_input_token / 1000) * input_tokens
                output_cost = (pricing.cost_per_output_token / 1000) * output_tokens
                total_cost = input_cost + output_cost
                costs.append((model_name, total_cost, input_cost, output_cost))
                
                comparisons.append({
                    "model_name": pricing.model_name,
                    "provider": pricing.provider,
                    "input_cost": round(input_cost, 6),
                    "output_cost": round(output_cost, 6),
                    "total_cost": round(total_cost, 6),
                    "cost_per_1m_tokens": round((total_cost / (input_tokens + output_tokens)) * 1_000_000, 2) if (input_tokens + output_tokens) > 0 else 0,
                    "is_available": True,
                })
            
            # Find cheapest and most expensive
            cheapest = None
            most_expensive = None
            
            if costs:
                costs_sorted = sorted(costs, key=lambda x: x[1])
                cheapest = costs_sorted[0][0]
                most_expensive = costs_sorted[-1][0]
            
            # Calculate cost range
            cost_range = None
            if costs:
                costs_values = [c[1] for c in costs]
                cost_range = {
                    "min": round(min(costs_values), 6),
                    "max": round(max(costs_values), 6),
                    "difference": round(max(costs_values) - min(costs_values), 6),
                }
            
            return {
                "success": True,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "models": comparisons,
                "cheapest_model": cheapest,
                "most_expensive_model": most_expensive,
                "cost_range": cost_range,
                "currency": "USD",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }
