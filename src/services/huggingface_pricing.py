"""Service for retrieving Hugging Face Inference API pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.config.settings import settings

logger = logging.getLogger(__name__)

_HF_MODELS_ENDPOINT = "https://api-inference.huggingface.co/models"


class HuggingFacePricingService(BasePricingProvider):
    """Hugging Face Serverless Inference API pricing.

    Covers popular open-source models (Llama, Mistral, Qwen, Phi, Gemma)
    available via HF's hosted inference endpoints.
    Source: https://huggingface.co/pricing
    """

    # Per 1k tokens in USD (Serverless Inference API)
    STATIC_PRICING = {
        "meta-llama/Llama-3.3-70B-Instruct": {
            "input": 0.00059, "output": 0.00079,
            "context_window": 131072,
            "use_cases": ["Reasoning", "Code", "Chat", "Analysis", "Research"],
            "strengths": ["Open source", "Strong reasoning", "Long context", "Community support"],
            "best_for": "Open-source alternative to GPT-4 class models for self-hosted or HF-hosted use",
            "supports_function_calling": True,
        },
        "meta-llama/Llama-3.1-70B-Instruct": {
            "input": 0.00059, "output": 0.00079,
            "context_window": 131072,
            "use_cases": ["General purpose", "Code", "Chat", "Analysis"],
            "strengths": ["Open source", "Strong quality", "Long context"],
            "best_for": "High-quality open-source inference with full model access",
            "supports_function_calling": True,
        },
        "meta-llama/Llama-3.1-8B-Instruct": {
            "input": 0.0001, "output": 0.0001,
            "context_window": 131072,
            "use_cases": ["High-volume tasks", "Chat", "Classification", "Summarization"],
            "strengths": ["Very affordable", "Open source", "Fast", "Long context"],
            "best_for": "High-throughput open-source inference at minimal cost",
            "supports_function_calling": True,
        },
        "meta-llama/Llama-3.2-3B-Instruct": {
            "input": 0.00004, "output": 0.00004,
            "context_window": 131072,
            "use_cases": ["Edge tasks", "Simple chat", "Classification"],
            "strengths": ["Tiny", "Very cheap", "Open source", "Fast"],
            "best_for": "Edge and resource-constrained open-source inference",
        },
        "meta-llama/Llama-3.2-11B-Vision-Instruct": {
            "input": 0.00018, "output": 0.00018,
            "context_window": 131072,
            "use_cases": ["Vision", "Multimodal", "Image Q&A"],
            "strengths": ["Open-source vision", "Affordable", "Llama 3.2"],
            "best_for": "Open-source vision tasks without proprietary model costs",
            "supports_vision": True,
        },
        "mistralai/Mistral-7B-Instruct-v0.3": {
            "input": 0.0001, "output": 0.0001,
            "context_window": 32768,
            "use_cases": ["Chat", "Code", "Instruction following"],
            "strengths": ["Open source", "Fast", "Efficient", "Low cost"],
            "best_for": "Cost-effective open-source instruction following tasks",
            "supports_function_calling": True,
        },
        "mistralai/Mixtral-8x7B-Instruct-v0.1": {
            "input": 0.00024, "output": 0.00024,
            "context_window": 32768,
            "use_cases": ["Code", "Reasoning", "Multilingual", "Analysis"],
            "strengths": ["MoE architecture", "Strong reasoning", "Open source"],
            "best_for": "Open-source high-quality reasoning and multilingual tasks",
            "supports_function_calling": True,
        },
        "Qwen/Qwen2.5-72B-Instruct": {
            "input": 0.00059, "output": 0.00079,
            "context_window": 131072,
            "use_cases": ["Multilingual", "Code", "Reasoning", "Long context"],
            "strengths": ["Strong multilingual", "Code capable", "Open source", "Long context"],
            "best_for": "Multilingual and code tasks requiring open-source high-quality inference",
            "supports_function_calling": True,
        },
        "Qwen/Qwen2.5-7B-Instruct": {
            "input": 0.0001, "output": 0.0001,
            "context_window": 131072,
            "use_cases": ["Multilingual chat", "Code", "Classification"],
            "strengths": ["Affordable", "Multilingual", "Open source"],
            "best_for": "Cost-effective multilingual inference in open-source environments",
        },
        "microsoft/Phi-3.5-mini-instruct": {
            "input": 0.0001, "output": 0.0001,
            "context_window": 131072,
            "use_cases": ["Edge", "Mobile", "Efficient inference", "Simple tasks"],
            "strengths": ["Very small", "Efficient", "Microsoft-quality", "Open source"],
            "best_for": "Resource-constrained deployments needing Microsoft-quality models",
        },
        "microsoft/Phi-4": {
            "input": 0.00014, "output": 0.00056,
            "context_window": 16384,
            "use_cases": ["Reasoning", "STEM", "Code", "Analysis"],
            "strengths": ["Outperforms larger models on reasoning", "Open source", "Compact"],
            "best_for": "Compact high-quality reasoning for STEM and code tasks",
            "supports_function_calling": True,
        },
        "google/gemma-2-27b-it": {
            "input": 0.0004, "output": 0.0004,
            "context_window": 8192,
            "use_cases": ["Research", "Chat", "Analysis", "Fine-tuning base"],
            "strengths": ["Google quality", "Open source", "Research-friendly"],
            "best_for": "Research applications and fine-tuning base for Google-style models",
        },
        "deepseek-ai/DeepSeek-R1-Distill-Llama-70B": {
            "input": 0.00059, "output": 0.00079,
            "context_window": 131072,
            "use_cases": ["Reasoning", "Math", "Code", "Analysis"],
            "strengths": ["Open-source reasoning", "DeepSeek R1 distilled", "Strong STEM"],
            "best_for": "Open-source reasoning tasks requiring R1-class capability at lower cost",
            "is_reasoning_model": True,
        },
        "HuggingFaceH4/zephyr-7b-beta": {
            "input": 0.0001, "output": 0.0001,
            "context_window": 4096,
            "use_cases": ["Chat", "Assistant tasks", "Simple Q&A"],
            "strengths": ["RLHF fine-tuned", "Open source", "Cheap"],
            "best_for": "Simple chat applications requiring free open-source models",
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        super().__init__("Hugging Face")
        self.api_key = api_key or getattr(settings, 'huggingface_api_key', None)
        self._live_model_api_endpoint = _HF_MODELS_ENDPOINT
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        try:
            if self.api_key:
                _ = await DataFetcher.fetch_with_cache(
                    cache_key="huggingface_models",
                    fetch_func=lambda: DataFetcher.fetch_api_models(
                        api_endpoint=_HF_MODELS_ENDPOINT,
                        api_key=self.api_key,
                        require_auth=True,
                    ),
                    ttl_seconds=3600,
                )
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning("Error fetching Hugging Face pricing: %s", e)
            return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        result = []
        for model_name, p in self.STATIC_PRICING.items():
            result.append(PricingMetrics(
                model_name=model_name,
                provider="Hugging Face",
                cost_per_input_token=p["input"] / 1000,
                cost_per_output_token=p["output"] / 1000,
                context_window=p["context_window"],
                currency="USD",
                unit="per_token",
                source="Hugging Face Inference API Pricing (Static)",
                latency_ms=400.0,
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
        svc = HuggingFacePricingService()
        return svc._get_static_pricing_data()
