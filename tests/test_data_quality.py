"""Tests for the data-quality aggregate: src/services/data_quality.py, the
get_data_quality MCP tool, and the /data-quality REST endpoint.

This is the single trust signal a customer can check without inspecting every
model's price_confirmed/price_as_of fields themselves or calling
check_price_drift, which lists individual disputes rather than a summary.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.data_quality import compute_data_quality_report  # noqa: E402
from mcp.tools.get_data_quality import GetDataQualityTool  # noqa: E402


def _pm(name, provider="P", inp=1.0, out=2.0, confirmed=True,
        pricing_model="per_token", price_as_of=None):
    return PricingMetrics(
        model_name=name, provider=provider,
        cost_per_input_token=inp, cost_per_output_token=out,
        price_confirmed=confirmed, pricing_model=pricing_model,
        price_as_of=price_as_of,
    )


class FakeOracle:
    def __init__(self, source="fake", model_count=100, fetched_at="2026-08-10"):
        self.source = source
        self.model_count = model_count
        self.fetched_at = fetched_at

    async def load(self, force=False):
        pass


# ---------------------------------------------------------------------------
# compute_data_quality_report
# ---------------------------------------------------------------------------

class TestComputeDataQualityReport:
    def test_all_confirmed_and_fresh(self):
        models = [
            _pm("a", price_as_of="2026-08-01"),
            _pm("b", price_as_of="2026-08-01"),
        ]
        report = compute_data_quality_report(models, FakeOracle())
        assert report.total_models == 2
        assert report.confirmed_models == 2
        assert report.confirmed_pct == 100.0
        assert report.withheld_for_drift == 0
        assert report.never_priced == 0
        assert report.stale_models == 0
        assert "All 2 priced models" in report.summary

    def test_withheld_for_drift_counted_separately_from_never_priced(self):
        withheld = _pm("withheld", inp=9.0, confirmed=False)  # nonzero price, unconfirmed
        never_priced = _pm("brand-new", inp=0.0, out=0.0, confirmed=False)
        models = [withheld, never_priced]
        report = compute_data_quality_report(models, FakeOracle())
        assert report.withheld_for_drift == 1
        assert report.never_priced == 1
        assert report.confirmed_models == 0

    def test_stale_only_counted_among_confirmed(self):
        stale = _pm("stale", confirmed=True, price_as_of="2020-01-01")
        fresh = _pm("fresh", confirmed=True, price_as_of="2026-08-01")
        report = compute_data_quality_report([stale, fresh], FakeOracle())
        assert report.stale_models == 1
        assert report.stale_pct == pytest.approx(50.0)

    def test_ignores_non_per_token_models(self):
        """Subscription-priced IDE tools have no registry to drift against —
        including them would just dilute the signal."""
        subscription = _pm("copilot", pricing_model="subscription", inp=0.0, out=0.0, confirmed=False)
        priced = _pm("gpt-4o", price_as_of="2026-08-01")
        report = compute_data_quality_report([subscription, priced], FakeOracle())
        assert report.total_models == 1

    def test_empty_list_does_not_divide_by_zero(self):
        report = compute_data_quality_report([], FakeOracle())
        assert report.total_models == 0
        assert report.confirmed_pct == 0.0
        assert report.stale_pct == 0.0

    def test_as_dict_rounds_percentages(self):
        models = [_pm("a", price_as_of="2026-08-01"), _pm("b", inp=0.0, out=0.0, confirmed=False)]
        report = compute_data_quality_report(models, FakeOracle())
        d = report.as_dict()
        assert d["confirmed_pct"] == 50.0
        assert isinstance(d["confirmed_pct"], float)

    def test_carries_registry_metadata(self):
        oracle = FakeOracle(source="litellm", model_count=42, fetched_at="2026-08-09")
        report = compute_data_quality_report([_pm("a")], oracle)
        assert report.registry_source == "litellm"
        assert report.registry_models == 42
        assert report.registry_fetched == "2026-08-09"


# ---------------------------------------------------------------------------
# GetDataQualityTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetDataQualityTool:
    async def test_returns_success_with_report_fields(self):
        models = [_pm("a", price_as_of="2026-08-01")]
        tool = GetDataQualityTool()
        tool.service = AsyncMock()
        tool.service.get_all_pricing_async = AsyncMock(return_value=(models, []))

        with patch("mcp.tools.get_data_quality.get_price_oracle",
                   return_value=FakeOracle()):
            result = await tool.execute({})

        assert result["success"] is True
        assert result["total_models"] == 1
        assert "summary" in result

    async def test_fetches_with_include_unconfirmed(self):
        tool = GetDataQualityTool()
        tool.service = AsyncMock()
        tool.service.get_all_pricing_async = AsyncMock(return_value=([], []))

        with patch("mcp.tools.get_data_quality.get_price_oracle",
                   return_value=FakeOracle()):
            await tool.execute({})

        tool.service.get_all_pricing_async.assert_awaited_once_with(include_unconfirmed=True)

    async def test_failure_is_reported_not_raised(self):
        tool = GetDataQualityTool()
        tool.service = AsyncMock()
        tool.service.get_all_pricing_async = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("mcp.tools.get_data_quality.get_price_oracle",
                   return_value=FakeOracle()):
            result = await tool.execute({})

        assert result["success"] is False
        assert "boom" in result["error"]
