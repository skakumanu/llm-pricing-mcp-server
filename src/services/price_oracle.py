"""External price oracle: fills missing prices and detects drift in curated ones.

Why this exists
---------------
``STATIC_PRICING`` is hand-maintained, and two problems follow from that:

1. Newly released models have no price at all, so they ship with
   ``price_confirmed=False`` and are excluded from every cost calculation.
2. Curated prices silently go stale. A cross-check against the reference registry
   found 15 of 54 matched models drifted more than 5%, several by 3-5x — and a
   wrong price is worse than a missing one, because it looks authoritative.

This service reads a community-maintained, machine-readable price registry and:

- **Fills gaps.** A model with no confirmed price gets one, marked with its source
  and fetch date. Strictly better than the 0.0 placeholder it replaces.
- **Flags drift.** Where the registry disagrees with a curated price it reports the
  difference but never overwrites. Curation stays authoritative; corrections are a
  human decision.

Availability
------------
Fetched on a TTL and cached in memory. On any failure the vendored snapshot in
``data/model_prices_snapshot.json`` is used, so a network outage or blocked egress
degrades freshness but never pricing itself.
"""
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

REGISTRY_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "data" / "model_prices_snapshot.json"

DEFAULT_TTL_SECONDS = 86_400  # 24h
DRIFT_THRESHOLD_PCT = 5.0


@dataclass
class PriceRecord:
    """A per-token price from the registry, normalised to USD per 1k tokens."""

    model_key: str
    input_per_1k: float
    output_per_1k: float
    cache_read_per_1k: Optional[float]
    context_window: Optional[int]
    supports_vision: bool
    supports_function_calling: bool
    provider: Optional[str]


@dataclass
class DriftFinding:
    """A curated price that disagrees with the registry."""

    model_name: str
    provider: str
    ours_per_1m: float
    registry_per_1m: float
    pct_difference: float
    direction: str  # "overstated" | "understated"
    price_as_of: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "provider": self.provider,
            "our_input_cost_per_1m_usd": round(self.ours_per_1m, 4),
            "registry_input_cost_per_1m_usd": round(self.registry_per_1m, 4),
            "pct_difference": round(self.pct_difference, 1),
            "direction": self.direction,
            "our_price_as_of": self.price_as_of,
        }


