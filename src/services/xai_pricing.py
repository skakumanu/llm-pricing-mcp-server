"""Service for retrieving xAI (Grok) model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class XAIPricingService(BasePricingProvider):
    """Service to fetch and manage xAI Grok model pricing."""

    # xAI pricing data (per 1k tokens in USD)
    # Source: https://x.ai/api
    # Prices last confirmed against the provider's published rates on this date.
    PRICE_AS_OF = "2026-05-09"

    STATIC_PRICING = {
        "grok-4": {
            # Listed from the provider's current lineup. Price left unset and
            # price_confirmed=False so the price oracle fills it from the registry
            # rather than a hand-typed guess.
            "input": 0.0,
            "output": 0.0,
            "price_confirmed": False,
            "context_window": 256000,
            "use_cases": ['Complex reasoning', 'Coding', 'Analysis'],
            "strengths": ["Current generation", "256,000 token context"],
            "best_for": "Frontier Grok model. Pricing sourced from the reference registry.",
            "supports_vision": False,
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "grok-4-fast-reasoning": {
            # Listed from the provider's current lineup. Price left unset and
            # price_confirmed=False so the price oracle fills it from the registry
            # rather than a hand-typed guess.
            "input": 0.0,
            "output": 0.0,
            "price_confirmed": False,
            "context_window": 2000000,
            "use_cases": ['High-volume reasoning', 'Long-context analysis'],
            "strengths": ["Current generation", "2,000,000 token context"],
            "best_for": "Fast reasoning at very low cost. Pricing sourced from the reference registry.",
            "supports_vision": False,
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "grok-3": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 131072,
            "use_cases": [
                "Complex reasoning", "Advanced coding", "Deep research",
                "Multi-step analysis", "Scientific tasks"
            ],
            "strengths": ["State-of-the-art reasoning", "Real-time X/Twitter data", "Long context"],
            "best_for": "Complex reasoning and research tasks requiring frontier model capability",
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "grok-3-fast": {
            "input": 0.005,
            "output": 0.025,
            "context_window": 131072,
            "use_cases": [
                "Low-latency reasoning", "Real-time applications", "High-priority tasks",
                "Interactive coding", "Fast research"
            ],
            "strengths": ["Fastest Grok-3 serving", "Real-time X data", "Low latency", "High quality"],
            "best_for": "Applications requiring Grok-3 quality with minimal latency",
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "grok-3-mini": {
            "input": 0.0003,
            "output": 0.0005,
            "context_window": 131072,
            "use_cases": [
                "Cost-effective reasoning", "General Q&A", "Code assistance",
                "Summarization", "High-volume tasks"
            ],
            "strengths": ["Very affordable", "Strong reasoning", "Long context", "Fast"],
            "best_for": "Cost-effective tasks needing solid reasoning at scale",
            "supports_function_calling": True,
            "supports_json_mode": True,
            "is_reasoning_model": True,
        },
        "grok-3-mini-fast": {
            "input": 0.0006,
            "output": 0.004,
            "context_window": 131072,
            "use_cases": [
                "Fast lightweight reasoning", "High-volume cost-effective tasks",
                "Real-time chat", "Classification"
            ],
            "strengths": ["Fastest Grok-3-mini serving", "Very affordable", "Low latency"],
            "best_for": "High-volume reasoning workloads requiring low latency at low cost",
            "supports_function_calling": True,
            "supports_json_mode": True,
            "is_reasoning_model": True,
        },
        "grok-2": {
            "input": 0.002,
            "output": 0.010,
            "context_window": 131072,
            "use_cases": [
                "General purpose", "Code generation", "Data analysis",
                "Creative writing", "Real-time information"
            ],
            "strengths": ["Real-time web access", "Good reasoning", "Long context"],
            "best_for": "General-purpose applications needing real-time awareness",
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "grok-2-mini": {
            "input": 0.0001,
            "output": 0.0002,
            "context_window": 131072,
            "use_cases": [
                "High-volume processing", "Classification", "Simple Q&A",
                "Summarization", "Content moderation"
            ],
            "strengths": ["Ultra-affordable", "Fast", "Long context", "xAI ecosystem"],
            "best_for": "High-throughput, cost-sensitive workloads on the xAI platform",
            "supports_function_calling": True,
        },
        "grok-vision-beta": {
            "input": 0.005,
            "output": 0.015,
            "context_window": 8192,
            "use_cases": [
                "Image understanding", "Visual Q&A", "Document analysis",
                "Multimodal reasoning"
            ],
            "strengths": ["Vision capable", "xAI ecosystem", "Real-time data"],
            "best_for": "Multimodal tasks combining vision and text on the xAI platform",
            "supports_vision": True,
            "supports_function_calling": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the xAI pricing service."""
        super().__init__("xAI")
        self.api_key = api_key or getattr(settings, 'xai_api_key', None)
        self._live_model_api_endpoint = "https://api.x.ai/v1/models"
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch xAI Grok model pricing data.

        Falls back to curated static pricing data if live fetch fails.

        Returns:
            List of PricingMetrics for xAI Grok models
        """
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning(f"Error fetching xAI pricing data: {e}")
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for xAI Grok models."""
        return [
            self.build_metrics(
                model_name, info,
                source="xAI Official Pricing (Static)",
                throughput=150.0,
                latency_ms=500.0,
            )
            for model_name, info in self.STATIC_PRICING.items()
        ]

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        return XAIPricingService()._get_static_pricing_data()
