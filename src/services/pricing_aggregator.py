"""Service for aggregating pricing data from multiple providers."""
import asyncio
import logging
from typing import Dict, List, Optional, Tuple, Type
from src.models.pricing import PricingMetrics, ProviderStatusInfo
from src.services.base_provider import BasePricingProvider
from src.services.openai_pricing import OpenAIPricingService
from src.services.anthropic_pricing import AnthropicPricingService
from src.services.google_pricing import GooglePricingService
from src.services.cohere_pricing import CoherePricingService
from src.services.mistral_pricing import MistralPricingService
from src.services.groq_pricing import GroqPricingService
from src.services.together_pricing import TogetherPricingService
from src.services.fireworks_pricing import FireworksPricingService
from src.services.perplexity_pricing import PerplexityPricingService
from src.services.ai21_pricing import AI21PricingService
from src.services.anyscale_pricing import AnyscalePricingService
from src.services.bedrock_pricing import BedrockPricingService
from src.services.xai_pricing import XAIPricingService
from src.services.deepseek_pricing import DeepSeekPricingService
from src.services.cerebras_pricing import CerebrasPricingService
from src.services.nvidia_pricing import NVIDIAPricingService
from src.services.replicate_pricing import ReplicatePricingService
from src.services.salesforce_pricing import SalesforcePricingService
from src.services.promptql_pricing import PromptQLPricingService
from src.services.snowflake_pricing import SnowflakePricingService
from src.services.oracle_pricing import OraclePricingService
from src.services.ide_pricing import IDEPricingService
from src.services.azure_openai_pricing import AzureOpenAIPricingService
from src.services.vertex_pricing import VertexAIPricingService
from src.services.huggingface_pricing import HuggingFacePricingService
from src.services.cloudflare_pricing import CloudflareAIPricingService
from src.services.deepinfra_pricing import DeepInfraPricingService
from src.services.novita_pricing import NovitaPricingService
from src.services.vercel_ai_gateway_pricing import VercelAIGatewayPricingService
from src.services.nebius_pricing import NebiusPricingService
from src.services.databricks_pricing import DatabricksPricingService
from src.services.price_oracle import get_price_oracle

logger = logging.getLogger(__name__)


# Every provider the aggregator fans out to, in output order. Each entry is the
# attribute name the service is stored under (kept stable: callers and tests
# reach for ``aggregator.openai_service``) and its class. Before v1.75.0 this
# list was hard-wired three times (constructor, gather, provider filter) and
# adding a provider meant editing all three and remembering the doc count.
PROVIDER_SERVICES: List[Tuple[str, Type[BasePricingProvider]]] = [
    ("openai_service", OpenAIPricingService),
    ("anthropic_service", AnthropicPricingService),
    ("google_service", GooglePricingService),
    ("cohere_service", CoherePricingService),
    ("mistral_service", MistralPricingService),
    ("groq_service", GroqPricingService),
    ("together_service", TogetherPricingService),
    ("fireworks_service", FireworksPricingService),
    ("perplexity_service", PerplexityPricingService),
    ("ai21_service", AI21PricingService),
    ("anyscale_service", AnyscalePricingService),
    ("bedrock_service", BedrockPricingService),
    ("xai_service", XAIPricingService),
    ("deepseek_service", DeepSeekPricingService),
    ("cerebras_service", CerebrasPricingService),
    ("nvidia_service", NVIDIAPricingService),
    ("replicate_service", ReplicatePricingService),
    ("salesforce_service", SalesforcePricingService),
    ("promptql_service", PromptQLPricingService),
    ("snowflake_service", SnowflakePricingService),
    ("oracle_service", OraclePricingService),
    ("ide_service", IDEPricingService),
    ("azure_openai_service", AzureOpenAIPricingService),
    ("vertex_service", VertexAIPricingService),
    ("huggingface_service", HuggingFacePricingService),
    ("cloudflare_service", CloudflareAIPricingService),
    # v1.75.0 — registry-backed hosts; most of their catalogue is registry-sourced
    ("deepinfra_service", DeepInfraPricingService),
    ("novita_service", NovitaPricingService),
    ("vercel_ai_gateway_service", VercelAIGatewayPricingService),
    ("nebius_service", NebiusPricingService),
    ("databricks_service", DatabricksPricingService),
]

# The number every "N providers" string in docs and the OpenAPI description
# derives from — tests/test_new_providers.py checks the docs agree with it.
PROVIDER_COUNT = len(PROVIDER_SERVICES)