class PriceOracle:
    """Loads the reference registry and answers price lookups against it."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._records: Dict[str, PriceRecord] = {}
        self._fetched_at: Optional[datetime] = None
        self._source: Optional[str] = None

    # -- loading -----------------------------------------------------------

    def _is_fresh(self) -> bool:
        if not self._records or self._fetched_at is None:
            return False
        return (datetime.now(UTC) - self._fetched_at).total_seconds() < self._ttl

    async def load(self, force: bool = False) -> None:
        """Populate the registry, preferring a live fetch and falling back to the snapshot."""
        if self._is_fresh() and not force:
            return

        raw = await self._fetch_remote()
        source = REGISTRY_URL
        if raw is None:
            raw = self._read_snapshot()
            source = str(SNAPSHOT_PATH)

        if not raw:
            logger.warning("Price oracle unavailable: no remote data and no usable snapshot")
            return

        self._records = self._parse(raw)
        self._fetched_at = datetime.now(UTC)
        self._source = source
        logger.info("Price oracle loaded %d model(s) from %s", len(self._records), source)

    async def _fetch_remote(self) -> Optional[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(REGISTRY_URL)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning("Price registry fetch failed (%s); falling back to snapshot", e)
            return None

    def _read_snapshot(self) -> Optional[Dict[str, Any]]:
        try:
            with SNAPSHOT_PATH.open(encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as e:
            logger.warning("Price snapshot unreadable at %s: %s", SNAPSHOT_PATH, e)
            return None

    @staticmethod
    def _parse(raw: Dict[str, Any]) -> Dict[str, PriceRecord]:
        out: Dict[str, PriceRecord] = {}
        for key, rec in raw.items():
            if not isinstance(rec, dict):
                continue
            if rec.get("mode") not in (None, "chat"):
                continue
            inp = rec.get("input_cost_per_token")
            outp = rec.get("output_cost_per_token")
            if inp is None or outp is None:
                continue
            cache = rec.get("cache_read_input_token_cost")
            out[key.lower()] = PriceRecord(
                model_key=key,
                # registry is USD per token; our convention is USD per 1k tokens
                input_per_1k=float(inp) * 1000,
                output_per_1k=float(outp) * 1000,
                cache_read_per_1k=float(cache) * 1000 if cache is not None else None,
                context_window=rec.get("max_input_tokens"),
                supports_vision=bool(rec.get("supports_vision", False)),
                supports_function_calling=bool(rec.get("supports_function_calling", False)),
                provider=rec.get("litellm_provider"),
            )
        return out

    # -- lookup ------------------------------------------------------------

    def lookup(self, model_name: str) -> Optional[PriceRecord]:
        """Find a registry price for a model name.

        Registry keys are sometimes bare (``claude-sonnet-5``) and sometimes
        namespaced (``anthropic/claude-sonnet-5``, ``us.anthropic.claude-opus-5``),
        so try the bare name first and then any key whose final segment matches.
        """
        if not model_name:
            return None
        name = model_name.lower()

        exact = self._records.get(name)
        if exact:
            return exact

        for key, rec in self._records.items():
            tail = key.rsplit("/", 1)[-1].rsplit(".", 1)[-1]
            if tail == name:
                return rec
        return None

    @property
    def loaded(self) -> bool:
        return bool(self._records)

    @property
    def model_count(self) -> int:
        return len(self._records)

    @property
    def source(self) -> Optional[str]:
        return self._source

    @property
    def fetched_at(self) -> Optional[str]:
        return self._fetched_at.date().isoformat() if self._fetched_at else None

    # -- gap filling -------------------------------------------------------

    def fill_missing_prices(self, models: List[Any]) -> int:
        """Give unconfirmed models a real price from the registry.

        Only touches models that have no confirmed price — a curated price is never
        overwritten here. Returns how many were filled.
        """
        if not self.loaded:
            return 0

        filled = 0
        for m in models:
            if getattr(m, "price_confirmed", True):
                continue
            rec = self.lookup(m.model_name)
            if rec is None:
                continue

            m.cost_per_input_token = rec.input_per_1k / 1000
            m.cost_per_output_token = rec.output_per_1k / 1000
            if rec.context_window and not m.context_window:
                m.context_window = rec.context_window
            if rec.supports_vision:
                m.supports_vision = True
            if rec.supports_function_calling:
                m.supports_function_calling = True
            m.price_confirmed = True
            m.price_as_of = self.fetched_at
            m.source = f"LiteLLM price registry ({rec.model_key})"
            filled += 1

        if filled:
            logger.info("Price oracle filled %d previously unpriced model(s)", filled)
        return filled

    # -- drift detection ---------------------------------------------------

    def find_drift(
        self, models: List[Any], threshold_pct: float = DRIFT_THRESHOLD_PCT
    ) -> List[DriftFinding]:
        """Report curated prices that disagree with the registry. Never mutates."""
        if not self.loaded:
            return []

        findings: List[DriftFinding] = []
        for m in models:
            if not getattr(m, "price_confirmed", True):
                continue
            # Skip anything already sourced from the registry — comparing it to
            # itself would report every filled model as drift-free noise.
            if (m.source or "").startswith("LiteLLM price registry"):
                continue

            rec = self.lookup(m.model_name)
            if rec is None:
                continue

            ours = m.cost_per_input_token * 1_000_000
            theirs = rec.input_per_1k / 1000 * 1_000_000
            if theirs <= 0 or ours <= 0:
                continue

            pct = (ours - theirs) / theirs * 100
            if abs(pct) < threshold_pct:
                continue

            findings.append(DriftFinding(
                model_name=m.model_name,
                provider=m.provider,
                ours_per_1m=ours,
                registry_per_1m=theirs,
                pct_difference=abs(pct),
                direction="overstated" if pct > 0 else "understated",
                price_as_of=getattr(m, "price_as_of", None),
            ))

        findings.sort(key=lambda f: f.pct_difference, reverse=True)
        return findings


# ---- Singleton wiring -------------------------------------------------------

_oracle: Optional[PriceOracle] = None


def get_price_oracle() -> PriceOracle:
    """Return the process-wide PriceOracle, creating it on first use."""
    global _oracle
    if _oracle is None:
        _oracle = PriceOracle()
    return _oracle


def reset_price_oracle() -> None:
    """Drop the singleton. Used by tests to avoid cross-test cache bleed."""
    global _oracle
    _oracle = None
