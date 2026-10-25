"""Service for retrieving DeepInfra model pricing data.

DeepInfra hosts open-weight models (Llama, DeepSeek, Qwen, Mistral, Gemma,
GLM, Kimi) behind an OpenAI-compatible API at per-token rates. The bulk of its
catalogue arrives as registry-sourced entries from
``data/registry_models/deepinfra.json`` (written by
``scripts/pickup_registry_models.py``); the table below is the hand-maintained
core.

Rate card: https://deepinfra.com/pricing

Provenance note (v1.75.0): the sandbox that authored this file could not reach
deepinfra.com, so every core entry below carries the reference registry's
number and is labelled ``price_source_kind: "registry"`` rather than being
presented as provider-confirmed. Verify each against the rate card, then drop
the two provenance keys and re-date the entry to promote it.
"""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class DeepInfraPricingService(BasePricingProvider):
    """Service to fetch and manage DeepInfra model pricing."""

    # DeepInfra pricing data (per 1k tokens in USD)
    # Source: https://deepinfra.com/pricing
    # Prices last confirmed on this date (registry-sourced, see module docstring).
    PRICE_AS_OF = "2026-10-25"

    STATIC_PRICING = {
        "meta-llama/Llama-3.3-70B-Instruct": {
            "input": 0.00023,
            "output": 0.0004,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "deepinfra/meta-llama/Llama-3.3-70B-Instruct",
            "context_window": 131072,
            "use_cases": ["Chat", "Reasoning", "Code generation", "Summarization", "Analysis"],
            "strengths": ["Open weights", "Strong general capability", "Long context", "Low cost per token"],
            "best_for": "General-purpose open-weight inference at a fraction of frontier-model cost",
            "supports_function_calling": True,
        },
        "meta-llama/Meta-Llama-3.1-8B-Instruct": {
            "input": 0.00003,
            "output": 0.00005,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct",
            "context_window": 131072,
            "use_cases": ["High-volume tasks", "Classification", "Simple chat", "Extraction"],
            "strengths": ["Very cheap", "Fast", "Long context", "Open weights"],
            "best_for": "High-throughput, low-cost tasks where a small model is enough",
            "supports_function_calling": True,
        },
        "deepseek-ai/DeepSeek-V3": {
            "input": 0.00032,
            "output": 0.00089,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "deepinfra/deepseek-ai/DeepSeek-V3",
            "context_window": 163840,
            "use_cases": ["Code generation", "Reasoning", "Chat", "Long documents"],
            "strengths": ["MoE efficiency", "Strong coding", "Long context", "Open weights"],
            "best_for": "Frontier-class open-weight quality for code and reasoning at low cost",
            "supports_function_calling": True,
        },
        "deepseek-ai/DeepSeek-R1": {
            "input": 0.0007,
            "output": 0.0024,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "deepinfra/deepseek-ai/DeepSeek-R1",
            "context_window": 163840,
            "use_cases": ["Complex reasoning", "Math", "Multi-step problems", "Code review"],
            "strengths": ["Chain-of-thought reasoning", "Open weights", "Long context"],
            "best_for": "Reasoning-heavy workloads where visible thinking is worth the extra output tokens",
            "supports_function_calling": True,
            "is_reasoning_model": True,
        },
        "Qwen/Qwen2.5-72B-Instruct": {
            "input": 0.00036,
            "output": 0.0004,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "deepinfra/Qwen/Qwen2.5-72B-Instruct",
            "context_window": 32768,
            "use_cases": ["Multilingual chat", "Code", "Structured output", "Analysis"],
            "strengths": ["Strong multilingual", "Good instruction following", "Open weights"],
            "best_for": "Multilingual and structured-output workloads on an open-weight model",
            "supports_function_calling": True,
        },
        "mistralai/Mistral-Small-24B-Instruct-2501": {
            "input": 0.00005,
            "output": 0.00008,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "deepinfra/mistralai/Mistral-Small-24B-Instruct-2501",
            "context_window": 32768,
            "use_cases": ["Chat", "Summarization", "Classification", "Low-latency assistants"],
            "strengths": ["Very cheap", "Compact", "Good quality for size", "Open weights"],
            "best_for": "Cheap, capable mid-size model for everyday assistant tasks",
            "supports_function_calling": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the DeepInfra pricing service."""
        super().__init__("DeepInfra")
        self.api_key = api_key or getattr(settings, 'deepinfra_api_key', None)
        # Candidate live model list: https://api.deepinfra.com/v1/openai/models.
        # Not enabled: it could not be verified as public/key-optional from the
        # environment that authored this file (FR14 — only ship a list that was
        # curled unauthenticated). Set _live_model_api_endpoint once verified.
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch DeepInfra model pricing data.

        Returns the curated core; registry-sourced models are merged in by
        ``get_pricing_with_status()``.

        Returns:
            List of PricingMetrics for DeepInfra models
        """
        return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for DeepInfra models."""
        return [
            self.build_metrics(
                model_name, pricing_info,
                source=self.source_for(model_name, pricing_info, "DeepInfra Official Pricing (Static)"),
                provider="DeepInfra",
            )
            for model_name, pricing_info in self.STATIC_PRICING.items()
        ]

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        return DeepInfraPricingService()._get_static_pricing_data()
