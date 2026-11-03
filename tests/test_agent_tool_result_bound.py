"""v1.90.1: tool results fed back to the agent's model are bounded, and the agent's
get_all_pricing is a compact, filterable edition of the catalogue.

Background: "What's the cheapest model?" made the pricing agent call get_all_pricing
and hand the whole catalogue (600+ models, ~140k tokens) to the model, which exceeded
a 128k context window in production and returned a provider error to the user.
"""
import json
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent.react_loop import (  # noqa: E402
    MAX_TOOL_RESULT_CHARS,
    ReActLoop,
    SYSTEM_PROMPT,
    bound_tool_result,
)
from agent.tools import (  # noqa: E402
    COMPACT_PRICING_LIMIT_DEFAULT,
    COMPACT_PRICING_LIMIT_MAX,
    build_agent_tools,
    compact_pricing_result,
    compact_pricing_schema,
)
from agent.llm_backend import LLMBackend, LLMResponse  # noqa: E402


def _model(name, provider, inp, out, **extra):
    m = {
        "model_name": name, "provider": provider,
        "cost_per_input_token": inp, "cost_per_output_token": out,
        "context_window": 128000, "price_as_of": "2026-11-01", "price_confirmed": True,
        "price_source_kind": "provider", "use_cases": ["a"] * 20, "strengths": ["b"] * 20,
        "best_for": "x" * 300, "cost_at_1m_tokens": {"input_cost": inp * 1e6, "output_cost": out * 1e6},
    }
    m.update(extra)
    return m


def _catalogue(n=700):
    models = [_model(f"model-{i:04d}", f"Prov{i % 7}", (i + 1) * 1e-7, (i + 1) * 5e-7) for i in range(n)]
    models.append(_model("free-but-unpriced", "Prov1", 0, 0, price_confirmed=False))
    models.append(_model("stale-one", "Prov2", 1e-9, 1e-9, price_is_stale=True))
    return {"success": True, "total_models": len(models), "models": models, "providers": [], "timestamp": "t"}


class TestBoundToolResult:
    def test_small_results_pass_through_unchanged(self):
        r = {"success": True, "models": [1, 2, 3]}
        assert bound_tool_result(r) is r

    def test_large_list_result_is_trimmed_with_a_note(self):
        raw = _catalogue(700)
        assert len(json.dumps(raw)) > MAX_TOOL_RESULT_CHARS
        bounded = bound_tool_result(raw)
        assert len(json.dumps(bounded)) <= MAX_TOOL_RESULT_CHARS
        assert bounded["truncated"] is True
        assert bounded["omitted"] == len(raw["models"]) - len(bounded["models"])
        assert "models" in bounded["note"] and "Narrow" in bounded["note"]
        assert bounded["success"] is True
        assert raw["models"][0] == bounded["models"][0]  # order kept, prefix kept

    def test_large_non_list_result_becomes_a_preview(self):
        raw = {"success": True, "blob": "x" * (MAX_TOOL_RESULT_CHARS * 2)}
        bounded = bound_tool_result(raw)
        assert bounded["truncated"] is True and "preview" in bounded
        assert len(json.dumps(bounded)) <= MAX_TOOL_RESULT_CHARS

    def test_custom_cap(self):
        raw = {"success": True, "items": list(range(5000))}
        bounded = bound_tool_result(raw, max_chars=2000)
        assert len(json.dumps(bounded)) <= 2000 and bounded["truncated"]


class _FakeBackend(LLMBackend):
    """First turn calls the tool; second turn answers. Records what it was fed."""

    def __init__(self):
        self.seen_messages = []

    async def complete(self, messages, tools, system=None):
        self.seen_messages.append([dict(m) for m in messages])
        if len(self.seen_messages) == 1:
            return LLMResponse(content="", tool_calls=[{"id": "c1", "tool": "big", "args": {}}])
        return LLMResponse(content="done")

    async def complete_stream(self, messages, tools, system=None):  # pragma: no cover
        yield {"type": "response", "response": await self.complete(messages, tools, system)}

    def append_assistant_turn(self, messages, response):
        messages.append({"role": "assistant", "content": response.content or "(tool call)"})


@pytest.mark.asyncio
async def test_loop_feeds_the_model_a_bounded_result_and_logs_the_bounded_one():
    from agent.tools import AgentTool

    async def big(_args):
        return _catalogue(700)

    tool = AgentTool(name="big", description="d", input_schema={"type": "object"}, execute=big)
    backend = _FakeBackend()
    result = await ReActLoop(llm_backend=backend, tools=[tool], max_iterations=3).run(
        [{"role": "user", "content": "cheapest?"}]
    )
    assert result.final_answer == "done"
    logged = result.tool_calls[0]["result"]
    assert logged["truncated"] is True
    fed = backend.seen_messages[1][-1]["content"]
    assert len(fed) <= MAX_TOOL_RESULT_CHARS + 500  # wrapper text only
    assert "omitted" in fed


