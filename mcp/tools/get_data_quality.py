"""MCP Tool: Report an aggregate data-quality/accuracy summary for customers."""
from typing import Any, Dict

from src.services.data_quality import compute_data_quality_report
from src.services.price_oracle import get_price_oracle
from src.services.pricing_aggregator import PricingAggregatorService


class GetDataQualityTool:
    """A single aggregate trust signal for the pricing catalogue.

    Complements check_price_drift (which lists individual disputes) with a
    one-shot summary a customer can check without calling anything else or
    inspecting every model's provenance fields themselves.
    """

    def __init__(self):
        self.service = PricingAggregatorService()

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            oracle = get_price_oracle()
            await oracle.load()

            models, _ = await self.service.get_all_pricing_async(include_unconfirmed=True)
            report = compute_data_quality_report(models, oracle)

            return {"success": True, **report.as_dict()}
        except Exception as e:
            return {"success": False, "error": str(e), "error_type": type(e).__name__}
