"""Service for retrieving OpenAI model pricing data."""
from typing import List, Optional
import httpx
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings


class OpenAIPricingService(BasePricingProvider):
    """Service to fetch and manage OpenAI model pricing."""
    
    # OpenAI pricing data (per 1k tokens in USD) - updated from their official pricing page
    # Source: https://openai.com/api/pricing/
    STATIC_PRICING = {
        "gpt-4": {
            "input": 0.03,
            "output": 0.06,
            "context_window": 8192,
            "use_cases": ["Complex reasoning", "Code generation", "Creative writing", "Data analysis"],
            "strengths": ["High accuracy", "Strong reasoning", "Reliable outputs"],
            "best_for": "High-stakes tasks requiring maximum accuracy and reasoning"
        },
        "gpt-4-turbo": {
            "input": 0.01,
            "output": 0.03,
            "context_window": 128000,
            "use_cases": ["Long document analysis", "Multi-turn conversations", "Large codebase understanding"],
            "strengths": ["Massive context window", "Cost-effective than GPT-4", "Fast performance"],
            "best_for": "Processing large documents and maintaining long conversations"
        },
        "gpt-4-turbo-preview": {
            "input": 0.01,
            "output": 0.03,
            "context_window": 128000,
            "use_cases": ["Testing new features", "Long document processing", "Complex multi-step tasks"],
            "strengths": ["Latest capabilities", "Large context", "Good value"],
            "best_for": "Beta testing new GPT-4 features with large context needs"
        },
        "gpt-3.5-turbo": {
            "input": 0.0005,
            "output": 0.0015,
            "context_window": 16385,
            "use_cases": ["Chatbots", "Simple Q&A", "Content generation", "Data extraction"],
            "strengths": ["Very low cost", "Fast responses", "Good for simple tasks"],
            "best_for": "High-volume applications where cost efficiency is critical"
        },
        "gpt-3.5-turbo-0125": {
            "input": 0.0005,
            "output": 0.0015,
            "context_window": 16385,
            "use_cases": ["Customer support", "Basic automation", "Simple text processing"],
            "strengths": ["Latest 3.5 version", "Cost-effective", "Reliable"],
            "best_for": "Cost-sensitive applications with moderate complexity"
        },
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the OpenAI pricing service.
        
        Args:
            api_key: Optional OpenAI API key for authenticated requests
        """
        super().__init__("OpenAI")
        self.api_key = api_key or settings.openai_api_key
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch OpenAI model pricing data.
        
        Since OpenAI doesn't provide a public pricing API, this method returns
        curated pricing data based on their official pricing page.
        
        In production, this could be enhanced to:
        1. Scrape the pricing page (with appropriate caching)
        2. Use OpenAI API to get available models and map to static pricing
        3. Integrate with a third-party pricing aggregation service
        
        Returns:
            List of PricingMetrics for OpenAI models
            
        Raises:
            Exception: If unable to fetch or parse pricing data
        """
        try:
            # If API key is available, optionally verify it's valid by making a test request
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
                        source="OpenAI Official Pricing (Static)",
                        use_cases=pricing_info.get("use_cases"),
                        strengths=pricing_info.get("strengths"),
                        best_for=pricing_info.get("best_for")
                    )
                )
            
            return pricing_list
            
        except Exception as e:
            raise Exception(f"Failed to fetch OpenAI pricing data: {str(e)}")
    
    async def _verify_api_key(self) -> bool:
        """
        Verify that the API key is valid by making a lightweight request.
        
        Returns:
            True if the API key is valid
            
        Raises:
            Exception: If the API key is invalid or the request fails
        """
        if not self.api_key:
            return False
        
        # Make a lightweight request to verify the API key
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={
                        "Authorization": f"Bearer {self.api_key}"
                    }
                )
                response.raise_for_status()
                return True
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    raise Exception("Invalid OpenAI API key")
                raise Exception(f"OpenAI API error: {e.response.status_code}")
            except Exception:
                # Verification failures are silently ignored and we continue with static data
                return False
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """
        Synchronous method for backward compatibility.
        
        Returns:
            List of PricingMetrics for OpenAI models
        """
        # Return static pricing data for backward compatibility
        pricing_list = []
        for model_name, pricing_info in OpenAIPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="OpenAI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="OpenAI Official Pricing (Static)"
                )
            )
        return pricing_list
