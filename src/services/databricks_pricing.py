"""Service for retrieving Databricks Foundation Model API pricing data.

Databricks serves frontier and open-weight models (Claude, GPT, Gemini, Llama,
DeepSeek, Qwen, GLM) through its Foundation Model APIs. It bills in DBUs, not
USD per token, so the USD figures the reference registry carries are
indicative conversions — actual cost depends on the customer's DBU rate and
cloud.

OWNER DECISION (v1.75.0): Databricks has no hand-confirmed core. Every
Databricks model comes from ``data/registry_models/databricks.json`` as a
registry-sourced entry carrying the DBU note below, and the usual "five
provider-confirmed models" requirement is recorded as satisfied-by-exception.
``STATIC_PRICING`` is therefore empty on purpose.
"""
from typing import List
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider

logger = logging.getLogger(__name__)


class DatabricksPricingService(BasePricingProvider):
    """Service to fetch and manage Databricks Foundation Model API pricing."""

    # Per 1k tokens in USD. Empty by decision — see module docstring.
    # Source (DBU rates): https://www.databricks.com/product/pricing/foundation-model-serving
    PRICE_AS_OF = "2026-10-25"

    STATIC_PRICING: dict = {}

    REGISTRY_PRICE_NOTE = (
        "Registry-sourced USD figure. Databricks bills Foundation Model APIs in DBUs, "
        "so actual cost depends on your DBU rate and cloud."
    )

    def __init__(self):
        """Initialize the Databricks pricing service.

        No live model list: Databricks' model endpoints are per-workspace and
        require workspace credentials, so there is no public list to sync against.
        """
        super().__init__("Databricks")

    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Databricks model pricing data.

        The curated core is empty; every model is merged in from the
        registry-sourced JSON by ``get_pricing_with_status()``.

        Returns:
            List of PricingMetrics for Databricks models (empty core)
        """
        return self._get_static_pricing_data()

    def _get_static_pricing_data(self) -> List[PricingMetrics]:
        """Get static pricing metrics for Databricks models (none by decision)."""
        return [
            self.build_metrics(
                model_name, pricing_info,
                source="Databricks Official Pricing (Static)",
                provider="Databricks",
            )
            for model_name, pricing_info in self.STATIC_PRICING.items()
        ]

    @staticmethod
    def get_pricing_data() -> List[PricingMetrics]:
        """Synchronous method for backward compatibility."""
        return DatabricksPricingService()._get_static_pricing_data()
