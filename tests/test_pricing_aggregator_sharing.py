"""Tests for the shared process-wide pricing-aggregator singleton.

Covers the core fix: MCP tools (STDIO and HTTP /mcp transports) and REST
endpoints must share one PricingAggregatorService instance per process
instead of each constructing its own, so pricing/cost answers agree within
the shared cache's TTL and a second surface doesn't force a redundant fetch.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402

from mcp.tools.tool_manager import ToolManager  # noqa: E402
from mcp.server import MCPServer  # noqa: E402
from src.services.pricing_aggregator import PricingAggregatorService, get_pricing_aggregator  # noqa: E402
import src.services.pricing_aggregator as pricing_aggregator_module  # noqa: E402

# Every tool whose __init__ now accepts an optional `aggregator` and stores it
# as `self.service` (the 13 tools the Design identified as directly
# constructing PricingAggregatorService()).
PRICING_TOOL_NAMES = [
    "get_all_pricing",
    "estimate_cost",
    "compare_costs",
    "get_performance_metrics",
    "get_use_cases",
    "predict_cost",
    "optimize_workload",
    "recommend_model",
    "check_price_drift",
    "get_data_quality",
    "record_usage",
    "analyze_session_usage",
    "import_session_usage",
]


@pytest.fixture(autouse=True)
def _reset_aggregator_singleton():
    """Isolate the module-level singleton across tests."""
    pricing_aggregator_module._aggregator = None
    yield
    pricing_aggregator_module._aggregator = None


# ---------------------------------------------------------------------------
# get_pricing_aggregator() singleton behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_pricing_aggregator_returns_same_instance_across_calls():
    first = await get_pricing_aggregator()
    second = await get_pricing_aggregator()
    assert first is second
    assert isinstance(first, PricingAggregatorService)


@pytest.mark.asyncio
async def test_get_pricing_aggregator_concurrent_calls_single_instance():
    """Lock-guarded double-checked init: concurrent first callers must still
    converge on exactly one instance, not one each."""
    import asyncio

    results = await asyncio.gather(*(get_pricing_aggregator() for _ in range(10)))
    assert len({id(r) for r in results}) == 1


# ---------------------------------------------------------------------------
# ToolManager — forwards (or doesn't) the aggregator to every pricing tool
# ---------------------------------------------------------------------------

def test_tool_manager_forwards_shared_aggregator_to_every_pricing_tool():
    sentinel = object()
    tm = ToolManager(aggregator=sentinel)
    for name in PRICING_TOOL_NAMES:
        instance = tm.tools[name]["instance"]
        assert instance.service is sentinel, f"{name} did not receive the shared aggregator"


def test_tool_manager_without_aggregator_each_tool_builds_its_own():
    """Unchanged prior behavior: no-arg ToolManager() still works (e.g. direct
    test instantiation), with each pricing tool falling back to its own instance."""
    tm = ToolManager()
    assert tm.aggregator is None
    services = [tm.tools[name]["instance"].service for name in PRICING_TOOL_NAMES]
    for svc in services:
        assert isinstance(svc, PricingAggregatorService)
    # Each tool got its own instance, not a shared one — matches pre-fix behavior
    # for the common/default (no shared aggregator supplied) path.
    assert len({id(s) for s in services}) == len(services)


def test_tool_manager_input_schemas_unchanged_shape():
    """Sanity check that adding the aggregator param didn't touch any input_schema."""
    tm = ToolManager()
    tools = tm.list_tools()
    assert len(tools) == 31
    for tool in tools:
        assert set(tool.keys()) == {"name", "description", "inputSchema"}


# ---------------------------------------------------------------------------
# MCPServer (STDIO transport) — wires the shared aggregator into its ToolManager
# ---------------------------------------------------------------------------

def test_mcp_server_forwards_aggregator_to_tool_manager():
    sentinel = object()
    server = MCPServer(aggregator=sentinel)
    assert server.tool_manager.aggregator is sentinel
    assert server.tool_manager.tools["estimate_cost"]["instance"].service is sentinel
    assert server.tool_manager.tools["compare_costs"]["instance"].service is sentinel


def test_mcp_server_default_has_no_shared_aggregator():
    server = MCPServer()
    assert server.tool_manager.aggregator is None


# ---------------------------------------------------------------------------
# HTTP /mcp transport (src/main.py) — shares the same singleton REST uses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_http_mcp_tool_manager_is_built_once_and_shared():
    """Two tools/list-equivalent calls must reuse the same ToolManager, not
    rebuild (and re-fetch through) a fresh one each time."""
    import src.main as main

    main._http_mcp_tool_manager = None
    first = await main._get_http_mcp_tool_manager()
    second = await main._get_http_mcp_tool_manager()
    assert first is second
    main._http_mcp_tool_manager = None


@pytest.mark.asyncio
async def test_http_mcp_tool_manager_uses_rest_aggregator_singleton():
    """The HTTP MCP transport's ToolManager must be built with the exact same
    aggregator instance REST endpoints get from get_pricing_aggregator() —
    the crux of avoiding divergent pricing caches between the two surfaces."""
    import src.main as main

    main._http_mcp_tool_manager = None
    rest_aggregator = await get_pricing_aggregator()
    tm = await main._get_http_mcp_tool_manager()
    assert tm.tools["estimate_cost"]["instance"].service is rest_aggregator
    main._http_mcp_tool_manager = None


