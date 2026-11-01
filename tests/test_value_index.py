"""Tests for the benchmark_service (quality scores / value index)."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.benchmark_service import (  # noqa: E402
    STATIC_SCORES,
    _lookup_static,
    enrich_models,
    get_quality_score,
    set_cache_ttl,
    _hf_cache,
)
from src.models.pricing import PricingMetrics  # noqa: E402


# ---------------------------------------------------------------------------
# Static lookup tests
# ---------------------------------------------------------------------------

def test_static_scores_nonempty():
    assert len(STATIC_SCORES) > 10


def test_lookup_static_exact():
    assert _lookup_static("gpt-4o") == 87
    assert _lookup_static("claude-opus-4-6") == 90
    assert _lookup_static("gemini-1.5-pro") == 80


def test_lookup_static_case_insensitive():
    assert _lookup_static("GPT-4O") == 87
    assert _lookup_static("Claude-Sonnet-4-6") == 82


def test_lookup_static_partial_match():
    # A versioned variant not in the table should still match via partial
    score = _lookup_static("gpt-4o-2024-11-20")
    assert score is not None
    assert isinstance(score, (int, float))


def test_lookup_static_unknown():
    assert _lookup_static("totally-unknown-xyz-model-9999") is None


def test_claude_5x_family_is_scored_and_ordered():
    """v1.89.1: the Claude 5.x models this repo's sessions run must have a score, each above
    its 4.x predecessor and in family order, so the router stops preferring the ten-times
    dearer claude-haiku-4-5 over claude-haiku-5-5 (which had quality_score=None)."""
    haiku, sonnet, opus, fable = (
        _lookup_static("claude-haiku-5-5"), _lookup_static("claude-sonnet-5-5"),
        _lookup_static("claude-opus-5-5"), _lookup_static("claude-fable-5-1"),
    )
    assert None not in (haiku, sonnet, opus, fable)
    assert haiku < sonnet < opus < fable <= 100
    assert haiku > _lookup_static("claude-haiku-4-5-20251001")
    assert sonnet > _lookup_static("claude-sonnet-4-6")
    assert opus > _lookup_static("claude-opus-4-6")
    # The undated registry key for Haiku 4.5 is scored too (it never partial-matched the dated key).
    assert _lookup_static("claude-haiku-4-5") == _lookup_static("claude-haiku-4-5-20251001")


# ---------------------------------------------------------------------------
# quality_score / quality_value_score on PricingMetrics
# ---------------------------------------------------------------------------

def _make_pricing_metric(**kwargs):
    defaults = dict(
        model_name="gpt-4o",
        provider="openai",
        cost_per_input_token=0.005,
        cost_per_output_token=0.015,
    )
    defaults.update(kwargs)
    return PricingMetrics(**defaults)


def test_quality_score_field_defaults_none():
    m = _make_pricing_metric()
    assert m.quality_score is None


def test_quality_value_score_none_when_no_quality():
    m = _make_pricing_metric()
    assert m.quality_value_score is None


def test_quality_value_score_computed():
    m = _make_pricing_metric(quality_score=80.0)
    # avg cost per 1M = (0.005 + 0.015) / 2 * 1_000_000 = 10_000
    # quality_value_score = 80 / 10_000 = 0.008
    assert m.quality_value_score is not None
    assert abs(m.quality_value_score - 0.008) < 1e-6


def test_quality_value_score_higher_for_cheaper_model():
    cheap = _make_pricing_metric(quality_score=70.0, cost_per_input_token=0.0001, cost_per_output_token=0.0002)
    expensive = _make_pricing_metric(quality_score=80.0, cost_per_input_token=0.01, cost_per_output_token=0.03)
    assert cheap.quality_value_score > expensive.quality_value_score


# ---------------------------------------------------------------------------
# enrich_models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_models_sets_quality_score():
    m = _make_pricing_metric(model_name="gpt-4o")
    m = MagicMock(spec=PricingMetrics)
    m.model_name = "gpt-4o"
    m.quality_score = None
    models = await enrich_models([m])
    assert models[0].quality_score == 87


@pytest.mark.asyncio
async def test_enrich_models_skips_already_set():
    m = MagicMock()
    m.model_name = "gpt-4o"
    m.quality_score = 50.0  # already set
    models = await enrich_models([m])
    assert models[0].quality_score == 50.0  # unchanged


@pytest.mark.asyncio
async def test_enrich_models_unknown_model_none():
    m = MagicMock()
    m.model_name = "completely-unknown-zzz-999"
    m.quality_score = None
    with patch("src.services.benchmark_service._fetch_hf_score", AsyncMock(return_value=None)):
        models = await enrich_models([m])
    assert models[0].quality_score is None


# ---------------------------------------------------------------------------
# get_quality_score
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_quality_score_known():
    score = await get_quality_score("gemini-1.5-flash")
    assert score == 70.0


@pytest.mark.asyncio
async def test_get_quality_score_unknown_falls_back_to_hf():
    with patch(
        "src.services.benchmark_service._fetch_hf_score",
        AsyncMock(return_value=55.0),
    ):
        score = await get_quality_score("open-source-model-xyz")
    assert score == 55.0


@pytest.mark.asyncio
async def test_get_quality_score_hf_failure_returns_none():
    with patch(
        "src.services.benchmark_service._fetch_hf_score",
        AsyncMock(return_value=None),
    ):
        score = await get_quality_score("totally-unknown-abc")
    assert score is None


# ---------------------------------------------------------------------------
# set_cache_ttl
# ---------------------------------------------------------------------------

def test_set_cache_ttl():
    import src.services.benchmark_service as bm
    set_cache_ttl(48)
    assert bm._cache_ttl_seconds == 48 * 3600
    # Reset back to default
    set_cache_ttl(24)


# ---------------------------------------------------------------------------
# Leaderboard download is shared and misses are cached (v1.75.0)
# ---------------------------------------------------------------------------

def _row(name, score):
    return {"row": {"model_name_for_query": name, "Average ⬆️": score}}


@pytest.fixture
def _fresh_caches():
    import src.services.benchmark_service as bm
    bm._reset_caches()
    yield bm
    bm._reset_caches()


@pytest.mark.asyncio
async def test_many_unknown_models_cost_one_download(_fresh_caches):
    bm = _fresh_caches
    download = AsyncMock(return_value=[_row("acme/open-model-7b", 61.5)])
    models = []
    for i in range(300):
        m = MagicMock()
        m.model_name = f"unknown-registry-model-{i}"
        m.quality_score = None
        models.append(m)
    hit = MagicMock()
    hit.model_name = "open-model-7b"
    hit.quality_score = None
    with patch.object(bm, "_download_leaderboard_rows", download):
        await enrich_models(models + [hit])
        # a second pass over the same catalogue is served from the caches
        for m in models:
            m.quality_score = None
        await enrich_models(models)
    assert download.await_count == 1
    assert hit.quality_score == 61.5
    assert all(m.quality_score is None for m in models)
    assert _hf_cache["unknown-registry-model-0"][0] is None  # the miss is cached


@pytest.mark.asyncio
async def test_failed_download_is_remembered_not_retried_per_model(_fresh_caches):
    bm = _fresh_caches
    download = AsyncMock(side_effect=RuntimeError("leaderboard down"))
    with patch.object(bm, "_download_leaderboard_rows", download):
        for i in range(20):
            assert await get_quality_score(f"nobody-knows-{i}") is None
    assert download.await_count == 1
    rows, _, ttl = bm._leaderboard_cache
    assert rows == [] and ttl == bm._FAILURE_TTL_SECONDS


@pytest.mark.asyncio
async def test_non_200_download_counts_as_failure(_fresh_caches):
    bm = _fresh_caches
    resp = MagicMock(status_code=503)
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=client):
        assert await get_quality_score("nobody-knows") is None
    assert bm._leaderboard_cache[2] == bm._FAILURE_TTL_SECONDS


@pytest.mark.asyncio
async def test_expired_leaderboard_is_downloaded_again(_fresh_caches):
    bm = _fresh_caches
    download = AsyncMock(return_value=[])
    with patch.object(bm, "_download_leaderboard_rows", download):
        await get_quality_score("fresh-unknown-a")
        rows, fetched_at, ttl = bm._leaderboard_cache
        bm._leaderboard_cache = (rows, fetched_at - ttl - 1, ttl)
        await get_quality_score("fresh-unknown-b")
    assert download.await_count == 2


@pytest.mark.asyncio
async def test_static_scores_never_touch_the_leaderboard(_fresh_caches):
    bm = _fresh_caches
    download = AsyncMock(return_value=[])
    with patch.object(bm, "_download_leaderboard_rows", download):
        assert await get_quality_score("gpt-4o") == 87
        assert await get_quality_score("claude-sonnet-4-6-20260101") == 82
    assert download.await_count == 0
