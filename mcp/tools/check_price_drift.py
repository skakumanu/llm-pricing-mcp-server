"""MCP Tool: Report curated prices that disagree with the reference registry."""
from typing import Any, Dict

from src.services.price_oracle import DRIFT_THRESHOLD_PCT, get_price_oracle
from src.services.pricing_aggregator import PricingAggregatorService


class CheckPriceDriftTool:
    """Compare hand-maintained prices against the external registry.

    Reports only — nothing is overwritten. A wrong price is worse than a missing
    one because it looks authoritative, so this surfaces disagreements for a human
    to adjudicate.
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

            models, _ = await self.service.get_all_pricing_async()
            if provider:
                models = [m for m in models if provider.lower() in m.provider.lower()]

            findings = oracle.find_drift(models, threshold_pct=threshold)
            shown = findings[:limit]

            overstated = [f for f in findings if f.direction == "overstated"]
            understated = [f for f in findings if f.direction == "understated"]

            result: Dict[str, Any] = {
                "success": True,
                "threshold_pct": threshold,
                "models_checked": len(models),
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
                    f"{len(findings)} of {len(models)} curated prices differ from the registry "
                    f"by more than {threshold:.0f}% ({len(overstated)} overstated, "
                    f"{len(understated)} understated). Largest: {worst.model_name} at "
                    f"${worst.ours_per_1m:.2f}/1M versus ${worst.registry_per_1m:.2f}/1M "
                    f"({worst.pct_difference:.0f}% {worst.direction}). "
                    f"Nothing was changed — update STATIC_PRICING to correct these."
                )

            return result

        except (TypeError, ValueError) as e:
            return {"success": False, "error": f"Invalid argument: {e}", "error_type": type(e).__name__}
        except Exception as e:
            return {"success": False, "error": str(e), "error_type": type(e).__name__}
