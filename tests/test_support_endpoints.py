"""HTTP surface of the support feature (v1.84.0).

/support/chat and /support/escalations/status are anonymous-but-rate-limited (both directions,
mirroring test_agent_chat_rate_limit.py); /billing/config is public; /billing/key/rotate needs
the customer's current key; /admin/customers/{id}/key/rotate is operator-only with the constant
refusals. Keys are assembled at runtime so no secret-shaped literal lands in a commit.
"""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402
from agent.support_agent import SupportResponse  # noqa: E402
from src.services.billing_service import BillingService  # noqa: E402
from src.services.orchestrator_service import OrchestratorService  # noqa: E402
from src.services.orchestrator_store import OrchestratorStore  # noqa: E402
from src.services import tiers  # noqa: E402

client = TestClient(app)

OPERATOR_KEY = "-".join(["op", "support", "k", "77a"])
WRONG_KEY = "-".join(["svc", "support", "wrong", "88b"])
ANON_BUCKET = "testclient:"


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    main_module._rate_limit_store.clear()
    yield
    main_module._rate_limit_store.clear()


def _exhaust_anonymous_bucket():
    limit = main_module.settings.rate_limit_per_minute
    assert limit > 0
    main_module._rate_limit_store[ANON_BUCKET].extend([time.time()] * limit)


@pytest.fixture
def env(tmp_path):
    billing = BillingService(str(tmp_path / "billing.db"))
    asyncio.run(billing.initialize())
    free = asyncio.run(billing.create_customer("free" + "@" + "example.org"))
    pro = asyncio.run(billing.create_customer("pro" + "@" + "example.org"))
    asyncio.run(billing.update_tier(pro.id, "pro"))
    pro = asyncio.run(billing.get_customer_by_id(pro.id))
    store = OrchestratorStore(str(tmp_path / "orch.db"))
    asyncio.run(store.initialize())
    svc = OrchestratorService(store)
    patches = [
        patch("src.main.settings.mcp_api_key", OPERATOR_KEY),
        patch("src.main.get_billing_service", return_value=billing),
        patch("src.services.billing_service.get_billing_service", return_value=billing),
        patch("src.main.get_orchestrator_service", return_value=svc),
        patch("src.services.orchestrator_service.get_orchestrator_service", return_value=svc),
        patch("src.services.orchestrator_store.get_orchestrator_store", return_value=store),
    ]
    for p in patches:
        p.start()
    try:
        yield SimpleNamespace(billing=billing, free=free, pro=pro, svc=svc, store=store)
    finally:
        for p in reversed(patches):
            p.stop()


def _fake_agent(reply="hello", **kw):
    agent = MagicMock()

    async def _chat(message, conversation_id=None, caller=None, client_ip=None, trigger=None):
        identified = bool(caller and caller.is_customer)
        conv = conversation_id or "sup-00000000-0000-4000-8000-000000000000"
        return SupportResponse(reply=reply, conversation_id=conv, identified=identified,
                               tier=(caller.tier if identified else "anonymous"), **kw)
    agent.chat = AsyncMock(side_effect=_chat)

    async def _get():
        return agent
    return patch("src.main.get_support_agent", _get), agent


# ---------------------------------------------------------------------------
# /support/chat
# ---------------------------------------------------------------------------

