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
        "gpt-4o": {
            "input": 0.0025,
            "output": 0.010,
            "context_window": 128000,
            "use_cases": ["Multimodal analysis", "Vision + text", "Complex reasoning", "real-time applications"],
            "strengths": ["Multimodal", "Fast", "Cost-effective", "High intelligence"],
            "best_for": "Multimodal applications requiring vision, audio, and text understanding",
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
        },
        "gpt-4o-mini": {
            "input": 0.00015,
            "output": 0.0006,
            "context_window": 128000,
            "use_cases": ["Fast chat", "Simple vision tasks", "High-volume multimodal", "Cost-sensitive apps"],
            "strengths": ["Very affordable", "Fast", "Multimodal", "Good intelligence"],
            "best_for": "High-volume multimodal applications with cost constraints",
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
        },
        "gpt-4.1": {
            "input": 0.002,
            "output": 0.008,
            "context_window": 1047576,
            "use_cases": ["Long document analysis", "Advanced coding", "Agentic workflows", "Complex reasoning"],
            "strengths": ["1M token context", "Superior coding", "Multimodal", "Batch available"],
            "best_for": "Flagship model for coding and long-context applications",
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
        },
        "gpt-4.1-mini": {
            "input": 0.0004,
            "output": 0.0016,
            "context_window": 1047576,
            "use_cases": ["Cost-effective coding", "Fast multimodal", "High-volume tasks", "Agentic systems"],
            "strengths": ["Very affordable", "1M context", "Strong reasoning", "Vision support"],
            "best_for": "Cost-effective high-performance applications with large context needs",
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
        },
        "gpt-4.1-nano": {
            "input": 0.0001,
            "output": 0.0004,
            "context_window": 1047576,
            "use_cases": ["Ultra-high-volume tasks", "Simple classification", "Fast inference", "Edge applications"],
            "strengths": ["Lowest cost", "Fastest GPT-4 class", "1M context", "Efficient"],
            "best_for": "Ultra-high-volume cost-sensitive applications",
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
        },
        "o4-mini": {
            "input": 0.0011,
            "output": 0.0044,
            "context_window": 200000,
            "use_cases": ["STEM reasoning", "Math problems", "Science", "Advanced coding", "Logic puzzles"],
            "strengths": ["Fast reasoning", "Affordable", "200K context", "Vision capable"],
            "best_for": "Cost-effective STEM reasoning with vision support",
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
            "is_reasoning_model": True,
        },
        "o3": {
            "input": 0.010,
            "output": 0.040,
            "context_window": 200000,
            "use_cases": ["Frontier reasoning", "Complex math", "Advanced research", "Multi-step problem solving"],
            "strengths": ["Top-tier reasoning", "200K context", "Science & math", "Function calling"],
            "best_for": "Most demanding reasoning tasks requiring frontier-level intelligence",
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
            "is_reasoning_model": True,
        },
        "o3-mini": {
            "input": 0.0011,
            "output": 0.0044,
            "context_window": 200000,
            "use_cases": ["STEM reasoning", "Code debugging", "Scientific tasks", "Math problems"],
            "strengths": ["Reasoning-focused", "Affordable", "STEM-optimized", "Fast"],
            "best_for": "STEM and coding tasks requiring systematic multi-step reasoning",
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
            "is_reasoning_model": True,
        },
        "o1": {
            "input": 0.015,
            "output": 0.060,
            "context_window": 200000,
            "use_cases": ["Complex reasoning", "Scientific research", "Multi-step math", "Strategic planning"],
            "strengths": ["Deep reasoning", "200K context", "High accuracy", "Science & math"],
            "best_for": "Tasks requiring deep chain-of-thought reasoning",
            "supports_vision": True,
            "supports_function_calling": True,
            "batch_available": True,
            "is_reasoning_model": True,
        },
        "o1-mini": {
            "input": 0.0011,
            "output": 0.0044,
            "context_window": 128000,
            "use_cases": ["Coding tasks", "Math problems", "Logical reasoning", "STEM applications"],
            "strengths": ["Affordable reasoning", "Fast", "Strong at code & math"],
            "best_for": "Reasoning tasks in code and math where cost matters",
            "supports_function_calling": True,
            "is_reasoning_model": True,
        },
        "gpt-4-turbo": {
            "input": 0.01,
            "output": 0.03,
            "context_window": 128000,
            "use_cases": ["Long document analysis", "Multi-turn conversations", "Large codebase understanding"],
            "strengths": ["Massive context window", "Vision capable", "Fast performance"],
            "best_for": "Processing large documents and maintaining long conversations",
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
        },
        "gpt-4-turbo-2024-04-09": {
            "input": 0.01,
            "output": 0.03,
            "context_window": 128000,
            "use_cases": ["Latest GPT-4 Turbo", "Vision tasks", "Function calling"],
            "strengths": ["Latest version", "Vision + JSON mode", "Reliable"],
            "best_for": "Production systems needing latest GPT-4 Turbo capabilities",
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
        },
        "gpt-4": {
            "input": 0.03,
            "output": 0.06,
            "context_window": 8192,
            "use_cases": ["Complex reasoning", "Code generation", "Creative writing", "Data analysis"],
            "strengths": ["High accuracy", "Strong reasoning", "Reliable outputs"],
            "best_for": "High-stakes tasks requiring maximum accuracy and reasoning",
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "gpt-3.5-turbo": {
            "input": 0.0005,
            "output": 0.0015,
            "context_window": 16385,
            "use_cases": ["Chatbots", "Simple Q&A", "Content generation", "Data extraction"],
            "strengths": ["Very low cost", "Fast responses", "Good for simple tasks"],
            "best_for": "High-volume applications where cost efficiency is critical",
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
        },
        "gpt-3.5-turbo-0125": {
            "input": 0.0005,
            "output": 0.0015,
            "context_window": 16385,
            "use_cases": ["Customer support", "Basic automation", "Simple text processing"],
            "strengths": ["Latest 3.5 version", "Cost-effective", "Reliable"],
            "best_for": "Cost-sensitive applications with moderate complexity",
            "supports_function_calling": True,
            "supports_json_mode": True,
            "batch_available": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the OpenAI pricing service.

        Args:
            api_key: Optional OpenAI API key for authenticated requests
        """
        super().__init__("OpenAI")
        self.api_key = api_key or settings.openai_api_key
        self._live_model_api_endpoint = "https://api.openai.com/v1/models"
        self._live_model_api_key = self.api_key

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
                        best_for=static_info.get("best_for"),
                        supports_vision=static_info.get("supports_vision", False),
                        supports_function_calling=static_info.get("supports_function_calling", False),
                        supports_json_mode=static_info.get("supports_json_mode", False),
                        batch_available=static_info.get("batch_available", False),
                        is_reasoning_model=static_info.get("is_reasoning_model", False),
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
        """Fetch live performance metrics from OpenAI status page.

        Uses public status page when no API key available.
        Falls back to direct API check when API key is available.

        Returns:
            Dict with model names as keys and {throughput, latency_ms} as values
        """
        try:
            perf_source = PERFORMANCE_SOURCES["OpenAI"]
            health_data = await DataFetcher.fetch_with_cache(
                cache_key="openai_performance",
                fetch_func=lambda: DataFetcher.check_api_health(
                    endpoint=perf_source.api_endpoint,
                    api_key=self.api_key,
                    public_endpoint=perf_source.public_status_page
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
                    best_for=pricing_info.get("best_for"),
                    supports_vision=pricing_info.get("supports_vision", False),
                    supports_function_calling=pricing_info.get("supports_function_calling", False),
                    supports_json_mode=pricing_info.get("supports_json_mode", False),
                    batch_available=pricing_info.get("batch_available", False),
                    is_reasoning_model=pricing_info.get("is_reasoning_model", False),
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
