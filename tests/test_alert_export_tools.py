"""Tests for register_price_alert, list_price_alerts, delete_price_alert,
and get_pricing_export_url MCP tools + agent tool wiring."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp.tools.register_price_alert import RegisterPriceAlertTool    # noqa: E402
from mcp.tools.list_price_alerts import ListPriceAlertsTool           # noqa: E402
from mcp.tools.delete_price_alert import DeletePriceAlertTool         # noqa: E402
from mcp.tools.get_pricing_export_url import GetPricingExportUrlTool  # noqa: E402
from mcp.tools.tool_manager import ToolManager                         # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_alert_svc(alerts=None, register_ret=None, delete_ret=True):
    svc = MagicMock()
    svc.list_alerts = AsyncMock(return_value=alerts or [])
    svc.register = AsyncMock(return_value=register_ret or {
        "id": 1, "url": "https://example.com/hook",
        "threshold_pct": 5.0, "provider": None, "model_name": None,
        "created_at": 1700000000.0,
    })
    svc.delete = AsyncMock(return_value=delete_ret)
    return svc


# ---------------------------------------------------------------------------
# RegisterPriceAlertTool
# ---------------------------------------------------------------------------

class TestRegisterPriceAlertTool:
    @pytest.mark.asyncio
    async def test_returns_success_with_id(self):
        mock_svc = _mock_alert_svc()
        with patch("mcp.tools.register_price_alert.get_pricing_alert_service", return_value=mock_svc):
            result = await RegisterPriceAlertTool().execute({"url": "https://example.com/hook"})
        assert result["success"] is True
        assert result["id"] == 1

    @pytest.mark.asyncio
    async def test_passes_all_args(self):
        mock_svc = _mock_alert_svc()
        with patch("mcp.tools.register_price_alert.get_pricing_alert_service", return_value=mock_svc):
            await RegisterPriceAlertTool().execute({
                "url": "https://example.com/hook",
                "threshold_pct": 10.0,
                "provider": "openai",
                "model_name": "gpt-4o",
            })
        mock_svc.register.assert_called_once_with(
            url="https://example.com/hook",
            threshold_pct=10.0,
            provider="openai",
            model_name="gpt-4o",
        )

    @pytest.mark.asyncio
    async def test_missing_url_returns_error(self):
        result = await RegisterPriceAlertTool().execute({})
        assert result["success"] is False
        assert "url" in result["error"]

    @pytest.mark.asyncio
    async def test_zero_threshold_returns_error(self):
        result = await RegisterPriceAlertTool().execute({"url": "https://x.com", "threshold_pct": 0})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_empty_provider_treated_as_none(self):
        mock_svc = _mock_alert_svc()
        with patch("mcp.tools.register_price_alert.get_pricing_alert_service", return_value=mock_svc):
            await RegisterPriceAlertTool().execute({"url": "https://x.com", "provider": ""})
        call_kwargs = mock_svc.register.call_args.kwargs
        assert call_kwargs["provider"] is None

    @pytest.mark.asyncio
    async def test_service_not_initialized_returns_error(self):
        with patch("mcp.tools.register_price_alert.get_pricing_alert_service",
                   side_effect=RuntimeError("not initialized")):
            result = await RegisterPriceAlertTool().execute({"url": "https://x.com"})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# ListPriceAlertsTool
# ---------------------------------------------------------------------------

class TestListPriceAlertsTool:
    @pytest.mark.asyncio
    async def test_returns_empty_list(self):
        mock_svc = _mock_alert_svc(alerts=[])
        with patch("mcp.tools.list_price_alerts.get_pricing_alert_service", return_value=mock_svc):
            result = await ListPriceAlertsTool().execute({})
        assert result["success"] is True
        assert result["alerts"] == []
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_returns_all_alerts(self):
        alerts = [
            {"id": 1, "url": "https://a.com", "threshold_pct": 5.0,
             "provider": None, "model_name": None, "created_at": 1700000000.0},
            {"id": 2, "url": "https://b.com", "threshold_pct": 10.0,
             "provider": "openai", "model_name": None, "created_at": 1700000001.0},
        ]
        mock_svc = _mock_alert_svc(alerts=alerts)
        with patch("mcp.tools.list_price_alerts.get_pricing_alert_service", return_value=mock_svc):
            result = await ListPriceAlertsTool().execute({})
        assert result["total"] == 2
        assert result["alerts"][0]["url"] == "https://a.com"

    @pytest.mark.asyncio
    async def test_service_not_initialized_returns_error(self):
        with patch("mcp.tools.list_price_alerts.get_pricing_alert_service",
                   side_effect=RuntimeError("not initialized")):
            result = await ListPriceAlertsTool().execute({})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# DeletePriceAlertTool
# ---------------------------------------------------------------------------

class TestDeletePriceAlertTool:
    @pytest.mark.asyncio
    async def test_delete_existing_returns_success(self):
        mock_svc = _mock_alert_svc(delete_ret=True)
        with patch("mcp.tools.delete_price_alert.get_pricing_alert_service", return_value=mock_svc):
            result = await DeletePriceAlertTool().execute({"alert_id": 1})
        assert result["success"] is True
        assert result["deleted"] is True
        assert result["alert_id"] == 1

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_not_found(self):
        mock_svc = _mock_alert_svc(delete_ret=False)
        with patch("mcp.tools.delete_price_alert.get_pricing_alert_service", return_value=mock_svc):
            result = await DeletePriceAlertTool().execute({"alert_id": 999})
        assert result["success"] is False
        assert result["deleted"] is False

    @pytest.mark.asyncio
    async def test_missing_alert_id_returns_error(self):
        result = await DeletePriceAlertTool().execute({})
        assert result["success"] is False
        assert "alert_id" in result["error"]

    @pytest.mark.asyncio
    async def test_string_alert_id_is_coerced(self):
        mock_svc = _mock_alert_svc(delete_ret=True)
        with patch("mcp.tools.delete_price_alert.get_pricing_alert_service", return_value=mock_svc):
            result = await DeletePriceAlertTool().execute({"alert_id": "3"})
        assert result["success"] is True
        mock_svc.delete.assert_called_once_with(3)

    @pytest.mark.asyncio
    async def test_invalid_alert_id_returns_error(self):
        result = await DeletePriceAlertTool().execute({"alert_id": "not-a-number"})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_service_not_initialized_returns_error(self):
        with patch("mcp.tools.delete_price_alert.get_pricing_alert_service",
                   side_effect=RuntimeError("not initialized")):
            result = await DeletePriceAlertTool().execute({"alert_id": 1})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# GetPricingExportUrlTool
# ---------------------------------------------------------------------------

class TestGetPricingExportUrlTool:
    @pytest.mark.asyncio
    async def test_default_returns_csv_url(self):
        result = await GetPricingExportUrlTool().execute({})
        assert result["success"] is True
        assert "format=csv" in result["url"]
        assert result["format"] == "csv"

    @pytest.mark.asyncio
    async def test_json_format(self):
        result = await GetPricingExportUrlTool().execute({"format": "json"})
        assert "format=json" in result["url"]
        assert result["format"] == "json"

    @pytest.mark.asyncio
    async def test_invalid_format_returns_error(self):
        result = await GetPricingExportUrlTool().execute({"format": "xml"})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_url_includes_model_name(self):
        result = await GetPricingExportUrlTool().execute({"model_name": "gpt-4o"})
        assert "model_name=gpt-4o" in result["url"]

    @pytest.mark.asyncio
    async def test_url_includes_provider(self):
        result = await GetPricingExportUrlTool().execute({"provider": "openai"})
        assert "provider=openai" in result["url"]

    @pytest.mark.asyncio
    async def test_url_includes_days(self):
        result = await GetPricingExportUrlTool().execute({"days": 7})
        assert "days=7" in result["url"]

    @pytest.mark.asyncio
    async def test_days_clamped_to_max(self):
        result = await GetPricingExportUrlTool().execute({"days": 9999})
        assert "days=365" in result["url"]

    @pytest.mark.asyncio
    async def test_description_contains_format(self):
        result = await GetPricingExportUrlTool().execute({"format": "csv"})
        assert "CSV" in result["description"]

    @pytest.mark.asyncio
    async def test_result_has_note(self):
        result = await GetPricingExportUrlTool().execute({})
        assert "note" in result

    @pytest.mark.asyncio
    async def test_empty_model_name_excluded_from_url(self):
        result = await GetPricingExportUrlTool().execute({"model_name": ""})
        assert "model_name" not in result["url"]


# ---------------------------------------------------------------------------
# ToolManager registration
# ---------------------------------------------------------------------------

class TestToolManagerRegistration:
    def test_all_new_tools_registered(self):
        tm = ToolManager()
        for name in ("register_price_alert", "list_price_alerts",
                     "delete_price_alert", "get_pricing_export_url"):
            assert tm.get_tool(name) is not None, f"{name} not registered"

    def test_register_alert_schema_requires_url(self):
        tm = ToolManager()
        schema = tm.get_tool("register_price_alert")["input_schema"]
        assert "url" in schema["required"]

    def test_delete_alert_schema_requires_alert_id(self):
        tm = ToolManager()
        schema = tm.get_tool("delete_price_alert")["input_schema"]
        assert "alert_id" in schema["required"]

    def test_export_url_schema_has_format_enum(self):
        tm = ToolManager()
        schema = tm.get_tool("get_pricing_export_url")["input_schema"]
        assert schema["properties"]["format"]["enum"] == ["csv", "json"]

    def test_all_new_tools_in_list_tools(self):
        tm = ToolManager()
        names = [t["name"] for t in tm.list_tools()]
        for name in ("register_price_alert", "list_price_alerts",
                     "delete_price_alert", "get_pricing_export_url"):
            assert name in names


# ---------------------------------------------------------------------------
# Agent tool wiring
# ---------------------------------------------------------------------------

class TestAgentToolWiring:
    def test_all_new_tools_in_build_agent_tools(self):
        from agent.tools import build_agent_tools
        tm = ToolManager()
        tools = {t.name: t for t in build_agent_tools(tm, MagicMock())}
        for name in ("register_price_alert", "list_price_alerts",
                     "delete_price_alert", "get_pricing_export_url"):
            assert name in tools, f"{name} missing from agent tools"

    def test_each_tool_has_valid_llm_schema(self):
        from agent.tools import build_agent_tools
        tm = ToolManager()
        tools = {t.name: t for t in build_agent_tools(tm, MagicMock())}
        for name in ("register_price_alert", "list_price_alerts",
                     "delete_price_alert", "get_pricing_export_url"):
            schema = tools[name].to_llm_schema()
            assert schema["name"] == name
            assert "input_schema" in schema
