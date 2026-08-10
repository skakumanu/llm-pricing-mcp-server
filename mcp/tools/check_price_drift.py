"""MCP Tool: Report curated prices that disagree with the reference registry."""
from typing import Any, Dict

from src.services.price_oracle import DRIFT_THRESHOLD_PCT, get_price_oracle
from src.services.pricing_aggregator import PricingAggregatorService


class CheckPriceDriftTool:
    """Compare hand-maintained prices against the external registry.

    A drifted price is automatically withheld from serving as soon as
    pricing_aggregator detects it (price_confirmed is set to False) — it never
    waits on this tool to run. This reports which prices are currently withheld
    and why. The price value itself is never overwritten by the registry; only
    whether it gets served is affected. Correcting STATIC_PRICING to resolve the
    drift is a human decision.
    """

    def __init__(self):
        self.service = PricingAggregatorService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            threshold = float(arguments.get("threshold_pct", DRIFT_THRESHOLD_PCT))
            if threshold < 0:
                return {"success": False, "error": "threshold_pct must be non-negative"}

            provider = arguments.get("provider")
            limit = min(int(arguments.get("limit", 50)), 200)

            oracle = get_price_oracle()
            await oracle.load()
            if not oracle.loaded:
                return {
                    "success": False,
                    "error": (
                        "Price registry unavailable — no live fetch and no readable "
                        "snapshot. Cannot check drift."
                    ),
                }

            # include_unconfirmed=True so models the aggregator already withheld
            # for drift stay visible here — they're exactly what this tool needs
            # to report. They still hold their original curated price, so
            # find_drift() can (and must) keep comparing them.
            models, _ = await self.service.get_all_pricing_async(include_unconfirmed=True)
            if provider:
                models = [m for m in models if provider.lower() in m.provider.lower()]
            priced_models = [m for m in models if m.cost_per_input_token > 0]

            findings = oracle.find_drift(priced_models, threshold_pct=threshold)
            shown = findings[:limit]

            overstated = [f for f in findings if f.direction == "overstated"]
            understated = [f for f in findings if f.direction == "understated"]

            result: Dict[str, Any] = {
                "success": True,
                "threshold_pct": threshold,
                "models_checked": len(priced_models),
                "registry_models": oracle.model_count,
                "registry_source": oracle.source,
                "registry_fetched": oracle.fetched_at,
                "drift_count": len(findings),
                "overstated_count": len(overstated),
                "understated_count": len(understated),
                "findings": [f.as_dict() for f in shown],
            }

            if len(shown) < len(findings):
                result["truncated"] = (
                    f"showing {len(shown)} of {len(findings)} findings; raise `limit` to see more"
                )

            if not findings:
                result["summary"] = (
                    f"No curated price differs from the registry by more than {threshold:.0f}%."
                )
            else:
                worst = findings[0]
                result["summary"] = (
                    f"{len(findings)} of {len(priced_models)} curated prices differ from the "
                    f"registry by more than {threshold:.0f}% ({len(overstated)} overstated, "
                    f"{len(understated)} understated) and have been withheld from serving. "
                    f"Largest: {worst.model_name} at ${worst.ours_per_1m:.2f}/1M versus "
                    f"${worst.registry_per_1m:.2f}/1M ({worst.pct_difference:.0f}% "
                    f"{worst.direction}). Update STATIC_PRICING to correct and restore them."
                )

            return result

        except (TypeError, ValueError) as e:
            return {"success": False, "error": f"Invalid argument: {e}", "error_type": type(e).__name__}
        except Exception as e:
            return {"success": False, "error": str(e), "error_type": type(e).__name__}
