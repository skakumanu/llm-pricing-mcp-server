"""Service for retrieving Oracle OCI Generative AI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class OraclePricingService(BasePricingProvider):
    """Service to fetch and manage Oracle OCI Generative AI model pricing.

    Oracle OCI Generative AI Service provides hosted LLM inference with
    on-demand and provisioned throughput pricing.  Prices below are
    on-demand (pay-as-you-go) rates.
    Source: https://www.oracle.com/cloud/price-list/#generative-ai
    """

    # Oracle OCI Generative AI pricing data (per 1k tokens in USD)
    # Source: https://www.oracle.com/cloud/price-list/#generative-ai
    STATIC_PRICING = {
        "meta.llama-3.1-405b-instruct": {
            "input": 0.00285,
            "output": 0.00285,
            "context_window": 128000,
            "use_cases": [
                "Complex reasoning", "Advanced coding", "Long document analysis",
                "Research", "Enterprise AI"
            ],
            "strengths": [
                "Largest Llama model", "OCI sovereign cloud", "128K context",
                "Enterprise compliance", "GDPR-ready regions"
            ],
            "best_for": "Enterprise workloads needing frontier open-source capability with Oracle cloud compliance"
        },
        "meta.llama-3.1-70b-instruct": {
            "input": 0.00093,
            "output": 0.00093,
            "context_window": 128000,
            "use_cases": [
                "General purpose", "Code generation", "Data analysis",
                "Customer support", "Content creation"
            ],
            "strengths": [
                "Strong performance", "OCI integration", "Long context",
                "Competitive pricing"
            ],
            "best_for": "General enterprise AI with strong Llama capability on Oracle infrastructure"
        },
        "meta.llama-3.2-90b-vision-instruct": {
            "input": 0.00096,
            "output": 0.00096,
            "context_window": 128000,
            "use_cases": [
                "Image understanding", "Visual data analysis", "Document processing",
                "Multimodal enterprise tasks"
            ],
            "strengths": [
                "Vision + text", "128K context", "OCI data residency",
                "Enterprise security"
            ],
            "best_for": "Multimodal enterprise applications with Oracle cloud data governance"
        },
        "meta.llama-3.2-11b-vision-instruct": {
            "input": 0.00018,
            "output": 0.00018,
            "context_window": 128000,
            "use_cases": [
                "Cost-effective vision", "Document classification",
                "Image tagging", "High-volume multimodal"
            ],
            "strengths": [
                "Affordable vision", "128K context", "OCI integration", "Fast"
            ],
            "best_for": "High-volume, cost-effective multimodal tasks on Oracle OCI"
        },
        "cohere.command-r-plus": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 128000,
            "use_cases": [
                "RAG pipelines", "Enterprise search", "Complex reasoning",
                "Multi-document analysis", "Tool use"
            ],
            "strengths": [
                "Best-in-class RAG", "128K context", "Tool use", "Multilingual",
                "OCI enterprise deployment"
            ],
            "best_for": "Enterprise RAG and search applications with Oracle's secure cloud infrastructure"
        },
        "cohere.command-r": {
            "input": 0.0005,
            "output": 0.0015,
            "context_window": 128000,
            "use_cases": [
                "Retrieval-augmented generation", "Summarization", "Q&A",
                "High-volume enterprise search"
            ],
            "strengths": [
                "Affordable RAG", "128K context", "Multilingual",
                "Cost-effective enterprise"
            ],
            "best_for": "High-volume enterprise RAG pipelines at low cost on Oracle OCI"
        },
        "cohere.command-r-08-2024": {
            "input": 0.0005,
            "output": 0.0015,
            "context_window": 128000,
            "use_cases": [
                "Retrieval-augmented generation", "Enterprise Q&A",
                "Summarization", "Classification"
            ],
            "strengths": [
                "Latest Command R", "Improved reasoning", "128K context",
                "OCI enterprise"
            ],
            "best_for": "Updated Command R for enterprise RAG with latest improvements on OCI"
        },
        "meta.llama-3-70b-instruct": {
            "input": 0.00099,
            "output": 0.00099,
            "context_window": 8192,
            "use_cases": [
                "General purpose", "Coding", "Analysis", "Content generation"
            ],
            "strengths": [
                "Proven Llama 3", "OCI integration", "Strong capability",
                "Enterprise support"
            ],
            "best_for": "Proven Llama 3 quality for general enterprise tasks on Oracle OCI"
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Oracle OCI Generative AI pricing service."""
        super().__init__("Oracle OCI")
        self.api_key = api_key or getattr(settings, 'oracle_api_key', None)
        # OCI uses API key + tenancy-based auth; simple bearer not applicable
        # Live sync is not enabled — static pricing is used directly.

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Oracle OCI Generative AI model pricing data.

        Falls back to curated static pricing if live fetch fails.

        Returns:
            List of PricingMetrics for Oracle OCI models
        """
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning(f"Error fetching Oracle OCI pricing data: {e}")
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for Oracle OCI Generative AI models."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Oracle OCI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Oracle OCI Generative AI Pricing (Static)",
                    throughput=100.0,
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
        for model_name, pricing_info in OraclePricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Oracle OCI",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Oracle OCI Generative AI Pricing (Static)",
                    throughput=100.0,
                    latency_ms=500.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
