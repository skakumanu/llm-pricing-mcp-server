"""Service for aggregating pricing data from multiple providers."""
from typing import List
from src.models.pricing import PricingMetrics
from src.services.openai_pricing import OpenAIPricingService
from src.services.anthropic_pricing import AnthropicPricingService


class PricingAggregatorService:
    """Service to aggregate pricing data from multiple LLM providers."""
    
    def __init__(self):
        """Initialize the aggregator service."""
        self.openai_service = OpenAIPricingService()
        self.anthropic_service = AnthropicPricingService()
    
    def get_all_pricing(self) -> List[PricingMetrics]:
        """
        Aggregate pricing data from all providers.
        
        Returns:
            Combined list of PricingMetrics from all providers
        """
        all_pricing = []
        
        # Fetch OpenAI pricing
        all_pricing.extend(self.openai_service.get_pricing_data())
        
        # Fetch Anthropic pricing
        all_pricing.extend(self.anthropic_service.get_pricing_data())
        
        return all_pricing
    
    def get_pricing_by_provider(self, provider: str) -> List[PricingMetrics]:
        """
        Get pricing data for a specific provider.
        
        Args:
            provider: Provider name (case-insensitive)
            
        Returns:
            List of PricingMetrics for the specified provider
        """
        provider_lower = provider.lower()
        
        if provider_lower == "openai":
            return self.openai_service.get_pricing_data()
        elif provider_lower == "anthropic":
            return self.anthropic_service.get_pricing_data()
        else:
            return []
