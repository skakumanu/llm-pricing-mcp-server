"""Tests for RecommendationCache (src/services/recommendation_cache.py)."""
import sys
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.recommendation_cache import (  # noqa: E402
    DEFAULT_TTL_SECONDS,
    RecommendationCache,
    _normalize_key,
)
from src.services.router import RouterConstraints, RouterResult  # noqa: E402


def _result(name="gpt-4o-mini", score=1.0, reason="because", alts=None):
    return RouterResult(
        recommended=name,
        score=score,
        reason=reason,
        alternatives=alts or [],
    )


def _counting_compute(result):
    """Return an async compute() that returns `result` and counts its calls."""
    calls = {"n": 0}

    async def compute():
        calls["n"] += 1
        return result

    return compute, calls


# ---------------------------------------------------------------------------
# Cache hit within TTL (acceptance criterion 1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_call_within_ttl_is_served_from_cache():
    cache = RecommendationCache()
    constraints = RouterConstraints(max_cost_per_1m_tokens=5.0)
    fresh = _result()
    compute, calls = _counting_compute(fresh)

    first = await cache.get_or_compute(constraints, compute)
    second = await cache.get_or_compute(constraints, compute)

    assert calls["n"] == 1  # compute only ran once
    assert first.cached is False
    assert second.cached is True
    assert second.recommended == first.recommended
    assert second.score == first.score
    assert second.reason == first.reason
    assert second.alternatives == first.alternatives


@pytest.mark.asyncio
async def test_cache_does_not_mutate_stored_entry():
    """A cache hit must not flip `cached` on the stored entry itself."""
    cache = RecommendationCache()
    constraints = RouterConstraints()
    fresh = _result()
    compute, _ = _counting_compute(fresh)

    await cache.get_or_compute(constraints, compute)
    await cache.get_or_compute(constraints, compute)
    third = await cache.get_or_compute(constraints, compute)

    assert third.cached is True
    assert fresh.cached is False  # original object untouched


# ---------------------------------------------------------------------------
# TTL expiry (acceptance criterion 2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_after_ttl_expiry_is_recomputed():
    cache = RecommendationCache(ttl_seconds=300)
    constraints = RouterConstraints(min_quality_score=80)
    compute, calls = _counting_compute(_result())

    first = await cache.get_or_compute(constraints, compute)
    assert first.cached is False
    assert calls["n"] == 1

    # Simulate the 5-minute freshness window having elapsed.
    key = _normalize_key(constraints)
    cache._entries[key].created_at = time.time() - 301

    second = await cache.get_or_compute(constraints, compute)
    assert calls["n"] == 2  # recomputed, not served stale
    assert second.cached is False


@pytest.mark.asyncio
async def test_call_just_under_ttl_is_still_cached():
    cache = RecommendationCache(ttl_seconds=300)
    constraints = RouterConstraints(min_quality_score=80)
    compute, calls = _counting_compute(_result())

    await cache.get_or_compute(constraints, compute)
    key = _normalize_key(constraints)
    cache._entries[key].created_at = time.time() - 299

    second = await cache.get_or_compute(constraints, compute)
    assert calls["n"] == 1
    assert second.cached is True


# ---------------------------------------------------------------------------
# No cross-request leakage (acceptance criterion 3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_different_constraints_are_cached_independently():
    cache = RecommendationCache()
    c1 = RouterConstraints(max_cost_per_1m_tokens=5.0)
    c2 = RouterConstraints(max_cost_per_1m_tokens=10.0)
    compute1, calls1 = _counting_compute(_result(name="model-a"))
    compute2, calls2 = _counting_compute(_result(name="model-b"))

    r1 = await cache.get_or_compute(c1, compute1)
    r2 = await cache.get_or_compute(c2, compute2)

    assert r1.recommended == "model-a"
    assert r2.recommended == "model-b"

    # Repeating each should hit its own entry only.
    r1_again = await cache.get_or_compute(c1, compute1)
    r2_again = await cache.get_or_compute(c2, compute2)
    assert calls1["n"] == 1
    assert calls2["n"] == 1
    assert r1_again.cached is True
    assert r2_again.cached is True


