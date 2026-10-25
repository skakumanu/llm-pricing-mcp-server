"""Service for retrieving Vercel AI Gateway model pricing data.

Vercel AI Gateway fronts first-party models (Anthropic, OpenAI, Google, xAI,
Mistral, Meta, DeepSeek, Cohere, Perplexity) behind one OpenAI-compatible
endpoint at per-token rates that generally track the upstream provider. Most
of its catalogue arrives as registry-sourced entries from
``data/registry_models/vercel_ai_gateway.json`` (written by
``scripts/pickup_registry_models.py``); the table below is the hand-maintained
core.

Rate card: https://vercel.com/ai-gateway/models

Provenance note (v1.75.0): the sandbox that authored this file could not reach
vercel.com, so every core entry below carries the reference registry's number
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


class VercelAIGatewayPricingService(BasePricingProvider):
    """Service to fetch and manage Vercel AI Gateway model pricing."""

    # Vercel AI Gateway pricing data (per 1k tokens in USD)
    # Source: https://vercel.com/ai-gateway/models
    # Prices last confirmed on this date (registry-sourced, see module docstring).
    PRICE_AS_OF = "2026-10-25"

    STATIC_PRICING = {
        "anthropic/claude-sonnet-4": {
            "input": 0.003,
            "output": 0.015,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "vercel_ai_gateway/anthropic/claude-sonnet-4",
            "context_window": 200000,
            "use_cases": ["Agentic coding", "Analysis", "Chat", "Long documents", "Vision"],
            "strengths": ["Frontier quality", "Strong coding", "Vision", "Tool use"],
            "best_for": "Frontier-quality general work routed through one gateway credential",
            "supports_vision": True,
            "supports_function_calling": True,
        },
        "anthropic/claude-haiku-4.5": {
            "input": 0.001,
            "output": 0.005,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "vercel_ai_gateway/anthropic/claude-haiku-4.5",
            "context_window": 200000,
            "use_cases": ["Fast chat", "Classification", "Extraction", "High-volume agents"],
            "strengths": ["Fast", "Low cost", "Vision", "Tool use"],
            "best_for": "Fast, inexpensive frontier-family model for high-volume tasks",
            "supports_vision": True,
            "supports_function_calling": True,
        },
        "google/gemini-2.5-flash": {
            "input": 0.0003,
            "output": 0.0025,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "vercel_ai_gateway/google/gemini-2.5-flash",
            "context_window": 1000000,
            "use_cases": ["Long-context summarization", "Multimodal", "Fast chat", "Extraction"],
            "strengths": ["1M context", "Multimodal", "Fast", "Low cost"],
            "best_for": "Very long inputs and multimodal tasks at low cost",
            "supports_vision": True,
            "supports_function_calling": True,
        },
        "google/gemini-2.5-pro": {
            "input": 0.00125,
            "output": 0.01,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "vercel_ai_gateway/google/gemini-2.5-pro",
            "context_window": 1048576,
            "use_cases": ["Complex reasoning", "Long documents", "Code", "Multimodal analysis"],
            "strengths": ["1M context", "Strong reasoning", "Multimodal"],
            "best_for": "Frontier reasoning over very long or multimodal inputs",
            "supports_vision": True,
            "supports_function_calling": True,
        },
        "meta/llama-3.3-70b": {
            "input": 0.00072,
            "output": 0.00072,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "vercel_ai_gateway/meta/llama-3.3-70b",
            "context_window": 128000,
            "use_cases": ["Chat", "Reasoning", "Code generation", "Summarization"],
            "strengths": ["Open weights", "Strong general capability", "Long context"],
            "best_for": "Open-weight general-purpose model behind the same gateway as frontier models",
            "supports_function_calling": True,
        },
        "deepseek/deepseek-r1": {
            "input": 0.00055,
            "output": 0.00219,
            "price_as_of": "2026-10-25",
            "price_source_kind": "registry",
            "registry_key": "vercel_ai_gateway/deepseek/deepseek-r1",
            "context_window": 128000,
            "use_cases": ["Complex reasoning", "Math", "Multi-step problems", "Code review"],
            "strengths": ["Chain-of-thought reasoning", "Open weights", "Long context"],
            "best_for": "Reasoning-heavy workloads where visible thinking is worth the extra output tokens",
            "is_reasoning_model": True,
        },
    }

    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Vercel AI Gateway pricing service."""
        super().__init__("Vercel AI Gateway")
        self.api_key = api_key or getattr(settings, 'vercel_ai_gateway_api_key', None)
        # Candidate live model list: https://ai-gateway.vercel.sh/v1/models.
        # Not enabled: it could not be verified as public/key-optional from the
        # environment that authored this file (FR14 — only ship a list that was
        # curled unauthenticated). Set _live_model_api_endpoint once verified.
        self._live_model_api_key = self.api_key

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Vercel AI Gateway model pricing data.

        Returns the curated core; registry-sourced models are merged in by
        ``get_pricing_with_status()``.

        Returns:
            List of PricingMetrics for Vercel AI Gateway models
        """
        return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for Vercel AI Gateway models."""
        return [
            self.build_metrics(
                model_name, pricing_info,
                source=self.source_for(model_name, pricing_info, "Vercel AI Gateway Official Pricing (Static)"),
                provider="Vercel AI Gateway",
            )
            for model_name, pricing_info in self.STATIC_PRICING.items()
        ]

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        return VercelAIGatewayPricingService()._get_static_pricing_data()