@pytest.mark.asyncio
async def test_http_mcp_tool_manager_does_not_rebuild_concurrently():
    import asyncio
    import src.main as main

    main._http_mcp_tool_manager = None
    results = await asyncio.gather(*(main._get_http_mcp_tool_manager() for _ in range(10)))
    assert len({id(r) for r in results}) == 1
    main._http_mcp_tool_manager = None


# ---------------------------------------------------------------------------
# End-to-end: a single running process triggers only one fetch cycle when two
# different MCP tools (or an MCP tool and a REST call) need pricing data
# within the TTL.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_tools_sharing_one_aggregator_hit_the_same_fetch_path():
    """With a shared aggregator, two different tools' pricing calls both flow
    through the identical PricingAggregatorService.get_all_pricing_async —
    i.e. one shared fetch path/cache, not two independent ones."""
    shared = PricingAggregatorService()
    call_count = {"n": 0}
    original = shared.get_all_pricing_async

    async def counting_get_all_pricing_async(*args, **kwargs):
        call_count["n"] += 1
        return await original(*args, **kwargs)

    shared.get_all_pricing_async = counting_get_all_pricing_async  # type: ignore[method-assign]

    tm = ToolManager(aggregator=shared)
    get_all_pricing_tool = tm.tools["get_all_pricing"]["instance"]
    get_use_cases_tool = tm.tools["get_use_cases"]["instance"]

    assert get_all_pricing_tool.service is shared
    assert get_use_cases_tool.service is shared

    await get_all_pricing_tool.execute({})
    await get_use_cases_tool.execute({})

    # Both tools went through the one shared aggregator instance's fetch path
    # (not two independently-constructed aggregators each with their own).
    assert call_count["n"] == 2
    assert get_all_pricing_tool.service is get_use_cases_tool.service


# ---------------------------------------------------------------------------
# REST <-> MCP parity (acceptance criteria 1 & 2)
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    with patch("src.main.settings.mcp_api_key", None):
        from src.main import app
        with TestClient(app) as c:
            yield c


def test_cost_estimate_rest_and_mcp_tool_agree_for_known_model(client):
    pricing_resp = client.get("/pricing")
    assert pricing_resp.status_code == 200
    models = pricing_resp.json()["models"]
    assert models
    model_name = models[0]["model_name"]

    rest_resp = client.post("/cost-estimate", json={
        "model_name": model_name, "input_tokens": 1000, "output_tokens": 500,
    })
    assert rest_resp.status_code == 200
    rest_data = rest_resp.json()

    mcp_resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {
            "name": "estimate_cost",
            "arguments": {"model_name": model_name, "input_tokens": 1000, "output_tokens": 500},
        },
    })
    assert mcp_resp.status_code == 200
    import json as _json
    mcp_data = _json.loads(mcp_resp.json()["result"]["content"][0]["text"])
    assert mcp_data["success"] is True

    # estimate_cost.py rounds to 6dp; POST /cost-estimate does not. Compare at
    # that precision — both derive from the identical compute_token_cost() math.
    assert round(rest_data["input_cost"], 6) == mcp_data["input_cost"]
    assert round(rest_data["output_cost"], 6) == mcp_data["output_cost"]
    assert round(rest_data["total_cost"], 6) == mcp_data["total_cost"]


def test_cost_estimate_rest_and_mcp_tool_agree_model_not_found(client):
    bogus_model = "definitely-not-a-real-model-xyz-12345"

    rest_resp = client.post("/cost-estimate", json={
        "model_name": bogus_model, "input_tokens": 100, "output_tokens": 100,
    })
    assert rest_resp.status_code == 404

    mcp_resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {
            "name": "estimate_cost",
            "arguments": {"model_name": bogus_model, "input_tokens": 100, "output_tokens": 100},
        },
    })
    assert mcp_resp.status_code == 200
    import json as _json
    mcp_data = _json.loads(mcp_resp.json()["result"]["content"][0]["text"])
    assert mcp_data["success"] is False
    assert "not found" in mcp_data["error"].lower()
    assert "not found" in rest_resp.json()["detail"].lower()


def test_compare_costs_batch_rest_and_mcp_tool_agree_for_known_model(client):
    pricing_resp = client.get("/pricing")
    models = pricing_resp.json()["models"]
    model_name = models[0]["model_name"]

    rest_resp = client.post("/cost-estimate/batch", json={
        "model_names": [model_name], "input_tokens": 2000, "output_tokens": 300,
    })
    assert rest_resp.status_code == 200
    rest_model = rest_resp.json()["models"][0]

    mcp_resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {
            "name": "compare_costs",
            "arguments": {"model_names": [model_name], "input_tokens": 2000, "output_tokens": 300},
        },
    })
    import json as _json
    mcp_data = _json.loads(mcp_resp.json()["result"]["content"][0]["text"])
    mcp_model = mcp_data["models"][0]

    assert round(rest_model["input_cost"], 6) == mcp_model["input_cost"]
    assert round(rest_model["output_cost"], 6) == mcp_model["output_cost"]
    assert round(rest_model["total_cost"], 6) == mcp_model["total_cost"]
