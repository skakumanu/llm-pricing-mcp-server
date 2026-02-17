"""Service for retrieving Anthropic model pricing data."""
from typing import List, Optional
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings


class AnthropicPricingService(BasePricingProvider):
    """Service to fetch and manage Anthropic model pricing."""
    
    # Anthropic pricing data (per 1k tokens in USD) - updated from their official pricing page
    # Source: https://www.anthropic.com/api
    STATIC_PRICING = {
        "claude-3-opus-20240229": {
            "input": 0.015,
            "output": 0.075,
            "context_window": 200000,
            "use_cases": ["Research analysis", "Complex problem solving", "Advanced coding", "Strategic planning"],
            "strengths": ["Superior intelligence", "Nuanced understanding", "Excellent at analysis"],
            "best_for": "Most demanding tasks requiring top-tier intelligence"
        },
        "claude-3-sonnet-20240229": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 200000,
            "use_cases": ["Content creation", "Data processing", "Code review", "Research assistance"],
            "strengths": ["Balanced performance/cost", "Large context", "Versatile"],
            "best_for": "Balanced workloads needing intelligence and efficiency"
        },
        "claude-3-haiku-20240307": {
            "input": 0.00025,
            "output": 0.00125,
            "context_window": 200000,
            "use_cases": ["Real-time chat", "Document processing", "Quick analysis", "Moderation"],
            "strengths": ["Fastest Claude", "Ultra-low cost", "Huge context"],
            "best_for": "High-speed applications requiring instant responses"
        },
        "claude-2.1": {
            "input": 0.008,
            "output": 0.024,
            "context_window": 200000,
            "use_cases": ["Long document Q&A", "Summarization", "General chat"],
            "strengths": ["Proven reliability", "Large context", "Stable"],
            "best_for": "Production systems requiring stability"
        },
        "claude-2.0": {
            "input": 0.008,
            "output": 0.024,
            "context_window": 100000,
            "use_cases": ["Legacy systems", "General assistance", "Text generation"],
            "strengths": ["Mature model", "Reliable", "Well-tested"],
            "best_for": "Maintaining existing Claude 2 integrations"
        },
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Anthropic pricing service.
        
        Args:
            api_key: Optional Anthropic API key for authenticated requests
        """
        super().__init__("Anthropic")
        self.api_key = api_key or settings.anthropic_api_key
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Anthropic model pricing data.
        
        Since Anthropic doesn't provide a public pricing API, this method returns
        curated pricing data based on their official pricing page.
        
        In production, this could be enhanced to:
        1. Scrape the pricing page (with appropriate caching)
        2. Use Anthropic API to verify available models
        3. Integrate with a third-party pricing aggregation service
        
        Returns:
            List of PricingMetrics for Anthropic models
            
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
                        cost_per_input_token=pricing_info["input"] / 1000,  # Convert to per token
                        cost_per_output_token=pricing_info["output"] / 1000,  # Convert to per token
                        context_window=pricing_info["context_window"],
                        currency="USD",
                        unit="per_token",
                        source="Anthropic Official Pricing (Static)",
                        throughput=75.0,  # Estimated tokens per second
                        latency_ms=350.0,  # Estimated latency in milliseconds
                        use_cases=pricing_info.get("use_cases"),
                        strengths=pricing_info.get("strengths"),
                        best_for=pricing_info.get("best_for")
                    )
                )
            
            return pricing_list
            
        except Exception as e:
            raise Exception(f"Failed to fetch Anthropic pricing data: {str(e)}")
    
    async def _verify_api_key(self) -> bool:
        """
        Verify that the API key is valid by making a lightweight request.
        
        Returns:
            True if the API key is valid, False otherwise
        """
        if not self.api_key:
            return False
        
        try:
            # Anthropic doesn't have a models endpoint, so we'll just check if the key format is valid
            # In a real implementation, you might make a minimal API call
            if not self.api_key.startswith("sk-ant-"):
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
            List of PricingMetrics for Anthropic models
        """
        # Return static pricing data for backward compatibility
        pricing_list = []
        for model_name, pricing_info in AnthropicPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Anthropic",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Anthropic Official Pricing (Static)"
                )
            )
        return pricing_list
