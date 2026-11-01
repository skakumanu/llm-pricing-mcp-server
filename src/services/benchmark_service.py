"""Benchmark quality scores for LLM models.

Provides quality_score (0-100, benchmark-derived) for known models via a
curated static mapping, supplemented by the HuggingFace Open LLM Leaderboard
for open-source models (graceful fallback).

The leaderboard is one dataset, so it is downloaded once and cached (24 h on
success, one hour on failure) and every model is matched against the cached
rows in memory; misses are cached per model for the same period. Before
v1.75.0 each unknown model issued its own identical HTTP request and a miss
was never cached, so a catalogue of 600 models cost hundreds of sequential
requests on every /performance, router or predict_cost call.
"""
import logging
import time
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.models.pricing import PricingMetrics

logger = logging.getLogger(__name__)

# Static quality scores (0–100, benchmark-derived from public leaderboards)
STATIC_SCORES: Dict[str, float] = {
    # OpenAI
    "gpt-4o": 87,
    "gpt-4o-mini": 72,
    "gpt-4-turbo": 85,
    "gpt-4": 83,
    "gpt-4-32k": 83,
    "gpt-3.5-turbo": 65,
    "gpt-3.5-turbo-16k": 65,
    "o1": 92,
    "o1-mini": 80,
    "o3-mini": 85,
    "o1-preview": 91,
    # Anthropic
    # Claude 5.x (v1.89.1): tier-relative scores placed from the public launch figures
    # (SWE-bench Pro: Opus 5.5 89.9 vs Sonnet 5.5 81.3; Humanity's Last Exam: Fable 5.1
    # 60.9 vs Opus 5 56.6; Haiku 5.5 beats the previous Opus/Sonnet on APEX-SWE), so each
    # 5.x model ranks above its 4.x predecessor and the family order is preserved. Until
    # these were listed, every 5.x model had quality_score=None and the router ranked the
    # ten-times-dearer claude-haiku-4-5 above claude-haiku-5-5.
    "claude-mythos-5-1": 96,
    "claude-fable-5-1": 95,
    "claude-opus-5-5": 92,
    "claude-sonnet-5-5": 86,
    "claude-haiku-5-5": 76,
    "claude-opus-4-6": 90,
    "claude-sonnet-4-6": 82,
    "claude-haiku-4-5-20251001": 72,
    "claude-haiku-4-5": 72,  # registry key without the date suffix never partial-matched the line above
    "claude-3-opus-20240229": 88,
    "claude-3-sonnet-20240229": 79,
    "claude-3-haiku-20240307": 68,
    "claude-3-5-sonnet-20241022": 84,
    "claude-3-5-haiku-20241022": 73,
    # Google
    "gemini-1.5-pro": 80,
    "gemini-1.5-flash": 70,
    "gemini-2.0-flash": 75,
    "gemini-2.0-flash-lite": 65,
    "gemini-1.0-pro": 72,
    "gemini-pro": 72,
    # Mistral
    "mistral-large-latest": 78,
    "mistral-medium-latest": 70,
    "mistral-small-latest": 62,
    "mistral-7b-instruct": 58,
    "mixtral-8x7b-instruct": 72,
    "mixtral-8x22b-instruct": 78,
    "codestral-latest": 76,
    # Cohere
    "command-r-plus": 77,
    "command-r": 68,
    "command": 60,
    "command-light": 52,
    # Meta / open-source (via Groq, Together, etc.)
    "llama-3.3-70b-versatile": 76,
    "llama-3.1-70b-instruct": 75,
    "llama-3.1-8b-instant": 60,
    "llama-3-70b-instruct": 74,
    "llama-3-8b-instruct": 58,
    "llama-2-70b-chat": 65,
    "gemma2-9b-it": 62,
    "gemma-7b-it": 58,
    "mixtral-8x7b-32768": 72,
}

# Normalised lookup (lowercase keys), plus the same entries longest-first for
# partial matching so the sort is not repeated on every lookup.
_NORMALIZED: Dict[str, float] = {k.lower(): v for k, v in STATIC_SCORES.items()}
_NORMALIZED_LONGEST_FIRST = sorted(_NORMALIZED.items(), key=lambda x: len(x[0]), reverse=True)

# In-memory HF cache: model_name -> (score or None, cached_at_unix). A None
# score is a cached miss, so an unknown model is matched once per TTL.
_hf_cache: Dict[str, tuple] = {}
_cache_ttl_seconds: int = 24 * 3600  # default; overridden by set_cache_ttl()

