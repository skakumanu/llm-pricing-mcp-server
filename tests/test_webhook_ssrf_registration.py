"""v1.80.1 — webhook SSRF guard across every registration surface and every fire site.

Real temp-file PricingAlertService / BudgetAlertService / UsageTrackerService are wired
into POST /pricing/alerts, POST /usage/alerts (customer key), the MCP register tools over
POST /mcp and via the STDIO ToolManager / direct execute, and the PricingAgent's bound
tools. Covers AC1-AC3 per surface, AC7 (legacy rows skipped, valid ones fire), AC9
(rejections reveal no IP), AC10 (payload + signature byte-identical), AC11 (hard_stop
402 unaffected), AC12 (blocked deliveries never break usage/dispatch), AC13 (agent path),
AC14 (docs), AC15 (no request can enable the opt-out). DNS is stubbed by the autouse
``webhook_dns`` fixture in tests/conftest.py; nothing touches the network.

Keys are assembled at runtime so no secret-shaped literal lands in a commit.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402
from src.config.settings import settings  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.billing_service import CustomerRecord  # noqa: E402
from src.services.budget_alerts import BudgetAlertService  # noqa: E402
from src.services.dispatch_executors import ChatResult  # noqa: E402
from src.services.dispatch_receipts import DispatchReceiptStore  # noqa: E402
from src.services.dispatch_service import DispatchService  # noqa: E402
from src.services.pricing_alerts import PricingAlertService, verify_webhook_signature  # noqa: E402
from src.services.router import ModelRouter  # noqa: E402
from src.services.savings_tracker import SavingsTrackerService  # noqa: E402
from src.services.usage_tracker import UsageTrackerService  # noqa: E402
from src.services.webhook_guard import PinnedWebhookTransport  # noqa: E402
from mcp.tools.import_session_usage import ImportSessionUsageTool  # noqa: E402
from mcp.tools.record_usage import RecordUsageTool  # noqa: E402
from mcp.tools.register_budget_alert import RegisterBudgetAlertTool  # noqa: E402
from mcp.tools.register_price_alert import RegisterPriceAlertTool  # noqa: E402
from mcp.tools.tool_manager import ToolManager  # noqa: E402

client = TestClient(app)

CUSTOMER_KEY = "-".join(["svc", "test", "ssrf", "4b1e"])
ORG = "org-ssrf-1"
VALID_URL = "https://hooks.slack.com/services/T000/B000/XXXX"
LEGACY_URL = "http://127.0.0.1:8080/x"
REASONS = {"scheme_not_allowed", "malformed_url", "private_destination", "unresolvable_host"}

REFUSED_URLS = [
    # AC2
    "http://localhost/x", "https://LOCALHOST./x", "https://foo.localhost/x", "https://127.0.0.1/x",
    "https://127.1/x", "https://2130706433/x", "https://0177.0.0.1/x", "https://0x7f000001/x",
    "https://0.0.0.0/x", "https://[::1]/x", "https://[::ffff:127.0.0.1]/x", "https://10.0.0.5/x",
    "https://172.16.0.1/x", "https://172.31.255.255/x", "https://192.168.1.1/x",
    "https://169.254.169.254/latest/meta-data/", "https://[fe80::1]/x", "https://[fdaa:0:1::2]/x",
    "https://100.64.0.1/x", "https://my-app.internal/x", "https://my-app.flycast/x",
    # AC3
    "file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/x", "data:text/plain,hi",
    "javascript:alert(1)", "wss://example.com/x", "example.com/hook", "https:///nohost", "",
    "http://example.com/x",
    # AC4
    "https://example.com@127.0.0.1/x",
    # AC5 / AC16 (configured in the fixture)
    "https://rebind.test/x", "https://multi.test/x", "https://nx.test/x",
]
SURFACES = ["http", "mcp_http", "stdio", "direct"]


def _customer():
    return CustomerRecord(
        id="cust-ssrf", email="ssrf@example.com", stripe_customer_id=None, stripe_subscription_id=None,
        api_key=CUSTOMER_KEY, tier="free", org_id=ORG, created_at=time.time(), updated_at=time.time(),
    )


def _fake_pricing(model_name):
    return SimpleNamespace(
        provider="openai", model_name="gpt-4o",
        cost_per_input_token=0.0000025, cost_per_output_token=0.00001,
    )


@pytest.fixture
def env(tmp_path, webhook_dns, monkeypatch):
    """Real price/budget/usage services over a temp DB, wired into every surface."""
    monkeypatch.setattr(settings, "webhook_allow_private_targets", False)
    webhook_dns["rebind.test"] = ["127.0.0.1"]
    webhook_dns["multi.test"] = ["93.184.216.34", "10.0.0.1"]
    webhook_dns.unresolvable.add("nx.test")

    db = str(tmp_path / "ssrf.db")
    loop = asyncio.new_event_loop()
    price = PricingAlertService(db)
    budget = BudgetAlertService(db)
    usage = UsageTrackerService(db)
    for s in (price, budget, usage):
        loop.run_until_complete(s.initialize())

    billing = MagicMock()
    billing.get_customer_by_api_key = AsyncMock(
        side_effect=lambda k: _customer() if k == CUSTOMER_KEY else None
    )
    aggregator = MagicMock()
    aggregator.find_model_pricing = AsyncMock(side_effect=_fake_pricing)
    main_module._rate_limit_store.clear()
    patches = [
        patch("src.main.get_billing_service", return_value=billing),
        patch("src.main.get_pricing_alert_service", return_value=price),
        patch("src.main.get_budget_alert_service", return_value=budget),
        patch("src.main.get_usage_tracker", return_value=usage),
        patch("src.main.get_pricing_aggregator", AsyncMock(return_value=aggregator)),
        patch("mcp.tools.register_price_alert.get_pricing_alert_service", return_value=price),
        patch("mcp.tools.register_budget_alert.get_budget_alert_service", return_value=budget),
        patch("mcp.tools.record_usage.get_budget_alert_service", return_value=budget),
        patch("mcp.tools.record_usage.get_usage_tracker", return_value=usage),
        patch("mcp.tools.import_session_usage.get_budget_alert_service", return_value=budget),
        patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=usage),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(db=db, loop=loop, price=price, budget=budget, usage=usage, aggregator=aggregator)
    finally:
        for p in reversed(patches):
            p.stop()
        main_module._rate_limit_store.clear()
        loop.close()


def _count(env, kind):
    svc = env.price if kind == "price" else env.budget
    return len(env.loop.run_until_complete(svc.list_alerts()))


def _mcp_call(name, arguments, headers=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    resp = client.post("/mcp", json=body, headers=headers or {})
    assert resp.status_code == 200
    data = resp.json()
    assert "error" not in data, data
    return json.loads(data["result"]["content"][0]["text"]), resp.text


def _register(env, kind, surface, url):
    """Register on one surface. Returns (ok, payload, raw_text)."""
    if kind == "price":
        args = {"url": url, "threshold_pct": 5.0}
        if surface == "http":
            resp = client.post("/pricing/alerts", json=args)
            return resp.status_code == 201, resp.json(), resp.text
        if surface == "mcp_http":
            result, text = _mcp_call("register_price_alert", args)
            return result["success"], result, text
        if surface == "stdio":
            result = env.loop.run_until_complete(ToolManager().execute_tool("register_price_alert", args))
            return result["success"], result, json.dumps(result)
        result = env.loop.run_until_complete(RegisterPriceAlertTool().execute(args))
        return result["success"], result, json.dumps(result)
    args = {"url": url, "threshold_usd": 50.0}
    if surface == "http":
        resp = client.post("/usage/alerts", json=args, headers={"x-api-key": CUSTOMER_KEY})
        return resp.status_code == 201, resp.json(), resp.text
    if surface == "mcp_http":
        result, text = _mcp_call("register_budget_alert", args, headers={"x-api-key": CUSTOMER_KEY})
        return result["success"], result, text
    # STDIO is anonymous for the org-scoped budget tool (customer_key_required), so the
    # in-process surface for budget is the tool itself with no caller (direct construction).
    result = env.loop.run_until_complete(RegisterBudgetAlertTool().execute(dict(args, org_id=ORG)))
    return result["success"], result, json.dumps(result)


# ---------------------------------------------------------------------------
# AC1 — public HTTPS registers on all four surfaces
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["price", "budget"])
@pytest.mark.parametrize("surface", SURFACES)
def test_ac1_public_https_registers(env, kind, surface):
    before = _count(env, kind)
    ok, payload, _ = _register(env, kind, surface, VALID_URL)
    assert ok, payload
    assert payload["url"] == VALID_URL
    assert _count(env, kind) == before + 1


def test_ac1_budget_over_stdio_still_needs_a_customer_key(env):
    """Sanity: the STDIO budget path is refused by auth before the URL is even looked at."""
    result = env.loop.run_until_complete(
        ToolManager().execute_tool("register_budget_alert", {"url": VALID_URL, "threshold_usd": 5})
    )
    assert result["success"] is False and result["error_code"] == "customer_key_required"


# ---------------------------------------------------------------------------
# AC2 / AC3 / AC4 / AC5 / AC16 — refused on every surface, nothing stored
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["price", "budget"])
@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("url", REFUSED_URLS)
def test_refused_on_every_surface_nothing_stored(env, kind, surface, url):
    before = _count(env, kind)
    ok, payload, text = _register(env, kind, surface, url)
    assert not ok
    assert _count(env, kind) == before
    if surface != "http" and url == "":
        assert payload["error"] == "url is required"  # the tools' existing empty check
        return
    if surface == "http":
        assert payload["error"] == "invalid_webhook_url"
        assert payload["reason"] in REASONS
        assert payload["message"]
    else:
        assert payload["success"] is False
        assert payload["error_code"] == "invalid_webhook_url"
        assert payload["reason"] in REASONS
    # AC9: a rejection never carries resolved addresses or DNS detail.
    for leak in ("10.0.0.1", "93.184.216.34", "gaierror", "Name or service"):
        assert leak not in text


def test_http_rejection_is_422_with_fixed_shape(env):
    resp = client.post("/pricing/alerts", json={"url": "https://rebind.test/x", "threshold_pct": 5})
    assert resp.status_code == 422
    assert resp.json() == {
        "error": "invalid_webhook_url",
        "reason": "private_destination",
        "message": "Webhook URL points to a private, internal or reserved destination.",
    }
    resp = client.post("/usage/alerts", json={"url": "https://nx.test/x", "threshold_usd": 5},
                       headers={"x-api-key": CUSTOMER_KEY})
    assert resp.status_code == 422
    assert resp.json()["reason"] == "unresolvable_host"
    assert resp.json()["message"] == "Could not resolve host."
    assert "nx.test" not in resp.text


def test_ac9_rebind_rejection_contains_no_ip(env):
    resp = client.post("/pricing/alerts", json={"url": "https://rebind.test/x", "threshold_pct": 5})
    assert "127.0.0.1" not in resp.text and "rebind" not in resp.json()["message"]


def test_budget_org_checks_still_come_first(env):
    resp = client.post("/usage/alerts", json={"url": "https://127.0.0.1/x", "threshold_usd": 5, "org_id": "other"},
                       headers={"x-api-key": CUSTOMER_KEY})
    assert resp.status_code == 403 and resp.json()["error"] == "org_mismatch"


def test_openapi_documents_422(env):
    paths = app.openapi()["paths"]
    assert "422" in paths["/pricing/alerts"]["post"]["responses"]
    assert "invalid_webhook_url" in paths["/usage/alerts"]["post"]["responses"]["422"]["description"]


# ---------------------------------------------------------------------------
# AC13 — the PricingAgent's bound tool refuses like the direct call
# ---------------------------------------------------------------------------

def test_ac13_agent_path_refused(env):
    from agent.tools import build_agent_tools
    tools = {t.name: t for t in build_agent_tools(ToolManager(), MagicMock())}
    before = _count(env, "price")
    result = env.loop.run_until_complete(
        tools["register_price_alert"].execute({"url": "https://169.254.169.254/", "threshold_pct": 5})
    )
    assert result["success"] is False
    assert result["error_code"] == "invalid_webhook_url"
    assert result["reason"] == "private_destination"
    assert _count(env, "price") == before
    ok = env.loop.run_until_complete(tools["register_price_alert"].execute({"url": VALID_URL, "threshold_pct": 5}))
    assert ok["success"] is True


# ---------------------------------------------------------------------------
# AC15 — no request can enable the opt-out
# ---------------------------------------------------------------------------

def test_ac15_no_request_enables_opt_out(env):
    resp = client.post(
        "/pricing/alerts",
        json={"url": LEGACY_URL, "threshold_pct": 5, "webhook_allow_private_targets": True,
              "allow_private_targets": True},
        headers={"X-Webhook-Allow-Private-Targets": "true", "WEBHOOK_ALLOW_PRIVATE_TARGETS": "1"},
    )
    assert resp.status_code == 422 and resp.json()["error"] == "invalid_webhook_url"
    result, _ = _mcp_call("register_price_alert",
                          {"url": LEGACY_URL, "threshold_pct": 5, "webhook_allow_private_targets": True})
    assert result["success"] is False and result["error_code"] == "invalid_webhook_url"
    assert settings.webhook_allow_private_targets is False


def test_ac15_opt_out_on_registers_local_http(env, monkeypatch):
    monkeypatch.setattr(settings, "webhook_allow_private_targets", True)
    resp = client.post("/pricing/alerts", json={"url": LEGACY_URL, "threshold_pct": 5})
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Delivery helpers
# ---------------------------------------------------------------------------

def _mock_client():
    mock = MagicMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    resp = MagicMock()
    resp.status_code = 200
    mock.post = AsyncMock(return_value=resp)
    return mock


async def _insert_legacy_price(db, url):
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO pricing_alerts (url, threshold_pct, provider, model_name, created_at) "
            "VALUES (?, 1.0, NULL, NULL, ?)", (url, time.time()),
        )
        await conn.commit()


async def _insert_legacy_budget(db, url, org_id=None, threshold=0.0001, hard_stop=0):
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO budget_alerts "
            "(url, org_id, threshold_usd, period_days, created_at, last_fired_at, hard_stop) "
            "VALUES (?, ?, ?, 30, ?, NULL, ?)", (url, org_id, threshold, time.time(), hard_stop),
        )
        await conn.commit()


TREND = {
    "model_name": "gpt-4o", "provider": "openai", "input_change_pct": 50.0, "output_change_pct": -2.5,
    "direction": "up", "first_seen": "2026-10-30T00:00:00", "last_seen": "2026-10-30T06:00:00",
}


def _tracker(total):
    tracker = MagicMock()
    tracker.get_summary = AsyncMock(return_value={"total_cost_usd": total, "total_requests": 7})
    return tracker


# ---------------------------------------------------------------------------
# AC7 — legacy rows kept, skipped at delivery, valid alert in the batch still fires
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("legacy", [LEGACY_URL, "https://169.254.169.254/latest/", "https://my-app.internal/x",
                                    "ftp://example.com/x", "not a url"])
def test_ac7_legacy_price_rows_skipped_valid_fires(env, caplog, legacy):
    env.loop.run_until_complete(_insert_legacy_price(env.db, legacy))
    env.loop.run_until_complete(env.price.register(VALID_URL, threshold_pct=1.0))
    mock = _mock_client()
    with patch("src.services.pricing_alerts.httpx.AsyncClient", return_value=mock), \
            caplog.at_level(logging.WARNING, logger="src.services.pricing_alerts"):
        fired = env.loop.run_until_complete(env.price.check_and_fire([TREND]))
    assert fired == 1
    assert mock.post.await_count == 1
    assert mock.post.await_args.args[0] == VALID_URL
    blocked = [r.getMessage() for r in caplog.records if "delivery blocked" in r.getMessage()]
    assert len(blocked) == 1 and legacy in blocked[0]
    assert len(env.loop.run_until_complete(env.price.list_alerts())) == 2  # kept, not deleted


def test_ac7_legacy_budget_row_skipped_valid_fires_cooldown_unchanged(env, caplog):
    env.loop.run_until_complete(_insert_legacy_budget(env.db, LEGACY_URL))
    env.loop.run_until_complete(env.budget.register(VALID_URL, threshold_usd=0.0001))
    mock = _mock_client()
    with patch("src.services.budget_alerts.httpx.AsyncClient", return_value=mock), \
            caplog.at_level(logging.WARNING, logger="src.services.budget_alerts"):
        fired = env.loop.run_until_complete(env.budget.check_and_fire(_tracker(5.0)))
    assert fired == 1
    assert [c.args[0] for c in mock.post.await_args_list] == [VALID_URL]
    assert any("Budget alert" in r.getMessage() and "delivery blocked (scheme_not_allowed)" in r.getMessage()
               for r in caplog.records)
    alerts = env.loop.run_until_complete(env.budget.list_alerts())
    assert len(alerts) == 2
    # Cooldown is marked for the blocked row exactly as for a failed delivery today.
    assert all(a["last_fired_at"] is not None for a in alerts)


def test_ac7_delivery_client_is_pinned_no_redirects_no_env(env):
    env.loop.run_until_complete(env.price.register(VALID_URL, threshold_pct=1.0))
    env.loop.run_until_complete(env.budget.register(VALID_URL, threshold_usd=0.0001))
    seen = []

    def _factory(**kwargs):
        seen.append(kwargs)
        return _mock_client()
    with patch("src.services.pricing_alerts.httpx.AsyncClient", side_effect=_factory), \
            patch("src.services.budget_alerts.httpx.AsyncClient", side_effect=_factory):
        env.loop.run_until_complete(env.price.check_and_fire([TREND]))
        env.loop.run_until_complete(env.budget.check_and_fire(_tracker(5.0)))
    assert len(seen) == 2
    for kwargs in seen:
        assert kwargs["timeout"] == 10.0
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        assert isinstance(kwargs["transport"], PinnedWebhookTransport)


# ---------------------------------------------------------------------------
# AC10 — payload and signature byte-identical
# ---------------------------------------------------------------------------

def test_ac10_price_payload_and_signature_unchanged(env):
    secret = "-".join(["whk", "test", "ssrf"])
    record = env.loop.run_until_complete(env.price.register(VALID_URL, threshold_pct=1.0))
    mock = _mock_client()
    with patch("src.services.pricing_alerts.httpx.AsyncClient", return_value=mock):
        assert env.loop.run_until_complete(env.price.check_and_fire([TREND], secret=secret)) == 1
    expected = json.dumps({
        "alert_id": record["id"], "threshold_pct": 1.0, "model_name": "gpt-4o", "provider": "openai",
        "input_change_pct": 50.0, "output_change_pct": -2.5, "direction": "up",
        "first_seen": "2026-10-30T00:00:00", "last_seen": "2026-10-30T06:00:00",
    }, separators=(",", ":")).encode()
    kwargs = mock.post.await_args.kwargs
    assert kwargs["content"] == expected
    sig = "sha256=" + hmac.new(secret.encode(), expected, hashlib.sha256).hexdigest()
    assert kwargs["headers"] == {"Content-Type": "application/json", "X-LLM-Pricing-Signature": sig}
    assert verify_webhook_signature(secret, kwargs["content"], kwargs["headers"]["X-LLM-Pricing-Signature"])


def test_ac10_budget_payload_and_signature_unchanged(env):
    secret = "-".join(["whk", "test", "ssrf"])
    record = env.loop.run_until_complete(env.budget.register(VALID_URL, threshold_usd=1.0, org_id=ORG))
    mock = _mock_client()
    with patch("src.services.budget_alerts.httpx.AsyncClient", return_value=mock):
        assert env.loop.run_until_complete(env.budget.check_and_fire(_tracker(5.0), secret=secret)) == 1
    expected = json.dumps({
        "alert_id": record["id"], "org_id": ORG, "threshold_usd": 1.0, "period_days": 30,
        "total_cost_usd": 5.0, "total_requests": 7,
    }, separators=(",", ":")).encode()
    kwargs = mock.post.await_args.kwargs
    assert kwargs["content"] == expected
    assert verify_webhook_signature(secret, expected, kwargs["headers"]["X-LLM-Pricing-Signature"])


# ---------------------------------------------------------------------------
# AC12 — blocked budget deliveries never break usage recording
# ---------------------------------------------------------------------------

class _FireSpy:
    """Wrap the real check_and_fire and record what it returned."""

    def __init__(self, budget):
        self.results = []
        self._real = budget.check_and_fire

    async def __call__(self, *args, **kwargs):
        result = await self._real(*args, **kwargs)
        self.results.append(result)
        return result


@pytest.fixture
def blocked_budget(env):
    env.loop.run_until_complete(_insert_legacy_budget(env.db, LEGACY_URL, threshold=0.0000001))
    env.loop.run_until_complete(_insert_legacy_budget(env.db, "https://169.254.169.254/x", threshold=0.0000001))
    spy = _FireSpy(env.budget)
    mock = _mock_client()
    with patch.object(env.budget, "check_and_fire", spy), \
            patch("src.services.budget_alerts.httpx.AsyncClient", return_value=mock):
        yield SimpleNamespace(spy=spy, client=mock)
    mock.post.assert_not_awaited()


def test_ac12_post_usage_unaffected(env, blocked_budget):
    resp = client.post("/usage", json={"model_name": "gpt-4o", "input_tokens": 100, "output_tokens": 50})
    assert resp.status_code == 200 and resp.json()["recorded"] is True
    assert blocked_budget.spy.results == [0]


def test_ac12_post_usage_batch_unaffected(env, blocked_budget):
    resp = client.post("/usage/batch", json={"events": [
        {"model_name": "gpt-4o", "input_tokens": 100, "output_tokens": 50},
        {"model_name": "gpt-4o", "input_tokens": 10, "output_tokens": 5},
    ]})
    assert resp.status_code == 200 and resp.json()["recorded"] == 2
    assert blocked_budget.spy.results == [0]


def test_ac12_record_usage_tool_unaffected(env, blocked_budget):
    result = env.loop.run_until_complete(RecordUsageTool(aggregator=env.aggregator).execute(
        {"model_name": "gpt-4o", "input_tokens": 100, "output_tokens": 50}
    ))
    assert result["success"] is True
    assert blocked_budget.spy.results == [0]


def test_ac12_import_session_usage_tool_unaffected(env, blocked_budget):
    result = env.loop.run_until_complete(ImportSessionUsageTool(aggregator=env.aggregator).execute({
        "session_id": "s-1",
        "entries": [{"model_name": "gpt-4o", "input_tokens": 100, "output_tokens": 50}],
    }))
    assert result["success"] is True
    assert blocked_budget.spy.results == [0]


# ---------------------------------------------------------------------------
# AC11 / AC12 — dispatch: hard_stop 402 unaffected, settlement unaffected
# ---------------------------------------------------------------------------

class _OkExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, req, api_key):
        self.calls.append(req.model)
        return ChatResult("ok", "stop", 100, 50, 9.0, 200, req.model)


def _pm(name, provider, quality, blended_per_1m):
    per_token = blended_per_1m / 1_000_000
    m = PricingMetrics(model_name=name, provider=provider, cost_per_input_token=per_token,
                       cost_per_output_token=per_token, context_window=128_000, price_confirmed=True,
                       price_as_of="2026-10-01")
    m.quality_score = quality
    return m


@pytest.fixture
def dispatch_env(env):
    savings = SavingsTrackerService(env.db)
    receipts = DispatchReceiptStore(env.db)
    for s in (savings, receipts):
        env.loop.run_until_complete(s.initialize())
    executor = _OkExecutor()
    aggregator = MagicMock()
    aggregator.get_all_pricing_async = AsyncMock(return_value=([_pm("model-a", "OpenAI", 85, 10.0)], []))
    svc = DispatchService(
        aggregator=aggregator, router=ModelRouter(aggregator), usage_tracker=env.usage, savings_tracker=savings,
        budget_alerts=env.budget, receipts=receipts, executor_factory=lambda spec, t: executor,
        default_max_tokens=100, attempt_timeout_ms=500, max_attempts=1,
    )
    with patch("src.main.get_dispatch_service", return_value=svc), \
            patch("src.main.get_dispatch_receipt_store", return_value=receipts), \
            patch("src.services.dispatch_service.count_tokens", lambda text: max(1, len(text.split()))):
        yield SimpleNamespace(executor=executor)


def _dispatch():
    provider_key = "-".join(["sk", "test", "ssrf", "000"])
    return client.post("/v1/chat/completions", json={
        "model": "gpt-4", "messages": [{"role": "user", "content": "hello there"}],
        "dispatch": {"objective": "cheapest", "min_quality_score": 70},
    }, headers={"x-api-key": CUSTOMER_KEY, "X-Provider-Key-OpenAI": provider_key})


def test_ac12_dispatch_settlement_unaffected_by_blocked_alert(env, dispatch_env, blocked_budget):
    resp = _dispatch()
    assert resp.status_code == 200, resp.text
    assert dispatch_env.executor.calls == ["model-a"]
    assert blocked_budget.spy.results == [0]


def test_ac11_hard_stop_with_blocked_url_still_402(env, dispatch_env):
    env.loop.run_until_complete(
        _insert_legacy_budget(env.db, LEGACY_URL, org_id=ORG, threshold=0.0001, hard_stop=1)
    )
    env.loop.run_until_complete(env.usage.record_event("OpenAI", "model-a", 10, 10, 0.5, org_id=ORG))
    mock = _mock_client()
    with patch("src.services.budget_alerts.httpx.AsyncClient", return_value=mock):
        resp = _dispatch()
    assert resp.status_code == 402
    assert resp.json()["error"] == "budget_hard_stop"
    assert dispatch_env.executor.calls == []
    mock.post.assert_not_awaited()


# ---------------------------------------------------------------------------
# AC14 — docs state the rules and the legacy handling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,needles", [
    ("README.md", ["public https", "invalid_webhook_url", "WEBHOOK_ALLOW_PRIVATE_TARGETS",
                   "skipped at delivery", "redirects are never followed"]),
    ("docs/ARCHITECTURE.md", ["webhook_guard.py", "PinnedWebhookTransport", "invalid_webhook_url"]),
    ("docs/DEPLOYMENT.md", ["WEBHOOK_ALLOW_PRIVATE_TARGETS", "must stay unset on Fly", "HTTPS_PROXY",
                            "skipped at delivery"]),
    ("static/api-docs/index.html", ["invalid_webhook_url", "public <code>https</code>"]),
    ("static/mcp-setup/index.html", ["invalid_webhook_url", "public https", "not delivered"]),
    ("static/whats-new/index.html", ["v1.80.1", "Webhook alerts now only deliver to public HTTPS URLs"]),
])
def test_ac14_docs_state_rules(path, needles):
    text = (project_root / path).read_text(encoding="utf-8")
    for needle in needles:
        assert needle in text, f"{path} is missing {needle!r}"
