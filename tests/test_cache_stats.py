"""Tests for recommendation-cache observability:

- ``get_cache_stats()`` (src/services/recommendation_cache.py) — the pure
  function computing hit/miss/total/hit_rate per call site.
- The ``get_cache_stats`` MCP tool (mcp/tools/get_cache_stats.py).
- The ``GET /cache-stats`` REST endpoint (src/main.py).

Covers the Spec's acceptance criteria: fresh-process zero state, hit/miss
counting under repeated vs. distinct requests, per-call-site isolation
(recommend_model vs. router_recommend, never combined), read-only stats
access, and that the existing per-response ``cached`` field is unaffected.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.recommendation_cache import (  # noqa: E402
    RecommendationCache,
    get_cache_stats,
    _stats,
)
from src.services.router import RouterConstraints, RouterResult  # noqa: E402
from mcp.tools.get_cache_stats import GetCacheStatsTool  # noqa: E402
from mcp.tools.recommend_model import RecommendModelTool  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_stats():
    """Reset the module-level registry to a fresh-process state (all zero)
    before each test, and restore whatever was there afterward, so tests in
    this file are isolated from each other and from any other test module
    that happens to exercise a real, named RecommendationCache elsewhere in
    the same pytest session (e.g. test_recommend_model_tool.py)."""
    snapshot = {name: dict(counts) for name, counts in _stats.items()}
    for counts in _stats.values():
        counts["hits"] = 0
        counts["misses"] = 0
    yield
    for name, counts in snapshot.items():
        _stats[name].update(counts)


def _result(name="gpt-4o-mini"):
    return RouterResult(recommended=name, score=1.0, reason="because", alternatives=[])


def _pm(name, provider, inp, out, quality=None, context=None):
    m = PricingMetrics(
        model_name=name, provider=provider,
        cost_per_input_token=inp, cost_per_output_token=out,
        context_window=context,
    )
    m.quality_score = quality
    return m


# ---------------------------------------------------------------------------
# get_cache_stats() — fresh state (acceptance criterion 1)
# ---------------------------------------------------------------------------

class TestGetCacheStatsFunction:
    def test_fresh_process_reports_zero_hits_and_misses_for_both_sites(self):
        stats = get_cache_stats()
        assert set(stats.keys()) == {"recommend_model", "router_recommend"}
        for name in ("recommend_model", "router_recommend"):
            assert stats[name]["hits"] == 0
            assert stats[name]["misses"] == 0
            assert stats[name]["total"] == 0
            assert stats[name]["hit_rate"] == 0.0

    # -----------------------------------------------------------------
    # N distinct + N repeated -> hit rate 50% (acceptance criterion 2)
    # -----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_n_distinct_then_n_repeated_yields_fifty_percent_hit_rate(self):
        cache = RecommendationCache(name="recommend_model")
        n = 4
        constraints_list = [RouterConstraints(avg_input_tokens=i) for i in range(n)]

        # N distinct requests -> N misses.
        for i, c in enumerate(constraints_list):
            compute = AsyncMock(return_value=_result(f"model-{i}"))
            await cache.get_or_compute(c, compute)

        # Same N requests repeated within the freshness window -> N hits.
        for c in constraints_list:
            compute = AsyncMock(return_value=_result("should-not-be-used"))
            await cache.get_or_compute(c, compute)

        stats = get_cache_stats()["recommend_model"]
        assert stats["misses"] == n
        assert stats["hits"] == n
        assert stats["total"] == 2 * n
        assert stats["hit_rate"] == 50.0

    def test_hit_rate_matches_get_data_quality_rounding_convention(self):
        # 2 hits, 1 miss -> 66.7%, rounded to one decimal like confirmed_pct.
        _stats["recommend_model"]["hits"] = 2
        _stats["recommend_model"]["misses"] = 1
        stats = get_cache_stats()["recommend_model"]
        assert stats["total"] == 3
        assert stats["hit_rate"] == pytest.approx(66.7)

    # -----------------------------------------------------------------
    # Per-call-site isolation (acceptance criteria 3 & 4)
    # -----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_counts_are_reported_separately_never_combined(self):
        rm_cache = RecommendationCache(name="recommend_model")
        rr_cache = RecommendationCache(name="router_recommend")

        constraints = RouterConstraints()
        await rm_cache.get_or_compute(constraints, AsyncMock(return_value=_result()))
        await rm_cache.get_or_compute(constraints, AsyncMock(return_value=_result()))  # hit

        stats = get_cache_stats()
        assert stats["recommend_model"]["hits"] == 1
        assert stats["recommend_model"]["misses"] == 1
        # router_recommend must be completely untouched by recommend_model traffic.
        assert stats["router_recommend"]["hits"] == 0
        assert stats["router_recommend"]["misses"] == 0
        # No combined/global figure anywhere in the response.
        assert "total_hits" not in stats
        assert "combined" not in stats
        assert set(stats.keys()) == {"recommend_model", "router_recommend"}

        # And the reverse: router_recommend traffic never leaks into recommend_model.
        await rr_cache.get_or_compute(constraints, AsyncMock(return_value=_result()))
        stats = get_cache_stats()
        assert stats["recommend_model"]["hits"] == 1  # unchanged
        assert stats["recommend_model"]["misses"] == 1  # unchanged
        assert stats["router_recommend"]["misses"] == 1

    @pytest.mark.asyncio
    async def test_unnamed_cache_instance_never_records_into_registry(self):
        """A caller that doesn't opt in (name=None, the default) must not
        pollute either named call site's counters — matches every existing
        RecommendationCache() usage in test_recommendation_cache.py."""
        cache = RecommendationCache()
        constraints = RouterConstraints()
        await cache.get_or_compute(constraints, AsyncMock(return_value=_result()))
        await cache.get_or_compute(constraints, AsyncMock(return_value=_result()))

        stats = get_cache_stats()
        assert stats["recommend_model"]["total"] == 0
        assert stats["router_recommend"]["total"] == 0

    # -----------------------------------------------------------------
    # Read-only (acceptance criterion: reading never alters counts)
    # -----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_reading_stats_does_not_itself_alter_counts(self):
        cache = RecommendationCache(name="recommend_model")
        constraints = RouterConstraints()
        await cache.get_or_compute(constraints, AsyncMock(return_value=_result()))

        first_read = get_cache_stats()
        second_read = get_cache_stats()
        third_read = get_cache_stats()
        assert first_read == second_read == third_read

    @pytest.mark.asyncio
    async def test_none_result_is_still_counted_as_a_miss_every_time(self):
        """A no-match (None) result is never cached (existing behavior,
        test_recommendation_cache.py) — so it recomputes, and therefore
        misses, on every call. Stats must reflect that honestly rather than
        masking it as a hit."""
        cache = RecommendationCache(name="recommend_model")
        constraints = RouterConstraints(min_quality_score=99.9)
        compute = AsyncMock(return_value=None)

        await cache.get_or_compute(constraints, compute)
        await cache.get_or_compute(constraints, compute)

        stats = get_cache_stats()["recommend_model"]
        assert stats["misses"] == 2
        assert stats["hits"] == 0


# ---------------------------------------------------------------------------
# GetCacheStatsTool (MCP tool)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetCacheStatsTool:
    async def test_returns_success_with_both_call_sites(self):
        tool = GetCacheStatsTool()
        result = await tool.execute({})

        assert result["success"] is True
        for name in ("recommend_model", "router_recommend"):
            assert name in result
            for field in ("hits", "misses", "total", "hit_rate"):
                assert field in result[name], f"{name} missing {field}"

    async def test_fresh_state_via_tool_is_zero_not_error(self):
        tool = GetCacheStatsTool()
        result = await tool.execute({})
        assert result["success"] is True
        assert result["recommend_model"]["hits"] == 0
        assert result["recommend_model"]["misses"] == 0
        assert result["router_recommend"]["hits"] == 0
        assert result["router_recommend"]["misses"] == 0

    async def test_ignores_arguments(self):
        """Mirrors get_data_quality's shape: arguments are accepted but ignored."""
        tool = GetCacheStatsTool()
        result = await tool.execute({"unexpected": "value"})
        assert result["success"] is True

    async def test_failure_is_reported_not_raised(self):
        tool = GetCacheStatsTool()
        with patch("mcp.tools.get_cache_stats.get_cache_stats", side_effect=RuntimeError("boom")):
            result = await tool.execute({})
        assert result["success"] is False
        assert "boom" in result["error"]
        assert result["error_type"] == "RuntimeError"

    async def test_reading_via_tool_does_not_mutate_counts(self):
        tool = GetCacheStatsTool()
        first = await tool.execute({})
        second = await tool.execute({})
        assert first == second


