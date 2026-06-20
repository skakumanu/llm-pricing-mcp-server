"""Service for retrieving Google Vertex AI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider

logger = logging.getLogger(__name__)


class VertexAIPricingService(BasePricingProvider):
    """Google Vertex AI pricing — Gemini and PaLM 2 via GCP.

    Distinct from Google AI Studio (google_pricing.py): Vertex AI adds
    enterprise SLA, VPC Service Controls, CMEK, and regional data residency.
    Source: https://cloud.google.com/vertex-ai/generative-ai/pricing
    """

    # Per 1k tokens in USD
    STATIC_PRICING = {
        "gemini-2.0-flash": {
            "input": 0.0001, "output": 0.0004,
            "context_window": 1048576,
            "use_cases": ["Multimodal", "Code", "Chat", "Analysis", "Summarization"],
            "strengths": ["1M context", "Multimodal", "Fast", "Low cost", "GCP integration"],
            "best_for": "Enterprise multimodal workloads needing 1M token context on GCP",
            "supports_vision": True, "supports_function_calling": True, "supports_json_mode": True,
        },
        "gemini-2.0-flash-lite": {
            "input": 0.000075, "output": 0.0003,
            "context_window": 1048576,
            "use_cases": ["High-volume tasks", "Summarization", "Classification", "Extraction"],
            "strengths": ["Ultra low cost", "1M context", "Fast"],
            "best_for": "High-volume cost-sensitive GCP workloads with large context needs",
            "supports_vision": True, "supports_function_calling": True,
        },
        "gemini-1.5-pro": {
            "input": 0.00125, "output": 0.005,
            "context_window": 2097152,
            "use_cases": ["Long document analysis", "Video understanding", "Complex reasoning", "Research"],
            "strengths": ["2M context", "Multimodal", "Enterprise SLA", "High quality"],
            "best_for": "Long-context document and video analysis with enterprise compliance",
            "supports_vision": True, "supports_function_calling": True, "supports_json_mode": True,
        },
        "gemini-1.5-flash": {
            "input": 0.000075, "output": 0.0003,
            "context_window": 1048576,
            "use_cases": ["Fast processing", "Summarization", "Chat", "Code"],
            "strengths": ["Low latency", "Low cost", "1M context", "Reliable"],
            "best_for": "Fast enterprise applications needing large context at low cost",
            "supports_vision": True, "supports_function_calling": True, "supports_json_mode": True,
        },
        "gemini-1.0-pro": {
            "input": 0.0005, "output": 0.0015,
            "context_window": 32760,
            "use_cases": ["Chat", "Code", "Analysis", "Classification"],
            "strengths": ["Stable", "Widely available", "GCP integration"],
            "best_for": "Production GCP workloads requiring stable, proven model performance",
            "supports_function_calling": True, "supports_json_mode": True,
        },
        "text-bison": {
            "input": 0.000125, "output": 0.000125,
            "context_window": 8192,
            "use_cases": ["Text generation", "Summarization", "Extraction", "Q&A"],
            "strengths": ["Proven", "Enterprise-grade", "GCP native"],
            "best_for": "Traditional GCP NLP workloads needing stable PaLM 2 performance",
        },
        "code-bison": {
            "input": 0.000125, "output": 0.000125,
            "context_window": 6144,
            "use_cases": ["Code generation", "Code explanation", "Code completion"],
            "strengths": ["Code-focused", "Reliable", "GCP native"],
            "best_for": "GCP-based code generation pipelines with enterprise compliance",
        },
        "textembedding-gecko": {
            "input": 0.0001, "output": 0.0001,
            "context_window": 3072,
            "use_cases": ["Semantic search", "RAG", "Clustering", "Recommendation"],
            "strengths": ["GCP native", "Vertex Vector Search integration", "Enterprise SLA"],
            "best_for": "Enterprise RAG pipelines on Vertex AI with Vector Search",
            "batch_available": True,
        },
        "text-multilingual-embedding": {
            "input": 0.0001, "output": 0.0001,
            "context_window": 2048,
            "use_cases": ["Multilingual search", "Cross-language RAG", "Global applications"],
            "strengths": ["100+ languages", "GCP native", "Enterprise SLA"],
            "best_for": "Multilingual enterprise search and recommendation systems on GCP",
            "batch_available": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        super().__init__("Vertex AI")

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning("Error building Vertex AI pricing: %s", e)
            return []

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        result = []
        for model_name, p in self.STATIC_PRICING.items():
            result.append(PricingMetrics(
                model_name=model_name,
                provider="Vertex AI",
                cost_per_input_token=p["input"] / 1000,
                cost_per_output_token=p["output"] / 1000,
                context_window=p["context_window"],
                currency="USD",
                unit="per_token",
                source="Google Vertex AI Pricing (Static)",
                latency_ms=700.0,
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
        svc = VertexAIPricingService()
        return svc._get_static_pricing_data()
