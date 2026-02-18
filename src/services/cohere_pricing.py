"""Service for retrieving Cohere model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


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
        
        This method attempts to fetch live data from:
        1. Cohere API to get available models
        2. Cohere pricing website for current pricing
        
        Falls back to curated static pricing data if live fetch fails.
        
        Returns:
            List of PricingMetrics for Cohere models
            
        Raises:
            Exception: If unable to fetch or parse pricing data
        """
        try:
            # Fetch available models from API (live data)
            models_list = None
            if self.api_key:
                models_list = await DataFetcher.fetch_with_cache(
                    cache_key="cohere_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=PRICING_SOURCES["Cohere"].api_endpoint,
                        api_key=self.api_key,
                        require_auth=True
                    ),
                    ttl_seconds=PRICING_SOURCES["Cohere"].cache_ttl_seconds
                )
            
            # Fetch pricing from website (live data)
            live_pricing_data = await DataFetcher.fetch_with_cache(
                cache_key="cohere_pricing_web",
                fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                    url=PRICING_SOURCES["Cohere"].pricing_url
                ),
                ttl_seconds=PRICING_SOURCES["Cohere"].cache_ttl_seconds,
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
                    source = "Cohere Official API"
                elif model_name in self.STATIC_PRICING:
                    pricing_info = self.STATIC_PRICING[model_name]
                    input_cost = pricing_info["input"] / 1000
                    output_cost = pricing_info["output"] / 1000
                    source = "Cohere Official Pricing (Cached)"
                else:
                    continue
                
                # Get performance metrics
                metrics = performance_data.get(model_name, {
                    "throughput": 100.0,
                    "latency_ms": 300.0
                })
                
                # Get metadata from static data if available
                static_info = self.STATIC_PRICING.get(model_name, {})
                
                pricing_list.append(
                    PricingMetrics(
                        model_name=model_name,
                        provider=self.provider_name,
                        cost_per_input_token=input_cost / 1000 if input_cost >= 1 else input_cost,
                        cost_per_output_token=output_cost / 1000 if output_cost >= 1 else output_cost,
                        context_window=static_info.get("context_window", 4096),
                        currency="USD",
                        unit="per_token",
                        source=source,
                        throughput=metrics.get("throughput", 100.0),
                        latency_ms=metrics.get("latency_ms", 300.0),
                        use_cases=static_info.get("use_cases"),
                        strengths=static_info.get("strengths"),
                        best_for=static_info.get("best_for")
                    )
                )
            
            if not pricing_list:
                raise Exception("No pricing data available from live sources or cache")
            
            return pricing_list
            
        except Exception as e:
            logger.error(f"Error fetching Cohere pricing: {str(e)}, falling back to static data")
            return self._get_static_pricing_data()
    
    async def _fetch_performance_metrics(self) -> dict:
        """Fetch live performance metrics from Cohere status page.
        
        Uses public status page when no API key available.
        
        Returns:
            Dict with model names as keys and {throughput, latency_ms} as values
        """
        try:
            perf_source = PERFORMANCE_SOURCES["Cohere"]
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="cohere_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=perf_source.api_endpoint,
                    api_key=self.api_key,
                    public_endpoint=perf_source.public_status_page
                ),
                ttl_seconds=perf_source.cache_ttl_seconds
            )
            
            if health_data and health_data.get("healthy"):
                latency = health_data.get("latency_ms", 300.0)
                return {
                    model: {
                        "latency_ms": latency,
                        "throughput": max(50.0, 300.0 / latency * 100.0)
                    }
                    for model in self.STATIC_PRICING.keys()
                }
        except Exception as e:
            logger.warning(f"Failed to fetch Cohere performance metrics: {str(e)}")
        
        return {
            model: {"throughput": 100.0, "latency_ms": 300.0}
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
                    source="Cohere Official Pricing (Fallback - Static)",
                    throughput=100.0,
                    latency_ms=300.0,
                    use_cases=pricing_info.get("use_cases"),
                    strengths=pricing_info.get("strengths"),
                    best_for=pricing_info.get("best_for")
                )
            )
        return pricing_list
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility.
        
        Returns:
            List of PricingMetrics for Cohere models
        """
        # Return static pricing data for backward compatibility
        pricing_list = []
        for model_name, pricing_info in CoherePricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Cohere",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Cohere Official Pricing (Static)",
                    throughput=100.0,
                    latency_ms=300.0,
                    use_cases=pricing_info.get("use_cases"),
                    strengths=pricing_info.get("strengths"),
                    best_for=pricing_info.get("best_for")
                )
            )
        return pricing_list
    
    async def _verify_api_key(self) -> bool:
        """
        Verify that the API key is valid.
        
        Returns:
            True if the API key is valid, False otherwise
        """
        # Placeholder for future API key verification
        # In production, this would make a lightweight API call
        return True
