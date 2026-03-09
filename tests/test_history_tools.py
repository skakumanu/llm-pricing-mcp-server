"""Tests for get_pricing_history and get_pricing_trends MCP tools and agent integration."""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp.tools.get_pricing_history import GetPricingHistoryTool  # noqa: E402
from mcp.tools.get_pricing_trends import GetPricingTrendsTool    # noqa: E402
from mcp.tools.tool_manager import ToolManager                    # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_history_service(history=None, trends=None):
    """Return a mock PricingHistoryService."""
    svc = MagicMock()
    svc.get_history = AsyncMock(return_value=history or {"snapshots": [], "total": 0})
    svc.get_trends = AsyncMock(return_value=trends or [])
    return svc


def _make_snapshot(model="gpt-4o", provider="openai"):
    return {
        "model_name": model, "provider": provider,
        "cost_per_input_token": 0.005, "cost_per_output_token": 0.015,
        "captured_at": time.time(),
    }


def _make_trend(model="gpt-4o", provider="openai", in_chg=50.0, out_chg=50.0):
    return {
        "model_name": model, "provider": provider,
        "input_change_pct": in_chg, "output_change_pct": out_chg,
        "direction": "increased" if in_chg > 0 else "decreased",
        "first_seen": time.time() - 86400, "last_seen": time.time(),
        "first_input": 0.005, "last_input": 0.010,
        "first_output": 0.015, "last_output": 0.030,
    }


# ---------------------------------------------------------------------------
# GetPricingHistoryTool
# ---------------------------------------------------------------------------

class TestGetPricingHistoryTool:
    @pytest.mark.asyncio
    async def test_returns_success_with_snapshots(self):
        snapshots = [_make_snapshot()]
        mock_svc = _mock_history_service(history={"snapshots": snapshots, "total": 1})
        with patch("mcp.tools.get_pricing_history.get_pricing_history_service", return_value=mock_svc):
            result = await GetPricingHistoryTool().execute({})
        assert result["success"] is True
        assert result["total"] == 1
        assert len(result["snapshots"]) == 1

    @pytest.mark.asyncio
    async def test_passes_filters_to_service(self):
        mock_svc = _mock_history_service()
        with patch("mcp.tools.get_pricing_history.get_pricing_history_service", return_value=mock_svc):
            await GetPricingHistoryTool().execute(
                {"model_name": "gpt-4o", "provider": "openai", "days": 7, "limit": 25}
            )
        mock_svc.get_history.assert_called_once_with(
            model_name="gpt-4o", provider="openai", days=7, limit=25
        )

    @pytest.mark.asyncio
    async def test_clamps_days_to_max(self):
        mock_svc = _mock_history_service()
        with patch("mcp.tools.get_pricing_history.get_pricing_history_service", return_value=mock_svc):
            await GetPricingHistoryTool().execute({"days": 9999, "limit": 9999})
        call_kwargs = mock_svc.get_history.call_args.kwargs
        assert call_kwargs["days"] == 365
        assert call_kwargs["limit"] == 500

    @pytest.mark.asyncio
    async def test_clamps_days_to_min(self):
        mock_svc = _mock_history_service()
        with patch("mcp.tools.get_pricing_history.get_pricing_history_service", return_value=mock_svc):
            await GetPricingHistoryTool().execute({"days": 0, "limit": 0})
        call_kwargs = mock_svc.get_history.call_args.kwargs
        assert call_kwargs["days"] == 1
        assert call_kwargs["limit"] == 1

    @pytest.mark.asyncio
    async def test_returns_error_when_service_not_initialized(self):
        with patch(
            "mcp.tools.get_pricing_history.get_pricing_history_service",
            side_effect=RuntimeError("not initialized"),
        ):
            result = await GetPricingHistoryTool().execute({})
        assert result["success"] is False
        assert "not initialized" in result["error"]

    @pytest.mark.asyncio
    async def test_returns_error_on_service_exception(self):
        mock_svc = _mock_history_service()
        mock_svc.get_history = AsyncMock(side_effect=Exception("db error"))
        with patch("mcp.tools.get_pricing_history.get_pricing_history_service", return_value=mock_svc):
            result = await GetPricingHistoryTool().execute({})
        assert result["success"] is False
        assert result["error_type"] == "Exception"


# ---------------------------------------------------------------------------
# GetPricingTrendsTool
# ---------------------------------------------------------------------------

