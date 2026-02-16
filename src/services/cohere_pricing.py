"""Service for retrieving Cohere model pricing data."""
from typing import List, Optional
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings


class CoherePricingService(BasePricingProvider):
    """Service to fetch and manage Cohere model pricing."""
    
    # Cohere pricing data (per 1k tokens in USD)
    # Source: https://cohere.com/pricing
    STATIC_PRICING = {
        "command-r-plus": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 128000,
            "use_cases": ["Enterprise search", "RAG systems", "Long document analysis", "Complex reasoning"],
            "strengths": ["Enterprise-optimized", "Excellent for RAG", "Strong context window"],
            "best_for": "Enterprise applications requiring long-context understanding"
        },
        "command-r": {
            "input": 0.0005,
            "output": 0.0015,
            "context_window": 128000,
            "use_cases": ["Customer support automation", "FAQ systems", "Information retrieval", "Document Q&A"],
            "strengths": ["Cost-effective RAG", "Good retrieval capabilities", "Large context"],
            "best_for": "Mid-tier applications needing retrieval-augmented generation"
        },
        "command": {
            "input": 0.001,
            "output": 0.002,
            "context_window": 4096,
            "use_cases": ["Content generation", "Copywriting", "Product descriptions"],
            "strengths": ["Good for generation", "Affordable", "Reliable"],
            "best_for": "Content creation tasks with reasonable budgets"
        },
        "command-light": {
            "input": 0.0003,
            "output": 0.0006,
            "context_window": 4096,
            "use_cases": ["Lightweight chatbots", "Quick classification", "Simple generation"],
            "strengths": ["Minimal cost", "Fast responses", "Good for simple tasks"],
            "best_for": "Budget-conscious applications with simpler requirements"
        },
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Cohere pricing service.
        
        Args:
            api_key: Optional Cohere API key for authenticated requests
        """
        super().__init__("Cohere")
        self.api_key = api_key or getattr(settings, 'cohere_api_key', None)
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Cohere model pricing data.
        
        Since Cohere doesn't provide a public pricing API, this method returns
        curated pricing data based on their official pricing page.
        
        Returns:
            List of PricingMetrics for Cohere models
            
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
                        source="Cohere Official Pricing (Static)",
                        throughput=100.0,  # Estimated tokens per second
                        latency_ms=300.0,  # Estimated latency in milliseconds
                        use_cases=pricing_info.get("use_cases"),
                        strengths=pricing_info.get("strengths"),
                        best_for=pricing_info.get("best_for")
                    )
                )
            
            return pricing_list
            
        except Exception as e:
            raise Exception(f"Failed to fetch Cohere pricing data: {str(e)}")
    
    async def _verify_api_key(self) -> bool:
        """
        Verify that the API key is valid.
        
        Returns:
            True if the API key is valid, False otherwise
        """
        # Placeholder for future API key verification
        # In production, this would make a lightweight API call
        return True
