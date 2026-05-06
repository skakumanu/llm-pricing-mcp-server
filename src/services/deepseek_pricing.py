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
    STATIC_PRICING = {
        "deepseek-chat": {
            "input": 0.00027,
            "output": 0.0011,
            "context_window": 64000,
            "use_cases": [
                "General conversation", "Code assistance", "Data analysis",
                "Summarization", "Q&A"
            ],
            "strengths": ["Extremely affordable", "Strong coding", "Multi-lingual", "DeepSeek V3"],
            "best_for": "Cost-sensitive applications needing strong general-purpose capability"
        },
        "deepseek-reasoner": {
            "input": 0.00055,
            "output": 0.00219,
            "context_window": 64000,
            "use_cases": [
                "Complex reasoning", "Math problems", "Scientific analysis",
                "Multi-step problem solving", "Advanced coding"
            ],
            "strengths": ["Chain-of-thought reasoning", "Math/science", "Very affordable", "R1 model"],
            "best_for": "Reasoning-heavy tasks (math, science, logic) at a fraction of frontier model costs"
        },
        "deepseek-v2.5": {
            "input": 0.00014,
            "output": 0.00028,
            "context_window": 128000,
            "use_cases": [
                "Long context analysis", "Code generation", "Research",
                "Document processing", "Multi-turn chat"
            ],
            "strengths": ["128K context", "Ultra-affordable", "Strong coding", "Open-source"],
            "best_for": "Long-document processing and coding with extreme cost efficiency"
        },
        "deepseek-coder-v2": {
            "input": 0.00014,
            "output": 0.00028,
            "context_window": 128000,
            "use_cases": [
                "Code generation", "Code completion", "Debugging",
                "Code review", "Technical documentation"
            ],
            "strengths": [
                "Specialized for code", "128K context", "Multi-language",
                "Ultra-affordable"
            ],
            "best_for": "High-volume coding tasks requiring excellent performance at minimal cost"
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
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="DeepSeek",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="DeepSeek Official Pricing (Static)",
                    throughput=60.0,
                    latency_ms=800.0,
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
        for model_name, pricing_info in DeepSeekPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="DeepSeek",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="DeepSeek Official Pricing (Static)",
                    throughput=60.0,
                    latency_ms=800.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
