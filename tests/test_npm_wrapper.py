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


def run_bridge(url: str, lines: list[str], timeout: float = 10) -> list[dict]:
    result = subprocess.run(
        ["node", str(BRIDGE)],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "LLM_PRICING_MCP_URL": url},
    )
    assert result.returncode == 0, f"bridge exited {result.returncode}: {result.stderr}"
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
