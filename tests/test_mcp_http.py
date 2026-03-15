"""Tests for the HTTP MCP transport endpoint (POST /mcp)."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    with patch("src.main.settings.mcp_api_key", None):
        from src.main import app
        with TestClient(app) as c:
            yield c


# ---------------------------------------------------------------------------
# GET /mcp — info endpoint
# ---------------------------------------------------------------------------

def test_mcp_info_returns_200(client):
    resp = client.get("/mcp")
    assert resp.status_code == 200
    data = resp.json()
    assert data["protocol"] == "JSON-RPC 2.0"
    assert data["endpoint"] == "/mcp"
    assert "config_example" in data


def test_mcp_info_no_auth_required(client):
    """GET /mcp must be reachable without any API key."""
    resp = client.get("/mcp")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /mcp — JSON-RPC 2.0 dispatch
# ---------------------------------------------------------------------------

def test_mcp_initialize(client):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert data["result"]["protocolVersion"] == "2024-11-05"
    assert "serverInfo" in data["result"]
    assert "tools" in data["result"]["capabilities"]


def test_mcp_initialized_notification_returns_204(client):
    """initialized notification has no id — server must return 204 No Content."""
    payload = {"jsonrpc": "2.0", "method": "initialized"}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 204


def test_mcp_tools_list(client):
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 2
    tools = data["result"]["tools"]
    assert isinstance(tools, list)
    assert len(tools) > 0
    # Each tool should have name and description
    for tool in tools:
        assert "name" in tool
        assert "description" in tool


def test_mcp_tools_call_get_all_pricing(client):
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "get_all_pricing", "arguments": {}},
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 3
    content = data["result"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"


def test_mcp_tools_call_unknown_tool(client):
    payload = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "nonexistent_tool", "arguments": {}},
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    # Should return a result (tool manager returns error dict, not JSON-RPC error)
    assert "result" in data or "error" in data


def test_mcp_tools_call_missing_name(client):
    payload = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {},
    }
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32602


def test_mcp_invalid_jsonrpc_version(client):
    payload = {"jsonrpc": "1.0", "id": 6, "method": "tools/list"}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32600


def test_mcp_unknown_method(client):
    payload = {"jsonrpc": "2.0", "id": 7, "method": "unknown/method"}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32601


def test_mcp_invalid_json_body(client):
    resp = client.post(
        "/mcp",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32700


def test_mcp_no_auth_required_post(client):
    """POST /mcp must be reachable without any API key."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    resp = client.post("/mcp", json=payload)
    assert resp.status_code == 200
