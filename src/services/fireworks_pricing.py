"""Service for retrieving Fireworks AI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


class FireworksPricingService(BasePricingProvider):
    """Service to fetch and manage Fireworks AI model pricing."""
    
    # Fireworks AI pricing data (per 1k tokens in USD)
    # Source: https://fireworks.ai/pricing
    STATIC_PRICING = {
        "accounts/fireworks/models/llama-v3p3-70b-instruct": {
            "input": 0.0009,
            "output": 0.0009,
            "context_window": 131072,
            "use_cases": ["Latest Llama", "Complex reasoning", "Code generation", "Long context"],
            "strengths": ["Latest Llama 3.3", "Enhanced intelligence", "Ultra-fast", "128K context"],
            "best_for": "Applications needing cutting-edge Llama with fastest inference"
        },
        "accounts/fireworks/models/llama-v3p2-90b-vision-instruct": {
            "input": 0.0009,
            "output": 0.0009,
            "context_window": 131072,
            "use_cases": ["Vision + text", "Image analysis", "Multimodal reasoning", "Visual Q&A"],
            "strengths": ["Multimodal", "Large vision model", "Fast", "Long context"],
            "best_for": "Multimodal applications requiring vision understanding at speed"
        },
        "accounts/fireworks/models/llama-v3p1-405b-instruct": {
            "input": 0.003,
            "output": 0.003,
            "context_window": 131072,
            "use_cases": ["Complex reasoning", "Research", "Advanced tasks", "Long-form"],
            "strengths": ["Largest Llama", "Fast inference", "Long context"],
            "best_for": "Complex tasks requiring top-tier reasoning with fast inference"
        },
        "accounts/fireworks/models/llama-v3p1-70b-instruct": {
            "input": 0.0009,
            "output": 0.0009,
            "context_window": 131072,
            "use_cases": ["General purpose", "Code", "Analysis", "Creative"],
            "strengths": ["Well-balanced", "Fast", "Long context"],
            "best_for": "General applications needing balanced performance"
        },
        "accounts/fireworks/models/qwen2p5-72b-instruct": {
            "input": 0.0009,
            "output": 0.0009,
            "context_window": 131072,
            "use_cases": ["Multilingual", "Math reasoning", "Code generation", "Analysis"],
            "strengths": ["Latest Qwen", "Excellent multilingual", "Strong math", "128K context"],
            "best_for": "Multilingual applications with strong reasoning needs"
        },
        "accounts/fireworks/models/mixtral-8x7b-instruct": {
            "input": 0.0005,
            "output": 0.0005,
            "context_window": 32768,
            "use_cases": ["Code", "Multilingual", "Reasoning"],
            "strengths": ["MoE architecture", "Versatile", "Fast"],
            "best_for": "Balanced performance and cost"
        },
        "accounts/fireworks/models/yi-large": {
            "input": 0.003,
            "output": 0.003,
            "context_window": 32768,
            "use_cases": ["Enterprise", "Complex reasoning", "Analysis"],
            "strengths": ["High performance", "Strong reasoning", "Fast"],
            "best_for": "Enterprise applications requiring strong reasoning"
        }
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Fireworks AI pricing service.
        
        Args:
            api_key: Optional Fireworks AI API key for authenticated requests
        """
        super().__init__("Fireworks AI")
        self.api_key = api_key or getattr(settings, 'fireworks_api_key', None)
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Fireworks AI model pricing data.
        
        Returns:
            List of PricingMetrics for Fireworks AI models
        """
        try:
            # Fetch available models from API (live data)
            models_list = None
            if self.api_key:
                models_list = await DataFetcher.fetch_with_cache(
                    cache_key="fireworks_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=PRICING_SOURCES["Fireworks AI"].api_endpoint,
                        api_key=self.api_key,
                        require_auth=True
                    ),
                    ttl_seconds=PRICING_SOURCES["Fireworks AI"].cache_ttl_seconds
                )
            
            # Fetch pricing from website (live data)
            live_pricing_data = await DataFetcher.fetch_with_cache(
                cache_key="fireworks_pricing_web",
                fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                    url=PRICING_SOURCES["Fireworks AI"].pricing_url
                ),
                ttl_seconds=PRICING_SOURCES["Fireworks AI"].cache_ttl_seconds,
                fallback_data=None
            )
            
            # Fetch performance metrics
            performance_data = await self._fetch_performance_metrics()
            
            # Use static pricing as base
            pricing_list = self._get_static_pricing_data(performance_data)
            
            return pricing_list
            
        except Exception as e:
            logger.warning(f"Error fetching Fireworks AI pricing data: {e}")
            return self._get_static_pricing_data({})
    
    def _get_static_pricing_data(self, performance_data: dict) -> List[PricingMetrics]:
        """Get static pricing metrics with optional performance data."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            perf = performance_data.get(model_name, {})
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Fireworks AI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Fireworks AI Official Pricing (Static)",
                    throughput=perf.get("throughput", 200.0),  # Known for fast inference
                    latency_ms=perf.get("latency_ms", 200.0),
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
    
    async def _fetch_performance_metrics(self) -> dict:
        """Fetch performance metrics for Fireworks AI models."""
        try:
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="fireworks_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=PERFORMANCE_SOURCES["Fireworks AI"].public_status_page,
                    api_key=None
                ),
                ttl_seconds=PERFORMANCE_SOURCES["Fireworks AI"].cache_ttl_seconds,
                fallback_data={"status": "unknown", "latency_ms": None}
            )
            
            latency = health_data.get("latency_ms", 200.0)
            
            performance_dict = {}
            for model_name in self.STATIC_PRICING.keys():
                performance_dict[model_name] = {
                    "throughput": 200.0,  # Fast inference
                    "latency_ms": latency if latency else 200.0
                }
            
            return performance_dict
            
        except Exception as e:
            logger.warning(f"Error fetching Fireworks AI performance metrics: {e}")
            return {}
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        pricing_list = []
        for model_name, pricing_info in FireworksPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Fireworks AI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Fireworks AI Official Pricing (Static)",
                    throughput=200.0,
                    latency_ms=200.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
