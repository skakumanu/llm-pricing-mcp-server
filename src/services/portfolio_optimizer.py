"""Portfolio optimizer: assign the right model to each workload, not one model to all.

Most teams pick a single model and run every task through it. That overpays on the
simple tasks and sometimes underserves the hard ones. This engine takes a list of
workloads (task type + monthly volume + quality floor) and returns a per-task model
allocation, then reports the saving against the best single-model deployment.

Algorithm
---------
1. Per workload, derive per-request token counts from the task profile.
2. Filter to models that satisfy that workload's constraints.
3. Assign the cheapest qualifying model to each workload (the floor allocation).
4. If budget headroom remains, spend it: repeatedly apply the single upgrade with the
   best quality-gain-per-extra-dollar that still fits. Greedy, but explainable — each
   upgrade is reported with what it bought and what it cost.
5. Baseline = cheapest single model able to serve *every* workload. Savings is measured
   against that, because that is what teams actually do today.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.services.task_profiles import estimate_output_tokens

logger = logging.getLogger(__name__)

DEFAULT_AVG_INPUT_TOKENS = 500


@dataclass
class Workload:
    """One task type with a monthly volume and its own constraints."""

    task_type: str
    monthly_requests: int
    avg_input_tokens: int = DEFAULT_AVG_INPUT_TOKENS
    avg_output_tokens: Optional[int] = None  # derived from task profile when None
    min_quality_score: Optional[float] = None
    require_function_calling: bool = False
    require_vision: bool = False
    min_context_tokens: Optional[int] = None
    label: Optional[str] = None

    def resolved_output_tokens(self) -> int:
        if self.avg_output_tokens is not None:
            return self.avg_output_tokens
        return estimate_output_tokens(self.task_type, self.avg_input_tokens)

    def display_name(self) -> str:
        return self.label or self.task_type


@dataclass
class Allocation:
    """A model assigned to a workload, with its projected monthly cost."""

    workload: Workload
    model: Any  # PricingMetrics
    monthly_cost: float
    cost_per_request: float
    upgraded: bool = False
    upgrade_note: Optional[str] = None


@dataclass
class OptimizationResult:
    allocations: List[Allocation] = field(default_factory=list)
    total_monthly_cost: float = 0.0
    baseline_model: Any = None
    baseline_monthly_cost: Optional[float] = None
    savings_usd: Optional[float] = None
    savings_pct: Optional[float] = None
    budget_usd: Optional[float] = None
    within_budget: Optional[bool] = None
    upgrades_applied: List[str] = field(default_factory=list)
    unsatisfiable: List[str] = field(default_factory=list)


def cost_per_request(model, input_tokens: int, output_tokens: int) -> float:
    """Cost in USD for a single request against this model.

    Provider prices are stored per 1k tokens.
    """
    return (
        (model.cost_per_input_token / 1000) * input_tokens
        + (model.cost_per_output_token / 1000) * output_tokens
    )


def _qualifies(model, wl: Workload, global_min_quality: Optional[float]) -> bool:
    """Whether a model satisfies a workload's hard constraints."""
    if getattr(model, "pricing_model", "per_token") != "per_token":
        return False  # subscription tools aren't per-request allocatable
    if wl.require_function_calling and not model.supports_function_calling:
        return False
    if wl.require_vision and not model.supports_vision:
        return False
    if wl.min_context_tokens is not None and (model.context_window or 0) < wl.min_context_tokens:
        return False

    floor = wl.min_quality_score
    if global_min_quality is not None:
        floor = global_min_quality if floor is None else max(floor, global_min_quality)
    if floor is not None:
        if model.quality_score is None or model.quality_score < floor:
            return False
    return True


def _candidates_for(models, wl: Workload, global_min_quality: Optional[float]) -> List[Any]:
    return [m for m in models if _qualifies(m, wl, global_min_quality)]


