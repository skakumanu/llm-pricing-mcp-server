"""Tests for the portfolio optimizer engine and the optimize_workload MCP tool."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.portfolio_optimizer import (  # noqa: E402
    Workload,
    cost_per_request,
    optimize,
)
from mcp.tools.optimize_workload import OptimizeWorkloadTool  # noqa: E402


def _model(name, provider, inp, out, quality=None, ctx=128000,
           fn=False, vision=False, pricing_model="per_token"):
    m = PricingMetrics(
        model_name=name,
        provider=provider,
        cost_per_input_token=inp,
        cost_per_output_token=out,
        context_window=ctx,
        supports_function_calling=fn,
        supports_vision=vision,
        pricing_model=pricing_model,
    )
    m.quality_score = quality
    return m


# cheap → expensive, quality ascending with price
MODELS = [
    _model("tiny", "ProviderA", 0.05, 0.10, quality=40),
    _model("small", "ProviderB", 0.20, 0.60, quality=62, fn=True),
    _model("mid", "Anthropic", 1.00, 3.00, quality=80, fn=True),
    _model("large", "OpenAI", 5.00, 15.00, quality=94, fn=True, vision=True),
    _model("sub-tool", "Cursor", 0.0, 0.0, quality=85, pricing_model="subscription"),
]


# ---------------------------------------------------------------------------
# cost_per_request
# ---------------------------------------------------------------------------

class TestCostPerRequest:
    def test_basic_math(self):
        m = _model("x", "P", 1.0, 2.0)  # $1/1k in, $2/1k out
        # 1000 in + 500 out = 1.0 + 1.0 = 2.0
        assert cost_per_request(m, 1000, 500) == pytest.approx(2.0)

    def test_zero_tokens(self):
        m = _model("x", "P", 1.0, 2.0)
        assert cost_per_request(m, 0, 0) == 0.0


# ---------------------------------------------------------------------------
# Workload
# ---------------------------------------------------------------------------

class TestWorkload:
    def test_output_derived_from_task_profile(self):
        wl = Workload(task_type="classification", monthly_requests=10)
        assert wl.resolved_output_tokens() == 15  # fixed profile

    def test_explicit_output_overrides_profile(self):
        wl = Workload(task_type="classification", monthly_requests=10, avg_output_tokens=999)
        assert wl.resolved_output_tokens() == 999

    def test_ratio_profile_scales_with_input(self):
        wl = Workload(task_type="summarization", monthly_requests=1, avg_input_tokens=1000)
        assert wl.resolved_output_tokens() == 150  # 0.15 ratio

    def test_display_name_prefers_label(self):
        assert Workload("qa", 1, label="Support bot").display_name() == "Support bot"
        assert Workload("qa", 1).display_name() == "qa"


# ---------------------------------------------------------------------------
# optimize()
# ---------------------------------------------------------------------------

class TestOptimize:
    def test_empty_workloads(self):
        r = optimize(MODELS, [])
        assert r.allocations == []
        assert r.total_monthly_cost == 0.0

    def test_picks_cheapest_qualifying_model(self):
        wl = Workload(task_type="classification", monthly_requests=1000)
        r = optimize(MODELS, [wl])
        assert len(r.allocations) == 1
        assert r.allocations[0].model.model_name == "tiny"

    def test_quality_floor_excludes_cheap_models(self):
        wl = Workload(task_type="classification", monthly_requests=100, min_quality_score=75)
        r = optimize(MODELS, [wl])
        assert r.allocations[0].model.quality_score >= 75

    def test_global_quality_floor_applies(self):
        wl = Workload(task_type="classification", monthly_requests=100)
        r = optimize(MODELS, [wl], min_quality_score=90)
        assert r.allocations[0].model.model_name == "large"

    def test_stricter_of_global_and_per_workload_floor_wins(self):
        wl = Workload(task_type="classification", monthly_requests=100, min_quality_score=50)
        r = optimize(MODELS, [wl], min_quality_score=90)
        assert r.allocations[0].model.quality_score >= 90

    def test_function_calling_requirement(self):
        wl = Workload(task_type="function_calling", monthly_requests=100,
                      require_function_calling=True)
        r = optimize(MODELS, [wl])
        assert r.allocations[0].model.supports_function_calling is True

    def test_vision_requirement(self):
        wl = Workload(task_type="qa", monthly_requests=100, require_vision=True)
        r = optimize(MODELS, [wl])
        assert r.allocations[0].model.supports_vision is True

    def test_context_window_requirement(self):
        small_ctx = _model("narrow", "P", 0.001, 0.001, quality=50, ctx=4096)
        models = MODELS + [small_ctx]
        wl = Workload(task_type="qa", monthly_requests=100, min_context_tokens=100000)
        r = optimize(models, [wl])
        assert r.allocations[0].model.model_name != "narrow"

    def test_subscription_models_excluded(self):
        wl = Workload(task_type="qa", monthly_requests=100)
        r = optimize(MODELS, [wl])
        names = {a.model.model_name for a in r.allocations}
        assert "sub-tool" not in names

    def test_unsatisfiable_workload_reported(self):
        wl = Workload(task_type="qa", monthly_requests=100, min_quality_score=99.9)
        r = optimize(MODELS, [wl])
        assert r.allocations == []
        assert len(r.unsatisfiable) == 1
        assert "qa" in r.unsatisfiable[0]

    def test_mixed_workloads_use_different_models(self):
        workloads = [
            Workload("classification", 10000, label="triage"),
            Workload("reasoning", 500, min_quality_score=90, label="hard analysis"),
        ]
        r = optimize(MODELS, workloads)
        assert len(r.allocations) == 2
        assert r.allocations[0].model.model_name == "tiny"
        assert r.allocations[1].model.model_name == "large"

    def test_total_cost_is_sum_of_allocations(self):
        workloads = [
            Workload("classification", 1000),
            Workload("qa", 500),
        ]
        r = optimize(MODELS, workloads)
        assert r.total_monthly_cost == pytest.approx(
            sum(a.monthly_cost for a in r.allocations)
        )

    def test_monthly_cost_scales_with_volume(self):
        r1 = optimize(MODELS, [Workload("classification", 1000)])
        r2 = optimize(MODELS, [Workload("classification", 2000)])
        assert r2.total_monthly_cost == pytest.approx(r1.total_monthly_cost * 2)


class TestBaselineAndSavings:
    def test_baseline_is_cheapest_universal_model(self):
        workloads = [Workload("classification", 1000), Workload("qa", 1000)]
        r = optimize(MODELS, workloads)
        assert r.baseline_model is not None
        assert r.baseline_model.model_name == "tiny"

    def test_savings_zero_when_one_model_serves_all(self):
        # Single workload with no constraints: portfolio == baseline
        r = optimize(MODELS, [Workload("classification", 1000)])
        assert r.savings_usd == pytest.approx(0.0)

    def test_savings_positive_when_tiering_helps(self):
        # High-quality floor on a low-volume task, none on a high-volume task.
        # Baseline must use the expensive model for BOTH; portfolio only for one.
        workloads = [
            Workload("classification", 100000, label="bulk"),
            Workload("reasoning", 100, min_quality_score=90, label="hard"),
        ]
        r = optimize(MODELS, workloads)
        assert r.baseline_model.model_name == "large"
        assert r.savings_usd > 0
        assert r.savings_pct > 0
        assert r.total_monthly_cost < r.baseline_monthly_cost

    def test_no_baseline_when_no_universal_model(self):
        # vision required for one task, and the only vision model fails the other's
        # quality ceiling is not expressible — instead require an impossible combo.
        narrow = _model("visiononly", "P", 0.01, 0.01, quality=30, ctx=8000, vision=True)
        models = [narrow, _model("textonly", "P", 0.01, 0.01, quality=95, ctx=200000)]
        workloads = [
            Workload("qa", 100, require_vision=True),
            Workload("reasoning", 100, min_quality_score=90),
        ]
        r = optimize(models, workloads)
        assert r.baseline_model is None
        assert r.savings_usd is None
        assert len(r.allocations) == 2


class TestBudget:
    def test_within_budget_flag_true(self):
        r = optimize(MODELS, [Workload("classification", 100)], monthly_budget_usd=1000.0)
        assert r.within_budget is True

    def test_within_budget_flag_false_when_floor_exceeds(self):
        r = optimize(
            MODELS,
            [Workload("content_generation", 1000000, min_quality_score=90)],
            monthly_budget_usd=1.0,
        )
        assert r.within_budget is False

    def test_budget_headroom_triggers_quality_upgrade(self):
        # Cheapest is 'tiny' (q=40). With generous budget the optimizer should
        # upgrade toward higher quality.
        wl = Workload("classification", 1000)
        r = optimize(MODELS, [wl], monthly_budget_usd=10000.0)
        assert r.allocations[0].model.quality_score > 40
        assert r.allocations[0].upgraded is True
        assert len(r.upgrades_applied) >= 1

    def test_upgrades_respect_budget_ceiling(self):
        wl = Workload("classification", 1000)
        r = optimize(MODELS, [wl], monthly_budget_usd=10000.0)
        assert r.total_monthly_cost <= 10000.0

    def test_no_upgrades_without_budget(self):
        r = optimize(MODELS, [Workload("classification", 1000)])
        assert r.upgrades_applied == []
        assert r.allocations[0].model.model_name == "tiny"

    def test_tight_budget_keeps_cheapest(self):
        wl = Workload("classification", 1000)
        floor = optimize(MODELS, [wl]).total_monthly_cost
        r = optimize(MODELS, [wl], monthly_budget_usd=floor * 1.0001)
        assert r.allocations[0].model.model_name == "tiny"
        assert r.upgrades_applied == []


# ---------------------------------------------------------------------------
# OptimizeWorkloadTool
# ---------------------------------------------------------------------------

@pytest.fixture
def tool():
    t = OptimizeWorkloadTool()
    svc = MagicMock()
    svc.get_all_pricing_async = AsyncMock(return_value=(MODELS, []))
    t.service = svc
    return t


@pytest.fixture(autouse=True)
def _no_enrich():
    """enrich_models would hit the network; return models unchanged."""
    async def passthrough(models):
        return models
    with patch("mcp.tools.optimize_workload.enrich_models", side_effect=passthrough):
        yield


@pytest.mark.asyncio
async def test_tool_basic(tool):
    r = await tool.execute({
        "workloads": [{"task_type": "classification", "monthly_requests": 1000}]
    })
    assert r["success"] is True
    assert len(r["allocations"]) == 1
    assert r["total_monthly_cost_usd"] >= 0


@pytest.mark.asyncio
async def test_tool_multi_workload_summary(tool):
    r = await tool.execute({
        "workloads": [
            {"task_type": "classification", "monthly_requests": 100000, "label": "bulk"},
            {"task_type": "reasoning", "monthly_requests": 100, "min_quality_score": 90},
        ]
    })
    assert r["success"] is True
    assert len(r["allocations"]) == 2
    assert r["savings_vs_baseline_usd"] > 0
    assert "saved" in r["summary"]


@pytest.mark.asyncio
async def test_tool_reports_distinct_models(tool):
    r = await tool.execute({
        "workloads": [
            {"task_type": "classification", "monthly_requests": 1000},
            {"task_type": "reasoning", "monthly_requests": 100, "min_quality_score": 90},
        ]
    })
    assert r["distinct_models_used"] == 2


@pytest.mark.asyncio
async def test_tool_label_used_in_output(tool):
    r = await tool.execute({
        "workloads": [{"task_type": "qa", "monthly_requests": 10, "label": "Support bot"}]
    })
    assert r["allocations"][0]["workload"] == "Support bot"


@pytest.mark.asyncio
async def test_tool_budget_exceeded_note(tool):
    r = await tool.execute({
        "workloads": [{
            "task_type": "content_generation",
            "monthly_requests": 1000000,
            "min_quality_score": 90,
        }],
        "monthly_budget_usd": 1.0,
    })
    assert r["success"] is True
    assert r["within_budget"] is False
    assert "exceeds" in r["budget_note"]


@pytest.mark.asyncio
async def test_tool_missing_workloads(tool):
    r = await tool.execute({})
    assert r["success"] is False
    assert "workloads" in r["error"]


@pytest.mark.asyncio
async def test_tool_empty_workloads_list(tool):
    r = await tool.execute({"workloads": []})
    assert r["success"] is False


@pytest.mark.asyncio
async def test_tool_invalid_task_type(tool):
    r = await tool.execute({
        "workloads": [{"task_type": "banana", "monthly_requests": 10}]
    })
    assert r["success"] is False
    assert "banana" in r["error"]


@pytest.mark.asyncio
async def test_tool_missing_monthly_requests(tool):
    r = await tool.execute({"workloads": [{"task_type": "qa"}]})
    assert r["success"] is False
    assert "monthly_requests" in r["error"]


@pytest.mark.asyncio
async def test_tool_negative_monthly_requests(tool):
    r = await tool.execute({
        "workloads": [{"task_type": "qa", "monthly_requests": -5}]
    })
    assert r["success"] is False


@pytest.mark.asyncio
async def test_tool_rejects_non_positive_budget(tool):
    r = await tool.execute({
        "workloads": [{"task_type": "qa", "monthly_requests": 10}],
        "monthly_budget_usd": 0,
    })
    assert r["success"] is False


@pytest.mark.asyncio
async def test_tool_all_workloads_unsatisfiable(tool):
    r = await tool.execute({
        "workloads": [{"task_type": "qa", "monthly_requests": 10, "min_quality_score": 99.9}]
    })
    assert r["success"] is False
    assert r["unsatisfiable"]
