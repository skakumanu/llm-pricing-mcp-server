"""MCP Tool: Optimize a multi-task workload across models instead of picking one."""
from typing import Any, Dict, List

from src.services.benchmark_service import enrich_models
from src.services.pricing_aggregator import PricingAggregatorService
from src.services.portfolio_optimizer import Workload, optimize
from src.services.task_profiles import get_task_description, list_task_types


class OptimizeWorkloadTool:
    """Assign the cheapest qualifying model per task, then report savings."""

    def __init__(self):
        self.service = PricingAggregatorService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            raw_workloads = arguments.get("workloads")
            if not raw_workloads or not isinstance(raw_workloads, list):
                return {
                    "success": False,
                    "error": "workloads is required and must be a non-empty list",
                }

            monthly_budget = arguments.get("monthly_budget_usd")
            if monthly_budget is not None:
                monthly_budget = float(monthly_budget)
                if monthly_budget <= 0:
                    return {"success": False, "error": "monthly_budget_usd must be positive"}

            global_min_quality = arguments.get("min_quality_score")
            if global_min_quality is not None:
                global_min_quality = float(global_min_quality)

            valid_types = list_task_types()
            workloads: List[Workload] = []

            for i, item in enumerate(raw_workloads):
                if not isinstance(item, dict):
                    return {"success": False, "error": f"workloads[{i}] must be an object"}

                task_type = item.get("task_type")
                if not task_type:
                    return {"success": False, "error": f"workloads[{i}].task_type is required"}
                if task_type not in valid_types:
                    return {
                        "success": False,
                        "error": (
                            f"workloads[{i}].task_type '{task_type}' is not recognised. "
                            f"Valid options: {valid_types}"
                        ),
                    }

                monthly_requests = item.get("monthly_requests")
                if monthly_requests is None:
                    return {
                        "success": False,
                        "error": f"workloads[{i}].monthly_requests is required",
                    }
                monthly_requests = int(monthly_requests)
                if monthly_requests < 0:
                    return {
                        "success": False,
                        "error": f"workloads[{i}].monthly_requests must be non-negative",
                    }

                workloads.append(Workload(
                    task_type=task_type,
                    monthly_requests=monthly_requests,
                    avg_input_tokens=int(item.get("avg_input_tokens", 500)),
                    avg_output_tokens=(
                        int(item["avg_output_tokens"])
                        if item.get("avg_output_tokens") is not None else None
                    ),
                    min_quality_score=(
                        float(item["min_quality_score"])
                        if item.get("min_quality_score") is not None else None
                    ),
                    require_function_calling=bool(item.get("require_function_calling", False)),
                    require_vision=bool(item.get("require_vision", False)),
                    min_context_tokens=(
                        int(item["min_context_tokens"])
                        if item.get("min_context_tokens") is not None else None
                    ),
                    label=item.get("label"),
                ))

            models, _ = await self.service.get_all_pricing_async()
            if not models:
                return {"success": False, "error": "No pricing data available"}
            models = await enrich_models(models)

            result = optimize(
                models=models,
                workloads=workloads,
                monthly_budget_usd=monthly_budget,
                min_quality_score=global_min_quality,
            )

            if not result.allocations:
                return {
                    "success": False,
                    "error": "No workload could be satisfied with the given constraints",
                    "unsatisfiable": result.unsatisfiable,
                }

            allocations = [
                {
                    "workload": a.workload.display_name(),
                    "task_type": a.workload.task_type,
                    "task_description": get_task_description(a.workload.task_type),
                    "model_name": a.model.model_name,
                    "provider": a.model.provider,
                    "quality_score": a.model.quality_score,
                    "monthly_requests": a.workload.monthly_requests,
                    "avg_input_tokens": a.workload.avg_input_tokens,
                    "avg_output_tokens": a.workload.resolved_output_tokens(),
                    "cost_per_request_usd": round(a.cost_per_request, 8),
                    "monthly_cost_usd": round(a.monthly_cost, 4),
                    "upgraded": a.upgraded,
                    "upgrade_note": a.upgrade_note,
                }
                for a in result.allocations
            ]

            response: Dict[str, Any] = {
                "success": True,
                "allocations": allocations,
                "total_monthly_cost_usd": round(result.total_monthly_cost, 4),
                "distinct_models_used": len({a["model_name"] for a in allocations}),
                "budget_usd": result.budget_usd,
                "within_budget": result.within_budget,
                "upgrades_applied": result.upgrades_applied,
                "unsatisfiable": result.unsatisfiable,
            }

            if result.baseline_model is not None:
                response["single_model_baseline"] = {
                    "model_name": result.baseline_model.model_name,
                    "provider": result.baseline_model.provider,
                    "quality_score": result.baseline_model.quality_score,
                    "monthly_cost_usd": round(result.baseline_monthly_cost, 4),
                }
                response["savings_vs_baseline_usd"] = round(result.savings_usd, 4)
                response["savings_vs_baseline_pct"] = round(result.savings_pct, 2)
                response["summary"] = (
                    f"Routing {len(allocations)} workload(s) across "
                    f"{response['distinct_models_used']} model(s) costs "
                    f"${response['total_monthly_cost_usd']:.2f}/mo versus "
                    f"${response['single_model_baseline']['monthly_cost_usd']:.2f}/mo on "
                    f"{result.baseline_model.model_name} alone — "
                    f"{response['savings_vs_baseline_pct']:.1f}% saved."
                )
            else:
                response["single_model_baseline"] = None
                response["summary"] = (
                    "No single model satisfies every workload's constraints, so a "
                    "multi-model portfolio is required rather than merely cheaper."
                )

            if result.within_budget is False:
                response["budget_note"] = (
                    f"Cheapest possible allocation is ${result.total_monthly_cost:.2f}/mo, "
                    f"which exceeds the ${result.budget_usd:.2f}/mo budget by "
                    f"${result.total_monthly_cost - result.budget_usd:.2f}. "
                    f"Reduce volume, relax quality floors, or raise the budget."
                )

            return response

        except (TypeError, ValueError) as e:
            return {"success": False, "error": f"Invalid argument: {e}", "error_type": type(e).__name__}
        except Exception as e:
            return {"success": False, "error": str(e), "error_type": type(e).__name__}
