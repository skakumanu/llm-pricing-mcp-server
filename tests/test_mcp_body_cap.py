"""v1.79.2 — POST /mcp enforces the global 1,000,000-byte body cap before parsing.

/mcp is a full-bypass path in the security middleware (public, no limiter), and
before v1.79.2 it had no body cap at all, so any caller could post an arbitrarily
large JSON-RPC body that mcp_http would read and parse. It now sits in
_public_body_capped_paths, so _body_too_large() runs on the declared Content-Length
and on the actual bytes (chunked / mis-declared bodies) before mcp_http is called.

The cap must not break legitimate bulk imports: a realistic 500-entry exact-count
import_session_usage call (the tool's own batch maximum) stays well under it.
"""
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402
from src.services.billing_service import CustomerRecord  # noqa: E402
from src.services.budget_alerts import BudgetAlertService  # noqa: E402
from src.services.usage_tracker import UsageTrackerService  # noqa: E402
from mcp.tools.import_session_usage import MAX_ENTRIES  # noqa: E402

client = TestClient(app)

CUSTOMER_KEY = "-".join(["svc", "cap", "cust", "d44f"])
ORG = "org-cap-1"
LIMIT = main_module.settings.max_body_bytes


def _customer():
    return CustomerRecord(
        id="cust-cap", email="cap@example.com", stripe_customer_id=None, stripe_subscription_id=None,
        api_key=CUSTOMER_KEY, tier="free", org_id=ORG, created_at=time.time(), updated_at=time.time(),
    )


def _fake_pricing(model_name):
    return SimpleNamespace(
        provider="anthropic", model_name=model_name,
        cost_per_input_token=0.000003, cost_per_output_token=0.000015,
    )


@pytest.fixture
def env(tmp_path):
    db = str(tmp_path / "cap.db")
    loop = asyncio.new_event_loop()
    usage = UsageTrackerService(db)
    budget = BudgetAlertService(db)
    loop.run_until_complete(usage.initialize())
    loop.run_until_complete(budget.initialize())
    billing = MagicMock()
    customer = _customer()
    billing.get_customer_by_api_key = AsyncMock(side_effect=lambda k: customer if k == CUSTOMER_KEY else None)
    tm = loop.run_until_complete(main_module._get_http_mcp_tool_manager())
    patches = [
        patch("src.main.get_billing_service", return_value=billing),
        patch.object(tm.aggregator, "find_model_pricing", AsyncMock(side_effect=_fake_pricing)),
        patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=usage),
        patch("mcp.tools.import_session_usage.get_budget_alert_service", return_value=budget),
        patch("mcp.tools.record_usage.get_usage_tracker", return_value=usage),
        patch("mcp.tools.record_usage.get_budget_alert_service", return_value=budget),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(loop=loop, usage=usage)
    finally:
        for p in reversed(patches):
            p.stop()
        loop.close()


def _total_requests(env):
    return env.loop.run_until_complete(env.usage.get_summary(org_id=None, days=365))["total_requests"]


def _record_usage_body(padding=0):
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "record_usage", "arguments": {
            "model_name": "claude-sonnet-4-5", "input_tokens": 10, "output_tokens": 5,
            "request_id": "cap-probe", "session_id": "x" * padding,
        }},
    }
    return json.dumps(body).encode("utf-8")


def _realistic_500_entry_payload():
    """A realistic coding-agent session export: 500 exact-count turns with ids and timestamps."""
    session = f"copilot-chat-{uuid.UUID(int=7)}"
    start = float(int(time.time()) - 86_400)  # within get_summary's look-back window
    models = ["claude-sonnet-4-5", "gpt-4o", "gemini-2.5-pro", "gpt-4o-mini"]
    entries = [
        {
            "model_name": models[i % len(models)],
            "occurred_at": start + i * 37.25,
            "input_tokens": 18_000 + (i * 131) % 9_000,
            "output_tokens": 600 + (i * 17) % 1_400,
            "request_id": f"{session}:turn-{i:04d}:{uuid.UUID(int=i + 1000)}",
        }
        for i in range(MAX_ENTRIES)
    ]
    return {
        "jsonrpc": "2.0", "id": 77, "method": "tools/call",
        "params": {"name": "import_session_usage", "arguments": {"session_id": session, "entries": entries}},
    }


# ---------------------------------------------------------------------------
# AC16: declared Content-Length over the cap
# ---------------------------------------------------------------------------

