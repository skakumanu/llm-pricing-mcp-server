"""Per-key sliding-window rate limiter (moved out of src/main.py in v1.80.0).

One process-wide store, one lock. The security middleware (per-IP / per-key
buckets), the dispatch branch of POST /v1/chat/completions and the MCP ``dispatch``
tool all consume from the same store, so a customer's dispatch bucket (keyed by
customer id) is shared across transports.

src/main.py re-imports ``_rate_limit_store``, ``_rate_limit_lock`` and
``cleanup_stale_rate_limit_entries`` as the same objects. This module deliberately
imports nothing from ``src.main`` and takes the settings object explicitly, so the
caller decides which settings instance governs the tier limits.
"""
import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict

from src.services.tiers import rate_limit_for

logger = logging.getLogger(__name__)

_rate_limit_store: Dict[str, Deque[float]] = defaultdict(deque)
_rate_limit_lock = asyncio.Lock()
_last_rate_limit_cleanup = time.time()


async def cleanup_stale_rate_limit_entries():
    """Periodically remove IP entries with no recent requests to prevent memory leak."""
    global _last_rate_limit_cleanup
    now = time.time()
    if now - _last_rate_limit_cleanup > 3600:  # Cleanup every hour
        async with _rate_limit_lock:
            stale_threshold = now - 3600
            to_remove = [
                ip for ip, bucket in _rate_limit_store.items()
                if not bucket or bucket[-1] < stale_threshold
            ]
            for ip in to_remove:
                del _rate_limit_store[ip]
            if to_remove:
                logger.debug("Rate limit cleanup: removed %d stale IP entries", len(to_remove))
        _last_rate_limit_cleanup = now


async def rate_limit_allow(bucket_key: str, tier: str, cfg: Any) -> bool:
    """Consume one request from `bucket_key`'s per-minute token bucket; False when exhausted.

    The one limiter implementation: the security middleware calls it for every
    non-bypassed path, and both dispatch entry points (the HTTP dispatch branch of
    /v1/chat/completions and the MCP ``dispatch`` tool) call it directly with the
    authenticated customer's id and tier. Same store, same lock. ``cfg`` supplies the
    tier limits (rate_limit_free / _pro / _enterprise / _per_minute).
    """
    tier_limit = rate_limit_for(tier, cfg)  # the one tier -> req/min map (src/services/tiers.py)
    now = time.time()
    window_start = now - 60
    async with _rate_limit_lock:
        bucket = _rate_limit_store[bucket_key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= tier_limit:
            return False
        bucket.append(now)
    return True
