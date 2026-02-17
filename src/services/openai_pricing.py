"""Service for retrieving OpenAI model pricing data."""
from typing import List, Optional
import httpx
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


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
        
        This method attempts to fetch live data from:
        1. OpenAI API to get available models
        2. OpenAI pricing website for current pricing
        
        Falls back to curated static pricing data if live fetch fails.
        
        Returns:
            List of PricingMetrics for OpenAI models
            
        Raises:
            Exception: If unable to fetch or parse pricing data
        """
        try:
            # Fetch available models from API (live data)
            models_list = None
            if self.api_key:
                models_list = await DataFetcher.fetch_with_cache(
                    cache_key="openai_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=PRICING_SOURCES["OpenAI"].api_endpoint,
                        api_key=self.api_key,
                        require_auth=True
                    ),
                    ttl_seconds=PRICING_SOURCES["OpenAI"].cache_ttl_seconds
                )
            
            # Fetch pricing from website (live data)
            live_pricing_data = await DataFetcher.fetch_with_cache(
                cache_key="openai_pricing_web",
                fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                    url=PRICING_SOURCES["OpenAI"].pricing_url
                ),
                ttl_seconds=PRICING_SOURCES["OpenAI"].cache_ttl_seconds,
                fallback_data=None
            )
            
            # Fetch performance metrics
            performance_data = await self._fetch_performance_metrics()
            
            # Build pricing list
            pricing_list = []
            
            # Use models from API if available, otherwise use static list keys
            models_to_price = models_list if models_list else list(self.STATIC_PRICING.keys())
            
            for model_name in models_to_price:
                # Try live pricing data first, fall back to static pricing
                if live_pricing_data and model_name in live_pricing_data:
                    pricing_info = live_pricing_data[model_name]
                    input_cost = pricing_info.get("input", 0.0)
                    output_cost = pricing_info.get("output", 0.0)
                    source = "OpenAI Official API"
                elif model_name in self.STATIC_PRICING:
                    pricing_info = self.STATIC_PRICING[model_name]
                    input_cost = pricing_info["input"] / 1000
                    output_cost = pricing_info["output"] / 1000
                    source = "OpenAI Official Pricing (Cached)"
                else:
                    continue
                
                # Get performance metrics
                metrics = performance_data.get(model_name, {
                    "throughput": 80.0,
                    "latency_ms": 320.0
                })
                
                # Get metadata from static data if available
                static_info = self.STATIC_PRICING.get(model_name, {})
                
                pricing_list.append(
                    PricingMetrics(
                        model_name=model_name,
                        provider=self.provider_name,
                        cost_per_input_token=input_cost / 1000 if input_cost >= 1 else input_cost,
                        cost_per_output_token=output_cost / 1000 if output_cost >= 1 else output_cost,
                        context_window=static_info.get("context_window", 8192),
                        currency="USD",
                        unit="per_token",
                        source=source,
                        throughput=metrics.get("throughput", 80.0),
                        latency_ms=metrics.get("latency_ms", 320.0),
                        use_cases=static_info.get("use_cases"),
                        strengths=static_info.get("strengths"),
                        best_for=static_info.get("best_for")
                    )
                )
            
            if not pricing_list:
                raise Exception("No pricing data available from live sources or cache")
            
            return pricing_list
            
        except Exception as e:
            logger.error(f"Error fetching OpenAI pricing: {str(e)}, falling back to static data")
            # Fall back to static pricing
            return self._get_static_pricing_data()
    
    async def _fetch_performance_metrics(self) -> dict:
        """Fetch live performance metrics from OpenAI API.
        
        Returns:
            Dict with model names as keys and {throughput, latency_ms} as values
        """
        try:
            perf_source = PERFORMANCE_SOURCES["OpenAI"]
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="openai_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=perf_source.api_endpoint,
                    api_key=self.api_key
                ),
                ttl_seconds=perf_source.cache_ttl_seconds
            )
            
            if health_data and health_data.get("healthy"):
                latency = health_data.get("latency_ms", 320.0)
                return {
                    model: {
                        "latency_ms": latency,
                        "throughput": max(50.0, 320.0 / latency * 80.0)  # Estimate based on latency
                    }
                    for model in self.STATIC_PRICING.keys()
                }
        except Exception as e:
            logger.warning(f"Failed to fetch OpenAI performance metrics: {str(e)}")
        
        # Return default estimated metrics
        return {
            model: {"throughput": 80.0, "latency_ms": 320.0}
            for model in self.STATIC_PRICING.keys()
        }
    
    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get fallback static pricing data when live fetch fails.
        
        Returns:
            List of PricingMetrics with static data
        """
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider=self.provider_name,
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="OpenAI Official Pricing (Fallback - Static)",
                    throughput=80.0,
                    latency_ms=320.0,
                    use_cases=pricing_info.get("use_cases"),
                    strengths=pricing_info.get("strengths"),
                    best_for=pricing_info.get("best_for")
                )
            )
        return pricing_list
    
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