class TestDeclaredLength:
    def test_ac16_content_length_over_cap_is_413_and_nothing_runs(self, env):
        body = _record_usage_body()
        with patch("src.main._handle_mcp_request", new=AsyncMock()) as handler:
            resp = client.post(
                "/mcp", content=body,
                headers={"Content-Type": "application/json", "x-api-key": CUSTOMER_KEY,
                         "Content-Length": str(LIMIT + 1)},
            )
        assert resp.status_code == 413
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == {"detail": "Request body too large"}
        handler.assert_not_awaited()
        assert _total_requests(env) == 0

    def test_ac16_real_oversized_body_is_413(self, env):
        body = _record_usage_body(padding=LIMIT + 10)
        with patch("src.main._handle_mcp_request", new=AsyncMock()) as handler:
            resp = client.post("/mcp", content=body,
                               headers={"Content-Type": "application/json", "x-api-key": CUSTOMER_KEY})
        assert resp.status_code == 413
        handler.assert_not_awaited()
        assert _total_requests(env) == 0

    def test_invalid_content_length_is_400(self, env):
        with patch("src.main._handle_mcp_request", new=AsyncMock()) as handler:
            resp = client.post("/mcp", content=b"{}",
                               headers={"Content-Type": "application/json", "Content-Length": "lots"})
        assert resp.status_code == 400
        handler.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC17: chunked body over the cap
# ---------------------------------------------------------------------------

class TestChunked:
    def test_ac17_chunked_over_cap_is_413(self, env):
        body = _record_usage_body(padding=LIMIT + 10)

        def chunks():
            for i in range(0, len(body), 64 * 1024):
                yield body[i:i + 64 * 1024]

        with patch("src.main._handle_mcp_request", new=AsyncMock()) as handler:
            resp = client.post("/mcp", content=chunks(),
                               headers={"Content-Type": "application/json", "x-api-key": CUSTOMER_KEY})
        assert resp.status_code == 413
        handler.assert_not_awaited()
        assert _total_requests(env) == 0

    def test_chunked_under_cap_still_works(self, env):
        body = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}).encode()

        def chunks():
            yield body[:10]
            yield body[10:]

        resp = client.post("/mcp", content=chunks(), headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        assert len(resp.json()["result"]["tools"]) == 32  # v1.84.0: + support_chat


# ---------------------------------------------------------------------------
# Boundary: exactly at the cap is not refused for size
# ---------------------------------------------------------------------------

class TestBoundary:
    def test_exactly_at_limit_is_not_413(self, env):
        base = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"pad": ""}})
        pad = LIMIT - len(base.encode())
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"pad": "p" * pad}}
        ).encode()
        assert len(body) == LIMIT
        resp = client.post("/mcp", content=body, headers={"Content-Type": "application/json"})
        assert resp.status_code == 200
        assert len(resp.json()["result"]["tools"]) == 32  # v1.84.0: + support_chat

    def test_one_byte_over_is_413(self, env):
        base = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"pad": ""}})
        pad = LIMIT - len(base.encode()) + 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"pad": "p" * pad}}
        ).encode()
        assert len(body) == LIMIT + 1
        resp = client.post("/mcp", content=body, headers={"Content-Type": "application/json"})
        assert resp.status_code == 413


# ---------------------------------------------------------------------------
# AC18: a realistic 500-entry exact-count import fits and imports
# ---------------------------------------------------------------------------

class TestBulkImportFits:
    def test_ac18_500_entry_import_is_under_cap_and_imports(self, env):
        payload = _realistic_500_entry_payload()
        compact = json.dumps(payload).encode()
        indented = json.dumps(payload, indent=2).encode()
        assert len(compact) < LIMIT
        assert len(indented) < LIMIT  # even pretty-printed clients fit
        resp = client.post("/mcp", content=compact,
                           headers={"Content-Type": "application/json", "x-api-key": CUSTOMER_KEY})
        assert resp.status_code == 200
        result = json.loads(resp.json()["result"]["content"][0]["text"])
        assert result["success"] is True, result
        assert result["summary"]["recorded_exact"] == MAX_ENTRIES
        assert _total_requests(env) == MAX_ENTRIES

    def test_description_documents_the_cap_and_splitting(self, env):
        tools = {t["name"]: t for t in client.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]}
        desc = tools["import_session_usage"]["description"]
        assert "1,000,000 bytes" in desc and "413" in desc
        assert "split" in desc and "same session_id" in desc and "idempotent" in desc
        assert "service api key" in desc.lower()


# ---------------------------------------------------------------------------
# GET /mcp and the public surface are untouched
# ---------------------------------------------------------------------------

def test_get_mcp_unaffected():
    resp = client.get("/mcp")
    assert resp.status_code == 200
    assert resp.json()["transport"] == "HTTP POST"


def test_mcp_setup_page_unaffected():
    assert client.get("/mcp-setup").status_code == 200
