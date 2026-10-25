"""Service for retrieving Nebius AI Studio model pricing data.

Nebius AI Studio hosts open-weight models (Llama, DeepSeek, Qwen, Nemotron,
Hermes, GLM, Kimi) behind an OpenAI-compatible API at per-token rates. Most
of its catalogue arrives as registry-sourced entries from
``data/registry_models/nebius.json`` (written by
``scripts/pickup_registry_models.py``); the table below is the hand-maintained
core.

Rate card: https://nebius.com/prices-ai-studio

Provenance note (v1.75.0): the sandbox that authored this file could not reach
nebius.com, so every core entry below carries the reference registry's number
and is labelled ``price_source_kind: "registry"`` rather than being presented
as provider-confirmed. Verify each against the rate card, then drop the two
provenance keys and re-date the entry to promote it.
"""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.config.settings import settings

logger = logging.getLogger(__name__)


class NebiusPricingService(BasePricingProvider):
    """Service to fetch and manage Nebius AI Studio model pricing."""

    # Nebius pricing data (per 1k tokens in USD)
    # Source: https://nebius.com/prices-ai-studio
    # Prices last confirmed on this date (registry-sourced, see module docstring).
    PRICE_AS_OF = "2026-10-25"

    STATIC_PRICING = {
        "meta-llama/Llama-3.3-70B-Instruct": {
            "input": 0.00013,
            "output": 0.0004,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "nebius/meta-llama/Llama-3.3-70B-Instruct",
            "context_window": 131072,
            "use_cases": ["Chat", "Reasoning", "Code generation", "Summarization", "Analysis"],
            "strengths": ["Open weights", "Strong general capability", "Long context", "Low cost per token"],
            "best_for": "General-purpose open-weight inference at a fraction of frontier-model cost",
            "supports_function_calling": True,
        },
        "meta-llama/Meta-Llama-3.1-8B-Instruct": {
            "input": 0.00002,
            "output": 0.00006,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "nebius/meta-llama/Meta-Llama-3.1-8B-Instruct",
            "context_window": 128000,
            "use_cases": ["High-volume tasks", "Classification", "Simple chat", "Extraction"],
            "strengths": ["Very cheap", "Fast", "Long context", "Open weights"],
            "best_for": "High-throughput, low-cost tasks where a small model is enough",
            "supports_function_calling": True,
        },
        "deepseek-ai/DeepSeek-V3-0324": {
            "input": 0.0005,
            "output": 0.0015,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "nebius/deepseek-ai/DeepSeek-V3-0324",
            "context_window": 128000,
            "use_cases": ["Code generation", "Reasoning", "Chat", "Long documents"],
            "strengths": ["MoE efficiency", "Strong coding", "Long context", "Open weights"],
            "best_for": "Frontier-class open-weight quality for code and reasoning at low cost",
            "supports_function_calling": True,
        },
        "deepseek-ai/DeepSeek-R1-0528": {
            "input": 0.0008,
            "output": 0.0024,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "nebius/deepseek-ai/DeepSeek-R1-0528",
            "context_window": 164000,
            "use_cases": ["Complex reasoning", "Math", "Multi-step problems", "Code review"],
            "strengths": ["Chain-of-thought reasoning", "Open weights", "Long context"],
            "best_for": "Reasoning-heavy workloads where visible thinking is worth the extra output tokens",
            "supports_function_calling": True,
            "is_reasoning_model": True,
        },
        "Qwen/Qwen2.5-72B-Instruct": {
            "input": 0.00013,
            "output": 0.0004,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "nebius/Qwen/Qwen2.5-72B-Instruct",
            "context_window": 128000,
            "use_cases": ["Multilingual chat", "Code", "Structured output", "Analysis"],
            "strengths": ["Strong multilingual", "Good instruction following", "Open weights"],
            "best_for": "Multilingual and structured-output workloads on an open-weight model",
            "supports_function_calling": True,
        },
        "openai/gpt-oss-120b": {
            "input": 0.00015,
            "output": 0.0006,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "nebius/openai/gpt-oss-120b",
            "context_window": 131072,
            "use_cases": ["Reasoning", "Agentic tasks", "Code", "Chat"],
            "strengths": ["Open-weight OpenAI model", "Strong reasoning for size", "Tool use"],
            "best_for": "OpenAI-family open-weight reasoning hosted at commodity prices",
            "supports_function_calling": True,
            "is_reasoning_model": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Nebius pricing service."""
        super().__init__("Nebius")
        self.api_key = api_key or getattr(settings, 'nebius_api_key', None)
        # Candidate live model list: https://api.studio.nebius.com/v1/models.
        # Not enabled: it could not be verified as public/key-optional from the
        # environment that authored this file (FR14 — only ship a list that was
        # curled unauthenticated). Set _live_model_api_endpoint once verified.
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Nebius model pricing data.

        Returns the curated core; registry-sourced models are merged in by
        ``get_pricing_with_status()``.

        Returns:
            List of PricingMetrics for Nebius models
        """
        return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for Nebius models."""
        return [
            self.build_metrics(
                model_name, pricing_info,
                source=self.source_for(model_name, pricing_info, "Nebius AI Studio Official Pricing (Static)"),
                provider="Nebius",
            )
            for model_name, pricing_info in self.STATIC_PRICING.items()
        ]

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        return NebiusPricingService()._get_static_pricing_data()