class TestSupportChatThrottled:
    def test_429_when_bucket_exhausted_and_agent_not_invoked(self, env):
        _exhaust_anonymous_bucket()
        ctx, agent = _fake_agent()
        with ctx:
            resp = client.post("/support/chat", json={"message": "hello"})
        assert resp.status_code == 429
        agent.chat.assert_not_called()

    def test_status_lookup_is_throttled_too(self, env):
        _exhaust_anonymous_bucket()
        resp = client.post("/support/escalations/status", json={"reference": "orc_aaaaaaaaaaaa"})
        assert resp.status_code == 429

    def test_each_call_consumes_budget(self, env):
        ctx, _ = _fake_agent()
        with ctx:
            resp = client.post("/support/chat", json={"message": "hello"})
        assert resp.status_code == 200
        assert len(main_module._rate_limit_store[ANON_BUCKET]) == 1

    def test_no_api_key_required_even_with_global_key(self, env):
        ctx, _ = _fake_agent()
        with ctx:
            resp = client.post("/support/chat", json={"message": "hello"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["identified"] is False and body["tier"] == "anonymous"
        assert body["conversation_id"].startswith("sup-")

    def test_customer_key_gets_tier_bucket_and_identity(self, env):
        ctx, agent = _fake_agent()
        with ctx:
            resp = client.post("/support/chat", json={"message": "my plan?"}, headers={"x-api-key": env.pro.api_key})
        assert resp.status_code == 200
        assert resp.json()["identified"] is True and resp.json()["tier"] == "pro"
        assert env.pro.id in main_module._rate_limit_store and ANON_BUCKET not in main_module._rate_limit_store
        caller = agent.chat.call_args.kwargs["caller"]
        assert caller.is_customer and caller.customer_id == env.pro.id and caller.tier == "pro"

    def test_wrong_key_is_anonymous(self, env):
        ctx, agent = _fake_agent()
        with ctx:
            resp = client.post("/support/chat", json={"message": "x"}, headers={"x-api-key": WRONG_KEY})
        assert resp.status_code == 200 and resp.json()["identified"] is False
        assert agent.chat.call_args.kwargs["caller"].is_anonymous

    def test_response_shape_with_escalation_and_fallback(self, env):
        ctx, _ = _fake_agent(reply="r", escalation={"reference": "orc_aaaaaaaaaaaa", "public_summary": "ok"},
                             fallback="spend_cap", sources=["FAQ.md"])
        with ctx:
            body = client.post("/support/chat", json={"message": "x"}).json()
        assert set(body) == {"reply", "conversation_id", "identified", "tier", "escalation", "fallback", "sources"}
        assert body["escalation"]["reference"] == "orc_aaaaaaaaaaaa" and body["fallback"] == "spend_cap"

    def test_malformed_conversation_id_is_400(self, env):
        ctx, agent = _fake_agent()
        agent.chat = AsyncMock(side_effect=ValueError("invalid_conversation_id"))
        with ctx:
            resp = client.post("/support/chat", json={"message": "x", "conversation_id": "nope"})
        assert resp.status_code == 400

    def test_validation(self, env):
        ctx, _ = _fake_agent()
        with ctx:
            assert client.post("/support/chat", json={"message": ""}).status_code == 422
            assert client.post("/support/chat", json={"message": "x" * 10_001}).status_code == 422
            assert client.post("/support/chat", json={}).status_code == 422

    def test_503_when_llm_key_missing(self, env):
        async def _get():
            raise ValueError("API key for provider 'anthropic' is not configured.")
        with patch("src.main.get_support_agent", _get):
            resp = client.post("/support/chat", json={"message": "x"})
        assert resp.status_code == 503

    def test_internal_error_never_echoes_message(self, env):
        ctx, agent = _fake_agent()
        sentinel = "SENTINEL-" + "q" * 10
        agent.chat = AsyncMock(side_effect=RuntimeError(sentinel))
        with ctx:
            resp = client.post("/support/chat", json={"message": sentinel})
        assert resp.status_code == 500 and sentinel not in resp.text


# ---------------------------------------------------------------------------
# /support/escalations/status
# ---------------------------------------------------------------------------

class TestEscalationStatus:
    def _escalate(self, env):
        payload = {"trigger_class": "customer_message", "event_type": "support.escalation",
                   "dedup_key": "msg:sup-11111111-2222-3333-4444-555555555555", "actor": "support_agent",
                   "data": {"title": "support escalation: refund", "body": "refund", "category": "refund",
                            "priority": "standard", "tier": "free", "identified": False}}
        return asyncio.run(env.svc.intake_event(payload, actor_kind="support_agent"))["run_id"]

    def test_known_reference_returns_public_view_only(self, env):
        ref = self._escalate(env)
        resp = client.post("/support/escalations/status", json={"reference": ref})
        assert resp.status_code == 200
        assert resp.json() == {"reference": ref, "status": "awaiting_owner", "owner_reply": None}

    @pytest.mark.parametrize("reference", ["orc_zzzzzzzzzzzz", "not-a-ref", "../etc", "orc_AAAAAAAAAAAA"])
    def test_unknown_references_get_one_constant_404(self, env, reference):
        self._escalate(env)
        resp = client.post("/support/escalations/status", json={"reference": reference})
        assert resp.status_code == 404
        assert resp.json() == {"detail": "No escalation matches this reference."}

    def test_reference_is_not_accepted_in_the_url(self, env):
        ref = self._escalate(env)
        assert client.get(f"/support/escalations/status?reference={ref}").status_code in (401, 404, 405)
        assert client.post("/support/escalations/status", json={}).status_code == 422

    def test_owner_reply_is_visible_after_done_manually(self, env):
        ref = self._escalate(env)
        item = asyncio.run(env.svc.approval_queue())[0]
        asyncio.run(env.svc.decide_approval(item["approval_id"], "done_manually", note="Refunded; 5 days."))
        body = client.post("/support/escalations/status", json={"reference": ref}).json()
        assert body["status"] == "done" and body["owner_reply"] == "Refunded; 5 days."
        actions = asyncio.run(env.store.list_actions())
        assert any(a["action"] == "support.escalation_status" and a["outcome"] == "found" for a in actions)


# ---------------------------------------------------------------------------
# /billing/config
# ---------------------------------------------------------------------------

class TestBillingConfig:
    def test_public_shape_without_stripe(self, env):
        with patch.object(main_module.settings, "stripe_secret_key", None):
            resp = client.get("/billing/config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["checkout_enabled"] is False and body["portal_enabled"] is False
        assert body["past_due_grace_days"] == main_module.settings.billing_past_due_grace_days
        assert [t["id"] for t in body["tiers"]] == ["free", "pro", "enterprise"]
        assert all(t["checkout_available"] is False for t in body["tiers"])
        assert body["tiers"] == tiers.tier_catalog(main_module.settings)

    def test_checkout_available_needs_secret_and_price_id(self, env):
        fake_secret = "-".join(["stripe", "secret", "placeholder"])
        with patch.object(main_module.settings, "stripe_secret_key", fake_secret), \
                patch.object(main_module.settings, "stripe_price_id_pro", "price_" + "P" * 14), \
                patch.object(main_module.settings, "stripe_price_id_enterprise", None):
            body = client.get("/billing/config").json()
        assert body["checkout_enabled"] is True and body["portal_enabled"] is True
        by_id = {t["id"]: t for t in body["tiers"]}
        assert by_id["pro"]["checkout_available"] is True
        assert by_id["enterprise"]["checkout_available"] is False and by_id["free"]["checkout_available"] is False

    def test_checkout_and_portal_503_detail_without_stripe(self, env):
        with patch.object(main_module.settings, "stripe_secret_key", None):
            c = client.post("/billing/checkout", json={"tier": "pro"}, headers={"x-api-key": env.pro.api_key})
            p = client.get("/billing/portal", headers={"x-api-key": env.pro.api_key})
        assert c.status_code == 503 and "not enabled on this server" in c.json()["detail"]
        assert p.status_code == 503 and "not enabled on this server" in p.json()["detail"]


# ---------------------------------------------------------------------------
# key rotation
# ---------------------------------------------------------------------------

class TestKeyRotation:
    def test_customer_rotates_and_old_key_stops(self, env):
        old = env.free.api_key
        resp = client.post("/billing/key/rotate", headers={"x-api-key": old})
        assert resp.status_code == 200
        body = resp.json()
        new = body["api_key"]
        assert new != old and len(new) > 30 and body["org_id"] == env.free.org_id and "only once" in body["message"]
        assert client.get("/billing/me", headers={"x-api-key": old}).status_code == 401
        me = client.get("/billing/me", headers={"x-api-key": new})
        assert me.status_code == 200 and me.json()["api_key_preview"] == new[:8] + "..."
        actions = asyncio.run(env.store.list_actions())
        rot = [a for a in actions if a["action"] == "billing.key_rotate"]
        assert len(rot) == 1 and rot[0]["actor"] == "customer" and rot[0]["target"] == f"org:{env.free.org_id[:8]}"
        assert old not in str(actions) and new not in str(actions)

    @pytest.mark.parametrize("headers", [{}, {"x-api-key": WRONG_KEY}, {"x-api-key": OPERATOR_KEY}])
    def test_rotate_needs_the_current_customer_key(self, env, headers):
        resp = client.post("/billing/key/rotate", headers=headers)
        assert resp.status_code == 401
        assert asyncio.run(env.billing.get_customer_by_api_key(env.free.api_key)) is not None

    def test_billing_me_reports_effective_tier_and_status(self, env):
        body = client.get("/billing/me", headers={"x-api-key": env.pro.api_key}).json()
        assert body["tier"] == "pro" and body["effective_tier"] == "pro" and body["billing_status"] == "active"
        assert body["rate_limit_per_minute"] == main_module.settings.rate_limit_pro


class TestOperatorKeyRotation:
    REFUSED = [pytest.param({}, id="no-key"), pytest.param("customer", id="customer-key"),
               pytest.param({"x-api-key": WRONG_KEY}, id="wrong-key"),
               pytest.param({"Authorization": f"Bearer {OPERATOR_KEY}"}, id="operator-as-bearer")]

    @pytest.mark.parametrize("headers", REFUSED)
    def test_constant_refusal(self, env, headers):
        if headers == "customer":
            headers = {"x-api-key": env.pro.api_key}
        resp = client.post(f"/admin/customers/{env.free.id}/key/rotate", headers=headers)
        assert resp.status_code == 401 and resp.json() == {"detail": "Unauthorized"}
        unknown = client.post("/admin/customers/does-not-exist/key/rotate", headers=headers)
        assert unknown.status_code == 401 and unknown.json() == resp.json()
        assert asyncio.run(env.billing.get_customer_by_api_key(env.free.api_key)) is not None

    def test_operator_rotates_and_is_audited_as_owner(self, env):
        old = env.free.api_key
        resp = client.post(f"/admin/customers/{env.free.id}/key/rotate", headers={"x-api-key": OPERATOR_KEY})
        assert resp.status_code == 200
        new = resp.json()["api_key"]
        assert new != old
        assert asyncio.run(env.billing.get_customer_by_api_key(old)) is None
        assert asyncio.run(env.billing.get_customer_by_api_key(new)).id == env.free.id
        rot = [a for a in asyncio.run(env.store.list_actions()) if a["action"] == "billing.key_rotate"]
        assert rot and rot[-1]["actor"] == "owner"
        assert client.post("/admin/customers/nope/key/rotate", headers={"x-api-key": OPERATOR_KEY}).status_code == 404

    def test_503_when_operator_key_unset(self, env):
        with patch("src.main.settings.mcp_api_key", None):
            resp = client.post(f"/admin/customers/{env.free.id}/key/rotate")
        assert resp.status_code == 503

    def test_admin_customers_lists_billing_status(self, env):
        body = client.get("/admin/customers", headers={"x-api-key": OPERATOR_KEY}).json()
        row = next(c for c in body["customers"] if c["id"] == env.pro.id)
        assert row["billing_status"] == "active" and row["effective_tier"] == "pro"


class TestFaqPage:
    def test_faq_is_public_and_bypasses_limiter(self, env):
        _exhaust_anonymous_bucket()
        resp = client.get("/faq/")
        assert resp.status_code == 200 and "Frequently asked questions" in resp.text
