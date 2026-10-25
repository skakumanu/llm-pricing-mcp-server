"""Service for retrieving Novita AI model pricing data.

Novita serves open-weight models (DeepSeek, Llama, Qwen, Kimi, GLM, MiniMax,
ERNIE) through an OpenAI-compatible endpoint at per-token rates. Most of its
catalogue arrives as registry-sourced entries from
``data/registry_models/novita.json`` (written by
``scripts/pickup_registry_models.py``); the table below is the hand-maintained
core.

Rate card: https://novita.ai/pricing

Provenance note (v1.75.0): the sandbox that authored this file could not reach
novita.ai, so every core entry below carries the reference registry's number
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


class NovitaPricingService(BasePricingProvider):
    """Service to fetch and manage Novita AI model pricing."""

    # Novita pricing data (per 1k tokens in USD)
    # Source: https://novita.ai/pricing
    # Prices last confirmed on this date (registry-sourced, see module docstring).
    PRICE_AS_OF = "2026-10-25"

    STATIC_PRICING = {
        "deepseek/deepseek-v3-0324": {
            "input": 0.00027,
            "output": 0.00112,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "novita/deepseek/deepseek-v3-0324",
            "context_window": 163840,
            "use_cases": ["Code generation", "Reasoning", "Chat", "Long documents"],
            "strengths": ["MoE efficiency", "Strong coding", "Long context", "Open weights"],
            "best_for": "Frontier-class open-weight quality for code and reasoning at low cost",
            "supports_function_calling": True,
        },
        "deepseek/deepseek-r1-0528": {
            "input": 0.0007,
            "output": 0.0025,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "novita/deepseek/deepseek-r1-0528",
            "context_window": 163840,
            "use_cases": ["Complex reasoning", "Math", "Multi-step problems", "Code review"],
            "strengths": ["Chain-of-thought reasoning", "Open weights", "Long context"],
            "best_for": "Reasoning-heavy workloads where visible thinking is worth the extra output tokens",
            "supports_function_calling": True,
            "is_reasoning_model": True,
        },
        "meta-llama/llama-3.3-70b-instruct": {
            "input": 0.000135,
            "output": 0.0004,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "novita/meta-llama/llama-3.3-70b-instruct",
            "context_window": 12288,
            "use_cases": ["Chat", "Reasoning", "Code generation", "Summarization"],
            "strengths": ["Open weights", "Strong general capability", "Low cost per token"],
            "best_for": "General-purpose open-weight inference at a fraction of frontier-model cost",
            "supports_function_calling": True,
        },
        "meta-llama/llama-3.1-8b-instruct": {
            "input": 0.00002,
            "output": 0.00005,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "novita/meta-llama/llama-3.1-8b-instruct",
            "context_window": 16384,
            "use_cases": ["High-volume tasks", "Classification", "Simple chat", "Extraction"],
            "strengths": ["Very cheap", "Fast", "Open weights"],
            "best_for": "High-throughput, low-cost tasks where a small model is enough",
        },
        "qwen/qwen-2.5-72b-instruct": {
            "input": 0.00038,
            "output": 0.0004,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "novita/qwen/qwen-2.5-72b-instruct",
            "context_window": 32000,
            "use_cases": ["Multilingual chat", "Code", "Structured output", "Analysis"],
            "strengths": ["Strong multilingual", "Good instruction following", "Open weights"],
            "best_for": "Multilingual and structured-output workloads on an open-weight model",
            "supports_function_calling": True,
        },
        "moonshotai/kimi-k2-instruct": {
            "input": 0.00057,
            "output": 0.0023,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "novita/moonshotai/kimi-k2-instruct",
            "context_window": 131072,
            "use_cases": ["Agentic coding", "Tool use", "Long-context chat", "Analysis"],
            "strengths": ["Large MoE", "Strong tool calling", "Long context", "Open weights"],
            "best_for": "Agentic and tool-calling workloads on a large open-weight model",
            "supports_function_calling": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Novita pricing service."""
        super().__init__("Novita")
        self.api_key = api_key or getattr(settings, 'novita_api_key', None)
        # Candidate live model list: https://api.novita.ai/openai/v1/models.
        # Not enabled: it could not be verified as public/key-optional from the
        # environment that authored this file (FR14 — only ship a list that was
        # curled unauthenticated). Set _live_model_api_endpoint once verified.
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Novita model pricing data.

        Returns the curated core; registry-sourced models are merged in by
        ``get_pricing_with_status()``.

        Returns:
            List of PricingMetrics for Novita models
        """
        return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for Novita models."""
        return [
            self.build_metrics(
                model_name, pricing_info,
                source=self.source_for(model_name, pricing_info, "Novita Official Pricing (Static)"),
                provider="Novita",
            )
            for model_name, pricing_info in self.STATIC_PRICING.items()
        ]

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        return NovitaPricingService()._get_static_pricing_data()
