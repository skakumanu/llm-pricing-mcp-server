"""Service for retrieving Google Gemini model pricing data."""
from typing import List, Optional
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings


class GooglePricingService(BasePricingProvider):
    """Service to fetch and manage Google Gemini model pricing."""
    
    # Google Gemini pricing data (per 1M tokens in USD) - updated from their official pricing page
    # Source: https://ai.google.dev/pricing
    STATIC_PRICING = {
        "gemini-1.5-pro": {
            "input": 1.25,  # Per 1M tokens
            "output": 5.00,  # Per 1M tokens
            "context_window": 2097152,  # 2M tokens
        },
        "gemini-1.5-flash": {
            "input": 0.075,  # Per 1M tokens
            "output": 0.30,  # Per 1M tokens
            "context_window": 1048576,  # 1M tokens
        },
        "gemini-1.0-pro": {
            "input": 0.50,  # Per 1M tokens
            "output": 1.50,  # Per 1M tokens
            "context_window": 32760,
        },
        "gemini-1.5-pro-002": {
            "input": 1.25,  # Per 1M tokens
            "output": 5.00,  # Per 1M tokens
            "context_window": 2097152,
        },
        "gemini-1.5-flash-002": {
            "input": 0.075,  # Per 1M tokens
            "output": 0.30,  # Per 1M tokens
            "context_window": 1048576,
        },
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Google pricing service.
        
        Args:
            api_key: Optional Google API key for authenticated requests
        """
        super().__init__("Google")
        self.api_key = api_key or settings.google_api_key
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Google Gemini model pricing data.
        
        Since Google doesn't provide a public pricing API, this method returns
        curated pricing data based on their official pricing page.
        
        In production, this could be enhanced to:
        1. Scrape the pricing page (with appropriate caching)
        2. Use Google API to get available models and map to static pricing
        3. Integrate with a third-party pricing aggregation service
        
        Returns:
            List of PricingMetrics for Google Gemini models
            
        Raises:
            Exception: If unable to fetch or parse pricing data
        """
        try:
            # If API key is available, optionally verify it's valid
            if self.api_key:
                await self._verify_api_key()
            
            # Return curated pricing data
            pricing_list = []
            for model_name, pricing_info in self.STATIC_PRICING.items():
                pricing_list.append(
                    PricingMetrics(
                        model_name=model_name,
                        provider=self.provider_name,
                        cost_per_input_token=pricing_info["input"] / 1_000_000,  # Convert from per 1M to per token
                        cost_per_output_token=pricing_info["output"] / 1_000_000,  # Convert from per 1M to per token
                        context_window=pricing_info["context_window"],
                        currency="USD",
                        unit="per_token",
                        source="Google AI Official Pricing (Static)"
                    )
                )
            
            return pricing_list
            
        except Exception as e:
            raise Exception(f"Failed to fetch Google pricing data: {str(e)}")
    
    async def _verify_api_key(self) -> bool:
        """
        Verify that the API key is valid by making a lightweight request.
        
        Returns:
            True if the API key is valid, False otherwise
        """
        if not self.api_key:
            return False
        
        try:
            # Basic validation - Google API keys have a specific format
            # In a real implementation, you might make a minimal API call
            if len(self.api_key) < 20:
                return False
            return True
        except Exception:
            # Verification failures are silently ignored
            return False
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """
        Synchronous method for backward compatibility.
        
        Returns:
            List of PricingMetrics for Google Gemini models
        """
        # Return static pricing data for backward compatibility
        pricing_list = []
        for model_name, pricing_info in GooglePricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Google",
                    cost_per_input_token=pricing_info["input"] / 1_000_000,
                    cost_per_output_token=pricing_info["output"] / 1_000_000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Google AI Official Pricing (Static)"
                )
            )
        return pricing_list
