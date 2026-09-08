"""MCP Tool: Report process-lifetime hit/miss stats for the recommendation cache."""
from typing import Any, Dict

from src.services.recommendation_cache import get_cache_stats


class GetCacheStatsTool:
    """Observability for the ~5-minute recommendation cache (PR #252).

    Reports hit/miss counts, total lookups, and hit rate separately for each
    of the two call sites the cache is wired into — the `recommend_model` MCP
    tool and `POST /router/recommend` — never combined into a single global
    figure. Process-lifetime only: counts reset on restart and are not shared
    across replicas. Read-only: calling this never changes the counts, and it
    has no effect on caching behavior, TTL, or eviction.
    """

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            stats = get_cache_stats()
            return {"success": True, **stats}
        except Exception as e:
            return {"success": False, "error": str(e), "error_type": type(e).__name__}