# ---------------------------------------------------------------------------
# GET /cache-stats (REST endpoint)
# ---------------------------------------------------------------------------

def _client():
    from src.main import app
    return TestClient(app)


class TestCacheStatsEndpoint:
    def test_endpoint_returns_both_call_sites_with_required_fields(self):
        client = _client()
        response = client.get("/cache-stats")
        assert response.status_code == 200
        data = response.json()

        for name in ("recommend_model", "router_recommend"):
            assert name in data
            for field in ("hits", "misses", "total", "hit_rate"):
                assert field in data[name]

    def test_endpoint_is_public_no_auth_required(self):
        """Matches /data-quality and /telemetry precedent — no x-api-key needed."""
        client = _client()
        response = client.get("/cache-stats")
        assert response.status_code == 200

    def test_reading_endpoint_repeatedly_does_not_change_counts(self):
        client = _client()
        first = client.get("/cache-stats").json()
        second = client.get("/cache-stats").json()
        assert first == second

    def test_endpoint_and_mcp_tool_never_drift_apart(self):
        """Both surfaces call the same get_cache_stats() function."""
        import asyncio
        client = _client()
        endpoint_data = client.get("/cache-stats").json()
        tool_result = asyncio.new_event_loop().run_until_complete(GetCacheStatsTool().execute({}))
        assert endpoint_data["recommend_model"] == tool_result["recommend_model"]
        assert endpoint_data["router_recommend"] == tool_result["router_recommend"]


# ---------------------------------------------------------------------------
# Existing `cached` field is unaffected (acceptance criterion)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestExistingCachedFieldUnaffected:
    async def test_recommend_model_cached_field_still_present_and_correct(self):
        """Adding name="recommend_model" to the tool's cache instance must not
        change the per-response `cached` field's presence or semantics."""
        models = [
            _pm("gpt-4o-mini", "openai", 0.00000015, 0.0000006, quality=72, context=128000),
        ]
        tool = RecommendModelTool()
        mock_svc = MagicMock()
        mock_svc.get_all_pricing_async = AsyncMock(return_value=(models, []))
        tool.service = mock_svc
        tool.router._aggregator = mock_svc

        with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)):
            first = await tool.execute({"description": "summarize this ticket"})
            second = await tool.execute({"description": "summarize this ticket"})

        assert first["success"] is True
        assert second["success"] is True
        assert first["cached"] is False
        assert second["cached"] is True
