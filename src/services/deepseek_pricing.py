"""Service for retrieving DeepSeek model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class DeepSeekPricingService(BasePricingProvider):
    """Service to fetch and manage DeepSeek model pricing."""

    # DeepSeek pricing data (per 1k tokens in USD)
    # Source: https://platform.deepseek.com/docs/pricing
    # Prices last confirmed against the provider's published rates on this date.
    PRICE_AS_OF = "2026-05-09"

    STATIC_PRICING = {
        # deepseek-chat / deepseek-reasoner (retired by DeepSeek 2026-07-24) and
        # deepseek-coder-v2 / deepseek-v2.5 (folded into later generations) were
        # removed in v1.74.0. The current API ids below are listed unconfirmed so
        # the price oracle fills them from the registry (deepseek-flash,
        # deepseek-v4-pro are first-party registry keys).
        "deepseek-flash": {
            "input": 0.0,
            "output": 0.0,
            "price_confirmed": False,
            "context_window": 128000,
            "use_cases": ["Chat", "Coding", "High-volume analysis", "Agents"],
            "strengths": ["Current generation (V4.1 Flash)", "Thinking and non-thinking modes"],
            "best_for": "DeepSeek's default fast model. Pricing sourced from the reference registry.",
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "deepseek-v4-pro": {
            "input": 0.0,
            "output": 0.0,
            "price_confirmed": False,
            "context_window": 1000000,
            "use_cases": ["Complex reasoning", "Long documents", "Coding", "Agents"],
            "strengths": ["Current flagship (V4 Pro)", "1M token context"],
            "best_for": (
                "DeepSeek's flagship for long-context and hard reasoning. "
                "Pricing sourced from the reference registry."
            ),
            "supports_function_calling": True,
            "supports_json_mode": True,
            "is_reasoning_model": True,
        },
        "deepseek-v3": {
            # Listed from the provider's current lineup. Price left unset and
            # price_confirmed=False so the price oracle fills it from the registry
            # rather than a hand-typed guess.
            "input": 0.0,
            "output": 0.0,
            "price_confirmed": False,
            "context_window": 65536,
            "use_cases": ['Coding', 'Chat', 'High-volume analysis'],
            "strengths": ["Current generation", "65,536 token context"],
            "best_for": "Cost-efficient general model. Pricing sourced from the reference registry.",
            "supports_vision": False,
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "deepseek-r1": {
            "input": 0.00055,
            "output": 0.00219,
            "price_as_of": "2026-09-13",
            "context_window": 64000,
            "use_cases": [
                "Advanced reasoning", "Complex math", "Scientific tasks",
                "Multi-step analysis", "Code reasoning"
            ],
            "strengths": ["Open-source reasoning", "R1 architecture", "Very affordable", "Chain-of-thought"],
            "best_for": "Open-source reasoning model rivaling frontier models at minimal cost",
            "supports_function_calling": True,
            "is_reasoning_model": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the DeepSeek pricing service."""
        super().__init__("DeepSeek")
        self.api_key = api_key or getattr(settings, 'deepseek_api_key', None)
        self._live_model_api_endpoint = "https://api.deepseek.com/v1/models"
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch DeepSeek model pricing data.

        Falls back to curated static pricing data if live fetch fails.

        Returns:
            List of PricingMetrics for DeepSeek models
        """
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning(f"Error fetching DeepSeek pricing data: {e}")
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for DeepSeek models."""
        return [
            self.build_metrics(
                model_name, pricing_info,
                source="DeepSeek Official Pricing (Static)",
                throughput=60.0,
                latency_ms=800.0,
                provider="DeepSeek",
            )
            for model_name, pricing_info in self.STATIC_PRICING.items()
        ]

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        return DeepSeekPricingService()._get_static_pricing_data()