class TestCompactPricing:
    def test_default_is_cheapest_first_and_small(self):
        out = compact_pricing_result(_catalogue(700), {})
        assert out["success"] and out["sort_by"] == "avg"
        assert out["returned"] == COMPACT_PRICING_LIMIT_DEFAULT == len(out["models"])
        assert out["total_models"] == 702
        assert out["models"][0]["model"] == "stale-one"  # cheapest priced row, status stale
        assert out["models"][0]["status"] == "stale"
        assert out["models"][1]["model"] == "model-0000"
        assert len(json.dumps(out)) < 10_000
        assert "more rows match" in out["note"]

    def test_unpriced_models_sort_last_never_first(self):
        out = compact_pricing_result(_catalogue(5), {"limit": 100})
        assert out["models"][-1]["model"] == "free-but-unpriced"
        assert out["models"][-1]["status"] == "unconfirmed"

    def test_provider_and_name_filters_are_case_insensitive(self):
        out = compact_pricing_result(_catalogue(70), {"provider": "prov3", "limit": 100})
        assert out["total_models"] == 10 and all(r["provider"] == "Prov3" for r in out["models"])
        out = compact_pricing_result(_catalogue(70), {"model_contains": "MODEL-006", "limit": 100})
        assert [r["model"] for r in out["models"]] == [f"model-006{i}" for i in range(10)]

    def test_unknown_provider_lists_known_ones(self):
        out = compact_pricing_result(_catalogue(10), {"provider": "Nope"})
        assert out["total_models"] == 0 and "Known providers" in out["note"]

    def test_sort_options_and_limit_cap(self):
        cat = _catalogue(300)
        by_out = compact_pricing_result(cat, {"sort_by": "output", "limit": 1000})
        assert by_out["returned"] == COMPACT_PRICING_LIMIT_MAX
        assert by_out["models"][0]["output_per_1m"] <= by_out["models"][1]["output_per_1m"]
        by_name = compact_pricing_result(cat, {"sort_by": "name", "limit": 3})
        assert [r["model"] for r in by_name["models"]] == ["free-but-unpriced", "model-0000", "model-0001"]
        bogus = compact_pricing_result(cat, {"sort_by": "weird", "limit": "x"})
        assert bogus["sort_by"] == "avg" and bogus["returned"] == COMPACT_PRICING_LIMIT_DEFAULT

    def test_rows_carry_per_1m_prices_and_status(self):
        out = compact_pricing_result(_catalogue(3), {"model_contains": "model-0000"})
        row = out["models"][0]
        assert row == {
            "model": "model-0000", "provider": "Prov0", "input_per_1m": 0.1, "output_per_1m": 0.5,
            "context_window": 128000, "status": "provider", "price_as_of": "2026-11-01",
        }

    def test_failed_results_pass_through(self):
        err = {"success": False, "error": "x"}
        assert compact_pricing_result(err, {}) is err

    def test_schema_adds_filters_and_keeps_currency(self):
        schema = compact_pricing_schema({"type": "object", "properties": {"currency": {"type": "string"}}, "required": []})
        assert set(schema["properties"]) >= {"currency", "provider", "model_contains", "sort_by", "limit"}
        assert "required" not in schema
        assert schema["properties"]["limit"]["maximum"] == COMPACT_PRICING_LIMIT_MAX


class _FakeToolManager:
    def __init__(self):
        self.calls = []

    def get_tool(self, name):
        if name == "get_all_pricing":
            return {"input_schema": {"type": "object", "properties": {"currency": {"type": "string"}}}}
        return None

    async def execute_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return _catalogue(700)


class _NoRag:
    def retrieve(self, query, top_k=5):
        return []


@pytest.mark.asyncio
async def test_agent_get_all_pricing_is_the_compact_edition():
    tm = _FakeToolManager()
    tools = {t.name: t for t in build_agent_tools(tm, _NoRag())}
    tool = tools["get_all_pricing"]
    assert "limit" in tool.input_schema["properties"] and "narrow" in tool.description.lower()
    out = await tool.execute({"sort_by": "avg", "limit": 10, "provider": "Prov1", "currency": "EUR"})
    # Only the MCP tool's own arguments reach it; the filters are applied here.
    assert tm.calls == [("get_all_pricing", {"currency": "EUR"})]
    assert out["returned"] == 10 and all(r["provider"] == "Prov1" for r in out["models"])
    assert len(json.dumps(out)) < MAX_TOOL_RESULT_CHARS


def test_system_prompt_tells_the_model_to_narrow_the_catalogue():
    assert "limit" in SYSTEM_PROMPT and "Never ask for the whole catalogue" in SYSTEM_PROMPT
