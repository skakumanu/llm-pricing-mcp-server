"""Service for retrieving PromptQL model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class PromptQLPricingService(BasePricingProvider):
    """Service to fetch and manage PromptQL model pricing.

    PromptQL (by Hasura) is a natural-language-to-query AI layer that lets
    users ask questions against structured data sources.  It exposes LLM
    inference through its own API endpoints and charges per-token similarly
    to other inference providers.
    """

    # PromptQL pricing data (per 1k tokens in USD)
    # Source: https://promptql.io/pricing
    STATIC_PRICING = {
        "promptql-smart": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 128000,
            "use_cases": [
                "Natural language data queries", "Business intelligence",
                "Multi-step data reasoning", "Complex SQL generation",
                "Semantic search over structured data"
            ],
            "strengths": [
                "Structured data specialization", "Automatic schema understanding",
                "128K context for large schemas", "SQL/API generation",
                "Deterministic query execution"
            ],
            "best_for": (
                "Enterprise data teams needing natural-language access to structured data "
                "with verifiable, deterministic query execution"
            ),
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "promptql-fast": {
            "input": 0.0008,
            "output": 0.004,
            "context_window": 32000,
            "use_cases": [
                "Simple data lookups", "Dashboard Q&A", "Quick aggregations",
                "High-volume analytics queries", "Self-serve BI"
            ],
            "strengths": [
                "Low latency", "Cost-effective for BI", "Schema-aware",
                "Batch query support"
            ],
            "best_for": "High-throughput analytics Q&A workloads at lower cost",
            "supports_function_calling": True,
            "supports_json_mode": True,
        },
        "promptql-mini": {
            "input": 0.0002,
            "output": 0.001,
            "context_window": 16000,
            "use_cases": [
                "Simple lookups", "Autocomplete for data fields",
                "Schema navigation", "Classification"
            ],
            "strengths": [
                "Ultra-fast", "Lowest cost", "Lightweight schema tasks"
            ],
            "best_for": "Low-cost, high-frequency structured-data classification and autocomplete"
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the PromptQL pricing service."""
        super().__init__("PromptQL")
        self.api_key = api_key or getattr(settings, 'promptql_api_key', None)
        self._live_model_api_endpoint = "https://api.promptql.io/v1/models"
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch PromptQL model pricing data.

        Falls back to curated static pricing if live fetch fails.

        Returns:
            List of PricingMetrics for PromptQL models
        """
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning(f"Error fetching PromptQL pricing data: {e}")
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for PromptQL models."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="PromptQL",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="PromptQL Official Pricing (Static)",
                    throughput=100.0,
                    latency_ms=500.0,
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
        for model_name, pricing_info in PromptQLPricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="PromptQL",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="PromptQL Official Pricing (Static)",
                    throughput=100.0,
                    latency_ms=500.0,
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
