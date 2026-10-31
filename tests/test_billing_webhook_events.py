"""Stripe webhook state machine (v1.84.0), AC18.

Every event's documented state against a real BillingService; event-id dedup; refund, dispute
and failed renewal open support_escalation runs; effective-tier grace maths; the upgrade ->
pro -> cancel -> free flow. Stripe fixtures are plain dicts (``construct_event`` is patched) and
every id is assembled at runtime so nothing looks like a real key.
"""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402
from src.services.billing_service import BillingService, CustomerRecord, effective_tier  # noqa: E402
from src.services.orchestrator_service import OrchestratorService  # noqa: E402
from src.services.orchestrator_store import OrchestratorStore  # noqa: E402

client = TestClient(app)

STRIPE_CUS = "cus_" + "W" * 14
STRIPE_SUB = "sub_" + "W" * 14
CHARGE = "ch_" + "W" * 14
PRICE_PRO = "price_" + "pro" + "X" * 11
PRICE_ENT = "price_" + "ent" + "Y" * 11
WHSEC = "whsec_" + "placeholder" + "Z" * 10
SK = "sk_" + "test_placeholder" + "Q" * 10
DAY = 86400


@pytest.fixture
def env(tmp_path):
    billing = BillingService(str(tmp_path / "billing.db"))
    asyncio.run(billing.initialize())
    customer = asyncio.run(billing.create_customer("payer" + "@" + "example.org"))
    store = OrchestratorStore(str(tmp_path / "orch.db"))
    asyncio.run(store.initialize())
    svc = OrchestratorService(store)
    import stripe
    events = {}

    def _construct(payload, sig, secret):
        return events["next"]

    patches = [
        patch.object(main_module.settings, "stripe_webhook_secret", WHSEC),
        patch.object(main_module.settings, "stripe_secret_key", SK),
        patch.object(main_module.settings, "stripe_price_id_pro", PRICE_PRO),
        patch.object(main_module.settings, "stripe_price_id_enterprise", PRICE_ENT),
        patch("src.main.get_billing_service", return_value=billing),
        patch("src.main.get_orchestrator_service", return_value=svc),
        patch("src.services.orchestrator_store.get_orchestrator_store", return_value=store),
        patch.object(stripe.Webhook, "construct_event", side_effect=_construct),
    ]
    for p in patches:
        p.start()

    counter = {"n": 0}

    def post(event_type, obj, event_id=None):
        counter["n"] += 1
        events["next"] = {"id": event_id or f"evt_{'E' * 10}{counter['n']:04d}", "type": event_type,
                          "data": {"object": obj}}
        return client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"})

    def reload():
        return asyncio.run(billing.get_customer_by_id(customer.id))

    def actions(prefix="billing."):
        return [a for a in asyncio.run(store.list_actions()) if a["action"].startswith(prefix)]

    def runs():
        return asyncio.run(store.list_runs())

    try:
        yield SimpleNamespace(billing=billing, customer=customer, store=store, svc=svc, post=post, reload=reload,
                              actions=actions, runs=runs)
    finally:
        for p in reversed(patches):
            p.stop()


def _checkout(env, tier="pro"):
    return env.post("checkout.session.completed", {
        "metadata": {"customer_id": env.customer.id, "tier": tier}, "customer": STRIPE_CUS, "subscription": STRIPE_SUB,
    })


