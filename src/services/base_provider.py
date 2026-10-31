"""Base provider interface for pricing services."""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, FrozenSet
from dataclasses import dataclass
import json
import logging
import httpx
from src.models.pricing import PricingMetrics
from src.services.price_oracle import registry_source_label

logger = logging.getLogger(__name__)

# Registry-sourced entries live here, one generated JSON file per provider
# (``<slug>.json``, slug = provider name lowercased, spaces -> "_", dots
# dropped), written by scripts/pickup_registry_models.py and merged into each
# provider's catalogue at fetch time. They are kept out of STATIC_PRICING on
# purpose: a curated table is a human's statement about a provider's rate card,
# and these are the registry's.
REGISTRY_MODELS_DIR = Path(__file__).resolve().parents[2] / "data" / "registry_models"


def registry_models_dir() -> Path:
    """Runtime location of the registry-sourced model files.

    ``settings.registry_models_dir`` overrides the repo path. On Fly the persistent volume is
    mounted over ``/app/data`` and hides the image's copy; before v1.84.1 that silently dropped
    every registry-sourced model from production (273 models served instead of 600+).
    """
    try:
        from src.config.settings import settings
        if settings.registry_models_dir:
            return Path(settings.registry_models_dir)
    except Exception:  # nosec B110 — settings unavailable (scripts, partial imports)
        pass
    return REGISTRY_MODELS_DIR


def registry_models_inventory(base: Optional[Path] = None) -> Dict[str, object]:
    """What the runtime can see: directory, whether it exists, files and model count.

    Logged at startup so a deployment that cannot see its registry-sourced models says so
    instead of quietly serving a smaller catalogue.
    """
    base = Path(base or registry_models_dir())
    files = sorted(base.glob("*.json")) if base.is_dir() else []
    models = 0
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
            models += len(doc.get("models", {})) if isinstance(doc, dict) else 0
        except Exception:  # nosec B110 — a broken file is reported as zero models
            pass
    return {"dir": str(base), "exists": base.is_dir(), "files": len(files), "models": models}


def registry_models_slug(provider_name: str) -> str:
    """File stem for a provider's registry-sourced models JSON."""
    return provider_name.lower().replace(" ", "_").replace(".", "")


def registry_models_path(provider_name: str, out_dir: Optional[Path] = None) -> Path:
    """Where a provider's registry-sourced models JSON lives (it may not exist yet)."""
    return Path(out_dir or registry_models_dir()) / f"{registry_models_slug(provider_name)}.json"


