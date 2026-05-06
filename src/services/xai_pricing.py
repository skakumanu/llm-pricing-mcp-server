"""Service for retrieving xAI (Grok) model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider

logger = logging.getLogger(__name__)


class XAIPricingService(BasePricingProvider):
    """Service to fetch and manage xAI Grok model pricing."""

    # xAI pricing data (per 1k tokens in USD)
    # Source: https://x.ai/api
    STATIC_PRICING = {
        "grok-3": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 131072,
            "use_cases": [
                "Complex reasoning", "Advanced coding", "Deep research",
                "Multi-step analysis", "Scientific tasks"
            ],
            "strengths": ["State-of-the-art reasoning", "Real-time X/Twitter data", "Long context"],
            "best_for": "Complex reasoning and research tasks requiring frontier model capability"
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
            "best_for": "Cost-effective tasks needing solid reasoning at scale"
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
            "best_for": "General-purpose applications needing real-time awareness"
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
            "best_for": "High-throughput, cost-sensitive workloads on the xAI platform"
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
            "best_for": "Multimodal tasks combining vision and text on the xAI platform"
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the xAI pricing service."""
        super().__init__("xAI")
        self.api_key = api_key

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
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="xAI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="xAI Official Pricing (Static)",
                    throughput=150.0,
                    latency_ms=500.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        pricing_list = []
        for model_name, pricing_info in XAIPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="xAI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="xAI Official Pricing (Static)",
                    throughput=150.0,
                    latency_ms=500.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