class TestEventStates:
    def test_checkout_completed_sets_tier_active(self, env):
        resp = _checkout(env)
        assert resp.status_code == 200 and resp.json() == {"ok": True}
        c = env.reload()
        assert c.tier == "pro" and c.billing_status == "active" and c.stripe_customer_id == STRIPE_CUS
        rec = env.actions()
        assert rec[-1]["action"] == "billing.checkout.session.completed" and rec[-1]["outcome"] == "applied:pro"
        assert rec[-1]["actor"] == "stripe" and rec[-1]["target"] == f"org:{c.org_id[:8]}"
        assert STRIPE_CUS not in str(rec)

    def test_subscription_updated_follows_price_and_status(self, env):
        _checkout(env)
        env.post("customer.subscription.updated", {"customer": STRIPE_CUS, "status": "past_due",
                                                   "items": {"data": [{"price": {"id": PRICE_ENT}}]}})
        c = env.reload()
        assert c.tier == "enterprise" and c.billing_status == "past_due" and c.past_due_since is not None
        env.post("customer.subscription.updated", {"customer": STRIPE_CUS, "status": "active",
                                                   "items": {"data": [{"price": {"id": PRICE_PRO}}]}})
        c = env.reload()
        assert c.tier == "pro" and c.billing_status == "active" and c.past_due_since is None

    def test_payment_failed_then_paid(self, env):
        _checkout(env)
        resp = env.post("invoice.payment_failed", {"customer": STRIPE_CUS})
        assert resp.status_code == 200
        c = env.reload()
        assert c.tier == "pro" and c.billing_status == "past_due" and c.past_due_since is not None
        since = c.past_due_since
        # a second failure keeps the original past_due_since (grace period counts from the first)
        env.post("invoice.payment_failed", {"customer": STRIPE_CUS})
        assert env.reload().past_due_since == since
        runs = env.runs()
        assert len(runs) == 2 and all(r["workflow_id"] == "support_escalation" for r in runs)
        assert {r["trigger_class"] for r in runs} == {"payment_event"}
        queue = asyncio.run(env.svc.approval_queue())
        assert queue[0]["artifact_summary_json"]["label"] == "customer escalation: payment_failure (pro)"
        env.post("invoice.paid", {"customer": STRIPE_CUS})
        c = env.reload()
        assert c.tier == "pro" and c.billing_status == "active" and c.past_due_since is None
        outcomes = [a["outcome"] for a in env.actions()]
        assert "escalated:past_due" in outcomes and "applied:active" in outcomes

    def test_refund_and_dispute_escalate_without_tier_change(self, env):
        _checkout(env)
        env.post("charge.refunded", {"customer": STRIPE_CUS, "amount_refunded": 2900})
        # A real Dispute object has no `customer`; the customer comes from the disputed charge.
        import stripe
        with patch.object(stripe.Charge, "retrieve", return_value={"id": CHARGE, "customer": STRIPE_CUS}) as rt:
            env.post("charge.dispute.created", {"charge": CHARGE, "amount": 2900, "reason": "fraudulent"})
        rt.assert_called_once_with(CHARGE)
        c = env.reload()
        assert c.tier == "pro" and c.billing_status == "active"
        labels = sorted(q["artifact_summary_json"]["label"] for q in asyncio.run(env.svc.approval_queue()))
        assert labels == ["customer escalation: billing_dispute (pro)", "customer escalation: refund (pro)"]
        for q in asyncio.run(env.svc.approval_queue()):
            assert q["artifact_summary_json"]["customer"] == {"identified": True, "org_ref": c.org_id[:8]}
            assert q["priority"] == "pro"
        assert STRIPE_CUS not in str(asyncio.run(env.store.list_events())) + str(env.actions())

    def test_subscription_deleted_returns_to_free_and_clears_past_due(self, env):
        _checkout(env)
        env.post("invoice.payment_failed", {"customer": STRIPE_CUS})
        env.post("customer.subscription.deleted", {"customer": STRIPE_CUS})
        c = env.reload()
        assert c.tier == "free" and c.billing_status == "active" and c.past_due_since is None

    def test_unknown_event_is_recorded_as_ignored(self, env):
        resp = env.post("payment_intent.created", {"customer": STRIPE_CUS})
        assert resp.status_code == 200 and resp.json() == {"ok": True}
        rec = env.actions()[-1]
        assert rec["action"] == "billing.payment_intent.created" and rec["outcome"] == "ignored"
        assert env.runs() == []

    def test_unknown_stripe_customer_is_ignored(self, env):
        resp = env.post("invoice.payment_failed", {"customer": "cus_" + "N" * 14})
        assert resp.status_code == 200
        assert env.actions()[-1]["outcome"] == "ignored" and env.runs() == []

    def test_duplicate_event_id_does_nothing(self, env):
        _checkout(env)
        first = env.post("charge.refunded", {"customer": STRIPE_CUS}, event_id="evt_" + "D" * 14)
        again = env.post("charge.refunded", {"customer": STRIPE_CUS}, event_id="evt_" + "D" * 14)
        assert first.json() == {"ok": True} and again.json() == {"ok": True, "duplicate": True}
        assert len(env.runs()) == 1
        dup = [a for a in env.actions() if a["outcome"] == "duplicate"]
        assert len(dup) == 1 and dup[0]["action"] == "billing.charge.refunded"

    def test_stripe_events_table_dedup_is_per_event_id(self, env):
        assert asyncio.run(env.billing.record_stripe_event("evt_1", "x")) is True
        assert asyncio.run(env.billing.record_stripe_event("evt_1", "x")) is False
        assert asyncio.run(env.billing.record_stripe_event("evt_2", "x")) is True
        asyncio.run(env.billing.forget_stripe_event("evt_1"))
        assert asyncio.run(env.billing.record_stripe_event("evt_1", "x")) is True

    def test_failed_delivery_is_not_treated_as_duplicate_on_retry(self, env):
        """A delivery that fails mid-way forgets the event id, so Stripe's retry applies the event
        instead of hitting the duplicate branch with the customer left on the old tier."""
        event_id = "evt_" + "R" * 14
        obj = {"metadata": {"customer_id": env.customer.id, "tier": "pro"}, "customer": STRIPE_CUS,
               "subscription": STRIPE_SUB}
        # the fixture's poster raises server exceptions; this client returns the 500 instead
        lenient = TestClient(app, raise_server_exceptions=False)
        import stripe
        with patch.object(env.billing, "update_tier", side_effect=RuntimeError("database is locked")), \
                patch.object(stripe.Webhook, "construct_event",
                             side_effect=lambda p, s, k: {"id": event_id, "type": "checkout.session.completed",
                                                          "data": {"object": obj}}):
            failed = lenient.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"})
        assert failed.status_code == 500
        assert env.reload().tier == "free"
        retry = env.post("checkout.session.completed", obj, event_id=event_id)
        assert retry.json() == {"ok": True}  # not {"duplicate": True}
        assert env.reload().tier == "pro"
        dup = [a for a in env.actions() if a["outcome"] == "duplicate"]
        assert dup == []

    def test_dispute_whose_charge_lookup_fails_is_retried_not_ignored(self, env):
        _checkout(env)
        import stripe
        lenient = TestClient(app, raise_server_exceptions=False)
        event_id = "evt_" + "F" * 14
        with patch.object(stripe.Charge, "retrieve", side_effect=RuntimeError("api down")), \
                patch.object(stripe.Webhook, "construct_event",
                             side_effect=lambda p, s, k: {"id": event_id, "type": "charge.dispute.created",
                                                          "data": {"object": {"charge": CHARGE}}}):
            failed = lenient.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"})
        assert failed.status_code == 500 and env.runs() == []
        with patch.object(stripe.Charge, "retrieve", return_value={"id": CHARGE, "customer": STRIPE_CUS}):
            ok = env.post("charge.dispute.created", {"charge": CHARGE}, event_id=event_id)
        assert ok.json() == {"ok": True} and len(env.runs()) == 1


