"""Service for retrieving Mistral AI model pricing data."""
from typing import List, Optional
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings


class MistralPricingService(BasePricingProvider):
    """Service to fetch and manage Mistral AI model pricing."""
    
    # Mistral AI pricing data (per 1k tokens in USD)
    # Source: https://mistral.ai/technology/#pricing
    STATIC_PRICING = {
        "mistral-large-latest": {
            "input": 0.004,
            "output": 0.012,
            "context_window": 32000,
            "use_cases": ["Complex reasoning", "Advanced analytics", "Code generation", "Multi-step planning"],
            "strengths": ["Excellent reasoning", "Strong code skills", "Well-balanced"],
            "best_for": "Complex tasks requiring strong reasoning and code understanding"
        },
        "mistral-medium-latest": {
            "input": 0.0027,
            "output": 0.0081,
            "context_window": 32000,
            "use_cases": ["General chat", "Content creation", "Code assistance", "Business logic"],
            "strengths": ["Good balance", "Versatile", "Cost-effective"],
            "best_for": "All-purpose assistant for varied business needs"
        },
        "mistral-small-latest": {
            "input": 0.001,
            "output": 0.003,
            "context_window": 32000,
            "use_cases": ["Customer support", "FAQ automation", "Text classification", "Simple tasks"],
            "strengths": ["Lightweight", "Affordable", "Fast responses"],
            "best_for": "High-volume applications requiring fast, affordable responses"
        },
        "mistral-tiny": {
            "input": 0.00025,
            "output": 0.00025,
            "context_window": 32000,
            "use_cases": ["Real-time processing", "Extreme cost optimization", "Simple classification"],
            "strengths": ["Minimal cost", "Instant responses", "Edge deployment"],
            "best_for": "Extreme cost optimization and ultra-fast responses"
        },
        "open-mistral-7b": {
            "input": 0.00025,
            "output": 0.00025,
            "context_window": 32000,
            "use_cases": ["Self-hosted deployment", "Privacy-focused applications", "Custom fine-tuning"],
            "strengths": ["Open source", "Self-hostable", "Privacy-friendly"],
            "best_for": "Organizations needing self-hosted or locally deployable models"
        },
        "open-mixtral-8x7b": {
            "input": 0.0007,
            "output": 0.0007,
            "context_window": 32000,
            "use_cases": ["Advanced open-source use", "Custom deployment", "High-performance inference"],
            "strengths": ["Mixture of experts", "Self-hostable", "Good performance"],
            "best_for": "Advanced use cases with self-hosted open-source requirements"
        },
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Mistral AI pricing service.
        
        Args:
            api_key: Optional Mistral API key for authenticated requests
        """
        super().__init__("Mistral AI")
        self.api_key = api_key or getattr(settings, 'mistral_api_key', None)
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Mistral AI model pricing data.
        
        Since Mistral doesn't provide a public pricing API, this method returns
        curated pricing data based on their official pricing page.
        
        Returns:
            List of PricingMetrics for Mistral models
            
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
                        source="Mistral AI Official Pricing (Static)",
                        throughput=90.0,  # Estimated tokens per second
                        latency_ms=280.0,  # Estimated latency in milliseconds
                        use_cases=pricing_info.get("use_cases"),
                        strengths=pricing_info.get("strengths"),
                        best_for=pricing_info.get("best_for")
                    )
                )
            
            return pricing_list
            
        except Exception as e:
            raise Exception(f"Failed to fetch Mistral AI pricing data: {str(e)}")
    
    async def _verify_api_key(self) -> bool:
        """
        Verify that the API key is valid.
        
        Returns:
            True if the API key is valid, False otherwise
        """
        # Placeholder for future API key verification
        # In production, this would make a lightweight API call
        return True
