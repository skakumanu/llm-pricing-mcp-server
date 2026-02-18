"""Service for retrieving Together AI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


class TogetherPricingService(BasePricingProvider):
    """Service to fetch and manage Together AI model pricing."""
    
    # Together AI pricing data (per 1k tokens in USD)
    # Source: https://www.together.ai/pricing
    STATIC_PRICING = {
        "meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo": {
            "input": 0.005,
            "output": 0.015,
            "context_window": 130000,
            "use_cases": ["Complex reasoning", "Research", "Advanced analysis", "Long documents"],
            "strengths": ["Largest Llama model", "Strong reasoning", "Long context"],
            "best_for": "Complex tasks requiring state-of-the-art open-source reasoning"
        },
        "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo": {
            "input": 0.00088,
            "output": 0.00088,
            "context_window": 131072,
            "use_cases": ["General purpose", "Code generation", "Analysis", "Creative work"],
            "strengths": ["Well-balanced", "Long context", "Cost-effective"],
            "best_for": "General-purpose applications with long context needs"
        },
        "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo": {
            "input": 0.00018,
            "output": 0.00018,
            "context_window": 131072,
            "use_cases": ["High-volume", "Edge deployment", "Simple tasks", "Q&A"],
            "strengths": ["Very affordable", "Fast", "Long context"],
            "best_for": "High-volume applications with cost constraints"
        },
        "mistralai/Mixtral-8x7B-Instruct-v0.1": {
            "input": 0.0006,
            "output": 0.0006,
            "context_window": 32768,
            "use_cases": ["Code generation", "Multilingual", "Reasoning", "General chat"],
            "strengths": ["Mixture of experts", "Versatile", "Good performance"],
            "best_for": "Applications needing balanced performance and affordability"
        },
        "mistralai/Mistral-7B-Instruct-v0.1": {
            "input": 0.0002,
            "output": 0.0002,
            "context_window": 8192,
            "use_cases": ["Simple tasks", "Chatbots", "Classification"],
            "strengths": ["Affordable", "Fast", "Efficient"],
            "best_for": "Cost-sensitive applications"
        },
        "Qwen/Qwen2-72B-Instruct": {
            "input": 0.0009,
            "output": 0.0009,
            "context_window": 32768,
            "use_cases": ["Multilingual", "Code generation", "Analysis"],
            "strengths": ["Strong multilingual", "Good code skills", "Well-rounded"],
            "best_for": "Multilingual applications and code generation"
        },
        "google/gemma-2-27b-it": {
            "input": 0.0008,
            "output": 0.0008,
            "context_window": 8192,
            "use_cases": ["Research", "General purpose", "Edge deployment"],
            "strengths": ["Open source", "Google quality", "Efficient"],
            "best_for": "Research and development with open-source requirements"
        },
        "databricks/dbrx-instruct": {
            "input": 0.0006,
            "output": 0.0006,
            "context_window": 32768,
            "use_cases": ["Enterprise", "Code generation", "Analysis"],
            "strengths": ["Enterprise-grade", "Strong performance", "MoE architecture"],
            "best_for": "Enterprise applications requiring strong performance"
        }
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Together AI pricing service.
        
        Args:
            api_key: Optional Together AI API key for authenticated requests
        """
        super().__init__("Together AI")
        self.api_key = api_key or getattr(settings, 'together_api_key', None)
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Together AI model pricing data.
        
        This method attempts to fetch live data from:
        1. Together AI API to get available models
        2. Together AI pricing website for current pricing
        
        Falls back to curated static pricing data if live fetch fails.
        
        Returns:
            List of PricingMetrics for Together AI models
            
        Raises:
            Exception: If unable to fetch or parse pricing data
        """
        try:
            # Fetch available models from API (live data)
            models_list = None
            if self.api_key:
                models_list = await DataFetcher.fetch_with_cache(
                    cache_key="together_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=PRICING_SOURCES["Together AI"].api_endpoint,
                        api_key=self.api_key,
                        require_auth=True
                    ),
                    ttl_seconds=PRICING_SOURCES["Together AI"].cache_ttl_seconds
                )
            
            # Fetch pricing from website (live data)
            live_pricing_data = await DataFetcher.fetch_with_cache(
                cache_key="together_pricing_web",
                fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                    url=PRICING_SOURCES["Together AI"].pricing_url
                ),
                ttl_seconds=PRICING_SOURCES["Together AI"].cache_ttl_seconds,
                fallback_data=None
            )
            
            # Fetch performance metrics
            performance_data = await self._fetch_performance_metrics()
            
            # Use static pricing as base (most reliable)
            # TODO: Parse live_pricing_data when available
            pricing_list = self._get_static_pricing_data(performance_data)
            
            return pricing_list
            
        except Exception as e:
            logger.warning(f"Error fetching Together AI pricing data: {e}")
            # Fall back to static data without performance metrics
            return self._get_static_pricing_data({})
    
    def _get_static_pricing_data(self, performance_data: dict) -> List[PricingMetrics]:
        """
        Get static pricing metrics with optional performance data.
        
        Args:
            performance_data: Dictionary of performance metrics by model
            
        Returns:
            List of PricingMetrics with static pricing data
        """
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            perf = performance_data.get(model_name, {})
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Together AI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Together AI Official Pricing (Static)",
                    throughput=perf.get("throughput", 100.0),
                    latency_ms=perf.get("latency_ms", 300.0),
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
    
    async def _fetch_performance_metrics(self) -> dict:
        """
        Fetch performance metrics for Together AI models.
        
        Returns:
            Dictionary mapping model names to performance data
        """
        try:
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="together_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=PERFORMANCE_SOURCES["Together AI"].public_status_page,
                    api_key=None  # Public status page, no auth needed
                ),
                ttl_seconds=PERFORMANCE_SOURCES["Together AI"].cache_ttl_seconds,
                fallback_data={"status": "unknown", "latency_ms": None}
            )
            
            latency = health_data.get("latency_ms", 300.0)
            
            # Typical Together AI performance
            performance_dict = {}
            for model_name in self.STATIC_PRICING.keys():
                performance_dict[model_name] = {
                    "throughput": 100.0,
                    "latency_ms": latency if latency else 300.0
                }
            
            return performance_dict
            
        except Exception as e:
            logger.warning(f"Error fetching Together AI performance metrics: {e}")
            return {}
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility.
        
        Returns:
            List of PricingMetrics for Together AI models
        """
        # Return static pricing data for backward compatibility
        pricing_list = []
        for model_name, pricing_info in TogetherPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Together AI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Together AI Official Pricing (Static)",
                    throughput=100.0,
                    latency_ms=300.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
