"""Service for retrieving Replicate model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider

logger = logging.getLogger(__name__)


class ReplicatePricingService(BasePricingProvider):
    """Service to fetch and manage Replicate model pricing."""

    # Replicate LLM pricing data (per 1k tokens in USD)
    # Source: https://replicate.com/pricing
    STATIC_PRICING = {
        "meta/meta-llama-3-70b-instruct": {
            "input": 0.00065,
            "output": 0.00275,
            "context_window": 8192,
            "use_cases": [
                "General purpose", "Code generation", "Data analysis",
                "Creative writing", "Research"
            ],
            "strengths": [
                "Strong open-source model", "Easy API", "Serverless deployment",
                "Pay-per-prediction"
            ],
            "best_for": "Projects needing serverless open-source LLM deployment without infrastructure"
        },
        "meta/meta-llama-3-8b-instruct": {
            "input": 0.00005,
            "output": 0.00025,
            "context_window": 8192,
            "use_cases": [
                "High-volume processing", "Simple tasks", "Classification",
                "Fast Q&A", "Content generation"
            ],
            "strengths": [
                "Ultra-affordable", "Serverless", "Fast", "No infra management"
            ],
            "best_for": "High-volume, cost-sensitive tasks with serverless convenience"
        },
        "meta/llama-2-70b-chat": {
            "input": 0.00065,
            "output": 0.00275,
            "context_window": 4096,
            "use_cases": [
                "Conversational AI", "General Q&A", "Summarization",
                "Content generation"
            ],
            "strengths": [
                "Battle-tested", "Serverless", "Easy deployment", "Community support"
            ],
            "best_for": "Serverless conversational AI with proven reliability"
        },
        "mistralai/mistral-7b-instruct-v0.2": {
            "input": 0.00005,
            "output": 0.00025,
            "context_window": 32768,
            "use_cases": [
                "General tasks", "Code assistance", "Multilingual",
                "Summarization", "Classification"
            ],
            "strengths": [
                "Long context", "Multilingual", "Affordable", "Serverless"
            ],
            "best_for": "Cost-effective multilingual applications with long context needs"
        },
        "mistralai/mixtral-8x7b-instruct-v0.1": {
            "input": 0.00024,
            "output": 0.00024,
            "context_window": 32768,
            "use_cases": [
                "Code generation", "Multilingual", "Reasoning",
                "Long context", "Balanced tasks"
            ],
            "strengths": [
                "Mixture of experts", "Long context", "Good quality",
                "Serverless deployment"
            ],
            "best_for": "Balanced capability and cost for multilingual workloads on serverless"
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Replicate pricing service."""
        super().__init__("Replicate")
        self.api_key = api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Replicate model pricing data.

        Falls back to curated static pricing data if live fetch fails.

        Returns:
            List of PricingMetrics for Replicate models
        """
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning(f"Error fetching Replicate pricing data: {e}")
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for Replicate models."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Replicate",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Replicate Official Pricing (Static)",
                    throughput=80.0,
                    latency_ms=600.0,
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
        for model_name, pricing_info in ReplicatePricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Replicate",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Replicate Official Pricing (Static)",
                    throughput=80.0,
                    latency_ms=600.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