class TestEffectiveTier:
    def _rec(self, **kw):
        base = dict(id="c", email="e@example.org", stripe_customer_id=None, stripe_subscription_id=None, api_key="k",
                    tier="pro", org_id="o", created_at=0.0, updated_at=0.0)
        base.update(kw)
        return CustomerRecord(**base)

    def test_grace_maths(self):
        now = 1_800_000_000.0
        assert effective_tier(self._rec(), 14, now) == "pro"
        assert effective_tier(self._rec(billing_status="past_due", past_due_since=now - 13 * DAY), 14, now) == "pro"
        assert effective_tier(self._rec(billing_status="past_due", past_due_since=now - 14 * DAY), 14, now) == "pro"
        just_over = self._rec(billing_status="past_due", past_due_since=now - 14 * DAY - 1)
        assert effective_tier(just_over, 14, now) == "free"
        assert effective_tier(self._rec(billing_status="past_due", past_due_since=None), 14, now) == "pro"
        assert effective_tier(self._rec(billing_status="past_due", past_due_since=now - DAY), 0, now) == "free"
        assert effective_tier(None, 14, now) == "free"

    def test_old_rows_default_to_active(self):
        rec = CustomerRecord(id="c", email="e@example.org", stripe_customer_id=None, stripe_subscription_id=None,
                             api_key="k", tier="pro", org_id="o", created_at=0.0, updated_at=0.0)
        assert rec.billing_status == "active" and rec.past_due_since is None

    def test_past_due_beyond_grace_is_served_as_free(self, env):
        _checkout(env)
        env.post("invoice.payment_failed", {"customer": STRIPE_CUS})
        asyncio.run(env.billing.set_billing_status(env.customer.id, "past_due", past_due_since=time.time() - 20 * DAY))
        c = env.reload()
        me = client.get("/billing/me", headers={"x-api-key": c.api_key}).json()
        assert me["tier"] == "pro" and me["effective_tier"] == "free" and me["billing_status"] == "past_due"
        assert me["rate_limit_per_minute"] == main_module.settings.rate_limit_free
        # the MCP caller and the limiter bucket see the effective tier
        caller = asyncio.run(main_module._resolve_mcp_caller(SimpleNamespace(headers={"x-api-key": c.api_key})))
        assert caller.tier == "free" and caller.customer_id == c.id
        env.post("invoice.paid", {"customer": STRIPE_CUS})
        me = client.get("/billing/me", headers={"x-api-key": c.api_key}).json()
        assert me["effective_tier"] == "pro" and me["rate_limit_per_minute"] == main_module.settings.rate_limit_pro


class TestAC18Flow:
    def test_free_to_pro_to_cancelled(self, env):
        key = env.customer.api_key
        me = client.get("/billing/me", headers={"x-api-key": key}).json()
        assert me["tier"] == "free" and me["rate_limit_per_minute"] == main_module.settings.rate_limit_free
        cfg = client.get("/billing/config").json()
        assert cfg["checkout_enabled"] is True and cfg["portal_enabled"] is True
        assert next(t for t in cfg["tiers"] if t["id"] == "pro")["checkout_available"] is True
        _checkout(env, "pro")
        me = client.get("/billing/me", headers={"x-api-key": key}).json()
        assert me["tier"] == "pro" and me["effective_tier"] == "pro"
        assert me["rate_limit_per_minute"] == main_module.settings.rate_limit_pro
        env.post("customer.subscription.deleted", {"customer": STRIPE_CUS})
        me = client.get("/billing/me", headers={"x-api-key": key}).json()
        assert me["tier"] == "free" and me["rate_limit_per_minute"] == main_module.settings.rate_limit_free
        kinds = [a["action"] for a in env.actions()]
        assert kinds == ["billing.checkout.session.completed", "billing.customer.subscription.deleted"]

    def test_webhook_without_stripe_secret_is_503(self, env):
        with patch.object(main_module.settings, "stripe_webhook_secret", None):
            resp = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "x"})
        assert resp.status_code == 503
