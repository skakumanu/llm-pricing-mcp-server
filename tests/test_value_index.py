"""Tests for the benchmark_service (quality scores / value index)."""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.benchmark_service import (  # noqa: E402
    STATIC_SCORES,
    _lookup_static,
    _NORMALIZED,
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
