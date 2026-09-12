"""Shared quality/cost tradeoff caveat, used by both recommend_model.py and
src/services/session_recommendation.py.

Below QUALITY_TRADEOFF_THRESHOLD, ranking purely by quality-per-dollar can pick a model
that merely clears a quality bar rather than one well-suited to tasks where a wrong
answer is expensive to discover and fix downstream. This module holds the single
definition of that threshold and the caveat dict it produces, so the two entry points
that can each recommend a lower-quality model for cost reasons (the free-text
recommend_model tool/POST /router/recommend, and the session-usage-based
analyze_session_usage tool/GET /usage/session/{session_id}) cannot silently diverge on
this safety behavior.
"""
from typing import Any, Dict, Optional

# Below this quality_score, ranking purely by quality-per-dollar can pick a
# model that merely clears a quality bar rather than one well-suited to tasks
# where a wrong answer is expensive to discover and fix downstream.
QUALITY_TRADEOFF_THRESHOLD = 75.0


def build_quality_tradeoff_caveat(quality_score: Optional[float]) -> Optional[Dict[str, Any]]:
    """Return a quality_tradeoff caveat dict for the given recommended model's
    quality_score, or None if no caveat applies (quality_score is None or at/above
    QUALITY_TRADEOFF_THRESHOLD).
    """
    if quality_score is None or quality_score >= QUALITY_TRADEOFF_THRESHOLD:
        return None

    return {
        "type": "quality_tradeoff",
        "message": (
            f"The top recommendation has a quality_score of {quality_score} "
            f"(below {QUALITY_TRADEOFF_THRESHOLD:g}). Ranking by quality-per-dollar can "
            "understate risk for tasks where a wrong answer is costly to discover and fix "
            "downstream, as distinct from tasks where a mediocre answer is only a minor "
            "inconvenience. Consider min_quality_score if this task is the former."
        ),
        "quality_score": quality_score,
        "quality_threshold": QUALITY_TRADEOFF_THRESHOLD,
    }