# Lowercased ``?provider=`` filter strings -> service attribute. Display names,
# short forms and the odd historical spelling all resolve; anything else is an
# unknown provider and returns an empty result.
PROVIDER_FILTER_ALIASES: Dict[str, str] = {
    "openai": "openai_service",
    "anthropic": "anthropic_service",
    "google": "google_service",
    "cohere": "cohere_service",
    "mistral": "mistral_service",
    "mistral ai": "mistral_service",
    "groq": "groq_service",
    "together": "together_service",
    "together ai": "together_service",
    "fireworks": "fireworks_service",
    "fireworks ai": "fireworks_service",
    "perplexity": "perplexity_service",
    "perplexity ai": "perplexity_service",
    "ai21": "ai21_service",
    "ai21 labs": "ai21_service",
    "anyscale": "anyscale_service",
    "bedrock": "bedrock_service",
    "amazon bedrock": "bedrock_service",
    "xai": "xai_service",
    "x.ai": "xai_service",
    "deepseek": "deepseek_service",
    "cerebras": "cerebras_service",
    "nvidia": "nvidia_service",
    "nvidia nim": "nvidia_service",
    "replicate": "replicate_service",
    "salesforce": "salesforce_service",
    "salesforce einstein": "salesforce_service",
    "promptql": "promptql_service",
    "snowflake": "snowflake_service",
    "snowflake cortex": "snowflake_service",
    "oracle": "oracle_service",
    "oracle oci": "oracle_service",
    "azure": "azure_openai_service",
    "azure openai": "azure_openai_service",
    "vertex": "vertex_service",
    "vertex ai": "vertex_service",
    "huggingface": "huggingface_service",
    "hugging face": "huggingface_service",
    "cloudflare": "cloudflare_service",
    "cloudflare ai": "cloudflare_service",
    "deepinfra": "deepinfra_service",
    "deep infra": "deepinfra_service",
    "novita": "novita_service",
    "novita ai": "novita_service",
    "vercel": "vercel_ai_gateway_service",
    "vercel ai gateway": "vercel_ai_gateway_service",
    "ai gateway": "vercel_ai_gateway_service",
    "nebius": "nebius_service",
    "nebius ai studio": "nebius_service",
    "databricks": "databricks_service",
}