def optimize(
    models: List[Any],
    workloads: List[Workload],
    monthly_budget_usd: Optional[float] = None,
    min_quality_score: Optional[float] = None,
) -> OptimizationResult:
    """Allocate a model per workload, minimising cost subject to constraints."""
    result = OptimizationResult(budget_usd=monthly_budget_usd)

    if not workloads:
        return result

    # --- Step 1-3: cheapest qualifying model per workload -------------------
    per_workload_candidates: Dict[int, List[Any]] = {}

    for idx, wl in enumerate(workloads):
        cands = _candidates_for(models, wl, min_quality_score)
        if not cands:
            result.unsatisfiable.append(
                f"{wl.display_name()}: no model satisfies its constraints "
                f"(quality>={wl.min_quality_score or min_quality_score}, "
                f"fn_calling={wl.require_function_calling}, vision={wl.require_vision}, "
                f"context>={wl.min_context_tokens})"
            )
            continue

        out_tokens = wl.resolved_output_tokens()
        cands.sort(key=lambda m: cost_per_request(m, wl.avg_input_tokens, out_tokens))
        per_workload_candidates[idx] = cands

        cheapest = cands[0]
        cpr = cost_per_request(cheapest, wl.avg_input_tokens, out_tokens)
        result.allocations.append(
            Allocation(
                workload=wl,
                model=cheapest,
                cost_per_request=cpr,
                monthly_cost=cpr * wl.monthly_requests,
            )
        )

    result.total_monthly_cost = sum(a.monthly_cost for a in result.allocations)

    # --- Step 4: spend leftover budget on the most efficient quality upgrades ---
    if monthly_budget_usd is not None and result.allocations:
        result.within_budget = result.total_monthly_cost <= monthly_budget_usd
        if result.within_budget:
            _apply_upgrades(result, per_workload_candidates, monthly_budget_usd)

    # --- Step 5: single-model baseline for comparison -----------------------
    _compute_baseline(result, models, workloads, min_quality_score)

    return result


def _apply_upgrades(
    result: OptimizationResult,
    per_workload_candidates: Dict[int, List[Any]],
    budget: float,
) -> None:
    """Greedily buy the best quality-per-extra-dollar upgrades that fit the budget."""
    # Map allocation position -> candidate list key. Allocations are appended in
    # workload order, skipping unsatisfiable ones, so rebuild the pairing.
    alloc_by_key = {}
    keys = sorted(per_workload_candidates.keys())
    for alloc, key in zip(result.allocations, keys):
        alloc_by_key[key] = alloc

    while True:
        best_swap = None  # (gain_per_dollar, key, model, extra_cost, quality_gain)

        for key, alloc in alloc_by_key.items():
            wl = alloc.workload
            out_tokens = wl.resolved_output_tokens()
            current_q = alloc.model.quality_score or 0.0

            for cand in per_workload_candidates[key]:
                cand_q = cand.quality_score or 0.0
                if cand_q <= current_q:
                    continue
                cand_monthly = cost_per_request(cand, wl.avg_input_tokens, out_tokens) * wl.monthly_requests
                extra = cand_monthly - alloc.monthly_cost
                if extra <= 0:
                    # Strictly better and cheaper — always take it.
                    best_swap = (float("inf"), key, cand, extra, cand_q - current_q)
                    break
                spend_after = result.total_monthly_cost + extra
                if spend_after > budget:
                    continue
                gain_per_dollar = (cand_q - current_q) / extra
                if best_swap is None or gain_per_dollar > best_swap[0]:
                    best_swap = (gain_per_dollar, key, cand, extra, cand_q - current_q)
            if best_swap and best_swap[0] == float("inf"):
                break

        if best_swap is None:
            return

        _, key, model, extra, q_gain = best_swap
        alloc = alloc_by_key[key]
        wl = alloc.workload
        out_tokens = wl.resolved_output_tokens()

        previous = alloc.model.model_name
        alloc.model = model
        alloc.cost_per_request = cost_per_request(model, wl.avg_input_tokens, out_tokens)
        alloc.monthly_cost = alloc.cost_per_request * wl.monthly_requests
        alloc.upgraded = True
        alloc.upgrade_note = (
            f"upgraded from {previous} (+{q_gain:.0f} quality for "
            f"{'no extra cost' if extra <= 0 else f'+${extra:.2f}/mo'})"
        )
        result.total_monthly_cost += extra
        result.upgrades_applied.append(f"{wl.display_name()}: {alloc.upgrade_note}")


def _compute_baseline(
    result: OptimizationResult,
    models: List[Any],
    workloads: List[Workload],
    min_quality_score: Optional[float],
) -> None:
    """Cheapest single model able to serve every workload — what teams do today."""
    universal = [
        m for m in models
        if all(_qualifies(m, wl, min_quality_score) for wl in workloads)
    ]
    if not universal:
        return

    def total_for(m) -> float:
        return sum(
            cost_per_request(m, wl.avg_input_tokens, wl.resolved_output_tokens()) * wl.monthly_requests
            for wl in workloads
        )

    best = min(universal, key=total_for)
    result.baseline_model = best
    result.baseline_monthly_cost = total_for(best)

    if result.baseline_monthly_cost > 0:
        result.savings_usd = result.baseline_monthly_cost - result.total_monthly_cost
        result.savings_pct = (result.savings_usd / result.baseline_monthly_cost) * 100