def render_registry_models_doc(doc: dict) -> bytes:
    """The one on-disk encoding of a registry-models document.

    One field per line, sorted keys, trailing newline: tiny diffs and
    byte-identical idempotency. Every writer (the weekly pickup, the retire
    job) and the byte-stability test go through this so a format change cannot
    leave two jobs rewriting the same file back and forth.
    """
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


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

    # Where this provider's registry-sourced models JSON lives. Class-level so a
    # test can point a stub provider at a tmp_path; the pickup script sets it
    # per instance when ``--out-dir`` differs from the default.
    REGISTRY_MODELS_DIR: Path = REGISTRY_MODELS_DIR

    # When set, every registry-sourced model this provider merges gets this as
    # its ``best_for`` text — the place for a caveat a customer must see next
    # to the number (Databricks bills in DBUs, so a USD figure is indicative).
    REGISTRY_PRICE_NOTE: Optional[str] = None

    # {resolved JSON path: (mtime_ns, models dict)} — re-read when the file
    # changes, so the weekly pickup's rewrite is picked up without a restart.
    _registry_models_cache: Dict[str, tuple] = {}

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

    def build_metrics(
        self,
        model_name: str,
        info: dict,
        source: str,
        *,
        cost_per_input_token: Optional[float] = None,
        cost_per_output_token: Optional[float] = None,
        throughput: Optional[float] = None,
        latency_ms: Optional[float] = None,
        provider: Optional[str] = None,
        **extra,
    ) -> PricingMetrics:
        """Build a PricingMetrics from a STATIC_PRICING entry.

        Every provider used to construct PricingMetrics inline, two or three times
        apiece — 51 near-identical call sites. Adding one field to the model meant
        editing all of them, and missing some was silent: ``price_confirmed`` was
        dropped by 24 sites, so a $0.00 placeholder came back marked *confirmed*
        and sorted as the cheapest option. This is the one place that mapping
        lives now.

        Args:
            model_name: Model identifier.
            info: The model's ``STATIC_PRICING`` entry. Prices in it are per 1k
                tokens, matching the file format.
            source: Where this price came from, for display.
            cost_per_input_token / cost_per_output_token: Per-token costs, already
                converted. Pass these when the price came from a live API; omit to
                derive them from *info*.
            throughput / latency_ms: Provider performance defaults.
            provider: Overrides ``self.provider_name``.
            **extra: Any other PricingMetrics field, for the rare special case.
        """
        if cost_per_input_token is None:
            cost_per_input_token = info.get("input", 0.0) / 1000
        if cost_per_output_token is None:
            cost_per_output_token = info.get("output", 0.0) / 1000

        fields = dict(
            model_name=model_name,
            provider=provider or self.provider_name,
            cost_per_input_token=cost_per_input_token,
            cost_per_output_token=cost_per_output_token,
            context_window=info.get("context_window"),
            currency="USD",
            unit="per_token",
            source=source,
            throughput=throughput,
            latency_ms=latency_ms,
            use_cases=info.get("use_cases"),
            strengths=info.get("strengths"),
            best_for=info.get("best_for"),
            supports_vision=info.get("supports_vision", False),
            supports_function_calling=info.get("supports_function_calling", False),
            supports_json_mode=info.get("supports_json_mode", False),
            batch_available=info.get("batch_available", False),
            is_reasoning_model=info.get("is_reasoning_model", False),
            # Provenance travels with the data rather than being stamped later, so
            # it survives callers that bypass get_pricing_with_status().
            price_confirmed=info.get("price_confirmed", True),
            # v1.75.0: an entry may declare that its number came from the
            # reference registry rather than the provider's rate card.
            price_source_kind=info.get("price_source_kind", "provider"),
            registry_key=info.get("registry_key"),
        )
        if info.get("price_as_of"):
            fields["price_as_of"] = info["price_as_of"]
        fields.update(extra)
        return PricingMetrics(**fields)

    @staticmethod
    def source_for(model_name: str, info: dict, curated_source: str) -> str:
        """Display ``source`` for one STATIC_PRICING entry.

        An entry that declares ``price_source_kind: "registry"`` (a core number
        that could not be verified against the rate card and was taken from the
        registry instead) names the registry and its key, exactly as a merged
        data/registry_models entry or a runtime gap-fill does; anything else is
        the provider's rate card.
        """
        if info.get("price_source_kind") == "registry":
            return registry_source_label(info.get("registry_key") or model_name)
        return curated_source

    # -- registry-sourced models (data/registry_models/<slug>.json) --------------

    def _registry_models_path(self) -> Path:
        base = self.REGISTRY_MODELS_DIR
        if base == REGISTRY_MODELS_DIR:  # untouched default: honour the deployment override
            base = registry_models_dir()
        return registry_models_path(self.provider_name, Path(base))

    def _load_registry_models(self) -> Dict[str, dict]:
        """The provider's registry-sourced models, ``{name: entry}``; ``{}`` when absent.

        Entries use the STATIC_PRICING per-1k convention plus
        ``price_source_kind``/``registry_key``/``price_as_of``, so they go
        through ``build_metrics`` unchanged.
        """
        path = self._registry_models_path()
        try:
            stat = path.stat()
        except OSError:
            return {}
        key = str(path)
        cached = BasePricingProvider._registry_models_cache.get(key)
        if cached and cached[0] == stat.st_mtime_ns:
            return cached[1]
        try:
            with path.open(encoding="utf-8") as fh:
                doc = json.load(fh)
            models = doc.get("models", {}) if isinstance(doc, dict) else {}
            models = {k: v for k, v in models.items() if isinstance(v, dict)}
        except Exception as exc:
            logger.warning("[%s] registry models file unreadable at %s: %s", self.provider_name, path, exc)
            return {}
        BasePricingProvider._registry_models_cache[key] = (stat.st_mtime_ns, models)
        return models

    def _merge_registry_models(self, pricing_list: List[PricingMetrics]) -> List[PricingMetrics]:
        """Append registry-sourced models the curated table does not already list.

        A curated STATIC_PRICING entry always wins a name collision (case-
        insensitive): the human's statement about the rate card outranks the
        registry's. Everything merged here carries ``price_source_kind="registry"``
        and its ``registry_key`` so consumers can tell the two apart.
        """
        registry = self._load_registry_models()
        if not registry:
            return pricing_list
        present = {m.model_name.lower() for m in pricing_list}
        merged = []
        skipped = 0
        for name, info in registry.items():
            if name.lower() in present:
                continue
            entry = dict(info)
            entry.setdefault("price_source_kind", "registry")
            if self.REGISTRY_PRICE_NOTE and not entry.get("best_for"):
                entry["best_for"] = self.REGISTRY_PRICE_NOTE
            try:
                metrics = self.build_metrics(
                    name, entry,
                    source=registry_source_label(entry.get("registry_key") or name),
                    provider=self.provider_name,
                )
            except Exception as exc:
                # One bad row (a hand edit, a merge-conflict leftover) must not take
                # the curated core and every valid registry entry down with it:
                # get_pricing_with_status() would otherwise report the provider
                # unavailable with an empty catalogue.
                skipped += 1
                logger.warning("[%s] skipping malformed registry-sourced entry %r: %s",
                               self.provider_name, name, exc)
                continue
            merged.append(metrics)
            present.add(name.lower())
        if merged or skipped:
            logger.debug("[%s] merged %d registry-sourced model(s), skipped %d malformed",
                         self.provider_name, len(merged), skipped)
        return pricing_list + merged

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

            # Registry-sourced models (v1.75.0) join the curated table before the
            # live sync runs, so they are filtered and discovered against like
            # any other entry; curated names always win a collision.
            pricing_data = self._merge_registry_models(pricing_data)

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