@pytest.mark.asyncio
async def test_provider_casing_normalizes_to_same_key():
    """preferred_provider='OpenAI' and 'openai' must hit the same cache entry,
    since the router already treats them as equivalent (case-insensitive)."""
    cache = RecommendationCache()
    c1 = RouterConstraints(preferred_provider="OpenAI")
    c2 = RouterConstraints(preferred_provider="openai")
    compute, calls = _counting_compute(_result())

    await cache.get_or_compute(c1, compute)
    second = await cache.get_or_compute(c2, compute)

    assert calls["n"] == 1
    assert second.cached is True


# ---------------------------------------------------------------------------
# None (no-match) results are never cached
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_none_result_is_never_cached():
    cache = RecommendationCache()
    constraints = RouterConstraints(min_quality_score=99.9)
    compute, calls = _counting_compute(None)

    first = await cache.get_or_compute(constraints, compute)
    second = await cache.get_or_compute(constraints, compute)

    assert first is None
    assert second is None
    assert calls["n"] == 2  # recomputed both times, never cached as a miss


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clear_removes_all_entries():
    cache = RecommendationCache()
    constraints = RouterConstraints()
    compute, calls = _counting_compute(_result())

    await cache.get_or_compute(constraints, compute)
    cache.clear()
    await cache.get_or_compute(constraints, compute)

    assert calls["n"] == 2


def test_default_ttl_is_five_minutes():
    assert DEFAULT_TTL_SECONDS == 300


# ---------------------------------------------------------------------------
# Bounded size: memory cannot grow without bound (Review finding fix)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_size_is_bounded_by_max_entries():
    """Once at capacity, inserting a new distinct entry must not grow the
    backing dict past max_entries — the oldest entry is evicted instead."""
    cache = RecommendationCache(max_entries=3)

    for i in range(5):
        constraints = RouterConstraints(avg_input_tokens=i)
        compute, _ = _counting_compute(_result(name=f"model-{i}"))
        await cache.get_or_compute(constraints, compute)
        assert len(cache._entries) <= 3


@pytest.mark.asyncio
async def test_expired_entries_are_purged_on_write_not_just_lazily():
    """Expired entries must be reclaimed even if their exact key is never
    looked up again, so a long-running process doesn't accumulate them."""
    cache = RecommendationCache(ttl_seconds=300)
    c1 = RouterConstraints(avg_input_tokens=1)
    compute1, _ = _counting_compute(_result(name="model-a"))
    await cache.get_or_compute(c1, compute1)

    # Expire the first entry without ever looking it up again.
    key1 = _normalize_key(c1)
    cache._entries[key1].created_at = time.time() - 301

    c2 = RouterConstraints(avg_input_tokens=2)
    compute2, _ = _counting_compute(_result(name="model-b"))
    await cache.get_or_compute(c2, compute2)

    assert key1 not in cache._entries
    assert len(cache._entries) == 1


@pytest.mark.asyncio
async def test_normalize_key_covers_every_dataclass_field():
    """Guards against the field list silently drifting out of sync with
    RouterConstraints: two constraints differing only in one field must
    never hash to the same key."""
    import dataclasses as dc

    base = RouterConstraints()
    for f in dc.fields(RouterConstraints):
        if f.type in ("bool", bool):
            varied = dc.replace(base, **{f.name: not getattr(base, f.name)})
        elif f.name in ("estimated_monthly_requests", "avg_input_tokens", "avg_output_tokens"):
            varied = dc.replace(base, **{f.name: getattr(base, f.name) + 1})
        else:
            continue
        assert _normalize_key(base) != _normalize_key(varied), (
            f"varying field {f.name!r} did not change the cache key"
        )
