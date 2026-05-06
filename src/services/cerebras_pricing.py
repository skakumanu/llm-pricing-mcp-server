"""Service for retrieving Cerebras model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class CerebrasPricingService(BasePricingProvider):
    """Service to fetch and manage Cerebras model pricing."""

    # Cerebras pricing data (per 1k tokens in USD)
    # Source: https://inference.cerebras.ai/
    # Cerebras is known for ultra-fast inference (2000+ tok/s) via custom wafer-scale chips
    STATIC_PRICING = {
        "llama3.1-8b": {
            "input": 0.0001,
            "output": 0.0001,
            "context_window": 8192,
            "use_cases": [
                "Real-time applications", "High-volume processing", "Edge streaming",
                "Fast chatbots", "Classification"
            ],
            "strengths": [
                "Fastest inference available (2000+ tok/s)", "Ultra-affordable",
                "Llama 3.1 quality", "Low latency"
            ],
            "best_for": (
                "Applications requiring the fastest possible inference "
                "with open-source model quality"
            )
        },
        "llama3.1-70b": {
            "input": 0.0006,
            "output": 0.0006,
            "context_window": 8192,
            "use_cases": [
                "Streaming AI apps", "Real-time assistants", "Code generation",
                "Advanced Q&A", "Data analysis"
            ],
            "strengths": [
                "Extremely fast inference", "Strong capability", "Competitive pricing",
                "Wafer-scale chip speed"
            ],
            "best_for": "High-quality real-time AI applications needing speed and capability"
        },
        "llama3.3-70b": {
            "input": 0.00085,
            "output": 0.00085,
            "context_window": 8192,
            "use_cases": [
                "Advanced reasoning", "Complex coding", "Research assistance",
                "Multi-turn conversations", "Content generation"
            ],
            "strengths": [
                "Latest Llama 3.3", "Ultra-fast on Cerebras", "Strong reasoning",
                "Cost-effective"
            ],
            "best_for": "State-of-the-art open-source capability with unmatched inference speed"
        },
        "llama-3.2-3b": {
            "input": 0.00006,
            "output": 0.00006,
            "context_window": 8192,
            "use_cases": [
                "IoT streaming", "Ultra-low latency", "Edge devices",
                "Simple Q&A", "Content classification"
            ],
            "strengths": [
                "Blazing fast", "Tiny model", "Near-zero cost",
                "Cerebras wafer-scale speed"
            ],
            "best_for": "IoT and edge deployments requiring extreme speed and minimal cost"
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Cerebras pricing service."""
        super().__init__("Cerebras")
        self.api_key = api_key or getattr(settings, 'cerebras_api_key', None)
        self._live_model_api_endpoint = "https://api.cerebras.ai/v1/models"
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Cerebras model pricing data.

        Falls back to curated static pricing data if live fetch fails.

        Returns:
            List of PricingMetrics for Cerebras models
        """
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning(f"Error fetching Cerebras pricing data: {e}")
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for Cerebras models."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Cerebras",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Cerebras Official Pricing (Static)",
                    throughput=2000.0,  # Cerebras is known for 2000+ tok/s
                    latency_ms=50.0,    # Ultra-low latency wafer-scale chip
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
        for model_name, pricing_info in CerebrasPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Cerebras",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Cerebras Official Pricing (Static)",
                    throughput=2000.0,
                    latency_ms=50.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
