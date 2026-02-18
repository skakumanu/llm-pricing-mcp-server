"""Service for retrieving Anyscale model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


class AnyscalePricingService(BasePricingProvider):
    """Service to fetch and manage Anyscale model pricing."""
    
    # Anyscale pricing data (per 1k tokens in USD)
    # Source: https://www.anyscale.com/pricing
    STATIC_PRICING = {
        "meta-llama/Meta-Llama-3.1-405B-Instruct": {
            "input": 0.0015,
            "output": 0.0015,
            "context_window": 32768,
            "use_cases": ["Complex reasoning", "Research", "Advanced analysis", "Enterprise"],
            "strengths": ["Largest Llama", "Ray-optimized", "Scalable", "Strong reasoning"],
            "best_for": "Large-scale enterprise applications requiring top reasoning"
        },
        "meta-llama/Meta-Llama-3.1-70B-Instruct": {
            "input": 0.001,
            "output": 0.001,
            "context_window": 32768,
            "use_cases": ["General purpose", "Code", "Analysis", "Production"],
            "strengths": ["Well-balanced", "Ray-optimized", "Scalable"],
            "best_for": "Production applications at scale"
        },
        "meta-llama/Meta-Llama-3.1-8B-Instruct": {
            "input": 0.00015,
            "output": 0.00015,
            "context_window": 32768,
            "use_cases": ["High-volume", "Simple tasks", "Cost-effective"],
            "strengths": ["Affordable", "Fast", "Scalable"],
            "best_for": "High-volume cost-effective applications"
        },
        "mistralai/Mixtral-8x7B-Instruct-v0.1": {
            "input": 0.0005,
            "output": 0.0005,
            "context_window": 32768,
            "use_cases": ["Code", "Multilingual", "Reasoning"],
            "strengths": ["MoE", "Versatile", "Ray-optimized"],
            "best_for": "Balanced performance at scale"
        },
        "mistralai/Mistral-7B-Instruct-v0.1": {
            "input": 0.00015,
            "output": 0.00015,
            "context_window": 8192,
            "use_cases": ["Simple tasks", "High-volume", "Chatbots"],
            "strengths": ["Affordable", "Fast", "Efficient"],
            "best_for": "Cost-sensitive applications at scale"
        },
        "codellama/CodeLlama-70b-Instruct-hf": {
            "input": 0.001,
            "output": 0.001,
            "context_window": 16384,
            "use_cases": ["Code generation", "Code completion", "Code review"],
            "strengths": ["Specialized for code", "Strong performance", "Scalable"],
            "best_for": "Large-scale code generation applications"
        }
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Anyscale pricing service.
        
        Args:
            api_key: Optional Anyscale API key for authenticated requests
        """
        super().__init__("Anyscale")
        self.api_key = api_key or getattr(settings, 'anyscale_api_key', None)
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """Fetch Anyscale model pricing data."""
        try:
            # Fetch available models from API
            models_list = None
            if self.api_key:
                models_list = await DataFetcher.fetch_with_cache(
                    cache_key="anyscale_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=PRICING_SOURCES["Anyscale"].api_endpoint,
                        api_key=self.api_key,
                        require_auth=True
                    ),
                    ttl_seconds=PRICING_SOURCES["Anyscale"].cache_ttl_seconds
                )
            
            # Fetch pricing from website
            live_pricing_data = await DataFetcher.fetch_with_cache(
                cache_key="anyscale_pricing_web",
                fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                    url=PRICING_SOURCES["Anyscale"].pricing_url
                ),
                ttl_seconds=PRICING_SOURCES["Anyscale"].cache_ttl_seconds,
                fallback_data=None
            )
            
            # Fetch performance metrics
            performance_data = await self._fetch_performance_metrics()
            
            pricing_list = self._get_static_pricing_data(performance_data)
            
            return pricing_list
            
        except Exception as e:
            logger.warning(f"Error fetching Anyscale pricing data: {e}")
            return self._get_static_pricing_data({})
    
    def _get_static_pricing_data(self, performance_data: dict) -> List[PricingMetrics]:
        """Get static pricing metrics with optional performance data."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            perf = performance_data.get(model_name, {})
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Anyscale",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Anyscale Official Pricing (Static)",
                    throughput=perf.get("throughput", 150.0),  # Ray-optimized for performance
                    latency_ms=perf.get("latency_ms", 250.0),
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
    
    async def _fetch_performance_metrics(self) -> dict:
        """Fetch performance metrics for Anyscale models."""
        try:
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="anyscale_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=PERFORMANCE_SOURCES["Anyscale"].public_status_page,
                    api_key=None
                ),
                ttl_seconds=PERFORMANCE_SOURCES["Anyscale"].cache_ttl_seconds,
                fallback_data={"status": "unknown", "latency_ms": None}
            )
            
            latency = health_data.get("latency_ms", 250.0)
            
            performance_dict = {}
            for model_name in self.STATIC_PRICING.keys():
                performance_dict[model_name] = {
                    "throughput": 150.0,  # Ray optimized
                    "latency_ms": latency if latency else 250.0
                }
            
            return performance_dict
            
        except Exception as e:
            logger.warning(f"Error fetching Anyscale performance metrics: {e}")
            return {}
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        pricing_list = []
        for model_name, pricing_info in AnyscalePricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Anyscale",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Anyscale Official Pricing (Static)",
                    throughput=150.0,
                    latency_ms=250.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
