"""Service for retrieving Perplexity AI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


class PerplexityPricingService(BasePricingProvider):
    """Service to fetch and manage Perplexity AI model pricing."""
    
    # Perplexity AI pricing data (per 1k tokens in USD)
    # Source: https://docs.perplexity.ai/docs/pricing
    STATIC_PRICING = {
        "sonar-reasoning": {
            "input": 0.001,
            "output": 0.005,
            "context_window": 127072,
            "use_cases": ["Research with reasoning", "Complex analysis", "Search-augmented tasks", "Deep research"],
            "strengths": ["Search integration", "Strong reasoning", "Up-to-date info", "Citations"],
            "best_for": "Research tasks requiring reasoning and current information"
        },
        "sonar": {
            "input": 0.0005,
            "output": 0.0005,
            "context_window": 127072,
            "use_cases": ["Search-augmented chat", "Current events", "Research", "Q&A with citations"],
            "strengths": ["Real-time search", "Citations", "Up-to-date", "Long context"],
            "best_for": "Applications needing current information with citations"
        },
        "sonar-pro": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 127072,
            "use_cases": ["Advanced research", "Professional analysis", "Deep research", "Expert queries"],
            "strengths": ["Highest quality", "Deep search", "Best citations", "Professional"],
            "best_for": "Professional research requiring highest quality answers"
        }
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Perplexity AI pricing service.
        
        Args:
            api_key: Optional Perplexity API key for authenticated requests
        """
        super().__init__("Perplexity AI")
        self.api_key = api_key or getattr(settings, 'perplexity_api_key', None)
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """Fetch Perplexity AI model pricing data."""
        try:
            # Fetch available models from API
            models_list = None
            if self.api_key:
                models_list = await DataFetcher.fetch_with_cache(
                    cache_key="perplexity_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=PRICING_SOURCES["Perplexity AI"].api_endpoint,
                        api_key=self.api_key,
                        require_auth=True
                    ),
                    ttl_seconds=PRICING_SOURCES["Perplexity AI"].cache_ttl_seconds
                )
            
            # Fetch pricing from website
            live_pricing_data = await DataFetcher.fetch_with_cache(
                cache_key="perplexity_pricing_web",
                fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                    url=PRICING_SOURCES["Perplexity AI"].pricing_url
                ),
                ttl_seconds=PRICING_SOURCES["Perplexity AI"].cache_ttl_seconds,
                fallback_data=None
            )
            
            # Fetch performance metrics
            performance_data = await self._fetch_performance_metrics()
            
            pricing_list = self._get_static_pricing_data(performance_data)
            
            return pricing_list
            
        except Exception as e:
            logger.warning(f"Error fetching Perplexity AI pricing data: {e}")
            return self._get_static_pricing_data({})
    
    def _get_static_pricing_data(self, performance_data: dict) -> List[PricingMetrics]:
        """Get static pricing metrics with optional performance data."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            perf = performance_data.get(model_name, {})
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Perplexity AI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Perplexity AI Official Pricing (Static)",
                    throughput=perf.get("throughput", 80.0),
                    latency_ms=perf.get("latency_ms", 400.0),
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
    
    async def _fetch_performance_metrics(self) -> dict:
        """Fetch performance metrics for Perplexity AI models."""
        try:
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="perplexity_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=PERFORMANCE_SOURCES["Perplexity AI"].public_status_page,
                    api_key=None
                ),
                ttl_seconds=PERFORMANCE_SOURCES["Perplexity AI"].cache_ttl_seconds,
                fallback_data={"status": "unknown", "latency_ms": None}
            )
            
            latency = health_data.get("latency_ms", 400.0)
            
            performance_dict = {}
            for model_name in self.STATIC_PRICING.keys():
                performance_dict[model_name] = {
                    "throughput": 80.0,
                    "latency_ms": latency if latency else 400.0
                }
            
            return performance_dict
            
        except Exception as e:
            logger.warning(f"Error fetching Perplexity AI performance metrics: {e}")
            return {}
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        pricing_list = []
        for model_name, pricing_info in PerplexityPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Perplexity AI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Perplexity AI Official Pricing (Static)",
                    throughput=80.0,
                    latency_ms=400.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
