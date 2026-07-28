"""Base provider interface for pricing services."""
from abc import ABC, abstractmethod
from typing import List, Optional, FrozenSet
from dataclasses import dataclass
import logging
import httpx
from src.models.pricing import PricingMetrics

logger = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    """Status information for a provider."""

    provider_name: str
    is_available: bool
    error_message: Optional[str] = None
    last_updated: Optional[str] = None


class BasePricingProvider(ABC):
    """Abstract base class for pricing providers."""

    # Shared in-memory cache for live model IDs: {cache_key: (frozenset, expires_at)}
    _live_model_cache: dict = {}

    # ISO date (YYYY-MM-DD) this provider's prices were last confirmed against its
    # published rates. Subclasses should set this whenever they touch STATIC_PRICING.
    # Models that set their own price_as_of keep it; the rest inherit this.
    PRICE_AS_OF: Optional[str] = None

    def __init__(self, provider_name: str):
        """Initialize the provider.

        Args:
            provider_name: Name of the provider
        """
        self.provider_name = provider_name

        # Subclasses set these to enable live model sync
        self._live_model_api_endpoint: Optional[str] = None
        self._live_model_api_key: Optional[str] = None
        # Override for providers using a non-Bearer auth header (e.g. Anthropic uses x-api-key)
        self._live_model_auth_header: str = "Authorization"
        self._live_model_auth_scheme: str = "Bearer"
        # Extra headers required by some APIs (e.g. Anthropic needs anthropic-version)
        self._live_model_extra_headers: dict = {}
        # Response parsing: top-level key holding the model list (None = response is the list)
        self._live_model_data_key: Optional[str] = "data"
        # Field within each model object that holds the model ID
        self._live_model_id_field: str = "id"
        # How long to cache the live model list (seconds); default 6 hours
        self._live_model_ttl_seconds: int = 21600

    @abstractmethod
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch pricing data from the provider.

        Returns:
            List of PricingMetrics for the provider's models

        Raises:
            Exception: If the provider is unreachable or returns invalid data
        """

    async def _fetch_live_model_ids(self) -> Optional[FrozenSet[str]]:
        """
        Fetch the set of model IDs currently available from the provider's API.

        Results are cached per provider for ``_live_model_ttl_seconds`` (default 6 h).
        Returns ``None`` when no endpoint is configured, no API key is available, or
        the request fails — callers should treat ``None`` as "unknown / skip filtering".

        Returns:
            Frozenset of live model ID strings, or None if unavailable
        """
        if not self._live_model_api_endpoint:
            return None

        import time
        cache_key = f"live_models_{self.provider_name}"
        now = time.monotonic()

        # Check in-memory cache
        if cache_key in BasePricingProvider._live_model_cache:
            cached_ids, expires_at = BasePricingProvider._live_model_cache[cache_key]
            if now < expires_at:
                return cached_ids

        try:
            headers: dict = dict(self._live_model_extra_headers)
            if self._live_model_api_key:
                if self._live_model_auth_header == "Authorization":
                    headers["Authorization"] = f"{self._live_model_auth_scheme} {self._live_model_api_key}"
                else:
                    headers[self._live_model_auth_header] = self._live_model_api_key

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(self._live_model_api_endpoint, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            # Extract model list from response
            if self._live_model_data_key and isinstance(data, dict):
                items = data.get(self._live_model_data_key, [])
            elif isinstance(data, list):
                items = data
            else:
                items = []

            live_ids: FrozenSet[str] = frozenset(
                item.get(self._live_model_id_field, "")
                for item in items
                if isinstance(item, dict) and item.get(self._live_model_id_field)
            )

            logger.info(
                "[%s] Live model sync: %d models available",
                self.provider_name, len(live_ids)
            )
            BasePricingProvider._live_model_cache[cache_key] = (
                live_ids, now + self._live_model_ttl_seconds
            )
            return live_ids

        except Exception as exc:
            logger.debug(
                "[%s] Live model sync skipped: %s", self.provider_name, exc
            )
            return None

    def _apply_live_filter(
        self,
        pricing_list: List[PricingMetrics],
        live_ids: FrozenSet[str],
    ) -> List[PricingMetrics]:
        """
        Filter *pricing_list* to only include models confirmed in *live_ids*.

        Matching rules (case-insensitive):
        1. Exact match: static name == live ID
        2. Prefix match: live ID starts with static name
           (handles versioned aliases, e.g. ``gpt-4o`` → ``gpt-4o-2024-11-20``)

        If the filtered list would be empty (all models unmatched), the full
        original list is returned as a safety fallback so we never serve zero
        models due to a naming-convention mismatch.

        Args:
            pricing_list: List of PricingMetrics to filter
            live_ids: Frozenset of model IDs from the live API

        Returns:
            Filtered list (or original list if filtering would empty it)
        """
        live_lower = {lid.lower() for lid in live_ids}
        kept, removed = [], []

        for model in pricing_list:
            name_lower = model.model_name.lower()
            matched = (
                name_lower in live_lower
                or any(lid.startswith(name_lower) for lid in live_lower)
            )
            if matched:
                kept.append(model)
            else:
                removed.append(model.model_name)

        if removed:
            logger.info(
                "[%s] Removed %d deprecated model(s): %s",
                self.provider_name, len(removed), removed,
            )

        # Safety: never return an empty list — fall back to unfiltered
        return kept if kept else pricing_list

    # Live model lists include non-chat artefacts. Skip anything matching these.
    _DISCOVERY_EXCLUDE = (
        "embed", "embedding", "whisper", "tts", "dall-e", "moderation",
        "rerank", "audio", "transcribe", "realtime", "image", "search",
        "codex-mini", "computer-use",
    )

    def _discover_new_models(
        self,
        pricing_list: List[PricingMetrics],
        live_ids: FrozenSet[str],
    ) -> List[PricingMetrics]:
        """Append models the provider is serving that STATIC_PRICING doesn't know about.

        The live sync used to be subtractive only: it removed retired models but had no
        way to surface newly released ones, so a hand-edit was the only way a new model
        ever appeared. Discovered models are added with ``price_confirmed=False`` and a
        zero price — they show up in the catalogue but are excluded from cost ranking,
        so a newly released model is visible without inventing a rate for it.
        """
        known = set()
        for m in pricing_list:
            name = m.model_name.lower()
            known.add(name)
            known.add(name.split(":")[-1])

        discovered = []
        for live_id in sorted(live_ids):
            lid = live_id.lower()
            if any(tok in lid for tok in self._DISCOVERY_EXCLUDE):
                continue
            # Skip if a static entry already covers it exactly or by prefix
            if lid in known or any(lid.startswith(k) or k.startswith(lid) for k in known):
                continue
            discovered.append(
                PricingMetrics(
                    model_name=live_id,
                    provider=self.provider_name,
                    cost_per_input_token=0.0,
                    cost_per_output_token=0.0,
                    currency="USD",
                    unit="per_token",
                    source=f"{self.provider_name} live model list (pricing not yet confirmed)",
                    price_confirmed=False,
                    best_for=(
                        "Discovered from the provider's live model list. Pricing has not "
                        "been confirmed, so this model is excluded from cost comparisons."
                    ),
                )
            )

        if discovered:
            logger.info(
                "[%s] Discovered %d model(s) absent from STATIC_PRICING: %s",
                self.provider_name, len(discovered), [m.model_name for m in discovered],
            )
        return pricing_list + discovered

    def _stamp_price_as_of(self, pricing_list: List[PricingMetrics]) -> List[PricingMetrics]:
        """Attach price provenance without touching each provider's builder.

        Providers construct PricingMetrics in several places apiece, so both
        provenance fields are applied here at the single choke point:

        - ``price_as_of``: falls back to the provider-level ``PRICE_AS_OF``. A model
          that sets its own keeps it, so one entry can be refreshed independently.
        - ``price_confirmed``: read from the model's ``STATIC_PRICING`` entry. Models
          marked unconfirmed are listed for discoverability but excluded from cost
          ranking, so a placeholder price can never masquerade as the cheapest option.
        """
        static = getattr(self, "STATIC_PRICING", None) or {}

        for model in pricing_list:
            if self.PRICE_AS_OF and not model.price_as_of:
                model.price_as_of = self.PRICE_AS_OF

            entry = static.get(model.model_name)
            if isinstance(entry, dict):
                if entry.get("price_confirmed") is False:
                    model.price_confirmed = False
                if entry.get("price_as_of"):
                    model.price_as_of = entry["price_as_of"]

        return pricing_list

    async def get_pricing_with_status(self) -> tuple[List[PricingMetrics], ProviderStatus]:
        """
        Fetch pricing data, apply live model filtering, and return with provider status.

        Returns:
            Tuple of (pricing_data, provider_status)
        """
        try:
            pricing_data = await self.fetch_pricing_data()

            # Apply live model filtering when an API endpoint is configured
            live_ids = await self._fetch_live_model_ids()
            if live_ids:
                pricing_data = self._apply_live_filter(pricing_data, live_ids)
                # Additive half: surface models the provider serves that we don't price yet
                pricing_data = self._discover_new_models(pricing_data, live_ids)

            # Attach price provenance so callers can judge how fresh a number is
            pricing_data = self._stamp_price_as_of(pricing_data)

            status = ProviderStatus(
                provider_name=self.provider_name,
                is_available=True,
                error_message=None
            )
            return pricing_data, status
        except Exception as e:
            status = ProviderStatus(
                provider_name=self.provider_name,
                is_available=False,
                error_message=str(e)
            )
            return [], status
