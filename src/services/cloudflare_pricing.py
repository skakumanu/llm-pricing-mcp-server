"""Service for retrieving Cloudflare Workers AI model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider

logger = logging.getLogger(__name__)


class CloudflareAIPricingService(BasePricingProvider):
    """Cloudflare Workers AI pricing.

    Cloudflare bills in Neurons. Free tier: 10,000 Neurons/day.
    Paid: $0.011 per 1,000 Neurons. Per-token estimates below are
    derived from published Neuron costs per model class.
    Source: https://developers.cloudflare.com/workers-ai/platform/pricing/
    """

    # Per 1k tokens in USD, the same convention as every other provider file
    # (the builder divides by 1000 to get per-token).
    #
    # History: until v1.73.11 this table held PER-TOKEN numbers (0.0000022 =
    # $2.20 per 1M) under a "per 1k" comment, and the builder still divided by
    # 1000 — so every Cloudflare price was served 1000x too low and sorted as
    # the cheapest option in the catalogue. The price oracle never caught it
    # because its key matching could not see these dotted/slashed names. The
    # values below are Cloudflare's published per-token list prices where the
    # reference registry carries them; models with no verifiable current price
    # (deprecated on Workers AI, or not a per-token text model) are listed but
    # marked unconfirmed, so a 2024-era Neuron estimate is never served as a
    # current rate.
    PRICE_AS_OF = "2026-06-20"

    STATIC_PRICING = {
        "@cf/meta/llama-3.1-8b-instruct": {
            # Only the fp8 / fp8-fast variants have a published current rate;
            # the base model's own rate could not be verified. Unconfirmed
            # rather than inherit a 1000x-wrong estimate.
            "input": 0.0, "output": 0.0,
            "price_confirmed": False,
            "context_window": 128000,
            "use_cases": ["Chat", "Code", "Summarization", "Edge inference"],
            "strengths": ["Edge-native", "Ultra-low latency", "Global CDN", "Free tier"],
            "best_for": "Edge inference with Cloudflare's global network and generous free tier",
            "supports_function_calling": True,
        },
        "@cf/meta/llama-3.2-1b-instruct": {
            "input": 0.000027, "output": 0.000201,
            "price_as_of": "2026-10-25",
            "context_window": 131072,
            "use_cases": ["Edge tasks", "IoT", "Real-time", "Classification"],
            "strengths": ["Tiny", "Ultra-fast", "Edge-native", "Almost free"],
            "best_for": "IoT and edge applications needing near-zero cost inference",
        },
        "@cf/meta/llama-3.2-3b-instruct": {
            "input": 0.0000509, "output": 0.000335,
            "price_as_of": "2026-10-25",
            "context_window": 131072,
            "use_cases": ["Edge chat", "Simple tasks", "Classification"],
            "strengths": ["Small", "Fast", "Edge-native", "Very cheap"],
            "best_for": "Lightweight edge inference with free tier coverage for most use cases",
        },
        "@cf/mistral/mistral-7b-instruct-v0.1": {
            "input": 0.001923, "output": 0.001923,
            "price_as_of": "2026-10-25",
            "context_window": 32768,
            "use_cases": ["Chat", "Instruction following", "Code"],
            "strengths": ["Open source", "Edge-native", "Efficient", "Reliable"],
            "best_for": "Open-source instruction following at the edge with Cloudflare distribution",
        },
        "@cf/google/gemma-7b-it": {
            # Retired from the Workers AI catalogue; no current list price.
            "input": 0.0, "output": 0.0,
            "price_confirmed": False,
            "context_window": 8192,
            "use_cases": ["Chat", "Instruction following", "Research"],
            "strengths": ["Google quality", "Open source", "Edge-native"],
            "best_for": "Google-quality open-source inference distributed via Cloudflare CDN",
        },
        "@cf/microsoft/phi-2": {
            # Retired from the Workers AI catalogue; no current list price.
            "input": 0.0, "output": 0.0,
            "price_confirmed": False,
            "context_window": 2048,
            "use_cases": ["Simple reasoning", "Education", "Lightweight tasks"],
            "strengths": ["Very small", "Efficient", "Microsoft-trained", "Edge-native"],
            "best_for": "Simple reasoning tasks at the edge with minimal resource usage",
        },
        "@cf/meta/llama-3.1-70b-instruct": {
            # Superseded by llama-3.3-70b-instruct-fp8-fast on Workers AI; no
            # current list price for this id.
            "input": 0.0, "output": 0.0,
            "price_confirmed": False,
            "context_window": 128000,
            "use_cases": ["Complex reasoning", "Code", "Analysis", "Chat"],
            "strengths": ["High quality", "Edge-native", "Global distribution"],
            "best_for": "Higher-quality edge inference where latency distribution is critical",
        },
        "@cf/qwen/qwen1.5-14b-chat-awq": {
            # Retired from the Workers AI catalogue; no current list price.
            "input": 0.0, "output": 0.0,
            "price_confirmed": False,
            "context_window": 32768,
            "use_cases": ["Multilingual", "Chat", "Code"],
            "strengths": ["Multilingual", "Edge-native", "Quantized for speed"],
            "best_for": "Multilingual edge applications with AWQ quantization for efficiency",
        },
        "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b": {
            "input": 0.000497, "output": 0.004881,
            "price_as_of": "2026-10-25",
            "context_window": 131072,
            "use_cases": ["Reasoning", "Math", "Code", "STEM"],
            "strengths": ["Open-source reasoning", "Edge-native", "DeepSeek R1 distilled"],
            "best_for": "Reasoning tasks at the edge leveraging DeepSeek R1 distillation",
            "is_reasoning_model": True,
        },
        "@cf/stabilityai/stable-diffusion-xl-base-1.0": {
            # Image model billed per generation step, not per token; a
            # per-token rate is not meaningful. Listed for discoverability only.
            "input": 0.0, "output": 0.0,
            "price_confirmed": False,
            "context_window": 77,
            "use_cases": ["Image generation", "Creative", "Design"],
            "strengths": ["Edge image gen", "Fast", "Global CDN", "Free tier"],
            "best_for": "Edge-distributed image generation with Cloudflare's global infrastructure",
            "supports_vision": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        super().__init__("Cloudflare AI")

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        try:
            return self._get_static_pricing_data()
        except Exception as e:
            logger.warning("Error building Cloudflare AI pricing: %s", e)
            return []

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        result = []
        for model_name, p in self.STATIC_PRICING.items():
            result.append(PricingMetrics(
                model_name=model_name,
                provider="Cloudflare AI",
                cost_per_input_token=p["input"] / 1000,
                cost_per_output_token=p["output"] / 1000,
                context_window=p["context_window"],
                currency="USD",
                unit="per_token",
                source="Cloudflare Workers AI Pricing (Static, per-token list prices)",
                latency_ms=80.0,   # Edge inference is extremely fast
                use_cases=p.get("use_cases", []),
                strengths=p.get("strengths", []),
                best_for=p.get("best_for", ""),
                supports_vision=p.get("supports_vision", False),
                supports_function_calling=p.get("supports_function_calling", False),
                supports_json_mode=p.get("supports_json_mode", False),
                batch_available=False,
                is_reasoning_model=p.get("is_reasoning_model", False),
                # Provenance travels with the data so the direct paths
                # (fetch_pricing_data / get_pricing_data) never serve a 0.0
                # placeholder as confirmed — see tests/test_price_provenance.py.
                price_confirmed=p.get("price_confirmed", True),
                price_as_of=p.get("price_as_of"),
            ))
        return result

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        svc = CloudflareAIPricingService()
        return svc._get_static_pricing_data()
