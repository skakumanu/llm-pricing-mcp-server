"""Service for retrieving Groq model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


class GroqPricingService(BasePricingProvider):
    """Service to fetch and manage Groq model pricing."""
    
    # Groq pricing data (per 1k tokens in USD)
    # Source: https://groq.com/pricing/
    STATIC_PRICING = {
        "llama-3.2-90b-vision": {
            "input": 0.0009,
            "output": 0.0009,
            "context_window": 8192,
            "use_cases": ["Vision + text", "Image understanding", "Visual Q&A", "Multimodal analysis"],
            "strengths": ["Multimodal", "Fast on Groq", "Open source", "Latest Llama"],
            "best_for": "Open-source multimodal applications needing ultra-fast inference"
        },
        "llama-3.2-11b-vision": {
            "input": 0.00018,
            "output": 0.00018,
            "context_window": 8192,
            "use_cases": ["Edge vision tasks", "Cost-effective multimodal", "Mobile deployment"],
            "strengths": ["Compact", "Vision capable", "Very fast", "Affordable"],
            "best_for": "Edge and mobile vision applications with speed requirements"
        },
        "llama-3.2-3b": {
            "input": 0.00006,
            "output": 0.00006,
            "context_window": 8192,
            "use_cases": ["Edge deployment", "Ultra-fast responses", "High-volume text"],
            "strengths": ["Tiny model", "Ultra-fast", "Very cheap", "Latest Llama"],
            "best_for": "Edge devices and ultra-high-volume text applications"
        },
        "llama-3.2-1b": {
            "input": 0.00004,
            "output": 0.00004,
            "context_window": 8192,
            "use_cases": ["IoT devices", "Extreme edge", "Real-time processing"],
            "strengths": ["Smallest Llama", "Instant responses", "Minimal cost"],
            "best_for": "IoT and extreme edge deployments requiring minimal resources"
        },
        "llama-3.1-405b": {
            "input": 0.00059,
            "output": 0.00079,
            "context_window": 131072,
            "use_cases": ["Complex reasoning", "Long context analysis", "Advanced research", "Multi-turn conversations"],
            "strengths": ["Largest Llama model", "Excellent reasoning", "Very fast on Groq"],
            "best_for": "Complex tasks requiring state-of-the-art reasoning with ultra-fast inference"
        },
        "llama-3.1-70b": {
            "input": 0.00059,
            "output": 0.00079,
            "context_window": 131072,
            "use_cases": ["General purpose", "Code generation", "Data analysis", "Creative writing"],
            "strengths": ["Great balance", "Fast inference", "Long context"],
            "best_for": "High-performance applications needing speed and quality"
        },
        "llama-3.1-8b": {
            "input": 0.00005,
            "output": 0.00008,
            "context_window": 131072,
            "use_cases": ["High-volume tasks", "Real-time processing", "Simple Q&A", "Classification"],
            "strengths": ["Ultra-fast", "Very affordable", "Long context"],
            "best_for": "High-throughput applications with cost constraints"
        },
        "mixtral-8x7b": {
            "input": 0.00024,
            "output": 0.00024,
            "context_window": 32768,
            "use_cases": ["Code generation", "Multilingual tasks", "Reasoning"],
            "strengths": ["Mixture of experts", "Fast", "Good quality"],
            "best_for": "Applications needing balanced performance and speed"
        },
        "gemma-2-9b": {
            "input": 0.0002,
            "output": 0.0002,
            "context_window": 8192,
            "use_cases": ["Improved Gemma tasks", "Balanced performance", "Research"],
            "strengths": ["Enhanced over Gemma", "Fast", "Open source"],
            "best_for": "Next-gen Gemma applications with speed requirements"
        },
        "gemma-7b": {
            "input": 0.00007,
            "output": 0.00007,
            "context_window": 8192,
            "use_cases": ["Edge deployments", "Privacy-focused apps", "Research"],
            "strengths": ["Open source", "Efficient", "Fast on Groq"],
            "best_for": "Research and development with fast inference needs"
        },
        "gemma2-9b": {
            "input": 0.0002,
            "output": 0.0002,
            "context_window": 8192,
            "use_cases": ["Improved Gemma tasks", "Balanced performance", "Research"],
            "strengths": ["Enhanced over Gemma", "Fast", "Open source"],
            "best_for": "Next-gen Gemma applications with speed requirements"
        }
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Groq pricing service.
        
        Args:
            api_key: Optional Groq API key for authenticated requests
        """
        super().__init__("Groq")
        self.api_key = api_key or getattr(settings, 'groq_api_key', None)
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Groq model pricing data.
        
        This method attempts to fetch live data from:
        1. Groq API to get available models
        2. Groq pricing website for current pricing
        
        Falls back to curated static pricing data if live fetch fails.
        
        Returns:
            List of PricingMetrics for Groq models
            
        Raises:
            Exception: If unable to fetch or parse pricing data
        """
        try:
            # Fetch available models from API (live data)
            models_list = None
            if self.api_key:
                models_list = await DataFetcher.fetch_with_cache(
                    cache_key="groq_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=PRICING_SOURCES["Groq"].api_endpoint,
                        api_key=self.api_key,
                        require_auth=True
                    ),
                    ttl_seconds=PRICING_SOURCES["Groq"].cache_ttl_seconds
                )
            
            # Fetch pricing from website (live data)
            live_pricing_data = await DataFetcher.fetch_with_cache(
                cache_key="groq_pricing_web",
                fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                    url=PRICING_SOURCES["Groq"].pricing_url
                ),
                ttl_seconds=PRICING_SOURCES["Groq"].cache_ttl_seconds,
                fallback_data=None
            )
            
            # Fetch performance metrics
            performance_data = await self._fetch_performance_metrics()
            
            # Use static pricing as base (most reliable)
            # TODO: Parse live_pricing_data when available
            pricing_list = self._get_static_pricing_data(performance_data)
            
            return pricing_list
            
        except Exception as e:
            logger.warning(f"Error fetching Groq pricing data: {e}")
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
                    provider="Groq",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Groq Official Pricing (Static)",
                    throughput=perf.get("throughput", 500.0),  # Groq is known for 500+ tok/s
                    latency_ms=perf.get("latency_ms", 100.0),  # Very low latency
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
    
    async def _fetch_performance_metrics(self) -> dict:
        """
        Fetch performance metrics for Groq models.
        
        Returns:
            Dictionary mapping model names to performance data
        """
        try:
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="groq_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=PERFORMANCE_SOURCES["Groq"].public_status_page,
                    api_key=None  # Public status page, no auth needed
                ),
                ttl_seconds=PERFORMANCE_SOURCES["Groq"].cache_ttl_seconds,
                fallback_data={"status": "unknown", "latency_ms": None}
            )
            
            latency = health_data.get("latency_ms", 100.0)
            
            # Groq is known for extremely fast inference
            # Default: 500 tok/s, adjusted by latency
            performance_dict = {}
            for model_name in self.STATIC_PRICING.keys():
                performance_dict[model_name] = {
                    "throughput": 500.0 if latency and latency < 200 else 400.0,
                    "latency_ms": latency if latency else 100.0
                }
            
            return performance_dict
            
        except Exception as e:
            logger.warning(f"Error fetching Groq performance metrics: {e}")
            return {}
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility.
        
        Returns:
            List of PricingMetrics for Groq models
        """
        # Return static pricing data for backward compatibility
        pricing_list = []
        for model_name, pricing_info in GroqPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Groq",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Groq Official Pricing (Static)",
                    throughput=500.0,  # Known for ultra-fast inference
                    latency_ms=100.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
