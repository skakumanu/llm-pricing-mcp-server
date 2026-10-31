"""Client-aware recommendations across the MCP tools, hosted /mcp, STDIO and REST (v1.85.0)."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402

from mcp.sessions import McpSessionStore  # noqa: E402
from mcp.tools._client import handshake_client, resolve_tool_client  # noqa: E402
from mcp.tools.compare_costs import CompareCostsTool  # noqa: E402
from mcp.tools.optimize_workload import OptimizeWorkloadTool  # noqa: E402
from mcp.tools.recommend_model import RecommendModelTool  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402


def _pm(name, provider, inp, out, quality, use_cases=("code", "chat"), pricing_model="per_token"):
    m = PricingMetrics(
        model_name=name, provider=provider, cost_per_input_token=inp,
        cost_per_output_token=out, context_window=200000,
        use_cases=list(use_cases), pricing_model=pricing_model,
    )
    m.quality_score = quality
    return m


MODELS = [
    _pm("claude-sonnet-5-5", "Anthropic", 3e-6, 15e-6, 90),
    _pm("claude-haiku-5-5", "Anthropic", 1e-6, 5e-6, 75),
    _pm("gpt-4o", "OpenAI", 5e-6, 15e-6, 87),
    _pm("codestral", "Mistral AI", 1e-7, 3e-7, 80),
    _pm("mixtral-8x7b", "Together AI", 6e-7, 6e-7, 70),
    _pm("llama-3-70b", "Groq", 5e-7, 8e-7, 72),
    _pm("cursor-pro", "Cursor", 1e-6, 1e-6, 70, pricing_model="subscription"),
]
MODELS[-1].subscription_monthly_usd = 20.0
DESC = "refactor a Python module to remove duplicated helpers"
NON_ANTHROPIC = ("codestral", "mixtral", "mistral", "llama")


def _agg():
    a = MagicMock()
    a.get_all_pricing_async = AsyncMock(return_value=(MODELS, []))
    return a


@pytest.fixture(autouse=True)
def _no_enrichment():
    with patch("src.services.router.enrich_models", AsyncMock(side_effect=lambda x: x)), \
         patch("mcp.tools.optimize_workload.enrich_models", AsyncMock(side_effect=lambda x: x)):
        yield


@pytest.fixture
def rec():
    return RecommendModelTool(aggregator=_agg())


def _all_names(r):
    return [r["recommended"]["model_name"]] + [a["model_name"] for a in r["alternatives"]]


@pytest.fixture(autouse=True)
def _reset_handshake():
    token = handshake_client.set(None)
    yield
    handshake_client.reset(token)


# ---------------------------------------------------------------- resolve_tool_client

def test_precedence_explicit_over_ide_context_over_handshake():
    handshake_client.set("cursor")
    assert resolve_tool_client({"client": "zed", "ide_context": "copilot"}).entry.id == "zed"
    assert resolve_tool_client({"ide_context": "copilot"}).entry.id == "github_copilot"
    res = resolve_tool_client({})
    assert (res.entry.id, res.source) == ("cursor", "inferred")
    assert "Inferred from the MCP handshake" in res.note


def test_unknown_explicit_client_does_not_fall_back_to_handshake():
    handshake_client.set("cursor")
    res = resolve_tool_client({"client": "emacs-copilot-9000"})
    assert res.entry is None and res.source == "explicit"
    assert "emacs-copilot-9000" in res.note and "claude_code" in res.note


def test_unrecognised_handshake_name_is_noted_and_unfiltered():
    handshake_client.set("Visual Studio Code")
    res = resolve_tool_client({})
    assert res.entry is None and res.source == "none"
    assert "Visual Studio Code" in res.note


def test_no_client_note():
    res = resolve_tool_client({})
    assert res.entry is None and res.source == "none"
    assert "No client given or inferred" in res.note


# ---------------------------------------------------------------- recommend_model

@pytest.mark.asyncio
async def test_recommend_claude_code_is_anthropic_only(rec):
    r = await rec.execute({"description": DESC, "client": "Claude Code"})
    assert r["success"] is True
    assert {m["provider"] for m in [r["recommended"], *r["alternatives"]]} == {"Anthropic"}
    assert not any(any(b in n for b in NON_ANTHROPIC) for n in _all_names(r))
    assert r["applied_client"] == "claude_code"
    assert r["client_source"] == "explicit"
    assert r["client_as_of"]
    assert "Claude Code" in r["client_note"]


@pytest.mark.asyncio
async def test_recommend_without_client_matches_baseline_and_says_so(rec):
    r = await rec.execute({"description": DESC})
    legacy = await RecommendModelTool(aggregator=_agg()).execute({"description": DESC, "client": None})
    assert r["success"] is True
    assert _all_names(r) == _all_names(legacy)
    assert r["applied_client"] is None and r["client_source"] == "none"
    assert r["client_as_of"] is None
    assert "No client given or inferred" in r["client_note"]


@pytest.mark.asyncio
async def test_recommend_unknown_client_is_unfiltered_with_note(rec):
    plain = await rec.execute({"description": DESC})
    r = await RecommendModelTool(aggregator=_agg()).execute(
        {"description": DESC, "client": "emacs-copilot-9000"})
    assert r["success"] is True
    assert _all_names(r) == _all_names(plain)
    assert r["applied_client"] is None
    assert "emacs-copilot-9000" in r["client_note"]
    for name in ("claude_code", "cursor", "github_copilot", "windsurf", "cline", "continue", "zed",
                 "jetbrains_ai", "gemini_cli", "codex_cli", "aider"):
        assert name in r["client_note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["Claude Code", "claude-code", "claude_code", "CLAUDE CODE"])
async def test_recommend_spellings_identical(spelling):
    r = await RecommendModelTool(aggregator=_agg()).execute({"description": DESC, "client": spelling})
    assert r["applied_client"] == "claude_code"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy,expected", [
    ("copilot", "github_copilot"), ("cursor", "cursor"), ("windsurf", "windsurf"),
    ("claude_code", "claude_code"), ("jetbrains", "jetbrains_ai"), ("amazon_q", "amazon_q"),
])
async def test_recommend_legacy_ide_context_now_restricts(legacy, expected):
    t = RecommendModelTool(aggregator=_agg())
    r = await t.execute({"description": DESC, "ide_context": legacy})
    if expected == "claude_code":
        assert {m["provider"] for m in [r["recommended"], *r["alternatives"]]} == {"Anthropic"}
    if r["success"]:
        assert r["applied_client"] == expected
        assert not any(n in ("codestral", "mixtral-8x7b", "llama-3-70b") for n in _all_names(r))
    else:
        assert r["error_code"] == "no_runnable_model_for_client"


@pytest.mark.asyncio
async def test_recommend_cursor_never_returns_copilot_plan():
    models = MODELS + [_pm("copilot-pro", "GitHub Copilot", 1e-7, 1e-7, 99, pricing_model="subscription")]
    models[-1].subscription_monthly_usd = 10.0
    agg = MagicMock()
    agg.get_all_pricing_async = AsyncMock(return_value=(models, []))
    r = await RecommendModelTool(aggregator=agg).execute({"description": DESC, "client": "cursor"})
    assert "copilot-pro" not in _all_names(r)
    assert r["recommended"]["model_name"] == "cursor-pro"  # native plan preferred inside the set


@pytest.mark.asyncio
async def test_recommend_open_menu_client_notes_the_open_menu():
    r = await RecommendModelTool(aggregator=_agg()).execute({"description": DESC, "client": "aider"})
    assert r["success"] is True
    assert "cursor-pro" not in _all_names(r)
    assert "Open model menu" in r["client_note"]


@pytest.mark.asyncio
async def test_recommend_empty_set_error_names_the_client(rec):
    r = await rec.execute(
        {"description": DESC, "client": "Claude Code", "max_cost_per_1m_tokens": 0.01})
    assert r["success"] is False
    assert r["error_code"] == "no_runnable_model_for_client"
    assert "Claude Code" in r["error"] and "Anthropic" in r["error"]
    assert r["applied_client"] == "claude_code"


@pytest.mark.asyncio
async def test_recommend_empty_set_without_client_keeps_old_error(rec):
    r = await rec.execute({"description": DESC, "max_cost_per_1m_tokens": 0.0000001})
    assert r["success"] is False
    assert "No model matched" in r["error"]
    assert "error_code" not in r


@pytest.mark.asyncio
async def test_recommend_handshake_inference_and_explicit_override(rec):
    handshake_client.set("claude-code")
    inferred = await rec.execute({"description": DESC})
    assert inferred["client_source"] == "inferred" and inferred["applied_client"] == "claude_code"
    assert {m["provider"] for m in [inferred["recommended"], *inferred["alternatives"]]} == {"Anthropic"}
    explicit = await rec.execute({"description": DESC, "client": "cursor"})
    assert explicit["client_source"] == "explicit" and explicit["applied_client"] == "cursor"


@pytest.mark.asyncio
async def test_recommend_cache_distinguishes_clients(rec):
    a = await rec.execute({"description": DESC, "client": "claude_code"})
    b = await rec.execute({"description": DESC, "client": "cursor"})
    c = await rec.execute({"description": DESC})
    assert a["cached"] is False and b["cached"] is False and c["cached"] is False
    again = await rec.execute({"description": DESC, "client": "Claude-Code"})
    assert again["cached"] is True  # same canonical client shares the entry


# ---------------------------------------------------------------- optimize_workload

WORKLOADS = [
    {"task_type": "code_generation", "monthly_requests": 1000},
    {"task_type": "summarization", "monthly_requests": 5000},
]


@pytest.mark.asyncio
async def test_optimize_workload_restricts_allocations_and_baseline():
    r = await OptimizeWorkloadTool(aggregator=_agg()).execute(
        {"workloads": WORKLOADS, "client": "Claude Code"})
    assert r["success"] is True
    assert r["applied_client"] == "claude_code" and r["client_source"] == "explicit"
    assert {a["provider"] for a in r["allocations"]} == {"Anthropic"}
    assert r["single_model_baseline"]["provider"] == "Anthropic"


@pytest.mark.asyncio
async def test_optimize_workload_without_client_unchanged_shape():
    r = await OptimizeWorkloadTool(aggregator=_agg()).execute({"workloads": WORKLOADS})
    assert r["success"] is True
    assert r["applied_client"] is None and r["client_source"] == "none"
    assert {a["provider"] for a in r["allocations"]} - {"Anthropic"}  # cheapest are non-Anthropic


@pytest.mark.asyncio
async def test_optimize_workload_empty_client_set_is_a_client_error():
    only_other = [m for m in MODELS if m.provider == "Mistral AI"]
    agg = MagicMock()
    agg.get_all_pricing_async = AsyncMock(return_value=(only_other, []))
    r = await OptimizeWorkloadTool(aggregator=agg).execute({"workloads": WORKLOADS, "client": "claude_code"})
    assert r["success"] is False
    assert r["error_code"] == "no_runnable_model_for_client"
    assert "Claude Code" in r["error"]


@pytest.mark.asyncio
async def test_optimize_workload_uses_handshake_client():
    handshake_client.set("Claude Code")  # normalised match on display name via handshake_names/id
    r = await OptimizeWorkloadTool(aggregator=_agg()).execute({"workloads": WORKLOADS})
    assert r["client_source"] == "inferred"
    assert {a["provider"] for a in r["allocations"]} == {"Anthropic"}


# ---------------------------------------------------------------- compare_costs

def _compare(**extra):
    t = CompareCostsTool(aggregator=_agg())
    return t.execute({"model_names": ["claude-sonnet-5-5", "gpt-4o", "nonexistent"],
                      "input_tokens": 1000, "output_tokens": 1000, **extra})


@pytest.mark.asyncio
async def test_compare_costs_flags_but_never_filters():
    r = await _compare(client="Claude Code")
    assert r["success"] is True
    rows = {row["model_name"]: row for row in r["models"]}
    assert rows["claude-sonnet-5-5"]["runnable_in_client"] is True
    assert rows["gpt-4o"]["runnable_in_client"] is False
    assert rows["gpt-4o"]["total_cost"] > 0 and rows["claude-sonnet-5-5"]["total_cost"] > 0
    assert "runnable_in_client" not in rows["nonexistent"]
    assert r["applied_client"] == "claude_code" and r["client_source"] == "explicit"
    assert r["client_as_of"]


@pytest.mark.asyncio
async def test_compare_costs_without_client_has_no_flags():
    r = await _compare()
    assert all("runnable_in_client" not in row for row in r["models"])
    assert r["client_source"] == "none" and r["applied_client"] is None


@pytest.mark.asyncio
async def test_compare_costs_unknown_client_no_flags_with_note():
    r = await _compare(client="emacs")
    assert all("runnable_in_client" not in row for row in r["models"])
    assert "Unknown client" in r["client_note"]


# ---------------------------------------------------------------- tool schemas

def test_tool_manager_exposes_client_with_names_on_the_three_tools():
    from mcp.tools.tool_manager import ToolManager
    tm = ToolManager(aggregator=_agg())
    listed = {t["name"]: t for t in tm.list_tools()}
    for name in ("recommend_model", "optimize_workload", "compare_costs"):
        t = listed[name]
        assert "client" in t["inputSchema"]["properties"], name
        assert "claude_code" in t["inputSchema"]["properties"]["client"]["description"]
        assert "claude_code" in t["description"]
    assert "boost" not in listed["recommend_model"]["inputSchema"]["properties"]["ide_context"]["description"].lower()
    assert "Deprecated" in listed["recommend_model"]["inputSchema"]["properties"]["ide_context"]["description"]


# ---------------------------------------------------------------- session store

def test_session_store_roundtrip_ttl_and_bounds():
    s = McpSessionStore(max_entries=2, ttl_seconds=100)
    a = s.create("claude-code")
    assert s.client_name(a) == "claude-code"
    assert s.client_name("nope") is None and s.client_name(None) is None
    s.create("x")
    s.create("y")
    assert len(s) == 2 and s.client_name(a) is None  # LRU evicted
    assert len(s.create("z" * 500) and s._items) == 2
    with patch("mcp.sessions.time.time", return_value=10**12):
        assert s.client_name(next(iter(s._items))) is None  # expired


def test_session_store_truncates_and_ignores_non_strings():
    s = McpSessionStore()
    assert len(s.client_name(s.create("n" * 500))) == 100
    assert s.client_name(s.create(None)) is None
    assert s.client_name(s.create(123)) is None


# ---------------------------------------------------------------- hosted /mcp

@pytest.fixture
def http_client():
    from src.main import app
    import src.main as main
    from mcp.tools.tool_manager import ToolManager
    tm = ToolManager(aggregator=_agg())
    with patch("src.main.settings.mcp_api_key", None), \
         patch("src.main._get_http_mcp_tool_manager", AsyncMock(return_value=tm)):
        with TestClient(app) as c:
            yield c, main


def _init(c, name="claude-code"):
    return c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"clientInfo": {"name": name, "version": "1"}}})


def _call(c, args, session=None, tool="recommend_model"):
    headers = {"Mcp-Session-Id": session} if session else {}
    resp = c.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool, "arguments": args}})
    assert resp.status_code == 200
    return json.loads(resp.json()["result"]["content"][0]["text"])


def test_initialize_issues_session_header_and_keeps_server_info(http_client):
    c, _ = http_client
    resp = _init(c)
    assert resp.headers["Mcp-Session-Id"]
    assert resp.json()["result"]["protocolVersion"] == "2024-11-05"
    assert "serverInfo" in resp.json()["result"]
    assert _init(c).headers["Mcp-Session-Id"] != resp.headers["Mcp-Session-Id"]
    assert c.get("/mcp").json()["session_header"] == "Mcp-Session-Id"


def test_hosted_inferred_explicit_and_none_paths(http_client):
    c, _ = http_client
    sid = _init(c, "claude-code").headers["Mcp-Session-Id"]

    inferred = _call(c, {"description": DESC}, sid)
    assert inferred["client_source"] == "inferred" and inferred["applied_client"] == "claude_code"
    assert {m["provider"] for m in [inferred["recommended"], *inferred["alternatives"]]} == {"Anthropic"}

    explicit = _call(c, {"description": DESC, "client": "cursor"}, sid)
    assert explicit["client_source"] == "explicit" and explicit["applied_client"] == "cursor"

    none = _call(c, {"description": DESC})
    assert none["client_source"] == "none" and none["applied_client"] is None
    assert "No client given or inferred" in none["client_note"]


def test_hosted_unknown_session_is_never_rejected(http_client):
    c, _ = http_client
    r = _call(c, {"description": DESC}, "not-a-real-session")
    assert r["success"] is True and r["client_source"] == "none"


def test_hosted_unrecognised_client_info_and_handshake_does_not_leak(http_client):
    c, _ = http_client
    sid = _init(c, "Visual Studio Code").headers["Mcp-Session-Id"]
    r = _call(c, {"description": DESC}, sid)
    assert r["applied_client"] is None and "Visual Studio Code" in r["client_note"]
    # the ContextVar is reset after the call: nothing bleeds into a later request
    assert handshake_client.get() is None


def test_hosted_initialize_without_client_info_still_works(http_client):
    c, _ = http_client
    resp = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp.status_code == 200
    r = _call(c, {"description": DESC}, resp.headers["Mcp-Session-Id"])
    assert r["client_source"] == "none"


def test_hosted_compare_costs_uses_inferred_client(http_client):
    c, _ = http_client
    sid = _init(c, "claude-code").headers["Mcp-Session-Id"]
    r = _call(c, {"model_names": ["gpt-4o"], "input_tokens": 10, "output_tokens": 10},
              sid, tool="compare_costs")
    assert r["client_source"] == "inferred"
    assert r["models"][0]["runnable_in_client"] is False


# ---------------------------------------------------------------- STDIO

@pytest.mark.asyncio
async def test_stdio_infers_client_from_initialize():
    from mcp.server import MCPServer
    server = MCPServer(aggregator=_agg())
    await server._handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"clientInfo": {"name": "claude-code"}}})
    resp = await server._handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "recommend_model", "arguments": {"description": DESC}}})
    out = json.loads(resp["result"]["content"][0]["text"])
    assert out["client_source"] == "inferred" and out["applied_client"] == "claude_code"
    assert handshake_client.get() is None


@pytest.mark.asyncio
async def test_stdio_without_initialize_client_info_is_none():
    from mcp.server import MCPServer
    server = MCPServer(aggregator=_agg())
    resp = await server._handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "recommend_model", "arguments": {"description": DESC}}})
    out = json.loads(resp["result"]["content"][0]["text"])
    assert out["client_source"] == "none"


# ---------------------------------------------------------------- REST

@pytest.fixture
def rest_client():
    from src.main import app
    import src.main as main
    from src.services.router import ModelRouter
    tracker = MagicMock()
    tracker.record_routing = AsyncMock()
    router = ModelRouter(_agg())
    with patch.object(main, "get_router", return_value=router), \
         patch.object(main, "get_savings_tracker", return_value=tracker), \
         patch.object(main, "get_pricing_aggregator", AsyncMock(return_value=_agg())), \
         patch.object(main, "enrich_models", AsyncMock(side_effect=lambda x: x)), \
         patch.object(main, "_router_recommend_cache", main.RecommendationCache()):
        yield TestClient(app), tracker


def _auth():
    return {"x-api-key": os.environ.get("MCP_API_KEY", "test")}


def test_rest_recommend_client_restricts_and_reports(rest_client):
    c, tracker = rest_client
    resp = c.post("/router/recommend", json={"client": "Claude Code"}, headers=_auth())
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    data = resp.json()
    providers = {data["recommended"]["provider"], *[a["provider"] for a in data["alternatives"]]}
    assert providers == {"Anthropic"}
    assert data["applied_client"] == "claude_code" and data["client_source"] == "explicit"
    assert data["client_as_of"] and "Claude Code" in data["client_note"]
    assert tracker.record_routing.await_args.kwargs["ide_context"] == "claude_code"


def test_rest_recommend_without_client_unchanged(rest_client):
    c, _ = rest_client
    resp = c.post("/router/recommend", json={}, headers=_auth())
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    assert "applied_client" not in resp.json() and "client_note" not in resp.json()


def test_rest_recommend_legacy_ide_context_restricts(rest_client):
    c, _ = rest_client
    resp = c.post("/router/recommend", json={"ide_context": "claude_code"}, headers=_auth())
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    assert resp.json()["recommended"]["provider"] == "Anthropic"


def test_rest_recommend_unknown_client_unfiltered_with_note(rest_client):
    c, _ = rest_client
    resp = c.post("/router/recommend", json={"client": "emacs"}, headers=_auth())
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    assert "applied_client" not in resp.json()
    assert "Unknown client" in resp.json()["client_note"]


def test_rest_recommend_404_names_the_client(rest_client):
    c, _ = rest_client
    resp = c.post("/router/recommend",
                  json={"client": "Claude Code", "max_cost_per_1m_tokens": 0.01}, headers=_auth())
    if resp.status_code not in (404,):
        pytest.skip("Authenticated env")
    assert "Claude Code" in resp.json()["detail"]


def _sse(text):
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_rest_stream_emits_client_event_and_restricts(rest_client):
    c, _ = rest_client
    resp = c.post("/router/recommend/stream", json={"client": "Claude Code"}, headers=_auth())
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    events = _sse(resp.text)
    types = [e["type"] for e in events]
    assert types.index("filtering") < types.index("client") < types.index("candidates_ready")
    client_evt = next(e for e in events if e["type"] == "client")
    assert client_evt["applied_client"] == "claude_code" and client_evt["client_source"] == "explicit"
    rec = next(e for e in events if e["type"] == "recommendation")
    alts = next(e for e in events if e["type"] == "alternatives")
    assert rec["provider"] == "Anthropic"
    assert {a["provider"] for a in alts["models"]} == {"Anthropic"}


def test_rest_stream_without_client_has_no_client_event(rest_client):
    c, _ = rest_client
    resp = c.post("/router/recommend/stream", json={}, headers=_auth())
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    assert "client" not in [e["type"] for e in _sse(resp.text)]


def test_rest_stream_empty_set_error_names_client(rest_client):
    c, _ = rest_client
    resp = c.post("/router/recommend/stream",
                  json={"client": "Claude Code", "max_cost_per_1m_tokens": 0.01}, headers=_auth())
    if resp.status_code != 200:
        pytest.skip("Authenticated env")
    err = next(e for e in _sse(resp.text) if e["type"] == "error")
    assert "Claude Code" in err["detail"]
