"""Service for retrieving Anthropic model pricing data."""
from typing import List
from src.models.pricing import PricingMetrics


class AnthropicPricingService:
    """Service to fetch and manage Anthropic model pricing."""
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """
        Retrieve Anthropic model pricing data.
        
        Note: This returns static pricing data. In production, this would
        fetch real-time data from Anthropic's pricing API.
        
        Returns:
            List of PricingMetrics for Anthropic models
        """
        # Static pricing data based on Anthropic's current pricing
        # In a production environment, this would fetch from an API
        return [
            PricingMetrics(
                model_name="claude-3-opus",
                provider="Anthropic",
                cost_per_input_token=0.000015,  # $0.015 per 1K tokens
                cost_per_output_token=0.000075,  # $0.075 per 1K tokens
                throughput=25.0,
                latency_ms=2000.0,
                context_window=200000
            ),
            PricingMetrics(
                model_name="claude-3-sonnet",
                provider="Anthropic",
                cost_per_input_token=0.000003,  # $0.003 per 1K tokens
                cost_per_output_token=0.000015,  # $0.015 per 1K tokens
                throughput=35.0,
                latency_ms=1500.0,
                context_window=200000
            ),
            PricingMetrics(
                model_name="claude-3-haiku",
                provider="Anthropic",
                cost_per_input_token=0.00000025,  # $0.00025 per 1K tokens
                cost_per_output_token=0.00000125,  # $0.00125 per 1K tokens
                throughput=50.0,
                latency_ms=900.0,
                context_window=200000
            ),
        ]
