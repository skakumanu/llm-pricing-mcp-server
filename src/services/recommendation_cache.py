"""In-memory TTL cache for model-recommendation decisions.

Wraps a compute callback (typically ``ModelRouter.get_optimal_model``) so
that identical, or normalized-equivalent, recommendation requests made
within a short freshness window return the same result without recomputing
the full routing decision. Wired into exactly ``recommend_model`` (MCP tool)
and ``POST /router/recommend`` — ``ModelRouter.get_optimal_model()`` itself
is untouched, so ``POST /router/recommend/stream`` and ``/v1/chat/completions``
are unaffected.

This follows the same TTL-cache shape as the two existing precedents in this
codebase — ``DataFetcher.fetch_with_cache`` (src/services/data_fetcher.py)
and ``benchmark_service._hf_cache`` — but applies it to the routing
*decision* itself rather than an underlying data fetch. Each call site
(the ``recommend_model`` tool, the ``/router/recommend`` endpoint) owns its
own ``RecommendationCache`` instance, so a cache hit in one never leaks into
the other.

In-memory only: not persisted across restarts, not shared across processes.
There is no caller-facing way to bypass or invalidate the cache on demand
(see Spec non-goals) — entries simply expire after ``ttl_seconds``. Each
instance is also bounded to ``max_entries`` (default ``DEFAULT_MAX_ENTRIES``)
distinct entries: expired entries are purged opportunistically on every
write, and once at capacity the least-recently-inserted/accessed entry is
evicted to make room, so memory usage cannot grow without bound even under
sustained traffic with many distinct constraint combinations.
"""
import dataclasses
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from src.services.router import RouterConstraints, RouterResult

DEFAULT_TTL_SECONDS = 300  # 5 minutes

# Upper bound on the number of distinct cache entries a single
# RecommendationCache instance will hold. Without a cap, a caller who varies
# any RouterConstraints field on every request (deliberately or not) would
# grow the backing dict without bound for the lifetime of the process, since
# expired entries were previously only ever purged lazily on a matching-key
# lookup that might never come. See Review finding on unbounded cache growth.
DEFAULT_MAX_ENTRIES = 1000


def _normalize_key(constraints: RouterConstraints) -> str:
    """Build a stable cache key from normalized ``RouterConstraints`` fields.

    String fields are lower-cased so that e.g. ``preferred_provider="OpenAI"``
    and ``preferred_provider="openai"`` hash to the same key — matching how
    the router itself already compares provider/task-type/ide-context
    strings case-insensitively.

    The field list is derived from ``dataclasses.fields(constraints)`` rather
    than hand-enumerated, so that a future field added to ``RouterConstraints``
    is automatically included in the key — two requests differing only in a
    new field can never silently collide and share a stale cache entry.
    """
    parts = []
    for f in dataclasses.fields(constraints):
        value = getattr(constraints, f.name)
        if isinstance(value, str):
            value = value.lower()
        parts.append(value)
    raw = "|".join(repr(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class _CachedRecommendation:
    """A stored ``RouterResult`` plus the bookkeeping needed to expire it."""

    result: RouterResult
    created_at: float
    ttl_seconds: float

    def is_valid(self) -> bool:
        return (time.time() - self.created_at) < self.ttl_seconds


class RecommendationCache:
    """Per-call-site in-memory TTL cache for recommendation decisions.

    Usage::

        cache = RecommendationCache()
        result = await cache.get_or_compute(
            constraints, lambda: router.get_optimal_model(constraints)
        )
    """

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: "OrderedDict[str, _CachedRecommendation]" = OrderedDict()

    async def get_or_compute(
        self,
        constraints: RouterConstraints,
        compute: Callable[[], Awaitable[Optional[RouterResult]]],
    ) -> Optional[RouterResult]:
        """Return a fresh-enough cached result, or compute and store a new one.

        Only successful (non-``None``) results are cached; a ``None`` result
        (no model matched the constraints) is always recomputed on the next
        call rather than being cached as a persistent miss.
        """
        key = _normalize_key(constraints)
        entry = self._entries.get(key)
        if entry is not None and entry.is_valid():
            self._entries.move_to_end(key)
            return dataclasses.replace(entry.result, cached=True)

        result = await compute()
        if result is not None:
            self._evict_expired()
            while len(self._entries) >= self._max_entries:
                # Bound memory: evict the least-recently-inserted/accessed
                # entry rather than growing without limit. This is a
                # best-effort safety valve, not a precision LRU.
                self._entries.popitem(last=False)
            self._entries[key] = _CachedRecommendation(
                result=result,
                created_at=time.time(),
                ttl_seconds=self._ttl_seconds,
            )
        return result

    def _evict_expired(self) -> None:
        """Drop all entries whose TTL has already elapsed.

        Called opportunistically on every write so that a long-running
        process never accumulates expired entries indefinitely, even if
        their exact key is never looked up again.
        """
        expired_keys = [k for k, v in self._entries.items() if not v.is_valid()]
        for k in expired_keys:
            del self._entries[k]

    def clear(self) -> None:
        """Remove all cached entries.

        Internal/test helper only — the Spec explicitly excludes a
        caller-facing way to force-bypass or force-invalidate the cache.
        """
        self._entries.clear()