# The leaderboard rows themselves: (rows, fetched_at_unix, ttl_seconds). One
# download serves every model; a failed download is remembered for an hour so
# an outage costs one request per hour, not one per model per call.
_leaderboard_cache: Optional[tuple] = None
_FAILURE_TTL_SECONDS = 3600

_LEADERBOARD_ROWS_URL = "https://datasets-server.huggingface.co/rows"
_LEADERBOARD_ROWS_PARAMS = {
    "dataset": "open-llm-leaderboard/results",
    "config": "default",
    "split": "train",
    "offset": 0,
    "limit": 5,
}

_HF_LEADERBOARD_URL = (
    "https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard"
    "/raw/main/src/backend/envs.py"
)


def set_cache_ttl(hours: int) -> None:
    """Override the default 24-hour HF cache TTL."""
    global _cache_ttl_seconds
    _cache_ttl_seconds = hours * 3600


def _lookup_static(model_name: str) -> Optional[float]:
    """Return a score from STATIC_SCORES using exact or partial match."""
    key = model_name.lower()
    if key in _NORMALIZED:
        return _NORMALIZED[key]
    # Partial match: find the best (longest) prefix
    for k, v in _NORMALIZED_LONGEST_FIRST:
        if key.startswith(k) or k in key:
            return v
    return None


def _reset_caches() -> None:
    """Forget cached leaderboard rows and per-model scores (tests)."""
    global _leaderboard_cache
    _hf_cache.clear()
    _leaderboard_cache = None


async def _download_leaderboard_rows() -> Optional[List[dict]]:
    """One HTTP request for the leaderboard rows; None on any failure."""
    import httpx
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(_LEADERBOARD_ROWS_URL, params=_LEADERBOARD_ROWS_PARAMS)
        if resp.status_code != 200:
            return None
        rows = resp.json().get("rows", [])
        return rows if isinstance(rows, list) else []


async def _leaderboard_rows() -> List[dict]:
    """The cached leaderboard rows, downloading at most once per TTL.

    A failed or non-200 download is cached as an empty list for
    ``_FAILURE_TTL_SECONDS`` so every model does not retry it.
    """
    global _leaderboard_cache
    now = time.time()
    if _leaderboard_cache is not None:
        rows, fetched_at, ttl = _leaderboard_cache
        if now - fetched_at < ttl:
            return rows

    rows: List[dict] = []
    ttl = _FAILURE_TTL_SECONDS
    try:
        fetched = await _download_leaderboard_rows()
        if fetched is not None:
            rows, ttl = fetched, _cache_ttl_seconds
    except Exception as exc:
        logger.debug("HF leaderboard fetch failed: %s", exc)
    _leaderboard_cache = (rows, now, ttl)
    return rows


async def _fetch_hf_score(model_name: str) -> Optional[float]:
    """
    Try to retrieve a quality score from the HuggingFace Open LLM Leaderboard.

    Returns None on any failure (network, parse, not found) so that callers
    always fall back gracefully to STATIC_SCORES or a default. Both hits and
    misses are cached per model, and all models share one download of the
    leaderboard rows.
    """
    cached = _hf_cache.get(model_name)
    if cached is not None:
        score, cached_at = cached
        if time.time() - cached_at < _cache_ttl_seconds:
            return score

    score = None
    name_lower = model_name.lower()
    for row in await _leaderboard_rows():
        row_data = row.get("row", {}) if isinstance(row, dict) else {}
        full_name = str(row_data.get("model_name_for_query", "")).lower()
        if full_name and (name_lower in full_name or full_name in name_lower):
            avg_score = row_data.get("Average ⬆️")
            if avg_score is not None:
                try:
                    score = float(avg_score)
                except (TypeError, ValueError):
                    continue
                break
    _hf_cache[model_name] = (score, time.time())
    return score


async def get_quality_score(model_name: str) -> Optional[float]:
    """
    Return a quality score (0–100) for a model.

    Priority:
    1. STATIC_SCORES (exact or partial match)
    2. HuggingFace Leaderboard API (24 h cached)
    3. None (caller should treat as unknown)
    """
    score = _lookup_static(model_name)
    if score is not None:
        return score
    return await _fetch_hf_score(model_name)


async def enrich_models(models: List["PricingMetrics"]) -> List["PricingMetrics"]:
    """
    Attach quality_score to each PricingMetrics in the list.

    Returns the same list with quality_score populated where available.
    """
    for model in models:
        if getattr(model, "quality_score", None) is None:
            score = await get_quality_score(model.model_name)
            model.quality_score = score
    return models