class PricingAggregatorService:
    """Service to aggregate pricing data from multiple LLM providers."""

    def __init__(self):
        """Initialize the aggregator service — one instance per PROVIDER_SERVICES entry."""
        for attr, cls in PROVIDER_SERVICES:
            setattr(self, attr, cls())

    def _services(self) -> List[BasePricingProvider]:
        return [getattr(self, attr) for attr, _ in PROVIDER_SERVICES]

    async def get_all_pricing_async(
        self, include_unconfirmed: bool = False
    ) -> tuple[List[PricingMetrics], List[ProviderStatusInfo]]:
        """
        Aggregate pricing data from all providers asynchronously.

        This method fetches data from all providers concurrently and handles
        partial failures gracefully. If a provider fails, its data is skipped
        but other providers' data is still returned.

        Args:
            include_unconfirmed: Include models whose per-token price has not been
                confirmed (newly released or discovered from a provider's live model
                list). Excluded by default: their price is a 0.0 placeholder, which
                would read as "free" to every cost ranking, tier, and estimate
                downstream. Opt in only where the caller wants a catalogue rather
                than a costing.

        Returns:
            Tuple of (all_pricing_data, provider_statuses)
        """
        # Fetch data from all providers concurrently
        # Using return_exceptions=True to handle individual provider failures gracefully
        tasks = [svc.get_pricing_with_status() for svc in self._services()]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_pricing = []
        provider_statuses = []

        for result in results:
            # Handle exceptions from individual providers
            if isinstance(result, Exception):
                # Provider failed completely - create error status
                provider_status = ProviderStatusInfo(
                    provider_name="Unknown",
                    is_available=False,
                    error_message=str(result),
                    models_count=0
                )
                provider_statuses.append(provider_status)
                continue

            pricing_data, status = result

            # Convert ProviderStatus to ProviderStatusInfo
            provider_status = ProviderStatusInfo(
                provider_name=status.provider_name,
                is_available=status.is_available,
                error_message=status.error_message,
                models_count=len(pricing_data)
            )
            provider_statuses.append(provider_status)

            # Add pricing data if available
            if pricing_data:
                all_pricing.extend(pricing_data)

        # Give unpriced models a real rate from the reference registry before
        # gating. A model that gets filled here becomes usable for costing instead
        # of being dropped. Curated prices are never overwritten, but one that
        # drifts from the registry beyond the threshold is withheld (price_confirmed
        # set to False) until a human corrects STATIC_PRICING — see check_price_drift
        # for what's currently withheld and why.
        try:
            oracle = get_price_oracle()
            await oracle.load()
            oracle.fill_missing_prices(all_pricing)
            oracle.demote_drifted(all_pricing)
            # A model the provider has shut down is withheld the day the
            # registry's deprecation date passes (v1.74.0) — it stays listed
            # with `retired_on` set so the weekly audit can open the removal PR.
            oracle.withhold_retired(all_pricing)
        except Exception as e:  # never let the oracle break pricing
            logger.warning("Price oracle unavailable, serving curated prices only: %s", e)

        if not include_unconfirmed:
            all_pricing = [m for m in all_pricing if m.price_confirmed]

        return all_pricing, provider_statuses

    async def get_pricing_by_provider_async(
        self, provider: str
    ) -> tuple[List[PricingMetrics], List[ProviderStatusInfo]]:
        """
        Get pricing data for a specific provider asynchronously.

        Args:
            provider: Provider name (case-insensitive)

        Returns:
            Tuple of (pricing_data, provider_statuses)
        """
        attr = PROVIDER_FILTER_ALIASES.get(provider.lower().strip())
        if attr is None:
            return [], []

        pricing_data, status = await getattr(self, attr).get_pricing_with_status()

        provider_status = ProviderStatusInfo(
            provider_name=status.provider_name,
            is_available=status.is_available,
            error_message=status.error_message,
            models_count=len(pricing_data)
        )

        return pricing_data, [provider_status]

    def get_all_pricing(self) -> List[PricingMetrics]:
        """
        Aggregate pricing data from all providers (synchronous for backward compatibility).

        Returns:
            Combined list of PricingMetrics from all providers
        """
        all_pricing = []

        # Fetch OpenAI pricing
        all_pricing.extend(self.openai_service.get_pricing_data())

        # Fetch Anthropic pricing
        all_pricing.extend(self.anthropic_service.get_pricing_data())

        # Fetch Google pricing
        all_pricing.extend(self.google_service.get_pricing_data())

        # Fetch Cohere pricing
        all_pricing.extend(self.cohere_service.get_pricing_data())

        # Fetch Mistral pricing
        all_pricing.extend(self.mistral_service.get_pricing_data())

        return all_pricing

    def get_pricing_by_provider(self, provider: str) -> List[PricingMetrics]:
        """
        Get pricing data for a specific provider (synchronous for backward compatibility).

        Args:
            provider: Provider name (case-insensitive)

        Returns:
            List of PricingMetrics for the specified provider
        """
        provider_lower = provider.lower()

        if provider_lower == "openai":
            return self.openai_service.get_pricing_data()
        elif provider_lower == "anthropic":
            return self.anthropic_service.get_pricing_data()
        elif provider_lower == "google":
            return self.google_service.get_pricing_data()
        elif provider_lower == "cohere":
            return self.cohere_service.get_pricing_data()
        elif provider_lower == "mistral" or provider_lower == "mistral ai":
            return self.mistral_service.get_pricing_data()
        else:
            return []

    async def find_model_pricing(self, model_name: str) -> Optional[PricingMetrics]:
        """
        Find pricing information for a specific model across all providers.

        Args:
            model_name: Name of the model (case-insensitive)

        Returns:
            PricingMetrics for the model if found, None otherwise
        """
        all_pricing, _ = await self.get_all_pricing_async()

        # Search for the model (case-insensitive)
        model_name_lower = model_name.lower()
        for pricing in all_pricing:
            if pricing.model_name.lower() == model_name_lower:
                return pricing

        return None


# ---------------------------------------------------------------------------
# Process-wide singleton — mirrors src/services/router.py's init_router()/
# get_router() and src/services/usage_tracker.py's get_usage_tracker(). Every
# REST endpoint and every MCP tool (STDIO or HTTP /mcp transport) within one
# running process shares this single, lazily-initialized aggregator instead
# of each maintaining its own independent pricing cache/TTL.
# ---------------------------------------------------------------------------
_aggregator: Optional[PricingAggregatorService] = None
_aggregator_lock = asyncio.Lock()


async def get_pricing_aggregator() -> PricingAggregatorService:
    """
    Lazy-load the process-wide pricing aggregator singleton on first use.
    Uses a lock to ensure thread-safe initialization.
    """
    global _aggregator

    if _aggregator is None:
        logger.info("First request - initializing pricing aggregator...")
        async with _aggregator_lock:
            # Double-check in case another coroutine initialized while we waited
            if _aggregator is None:
                _aggregator = PricingAggregatorService()
                logger.info("Pricing aggregator initialized successfully")

    return _aggregator
