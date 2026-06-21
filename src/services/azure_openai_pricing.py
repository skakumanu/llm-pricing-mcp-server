"""Service for retrieving Azure OpenAI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider

logger = logging.getLogger(__name__)


class AzureOpenAIPricingService(BasePricingProvider):
    """Azure OpenAI Pay-As-You-Go pricing (East US region defaults).

    Prices differ from OpenAI direct: Azure adds regional availability,
    enterprise SLA, and VNet/private endpoint support.
    Source: https://azure.microsoft.com/en-us/pricing/details/cognitive-services/openai-service/
    """

    # Per 1k tokens in USD (PAYG, East US)
    STATIC_PRICING = {
        "gpt-4o": {
            "input": 0.0025, "output": 0.010,
            "context_window": 128000,
            "use_cases": ["Advanced reasoning", "Vision", "Code generation", "Chat", "Analysis"],
            "strengths": ["Multimodal", "Enterprise SLA", "Azure integration", "VNet support"],
            "best_for": "Enterprise workloads needing GPT-4o with Azure compliance and security",
            "supports_vision": True, "supports_function_calling": True, "supports_json_mode": True,
        },
        "gpt-4o-mini": {
            "input": 0.000150, "output": 0.000600,
            "context_window": 128000,
            "use_cases": ["Fast chat", "Classification", "Summarization", "Extraction"],
            "strengths": ["Low cost", "Fast", "Azure integration", "High throughput"],
            "best_for": "High-volume cost-sensitive enterprise applications",
            "supports_function_calling": True, "supports_json_mode": True,
        },
        "o1": {
            "input": 0.015, "output": 0.060,
            "context_window": 200000,
            "use_cases": ["Complex reasoning", "Math", "Science", "Code review"],
            "strengths": ["Deep reasoning", "Enterprise SLA", "Long context"],
            "best_for": "Complex enterprise reasoning tasks with strict compliance requirements",
            "is_reasoning_model": True, "supports_function_calling": True,
        },
        "o1-mini": {
            "input": 0.003, "output": 0.012,
            "context_window": 128000,
            "use_cases": ["Coding", "Reasoning", "STEM tasks"],
            "strengths": ["Affordable reasoning", "Fast", "Code focus"],
            "best_for": "Cost-effective reasoning for code and STEM applications",
            "is_reasoning_model": True,
        },
        "o3-mini": {
            "input": 0.0011, "output": 0.0044,
            "context_window": 200000,
            "use_cases": ["Reasoning", "Code", "Analysis", "Math"],
            "strengths": ["Latest reasoning", "Cost-efficient", "Long context"],
            "best_for": "Affordable next-gen reasoning tasks in enterprise environments",
            "is_reasoning_model": True, "supports_function_calling": True,
        },
        "gpt-4-turbo": {
            "input": 0.010, "output": 0.030,
            "context_window": 128000,
            "use_cases": ["Vision", "Long context", "Complex tasks", "Code"],
            "strengths": ["Vision capable", "128k context", "Reliable"],
            "best_for": "Long-context vision workloads requiring enterprise SLA",
            "supports_vision": True, "supports_function_calling": True, "supports_json_mode": True,
        },
        "gpt-35-turbo": {
            "input": 0.0005, "output": 0.0015,
            "context_window": 16385,
            "use_cases": ["Chat", "Q&A", "Summarization", "Light coding"],
            "strengths": ["Very low cost", "Fast", "Reliable", "Wide availability"],
            "best_for": "High-volume low-cost enterprise chat applications",
            "supports_function_calling": True, "supports_json_mode": True,
        },
        "text-embedding-3-large": {
            "input": 0.00013, "output": 0.00013,
            "context_window": 8191,
            "use_cases": ["Semantic search", "RAG", "Clustering", "Classification"],
            "strengths": ["High accuracy", "Azure integration", "Batch support"],
            "best_for": "Enterprise RAG and semantic search with Azure Cognitive Search",
            "batch_available": True,
        },
        "text-embedding-3-small": {
            "input": 0.00002, "output": 0.00002,
            "context_window": 8191,
            "use_cases": ["Embeddings", "Search", "Similarity"],
            "strengths": ["Very cheap", "Fast", "Good quality"],
            "best_for": "High-volume embedding workloads where cost is the primary constraint",
            "batch_available": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        super().__init__("Azure OpenAI")

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning("Error building Azure OpenAI pricing: %s", e)
            return []

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        result = []
        for model_name, p in self.STATIC_PRICING.items():
            result.append(PricingMetrics(
                model_name=model_name,
                provider="Azure OpenAI",
                cost_per_input_token=p["input"] / 1000,
                cost_per_output_token=p["output"] / 1000,
                context_window=p["context_window"],
                currency="USD",
                unit="per_token",
                source="Azure OpenAI Pricing (Static, PAYG East US)",
                latency_ms=600.0,
                use_cases=p.get("use_cases", []),
                strengths=p.get("strengths", []),
                best_for=p.get("best_for", ""),
                supports_vision=p.get("supports_vision", False),
                supports_function_calling=p.get("supports_function_calling", False),
                supports_json_mode=p.get("supports_json_mode", False),
                batch_available=p.get("batch_available", False),
                is_reasoning_model=p.get("is_reasoning_model", False),
            ))
        return result

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        svc = AzureOpenAIPricingService()
        return svc._get_static_pricing_data()
