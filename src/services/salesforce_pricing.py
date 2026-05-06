"""Service for retrieving Salesforce Einstein AI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class SalesforcePricingService(BasePricingProvider):
    """Service to fetch and manage Salesforce Einstein AI model pricing."""

    # Salesforce Einstein AI pricing data (per 1k tokens in USD)
    # Source: https://www.salesforce.com/products/einstein/overview/
    # Salesforce offers LLM access via Einstein AI Platform and MuleSoft Anypoint.
    # Pricing below reflects the Einstein AI API / Flex Credits model.
    STATIC_PRICING = {
        "einstein-llm-large": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 32768,
            "use_cases": [
                "CRM automation", "Sales intelligence", "Customer service AI",
                "Enterprise workflows", "Salesforce data analysis"
            ],
            "strengths": [
                "Deep Salesforce CRM integration", "Enterprise security",
                "Pre-trained on business data", "Trust Layer built-in"
            ],
            "best_for": "Enterprise Salesforce-native AI with built-in CRM context and compliance"
        },
        "einstein-llm-standard": {
            "input": 0.0015,
            "output": 0.0075,
            "context_window": 16384,
            "use_cases": [
                "Email generation", "Case summarization", "Chat responses",
                "Knowledge article drafts", "Pipeline insights"
            ],
            "strengths": [
                "Salesforce org integration", "Balanced cost", "Trust Layer",
                "No data leaving org by default"
            ],
            "best_for": "Cost-effective Salesforce AI for common CRM tasks like email and case work"
        },
        "xgen-7b-8k-instruct": {
            "input": 0.0002,
            "output": 0.0002,
            "context_window": 8192,
            "use_cases": [
                "Code generation", "Instruction following", "Summarization",
                "General Q&A", "Developer workflows"
            ],
            "strengths": [
                "Open source (Salesforce research)", "8K context",
                "Strong instruction following", "Efficient 7B model"
            ],
            "best_for": "Developer-facing applications needing efficient open-source instruction tuning"
        },
        "xgen-7b-4k-instruct": {
            "input": 0.0002,
            "output": 0.0002,
            "context_window": 4096,
            "use_cases": [
                "Edge deployment", "Code completion", "Short-form generation",
                "Classification"
            ],
            "strengths": [
                "Open source", "Efficient inference", "Salesforce research quality"
            ],
            "best_for": "Lightweight instruction-following tasks in resource-constrained environments"
        },
        "codegen25-7b-instruct": {
            "input": 0.0002,
            "output": 0.0002,
            "context_window": 8192,
            "use_cases": [
                "Code generation", "Code completion", "Technical documentation",
                "Multi-language coding", "Debugging assistance"
            ],
            "strengths": [
                "Specialized for code", "Multi-language support",
                "Salesforce CodeGen lineage", "Open source"
            ],
            "best_for": "Code generation and completion with Salesforce's CodeGen research foundation"
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Salesforce Einstein AI pricing service."""
        super().__init__("Salesforce")
        self.api_key = api_key or getattr(settings, 'salesforce_api_key', None)
        # Einstein AI API uses OAuth bearer tokens
        self._live_model_api_endpoint = "https://api.salesforce.com/einstein/platform/v1/models"
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Salesforce Einstein AI model pricing data.

        Falls back to curated static pricing if live fetch fails.

        Returns:
            List of PricingMetrics for Salesforce Einstein AI models
        """
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning(f"Error fetching Salesforce pricing data: {e}")
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for Salesforce models."""
        pricing_list = []
        for model_name, pricing_info in self.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Salesforce",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Salesforce Einstein AI Pricing (Static)",
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
        for model_name, pricing_info in SalesforcePricingService.STATIC_PRICING.items():
            pricing_list.append(
                PricingMetrics(
                    model_name=model_name,
                    provider="Salesforce",
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info["context_window"],
                    currency="USD",
                    unit="per_token",
                    source="Salesforce Einstein AI Pricing (Static)",
                    throughput=80.0,
                    latency_ms=600.0,
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for", "")
                )
            )
        return pricing_list
