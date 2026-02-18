"""Service for retrieving AI21 Labs model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


class AI21PricingService(BasePricingProvider):
    """Service to fetch and manage AI21 Labs model pricing."""
    
    # AI21 Labs pricing data (per 1k tokens in USD)
    # Source: https://www.ai21.com/pricing
    STATIC_PRICING = {
        "jamba-1.5-large": {
            "input": 0.002,
            "output": 0.008,
            "context_window": 256000,
            "use_cases": ["Long documents", "Complex analysis", "Enterprise tasks", "Research"],
            "strengths": ["Hybrid SSM-Transformer", "256K context", "Enterprise-grade", "Strong reasoning"],
            "best_for": "Enterprise applications with very long context requirements"
        },
        "jamba-1.5-mini": {
            "input": 0.0002,
            "output": 0.0004,
            "context_window": 256000,
            "use_cases": ["High-volume", "Long documents", "Cost-sensitive", "Simple tasks"],
            "strengths": ["Affordable", "256K context", "Fast", "Efficient"],
            "best_for": "High-volume applications needing long context at low cost"
        },
        "j2-ultra": {
            "input": 0.015,
            "output": 0.015,
            "context_window": 8192,
            "use_cases": ["Enterprise", "Complex tasks", "High quality", "Production"],
            "strengths": ["Highest quality", "Enterprise support", "Reliable", "Well-tested"],
            "best_for": "Mission-critical enterprise applications requiring highest quality"
        },
        "j2-mid": {
            "input": 0.010,
            "output": 0.010,
            "context_window": 8192,
            "use_cases": ["General purpose", "Business tasks", "Content creation"],
            "strengths": ["Well-balanced", "Good quality", "Enterprise support"],
            "best_for": "General enterprise applications"
        },
        "j2-light": {
            "input": 0.003,
            "output": 0.003,
            "context_window": 8192,
            "use_cases": ["Simple tasks", "High-volume", "Cost-effective"],
            "strengths": ["Fast", "Affordable", "Efficient"],
            "best_for": "Cost-effective enterprise tasks"
        }
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the AI21 Labs pricing service.
        
        Args:
            api_key: Optional AI21 API key for authenticated requests
        """
        super().__init__("AI21 Labs")
        self.api_key = api_key or getattr(settings, 'ai21_api_key', None)
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """Fetch AI21 Labs model pricing data."""
        try:
            # Fetch available models from API
            models_list = None
            if self.api_key:
                models_list = await DataFetcher.fetch_with_cache(
                    cache_key="ai21_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=PRICING_SOURCES["AI21 Labs"].api_endpoint,
                        api_key=self.api_key,
                        require_auth=True
                    ),
                    ttl_seconds=PRICING_SOURCES["AI21 Labs"].cache_ttl_seconds
                )
            
            # Fetch pricing from website
            live_pricing_data = await DataFetcher.fetch_with_cache(
                cache_key="ai21_pricing_web",
                fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                    url=PRICING_SOURCES["AI21 Labs"].pricing_url
                ),
                ttl_seconds=PRICING_SOURCES["AI21 Labs"].cache_ttl_seconds,
                fallback_data=None
            )
            
            # Fetch performance metrics
            performance_data = await self._fetch_performance_metrics()
            
            pricing_list = self._get_static_pricing_data(performance_data)
            
            return pricing_list
            
        except Exception as e:
            logger.warning(f"Error fetching AI21 Labs pricing data: {e}")
            return self._get_static_pricing_data({})
    
    def _get_static_pricing_data(self, performance_data: dict) -> List[PricingMetrics]:
        """Get static pricing metrics with optional performance data."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            perf = performance_data.get(model_name, {})
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="AI21 Labs",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="AI21 Labs Official Pricing (Static)",
                    throughput=perf.get("throughput", 70.0),
                    latency_ms=perf.get("latency_ms", 350.0),
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
    
    async def _fetch_performance_metrics(self) -> dict:
        """Fetch performance metrics for AI21 Labs models."""
        try:
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="ai21_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=PERFORMANCE_SOURCES["AI21 Labs"].public_status_page,
                    api_key=None
                ),
                ttl_seconds=PERFORMANCE_SOURCES["AI21 Labs"].cache_ttl_seconds,
                fallback_data={"status": "unknown", "latency_ms": None}
            )
            
            latency = health_data.get("latency_ms", 350.0)
            
            performance_dict = {}
            for model_name in self.STATIC_PRICING.keys():
                performance_dict[model_name] = {
                    "throughput": 70.0,
                    "latency_ms": latency if latency else 350.0
                }
            
            return performance_dict
            
        except Exception as e:
            logger.warning(f"Error fetching AI21 Labs performance metrics: {e}")
            return {}
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        pricing_list = []
        for model_name, pricing_info in AI21PricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="AI21 Labs",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="AI21 Labs Official Pricing (Static)",
                    throughput=70.0,
                    latency_ms=350.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
