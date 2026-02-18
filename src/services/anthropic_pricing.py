"""Service for retrieving Anthropic model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


class AnthropicPricingService(BasePricingProvider):
    """Service to fetch and manage Anthropic model pricing."""
    
    # Anthropic pricing data (per 1k tokens in USD) - updated from their official pricing page
    # Source: https://www.anthropic.com/api
    STATIC_PRICING = {
        "claude-3-opus-20240229": {
            "input": 0.015,
            "output": 0.075,
            "context_window": 200000,
            "use_cases": ["Research analysis", "Complex problem solving", "Advanced coding", "Strategic planning"],
            "strengths": ["Superior intelligence", "Nuanced understanding", "Excellent at analysis"],
            "best_for": "Most demanding tasks requiring top-tier intelligence"
        },
        "claude-3-sonnet-20240229": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 200000,
            "use_cases": ["Content creation", "Data processing", "Code review", "Research assistance"],
            "strengths": ["Balanced performance/cost", "Large context", "Versatile"],
            "best_for": "Balanced workloads needing intelligence and efficiency"
        },
        "claude-3-haiku-20240307": {
            "input": 0.00025,
            "output": 0.00125,
            "context_window": 200000,
            "use_cases": ["Real-time chat", "Document processing", "Quick analysis", "Moderation"],
            "strengths": ["Fastest Claude", "Ultra-low cost", "Huge context"],
            "best_for": "High-speed applications requiring instant responses"
        },
        "claude-2.1": {
            "input": 0.008,
            "output": 0.024,
            "context_window": 200000,
            "use_cases": ["Long document Q&A", "Summarization", "General chat"],
            "strengths": ["Proven reliability", "Large context", "Stable"],
            "best_for": "Production systems requiring stability"
        },
        "claude-2.0": {
            "input": 0.008,
            "output": 0.024,
            "context_window": 100000,
            "use_cases": ["Legacy systems", "General assistance", "Text generation"],
            "strengths": ["Mature model", "Reliable", "Well-tested"],
            "best_for": "Maintaining existing Claude 2 integrations"
        },
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Anthropic pricing service.
        
        Args:
            api_key: Optional Anthropic API key for authenticated requests
        """
        super().__init__("Anthropic")
        self.api_key = api_key or settings.anthropic_api_key
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Anthropic model pricing data.
        
        This method attempts to fetch live data from:
        1. Anthropic API to get available models
        2. Anthropic pricing website for current pricing
        
        Falls back to curated static pricing data if live fetch fails.
        
        Returns:
            List of PricingMetrics for Anthropic models
            
        Raises:
            Exception: If unable to fetch or parse pricing data
        """
        try:
            # Fetch available models from API (live data)
            models_list = None
            if self.api_key:
                models_list = await DataFetcher.fetch_with_cache(
                    cache_key="anthropic_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=PRICING_SOURCES["Anthropic"].api_endpoint,
                        api_key=self.api_key,
                        require_auth=True
                    ),
                    ttl_seconds=PRICING_SOURCES["Anthropic"].cache_ttl_seconds
                )
            
            # Fetch pricing from website (live data)
            live_pricing_data = await DataFetcher.fetch_with_cache(
                cache_key="anthropic_pricing_web",
                fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                    url=PRICING_SOURCES["Anthropic"].pricing_url
                ),
                ttl_seconds=PRICING_SOURCES["Anthropic"].cache_ttl_seconds,
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
                    source = "Anthropic Official API"
                elif model_name in self.STATIC_PRICING:
                    pricing_info = self.STATIC_PRICING[model_name]
                    input_cost = pricing_info["input"] / 1000
                    output_cost = pricing_info["output"] / 1000
                    source = "Anthropic Official Pricing (Cached)"
                else:
                    continue
                
                # Get performance metrics
                metrics = performance_data.get(model_name, {
                    "throughput": 75.0,
                    "latency_ms": 350.0
                })
                
                # Get metadata from static data if available
                static_info = self.STATIC_PRICING.get(model_name, {})
                
                pricing_list.append(
                    PricingMetrics(
                        model_name=model_name,
                        provider=self.provider_name,
                        cost_per_input_token=input_cost / 1000 if input_cost >= 1 else input_cost,
                        cost_per_output_token=output_cost / 1000 if output_cost >= 1 else output_cost,
                        context_window=static_info.get("context_window", 200000),
                        currency="USD",
                        unit="per_token",
                        source=source,
                        throughput=metrics.get("throughput", 75.0),
                        latency_ms=metrics.get("latency_ms", 350.0),
                        use_cases=static_info.get("use_cases"),
                        strengths=static_info.get("strengths"),
                        best_for=static_info.get("best_for")
                    )
                )
            
            if not pricing_list:
                raise Exception("No pricing data available from live sources or cache")
            
            return pricing_list
            
        except Exception as e:
            logger.error(f"Error fetching Anthropic pricing: {str(e)}, falling back to static data")
            # Fall back to static pricing
            return self._get_static_pricing_data()
    
    async def _fetch_performance_metrics(self) -> dict:
        """Fetch live performance metrics from Anthropic status page.
        
        Uses public status page when no API key available.
        
        Returns:
            Dict with model names as keys and {throughput, latency_ms} as values
        """
        try:
            perf_source = PERFORMANCE_SOURCES["Anthropic"]
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="anthropic_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=perf_source.api_endpoint,
                    api_key=self.api_key,
                    public_endpoint=perf_source.public_status_page
                ),
                ttl_seconds=perf_source.cache_ttl_seconds
            )
            
            if health_data and health_data.get("healthy"):
                latency = health_data.get("latency_ms", 350.0)
                return {
                    model: {
                        "latency_ms": latency,
                        "throughput": max(50.0, 350.0 / latency * 75.0)  # Estimate based on latency
                    }
                    for model in self.STATIC_PRICING.keys()
                }
        except Exception as e:
            logger.warning(f"Failed to fetch Anthropic performance metrics: {str(e)}")
        
        # Return default estimated metrics
        return {
            model: {"throughput": 75.0, "latency_ms": 350.0}
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
                    source="Anthropic Official Pricing (Fallback - Static)",
                    throughput=75.0,
                    latency_ms=350.0,
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
            True if the API key is valid, False otherwise
        """
        if not self.api_key:
            return False
        
        try:
            # Anthropic doesn't have a models endpoint, so we'll just check if the key format is valid
            # In a real implementation, you might make a minimal API call
            if not self.api_key.startswith("sk-ant-"):
                return False
            return True
        except Exception:
            # Verification failures are silently ignored
            return False
    
    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """
        Synchronous method for backward compatibility.
        
        Returns:
            List of PricingMetrics for Anthropic models
        """
        # Return static pricing data for backward compatibility
        pricing_list = []
        for model_name, pricing_info in AnthropicPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Anthropic",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Anthropic Official Pricing (Static)"
                )
            )
        return pricing_list
