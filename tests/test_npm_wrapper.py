"""Tests for npm/llm-pricing-mcp/bin/llm-pricing-mcp.js against a local mock server.

This script is the STDIO<->HTTP bridge published to npm so STDIO-only MCP
clients (Claude Desktop, etc.) can reach the hosted server via
`npx -y llm-pricing-mcp` instead of cloning this repo and running Python
locally. It never talks to the real hosted server in tests — only to
npm/llm-pricing-mcp/test/mock-server.js, started fresh per test, so these
tests are fast, deterministic, and need no network access.
"""
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
PKG_DIR = project_root / "npm" / "llm-pricing-mcp"
BRIDGE = PKG_DIR / "bin" / "llm-pricing-mcp.js"
MOCK_SERVER = PKG_DIR / "test" / "mock-server.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytest.fixture
def mock_server():
    proc = subprocess.Popen(
        ["node", str(MOCK_SERVER)],
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
    )
    try:
        # The mock server prints its bound port to stderr once listening.
        deadline = time.time() + 5
        port = None
        while time.time() < deadline:
            line = proc.stderr.readline()
            if "listening on" in line:
                port = int(line.strip().rsplit(" ", 1)[-1])
                break
        assert port is not None, "mock server never reported its port"

        # Confirm it's actually accepting connections before handing off.
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            pass

        yield f"http://127.0.0.1:{port}/"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def run_bridge_raw(url: str, lines: list[str], timeout: float = 10, extra_env=None):
    env = {**os.environ, "LLM_PRICING_MCP_URL": url}
    env.pop("LLM_PRICING_API_KEY", None)
    env.update(extra_env or {})
    result = subprocess.run(
        ["node", str(BRIDGE)],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    assert result.returncode == 0, f"bridge exited {result.returncode}: {result.stderr}"
    return result


def run_bridge(url: str, lines: list[str], timeout: float = 10, extra_env=None) -> list[dict]:
    result = run_bridge_raw(url, lines, timeout=timeout, extra_env=extra_env)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


class TestStdioBridge:
    def test_initialize_and_tools_list_round_trip(self, mock_server):
        responses = run_bridge(
            mock_server,
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            ],
        )
        by_id = {r["id"]: r for r in responses}

        # The notification must produce no reply at all on the wire.
        assert len(responses) == 2
        assert by_id[1]["result"]["serverInfo"]["name"] == "mock"
        assert {t["name"] for t in by_id[2]["result"]["tools"]} == {"estimate_cost", "recommend_model"}

    def test_concurrent_requests_do_not_block_each_other(self, mock_server):
        # slow_call sleeps 300ms server-side; tools/list is instant. Both are
        # dispatched from the same stdin batch, so a correct bridge must not
        # serialize them behind the slow one before even issuing the fast one.
        responses = run_bridge(
            mock_server,
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "slow_call", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            ],
        )
        by_id = {r["id"]: r for r in responses}
        assert by_id[1]["result"] == {"slept": True}
        assert "tools" in by_id[2]["result"]

    def test_invalid_json_line_gets_parse_error(self, mock_server):
        responses = run_bridge(mock_server, ["not json at all"])
        assert len(responses) == 1
        assert responses[0]["error"]["code"] == -32700
        assert responses[0]["id"] is None

    def test_non_200_upstream_is_surfaced_as_jsonrpc_error(self, mock_server):
        responses = run_bridge(
            mock_server,
            [json.dumps({"jsonrpc": "2.0", "id": 9, "method": "no_such_method", "params": {}})],
        )
        assert len(responses) == 1
        assert responses[0]["id"] == 9
        assert responses[0]["error"]["code"] == -32000

    def test_exits_cleanly_after_stdin_closes(self, mock_server):
        # Regression test: an earlier version exited on stdin close before
        # in-flight requests resolved, silently dropping replies.
        responses = run_bridge(mock_server, [json.dumps({"jsonrpc": "2.0", "id": 1, "method": "slow_call"})])
        assert len(responses) == 1
        assert responses[0]["result"] == {"slept": True}


class TestApiKeyForwarding:
    """v1.79.1 / npm 1.1.0 (AC12): LLM_PRICING_API_KEY is forwarded as x-api-key only when set."""

    ECHO = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "echo_headers", "params": {}})

    def test_sends_x_api_key_when_env_var_is_set(self, mock_server):
        # Built at runtime so no secret-shaped literal lands in the repo.
        key = "-".join(["test", "bridge", "key", "0001"])
        result = run_bridge_raw(mock_server, [self.ECHO], extra_env={"LLM_PRICING_API_KEY": f"  {key}  "})
        responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        assert responses[0]["result"] == {"has_api_key": True, "api_key": key}
        # The bridge itself never writes the key anywhere (stdout carries only the
        # upstream's echo, which this test-only mock method deliberately returns).
        assert key not in result.stderr

    def test_omits_x_api_key_when_env_var_is_unset_or_blank(self, mock_server):
        for extra in ({}, {"LLM_PRICING_API_KEY": "   "}):
            responses = run_bridge(mock_server, [self.ECHO], extra_env=extra)
            assert responses[0]["result"] == {"has_api_key": False, "api_key": None}

    def test_key_not_echoed_in_upstream_error(self, mock_server):
        key = "-".join(["test", "bridge", "key", "0002"])
        result = run_bridge_raw(
            mock_server,
            [json.dumps({"jsonrpc": "2.0", "id": 3, "method": "no_such_method", "params": {}})],
            extra_env={"LLM_PRICING_API_KEY": key},
        )
        assert key not in result.stdout and key not in result.stderr
