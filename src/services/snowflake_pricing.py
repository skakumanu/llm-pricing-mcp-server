"""Service for retrieving Snowflake Cortex AI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class SnowflakePricingService(BasePricingProvider):
    """Service to fetch and manage Snowflake Cortex AI model pricing.

    Snowflake Cortex provides LLM inference via SQL functions (COMPLETE, SUMMARIZE,
    SENTIMENT, etc.).  Pricing is denominated in Snowflake credits; USD equivalents
    below use a reference rate of $3.00 per Snowflake credit.
    """

    # Snowflake Cortex AI pricing data (per 1k tokens in USD)
    # Credit costs per 1M tokens × $3.00/credit = USD per 1M; divide by 1000 for per-1k.
    # Source: https://www.snowflake.com/en/data-cloud/cortex/
    STATIC_PRICING = {
        "snowflake-arctic": {
            "input": 0.0018,
            "output": 0.0018,
            "context_window": 4096,
            "use_cases": [
                "Enterprise data analysis", "SQL generation", "Business intelligence",
                "Data summarization", "Cost-effective inference at scale"
            ],
            "strengths": [
                "Snowflake-native model", "128 active experts (MoE)", "Apache-2 license",
                "Runs inside Snowflake — data never leaves", "Strong enterprise coding"
            ],
            "best_for": (
                "Snowflake-native enterprise AI workloads where data residency "
                "and cost efficiency are critical"
            )
        },
        "llama3.1-70b": {
            "input": 0.0015,
            "output": 0.0015,
            "context_window": 128000,
            "use_cases": [
                "General reasoning", "Code generation", "Data analysis",
                "Long context summarization", "Multi-step workflows"
            ],
            "strengths": [
                "Strong open-source model", "128K context", "Snowflake data integration",
                "No data egress"
            ],
            "best_for": "General-purpose Snowflake AI with long-context reasoning on warehouse data",
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "llama3.1-8b": {
            "input": 0.0003,
            "output": 0.0003,
            "context_window": 128000,
            "use_cases": [
                "High-volume classification", "Data tagging", "Simple summarization",
                "Real-time inference", "Cost-optimized pipelines"
            ],
            "strengths": [
                "Very affordable", "Fast inference", "128K context",
                "Snowflake integration"
            ],
            "best_for": "High-throughput, cost-sensitive Snowflake data pipelines",
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "mistral-large": {
            "input": 0.0153,
            "output": 0.0153,
            "context_window": 32768,
            "use_cases": [
                "Complex reasoning", "Multilingual tasks", "Advanced code generation",
                "Enterprise workflows", "Research"
            ],
            "strengths": [
                "Mistral Large quality", "Multilingual", "Strong reasoning",
                "Enterprise-grade via Snowflake"
            ],
            "best_for": "Complex enterprise reasoning and multilingual tasks within Snowflake",
            "supports_function_calling": True,
        },
        "mistral-7b": {
            "input": 0.0018,
            "output": 0.0018,
            "context_window": 32768,
            "use_cases": [
                "General tasks", "Multilingual", "Code assistance",
                "Summarization", "Classification"
            ],
            "strengths": [
                "Affordable", "Multilingual", "Good quality", "Fast on Snowflake"
            ],
            "best_for": "Cost-effective multilingual tasks and code assistance in Snowflake"
        },
        "mixtral-8x7b": {
            "input": 0.0018,
            "output": 0.0018,
            "context_window": 32768,
            "use_cases": [
                "Code generation", "Multilingual reasoning", "Balanced workloads",
                "Long context tasks"
            ],
            "strengths": [
                "Mixture of experts", "Balanced quality/cost", "Multilingual",
                "Snowflake integration"
            ],
            "best_for": "Balanced enterprise tasks needing strong multilingual reasoning in Snowflake"
        },
        "reka-flash": {
            "input": 0.0024,
            "output": 0.0024,
            "context_window": 100000,
            "use_cases": [
                "Multimodal reasoning", "Long-document analysis", "Complex Q&A",
                "Enterprise research"
            ],
            "strengths": [
                "Long context (100K)", "Reka quality", "Available in Cortex",
                "Enterprise reliability"
            ],
            "best_for": "Long-document and multimodal enterprise tasks within Snowflake Cortex",
            "supports_vision": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Snowflake Cortex pricing service."""
        super().__init__("Snowflake")
        self.api_key = api_key or getattr(settings, 'snowflake_api_key', None)
        # Snowflake uses account-based auth; live model list not available via simple bearer token
        # Live sync is not enabled — static pricing is used directly.

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Snowflake Cortex AI model pricing data.

        Falls back to curated static pricing if live fetch fails.

        Returns:
            List of PricingMetrics for Snowflake Cortex models
        """
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning(f"Error fetching Snowflake pricing data: {e}")
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for Snowflake Cortex models."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Snowflake",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Snowflake Cortex AI Pricing (Static, ~$3/credit)",
                    throughput=90.0,
                    latency_ms=550.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", ""),
                    supports_vision=pricing_info.get("supports_vision", False),
                    supports_function_calling=pricing_info.get("supports_function_calling", False),
                    supports_json_mode=pricing_info.get("supports_json_mode", False),
                    batch_available=pricing_info.get("batch_available", False),
                    is_reasoning_model=pricing_info.get("is_reasoning_model", False),
                )
            )
        return pricing_list

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        pricing_list = []
        for model_name, pricing_info in SnowflakePricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Snowflake",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Snowflake Cortex AI Pricing (Static, ~$3/credit)",
                    throughput=90.0,
                    latency_ms=550.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", ""),
                    supports_vision=pricing_info.get("supports_vision", False),
                    supports_function_calling=pricing_info.get("supports_function_calling", False),
                    supports_json_mode=pricing_info.get("supports_json_mode", False),
                    batch_available=pricing_info.get("batch_available", False),
                    is_reasoning_model=pricing_info.get("is_reasoning_model", False),
                )
            )
        return pricing_list
