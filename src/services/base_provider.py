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
