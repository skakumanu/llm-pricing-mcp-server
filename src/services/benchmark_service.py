"""Benchmark quality scores for LLM models.

Provides quality_score (0-100, benchmark-derived) for known models via a
curated static mapping, supplemented by a HuggingFace Datasets API call for
open-source models (cached 24 h, graceful fallback).
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
    "claude-opus-4-6": 90,
    "claude-sonnet-4-6": 82,
    "claude-haiku-4-5-20251001": 72,
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

# Normalised lookup (lowercase keys)
_NORMALIZED: Dict[str, float] = {k.lower(): v for k, v in STATIC_SCORES.items()}

# In-memory HF cache: model_name -> (score, cached_at_unix)
_hf_cache: Dict[str, tuple] = {}
_cache_ttl_seconds: int = 24 * 3600  # default; overridden by set_cache_ttl()

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
    for k, v in sorted(_NORMALIZED.items(), key=lambda x: len(x[0]), reverse=True):
        if key.startswith(k) or k in key:
            return v
    return None


async def _fetch_hf_score(model_name: str) -> Optional[float]:
    """
    Try to retrieve a quality score from the HuggingFace Open LLM Leaderboard.

    Returns None on any failure (network, parse, not found) so that callers
    always fall back gracefully to STATIC_SCORES or a default.
    """
    cached = _hf_cache.get(model_name)
    if cached is not None:
        score, cached_at = cached
        if time.time() - cached_at < _cache_ttl_seconds:
            return score

    try:
        import httpx
        # The Open LLM Leaderboard exposes a dataset API we can query
        url = "https://datasets-server.huggingface.co/rows"
        params = {
            "dataset": "open-llm-leaderboard/results",
            "config": "default",
            "split": "train",
            "offset": 0,
            "limit": 5,
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return None
            data = resp.json()
            rows = data.get("rows", [])
            name_lower = model_name.lower()
            for row in rows:
                row_data = row.get("row", {})
                full_name = str(row_data.get("model_name_for_query", "")).lower()
                if name_lower in full_name or full_name in name_lower:
                    avg_score = row_data.get("Average ⬆️")
                    if avg_score is not None:
                        score = float(avg_score)
                        _hf_cache[model_name] = (score, time.time())
                        return score
    except Exception as exc:
        logger.debug("HF leaderboard fetch failed for %s: %s", model_name, exc)

    return None


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


async def enrich_models(models: List) -> List:
    """
    Attach quality_score to each PricingMetrics in the list.

    Returns the same list with quality_score populated where available.
    """
    for model in models:
        if getattr(model, "quality_score", None) is None:
            score = await get_quality_score(model.model_name)
            model.quality_score = score
    return models