class TestGetPricingTrendsTool:
    @pytest.mark.asyncio
    async def test_returns_success_with_trends(self):
        trends = [_make_trend()]
        mock_svc = _mock_history_service(trends=trends)
        with patch("mcp.tools.get_pricing_trends.get_pricing_history_service", return_value=mock_svc):
            result = await GetPricingTrendsTool().execute({})
        assert result["success"] is True
        assert result["total"] == 1
        assert result["trends"][0]["model_name"] == "gpt-4o"

    @pytest.mark.asyncio
    async def test_passes_params_to_service(self):
        mock_svc = _mock_history_service()
        with patch("mcp.tools.get_pricing_trends.get_pricing_history_service", return_value=mock_svc):
            await GetPricingTrendsTool().execute({"days": 7, "limit": 5})
        mock_svc.get_trends.assert_called_once_with(days=7, limit=5)

    @pytest.mark.asyncio
    async def test_days_returned_in_result(self):
        mock_svc = _mock_history_service()
        with patch("mcp.tools.get_pricing_trends.get_pricing_history_service", return_value=mock_svc):
            result = await GetPricingTrendsTool().execute({"days": 14})
        assert result["days"] == 14

    @pytest.mark.asyncio
    async def test_clamps_to_bounds(self):
        mock_svc = _mock_history_service()
        with patch("mcp.tools.get_pricing_trends.get_pricing_history_service", return_value=mock_svc):
            await GetPricingTrendsTool().execute({"days": 9999, "limit": 9999})
        call_kwargs = mock_svc.get_trends.call_args.kwargs
        assert call_kwargs["days"] == 365
        assert call_kwargs["limit"] == 100

    @pytest.mark.asyncio
    async def test_returns_error_when_service_not_initialized(self):
        with patch(
            "mcp.tools.get_pricing_trends.get_pricing_history_service",
            side_effect=RuntimeError("not initialized"),
        ):
            result = await GetPricingTrendsTool().execute({})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_returns_error_on_service_exception(self):
        mock_svc = _mock_history_service()
        mock_svc.get_trends = AsyncMock(side_effect=Exception("db error"))
        with patch("mcp.tools.get_pricing_trends.get_pricing_history_service", return_value=mock_svc):
            result = await GetPricingTrendsTool().execute({})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# ToolManager registration
# ---------------------------------------------------------------------------

class TestToolManagerRegistration:
    def test_get_pricing_history_registered(self):
        tm = ToolManager()
        tool = tm.get_tool("get_pricing_history")
        assert tool is not None
        assert tool["name"] == "get_pricing_history"

    def test_get_pricing_trends_registered(self):
        tm = ToolManager()
        tool = tm.get_tool("get_pricing_trends")
        assert tool is not None
        assert tool["name"] == "get_pricing_trends"

    def test_get_pricing_history_schema(self):
        tm = ToolManager()
        schema = tm.get_tool("get_pricing_history")["input_schema"]
        props = schema["properties"]
        assert "model_name" in props
        assert "provider" in props
        assert "days" in props
        assert "limit" in props

    def test_get_pricing_trends_schema(self):
        tm = ToolManager()
        schema = tm.get_tool("get_pricing_trends")["input_schema"]
        props = schema["properties"]
        assert "days" in props
        assert "limit" in props

    def test_list_tools_includes_history_tools(self):
        tm = ToolManager()
        names = [t["name"] for t in tm.list_tools()]
        assert "get_pricing_history" in names
        assert "get_pricing_trends" in names

    @pytest.mark.asyncio
    async def test_execute_get_pricing_history_via_manager(self):
        tm = ToolManager()
        mock_svc = _mock_history_service()
        with patch("mcp.tools.get_pricing_history.get_pricing_history_service", return_value=mock_svc):
            result = await tm.execute_tool("get_pricing_history", {})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_get_pricing_trends_via_manager(self):
        tm = ToolManager()
        mock_svc = _mock_history_service()
        with patch("mcp.tools.get_pricing_trends.get_pricing_history_service", return_value=mock_svc):
            result = await tm.execute_tool("get_pricing_trends", {})
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Agent tool wrappers (build_agent_tools)
# ---------------------------------------------------------------------------

class TestAgentToolsIntegration:
    def test_history_tools_present_in_agent_tools(self):
        from agent.tools import build_agent_tools

        # Build a minimal ToolManager with only the new tools registered
        tm = ToolManager()
        mock_rag = MagicMock()

        tools = build_agent_tools(tm, mock_rag)
        names = [t.name for t in tools]
        assert "get_pricing_history" in names
        assert "get_pricing_trends" in names

    def test_history_tool_has_llm_schema(self):
        from agent.tools import build_agent_tools
        tm = ToolManager()
        mock_rag = MagicMock()
        tools = {t.name: t for t in build_agent_tools(tm, mock_rag)}

        schema = tools["get_pricing_history"].to_llm_schema()
        assert schema["name"] == "get_pricing_history"
        assert "input_schema" in schema
        assert "days" in schema["input_schema"]["properties"]

    def test_trends_tool_has_llm_schema(self):
        from agent.tools import build_agent_tools
        tm = ToolManager()
        mock_rag = MagicMock()
        tools = {t.name: t for t in build_agent_tools(tm, mock_rag)}

        schema = tools["get_pricing_trends"].to_llm_schema()
        assert schema["name"] == "get_pricing_trends"
        assert "input_schema" in schema
