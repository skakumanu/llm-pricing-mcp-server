"""Service for retrieving OpenAI model pricing data."""
from typing import List
from src.models.pricing import PricingMetrics


class OpenAIPricingService:
    """Service to fetch and manage OpenAI model pricing."""
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """
        Retrieve OpenAI model pricing data.
        
        Note: This returns static pricing data. In production, this would
        fetch real-time data from OpenAI's pricing API.
        
        Returns:
            List of PricingMetrics for OpenAI models
        """
        # Static pricing data based on OpenAI's current pricing
        # In a production environment, this would fetch from an API
        return [
            PricingMetrics(
                model_name="gpt-4",
                provider="OpenAI",
                cost_per_input_token=0.00003,  # $0.03 per 1K tokens
                cost_per_output_token=0.00006,  # $0.06 per 1K tokens
                throughput=20.0,
                latency_ms=2500.0,
                context_window=8192
            ),
            PricingMetrics(
                model_name="gpt-4-turbo",
                provider="OpenAI",
                cost_per_input_token=0.00001,  # $0.01 per 1K tokens
                cost_per_output_token=0.00003,  # $0.03 per 1K tokens
                throughput=40.0,
                latency_ms=1500.0,
                context_window=128000
            ),
            PricingMetrics(
                model_name="gpt-3.5-turbo",
                provider="OpenAI",
                cost_per_input_token=0.0000005,  # $0.0005 per 1K tokens
                cost_per_output_token=0.0000015,  # $0.0015 per 1K tokens
                throughput=60.0,
                latency_ms=800.0,
                context_window=16385
            ),
        ]
