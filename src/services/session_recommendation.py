"""Shared session-usage -> model-recommendation logic.

GET /usage/session/{session_id} (src/main.py) and the analyze_session_usage MCP tool
(mcp/tools/analyze_session_usage.py) both need to turn a get_session_usage() result into
a grounded recommendation using the same ModelRouter engine that backs recommend_model /
POST /router/recommend. This module holds that shared computation once so the blended
cost-per-1M formula, the is_optimal criteria, and the rationale wording can never drift
between the two entry points (they used to be duplicated verbatim in both places).
"""
from typing import Any, Dict, Optional

from src.services.router import RouterConstraints


def _avg_cost_per_1m(model) -> float:
    """Blended $/1M formula shared with router.py/recommend_model.py (equal input/output weight)."""
    return (model.cost_per_input_token + model.cost_per_output_token) / 2 * 1_000_000


def _build_recommendation_payload(usage: Dict[str, Any], result) -> Dict[str, Any]:
    """Turn a ModelRouter result + a get_session_usage() dict into the recommendation payload."""
    session_id = usage["session_id"]
    total_requests = usage["total_requests"]
    total_input_tokens = usage["total_input_tokens"]
    total_output_tokens = usage["total_output_tokens"]
    total_tokens = total_input_tokens + total_output_tokens
    total_cost_usd = usage["total_cost_usd"]
    by_model = usage["by_model"]

    recommended = result.recommended
    session_cost_per_1m = (total_cost_usd / total_tokens * 1_000_000) if total_tokens else 0.0
    recommended_cost_per_1m = _avg_cost_per_1m(recommended)
    estimated_cost_if_recommended = (
        total_input_tokens * recommended.cost_per_input_token
        + total_output_tokens * recommended.cost_per_output_token
    )

    single_model_used = len(by_model) == 1
    is_optimal = (
        single_model_used
        and by_model[0]["model_name"] == recommended.model_name
        and by_model[0]["provider"] == recommended.provider
    )

    if is_optimal:
        savings_usd = 0.0
        rationale = (
            f"Session '{session_id}' made {total_requests} request(s) totalling "
            f"{total_tokens} tokens (${total_cost_usd:.6f} actual, "
            f"~${session_cost_per_1m:.2f}/1M blended) using {recommended.model_name} "
            f"({recommended.provider}) throughout — already the optimal model for this "
            f"session's observed volume; no change recommended."
        )
    else:
        savings_usd = round(total_cost_usd - estimated_cost_if_recommended, 6)
        rationale = (
            f"Session '{session_id}' made {total_requests} request(s) totalling "
            f"{total_tokens} tokens (${total_cost_usd:.6f} actual, "
            f"~${session_cost_per_1m:.2f}/1M blended cost profile). Applying the same "
            f"token pattern to {recommended.model_name} ({recommended.provider}, "
            f"~${recommended_cost_per_1m:.2f}/1M) would cost approximately "
            f"${estimated_cost_if_recommended:.6f} — "
            f"{'a savings' if savings_usd > 0 else 'an increase'} of "
            f"${abs(savings_usd):.6f}."
        )

    return {
        "is_optimal": is_optimal,
        "recommended_model": recommended.model_name,
        "recommended_provider": recommended.provider,
        "recommended_cost_per_1m_tokens": round(recommended_cost_per_1m, 6),
        "session_actual_cost_per_1m_tokens": round(session_cost_per_1m, 6),
        "estimated_cost_if_recommended_usd": round(estimated_cost_if_recommended, 6),
        "savings_usd": savings_usd,
        "rationale": rationale,
    }


async def compute_session_recommendation(usage: Dict[str, Any], router) -> Optional[Dict[str, Any]]:
    """Build RouterConstraints from a session's own observed averages, call the given
    ModelRouter, and return the recommendation payload — or None if the router found no
    matching model. Callers are responsible for their own exception handling around this
    (the REST endpoint lets an unexpected error surface as a 500; the MCP tool catches it
    and reports `recommendation_error` instead), so this function does not swallow errors.
    """
    total_requests = usage["total_requests"]
    avg_input_tokens = max(1, round(usage["total_input_tokens"] / total_requests))
    avg_output_tokens = max(1, round(usage["total_output_tokens"] / total_requests))
    constraints = RouterConstraints(
        estimated_monthly_requests=total_requests,
        avg_input_tokens=avg_input_tokens,
        avg_output_tokens=avg_output_tokens,
    )
    result = await router.get_optimal_model(constraints)
    if result is None:
        return None
    return _build_recommendation_payload(usage, result)
