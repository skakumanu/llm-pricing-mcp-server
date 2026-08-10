"""Tests for the check_price_drift MCP tool.

A drifted price is withheld from serving (price_confirmed set to False) the
moment pricing_aggregator detects it — see TestAggregatorDemotesDrift in
test_price_provenance.py and TestDemoteDrifted in test_price_oracle.py. This
tool's job changed accordingly: it used to report drift that was still being
served; it now reports drift that has already been withheld, so a human knows
what to fix in STATIC_PRICING. These tests cover that reporting logic in
isolation from the live registry.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.price_oracle import DriftFinding  # noqa: E402
from mcp.tools.check_price_drift import CheckPriceDriftTool  # noqa: E402


def _pm(name, provider="P", inp=1.0, out=2.0, confirmed=True, source=None):
    return PricingMetrics(
        model_name=name, provider=provider,
        cost_per_input_token=inp, cost_per_output_token=out,
        price_confirmed=confirmed, source=source,
    )


class FakeOracle:
    """Stands in for the real registry so this tool's own logic — not the
    live registry's current contents — is what's under test."""

    def __init__(self, findings=None, model_count=100, source="fake", loaded=True):
        self._findings = findings or []
        self.model_count = model_count
        self.source = source
        self.fetched_at = "2026-08-10"
        self.loaded = loaded

    async def load(self, force=False):
        pass

    def find_drift(self, models, threshold_pct=5.0):
        return self._findings


@pytest.mark.asyncio
class TestCheckPriceDrift:
    async def test_fetches_with_include_unconfirmed(self):
        """Must ask for unconfirmed models too, or withheld ones never reach
        find_drift in the first place — the aggregator already filtered them
        out of the default output by design."""
        tool = CheckPriceDriftTool()
        tool.service = AsyncMock()
        tool.service.get_all_pricing_async = AsyncMock(return_value=([], []))

        with patch("mcp.tools.check_price_drift.get_price_oracle",
                   return_value=FakeOracle()):
            await tool.execute({})

        tool.service.get_all_pricing_async.assert_awaited_once_with(include_unconfirmed=True)

    async def test_reports_a_withheld_model(self):
        """A model the aggregator already withheld still holds its curated
        price and must be reported — this is what lets a human find and fix it."""
        withheld = _pm("drifted-model", inp=9e-06, confirmed=False)
        finding = DriftFinding(
            model_name="drifted-model", provider="P",
            ours_per_1m=9.0, registry_per_1m=1.0, pct_difference=800.0,
            direction="overstated", price_as_of=None,
        )
        tool = CheckPriceDriftTool()
        tool.service = AsyncMock()
        tool.service.get_all_pricing_async = AsyncMock(return_value=([withheld], []))

        with patch("mcp.tools.check_price_drift.get_price_oracle",
                   return_value=FakeOracle(findings=[finding])):
            result = await tool.execute({})

        assert result["success"] is True
        assert result["drift_count"] == 1
        assert result["findings"][0]["model_name"] == "drifted-model"
        assert "withheld" in result["summary"].lower()

    async def test_excludes_never_priced_placeholders_from_the_count(self):
        """A 0.0-cost, never-priced model isn't 'checked' — there's nothing to
        compare it against, unlike a withheld model which still has a price."""
        never_priced = _pm("brand-new", inp=0.0, out=0.0, confirmed=False)
        tool = CheckPriceDriftTool()
        tool.service = AsyncMock()
        tool.service.get_all_pricing_async = AsyncMock(return_value=([never_priced], []))

        with patch("mcp.tools.check_price_drift.get_price_oracle",
                   return_value=FakeOracle()):
            result = await tool.execute({})

        assert result["models_checked"] == 0

    async def test_no_drift_reports_zero(self):
        tool = CheckPriceDriftTool()
        tool.service = AsyncMock()
        tool.service.get_all_pricing_async = AsyncMock(
            return_value=([_pm("fine", inp=1.0)], [])
        )
        with patch("mcp.tools.check_price_drift.get_price_oracle",
                   return_value=FakeOracle(findings=[])):
            result = await tool.execute({})

        assert result["drift_count"] == 0
        assert "No curated price differs" in result["summary"]

    async def test_registry_unavailable_returns_error(self):
        tool = CheckPriceDriftTool()
        tool.service = AsyncMock()
        with patch("mcp.tools.check_price_drift.get_price_oracle",
                   return_value=FakeOracle(loaded=False)):
            result = await tool.execute({})

        assert result["success"] is False
