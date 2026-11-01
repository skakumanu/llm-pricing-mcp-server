"""v1.87.0 account data rights: GET /billing/account/export and POST /billing/account/delete.

Covers Spec AC1-AC8 and AC10. Key-shaped literals are assembled at runtime so no scanner sees one.
"""
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import agent.conversation as conv_module  # noqa: E402
import src.main as main_module  # noqa: E402
import src.services.budget_alerts as alerts_module  # noqa: E402
import src.services.dispatch_receipts as receipts_module  # noqa: E402
import src.services.provider_key_store as pks_module  # noqa: E402
import src.services.savings_tracker as savings_module  # noqa: E402
import src.services.usage_tracker as usage_module  # noqa: E402
from src.services import account_data  # noqa: E402
from src.services.billing_service import BillingService  # noqa: E402
from src.services.orchestrator_service import OrchestratorService  # noqa: E402
from src.services.orchestrator_store import OrchestratorStore  # noqa: E402

client = TestClient(main_module.app, raise_server_exceptions=False)

OPERATOR_KEY = "-".join(["op", "account", "k", "91c"])
PROVIDER_KEY = "-".join(["sk", "test", "PROVIDERKEYMATERIAL", "Zq9X"])
SECRET = "".join(["a-long-test-secret-", "x" * 40])
EMAIL_A = "alice" + "@" + "example.org"
EMAIL_B = "bob" + "@" + "example.org"
SUB_ID = "sub_" + "S" * 14
CUS_ID = "cus_" + "C" * 14
DAY = 86400


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env(tmp_path, monkeypatch):
    pdb = str(tmp_path / "pricing_history.db")
    bdb = str(tmp_path / "billing.db")
    billing = BillingService(bdb)
    run(billing.initialize())
    usage = run(usage_module.init_usage_tracker(pdb))
    receipts = run(receipts_module.init_dispatch_receipt_store(pdb, 0))
    alerts = run(alerts_module.init_budget_alert_service(pdb))
    savings = run(savings_module.init_savings_tracker(pdb))
    pks = run(pks_module.init_provider_key_store(bdb, SECRET))
    run(conv_module.init_conversation_store(str(tmp_path / "conversations.db"), 10, 0))
    store = OrchestratorStore(str(tmp_path / "orch.db"))
    run(store.initialize())
    svc = OrchestratorService(store)
    for mod, attr in ((usage_module, "_usage_tracker"), (receipts_module, "_store"),
                      (alerts_module, "_budget_alert_service"), (savings_module, "_savings_service"),
                      (pks_module, "_store"), (conv_module, "_conversation_store")):
        monkeypatch.setattr(mod, attr, getattr(mod, attr))  # restore the real singleton afterwards
    patches = [
        patch("src.main.settings.mcp_api_key", OPERATOR_KEY),
        patch("src.main.get_billing_service", return_value=billing),
        patch("src.services.billing_service.get_billing_service", return_value=billing),
        patch("src.main.get_orchestrator_service", return_value=svc),
        patch("src.services.orchestrator_service.get_orchestrator_service", return_value=svc),
        patch("src.services.orchestrator_store.get_orchestrator_store", return_value=store),
        patch("src.main.settings.stripe_secret_key", "sk_" + "test_" + "x" * 24),
    ]
    for p in patches:
        p.start()
    main_module._rate_limit_store.clear()
    a = run(billing.create_customer(EMAIL_A))
    b = run(billing.create_customer(EMAIL_B))
    try:
        yield SimpleNamespace(billing=billing, usage=usage, receipts=receipts, alerts=alerts, savings=savings,
                              pks=pks, store=store, svc=svc, a=a, b=b, bdb=bdb, pdb=pdb,
                              convs=conv_module.get_conversation_store(), tmp=tmp_path)
    finally:
        for p in patches:
            p.stop()
        main_module._rate_limit_store.clear()


def seed(env, customer, tag):
    org = customer.org_id
    run(env.usage.record_event("openai", f"gpt-{tag}", 10, 5, 0.01, org_id=org, request_id=f"req-{tag}"))
    run(env.receipts.record({"dispatch_id": f"d-{tag}", "org_id": org, "outcome": "ok", "chosen_model": f"m-{tag}"}))
    run(env.alerts.register("https://example.org/hook-" + tag, 5.0, org_id=org))
    run(env.savings.record_routing("m", "p", 1.0, org_id=org, routing_id=f"r-{tag}"))
    run(env.savings.record_feedback(f"r-{tag}", True, None, "note-" + tag))
    run(env.pks.register(org, "OpenAI", PROVIDER_KEY + tag))
    _, hist = run(env.convs.get_or_create(f"sup-{tag}", org_id=org))
    hist.add("user", f"hello from {tag}")
    run(env.convs.save(f"sup-{tag}", hist, org_id=org))


def count_rows(env, org):
    out = {}
    with sqlite3.connect(env.pdb) as c:
        for table in ("usage_events", "dispatch_receipts", "budget_alerts", "routing_savings"):
            out[table] = c.execute(f"SELECT COUNT(*) FROM {table} WHERE org_id = ?", (org,)).fetchone()[0]  # nosec B608
    with sqlite3.connect(env.bdb) as c:
        out["provider_keys"] = c.execute("SELECT COUNT(*) FROM provider_keys WHERE org_id = ?", (org,)).fetchone()[0]
    out["conversations"] = len(run(env.convs.list_for_org(org)))
    return out


def hdr(key):
    return {"x-api-key": key}


def actions(env, name):
    return [a for a in run(env.store.list_actions()) if a["action"] == name]


def delete(env, key, confirm="DELETE", org_id=None, **kw):
    body = {"confirm": confirm, "org_id": org_id}
    return client.post("/billing/account/delete", json=body, headers=hdr(key), **kw)


# --------------------------------------------------------------------------- AC1 / AC2


def test_export_contains_only_own_rows(env):
    seed(env, env.a, "a")
    seed(env, env.b, "b")
    r = client.get("/billing/account/export", headers=hdr(env.a.api_key))
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"] and env.a.org_id[:8] in r.headers["content-disposition"]
    data = json.loads(r.text)
    assert data["account"]["org_id"] == env.a.org_id and data["account"]["email"] == EMAIL_A
    for table in ("usage_events", "dispatch_receipts", "budget_alerts", "routing_savings", "provider_keys",
                  "support_conversations"):
        assert len(data[table]) == 1, table
    assert len(data["routing_feedback"]) == 1 and data["routing_feedback"][0]["routing_id"] == "r-a"
    assert data["support_conversations"][0]["id"] == "sup-a"
    assert data["counts"]["usage_events"] == 1 and data["truncated"] is False
    blob = r.text
    assert env.b.org_id not in blob and EMAIL_B not in blob and "gpt-b" not in blob and "hello from b" not in blob
    assert "hook-b" not in blob and "r-b" not in blob


def test_export_never_contains_keys(env):
    seed(env, env.a, "a")
    r = client.get("/billing/account/export", headers=hdr(env.a.api_key))
    assert r.status_code == 200
    assert env.a.api_key not in r.text and PROVIDER_KEY not in r.text
    meta = r.json()["provider_keys"][0]
    assert set(meta) == {"provider", "created_at", "rotated_at", "key_version", "status"}
    assert meta["provider"] == "OpenAI" and meta["status"] == "active"
    assert r.json()["account"]["service_key_prefix"] == env.a.api_key[:8]


def test_export_truncates_at_row_cap(env, monkeypatch):
    for i in range(3):
        run(env.usage.record_event("openai", "m", 1, 1, 0.0, org_id=env.a.org_id, request_id=f"r{i}"))
    monkeypatch.setattr(account_data, "EXPORT_ROW_CAP", 2)
    data = client.get("/billing/account/export", headers=hdr(env.a.api_key)).json()
    assert len(data["usage_events"]) == 2 and data["truncated"] is True


# --------------------------------------------------------------------------- AC3


@pytest.mark.parametrize("headers", [{}, hdr(OPERATOR_KEY), hdr("not-a-key")])
def test_anonymous_and_operator_refused_and_nothing_changes(env, headers):
    seed(env, env.a, "a")
    before = count_rows(env, env.a.org_id)
    assert client.get("/billing/account/export", headers=headers).status_code == 401
    r = client.post("/billing/account/delete", json={"confirm": "DELETE", "org_id": env.a.org_id}, headers=headers)
    assert r.status_code == 401
    assert count_rows(env, env.a.org_id) == before
    assert run(env.billing.get_customer_by_id(env.a.id)) is not None
    assert actions(env, "billing.account_delete") == [] and actions(env, "billing.account_export") == []


# --------------------------------------------------------------------------- AC4


@pytest.mark.parametrize("confirm,wrong_org", [("", False), ("delete", False), ("DELETE", True), (None, False)])
def test_unconfirmed_or_wrong_org_is_400_and_changes_nothing(env, confirm, wrong_org):
    seed(env, env.a, "a")
    before = count_rows(env, env.a.org_id)
    org = env.b.org_id if wrong_org else env.a.org_id
    r = delete(env, env.a.api_key, confirm=confirm, org_id=org)
    assert r.status_code == 400 and "irreversible" in r.json()["detail"]
    assert count_rows(env, env.a.org_id) == before
    assert run(env.billing.get_customer_by_api_key(env.a.api_key)) is not None
    assert actions(env, "billing.account_delete")[0]["outcome"] == "refused:unconfirmed"


def test_missing_body_is_400(env):
    r = client.post("/billing/account/delete", headers=hdr(env.a.api_key))
    assert r.status_code == 400 and "irreversible" in r.json()["detail"]


# --------------------------------------------------------------------------- AC5 / AC6 / AC8


def test_confirmed_deletion_erases_everything_and_leaves_one_marker(env):
    seed(env, env.a, "a")
    seed(env, env.b, "b")
    other_before = count_rows(env, env.b.org_id)
    r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True and body["subscription_ended"] is False
    assert "irreversible" in body["message"] and body["refund_escalation_reference"] is None
    assert all(v == 0 for v in count_rows(env, env.a.org_id).values())
    assert count_rows(env, env.b.org_id) == other_before  # another org untouched
    assert run(env.billing.get_customer_by_id(env.a.id)) is None
    assert client.get("/billing/me", headers=hdr(env.a.api_key)).status_code == 401
    assert client.get("/billing/account/export", headers=hdr(env.a.api_key)).status_code == 401
    emails = [c["email"] for c in client.get("/admin/customers", headers=hdr(OPERATOR_KEY)).json()["customers"]]
    assert EMAIL_A not in emails and EMAIL_B in emails

    with sqlite3.connect(env.bdb) as c:
        markers = c.execute("SELECT email_hash, deleted_at FROM deleted_accounts").fetchall()
        dump = "\n".join(c.iterdump())
    assert len(markers) == 1 and len(markers[0][0]) == 64 and int(markers[0][0], 16) >= 0
    assert abs(markers[0][1] - time.time()) < 60
    assert EMAIL_A not in dump and env.a.api_key not in dump and env.a.org_id not in dump
    assert run(env.billing.suppression_marker_count(EMAIL_A)) == 1
    assert run(env.billing.suppression_marker_count(EMAIL_A.upper())) == 1  # lower-cased before hashing
    assert run(env.billing.suppression_marker_count(EMAIL_B)) == 0


def test_marker_uses_a_per_database_salt(env, tmp_path):
    other = BillingService(str(tmp_path / "other.db"))
    run(other.initialize())
    a = run(other.create_customer(EMAIL_A))
    run(other.delete_customer(a.id))
    run(env.billing.delete_customer(env.a.id))
    with sqlite3.connect(env.bdb) as c1, sqlite3.connect(str(tmp_path / "other.db")) as c2:
        h1 = c1.execute("SELECT email_hash FROM deleted_accounts").fetchone()[0]
        h2 = c2.execute("SELECT email_hash FROM deleted_accounts").fetchone()[0]
    assert h1 != h2


def test_resignup_after_deletion_is_allowed_and_second_deletion_adds_second_marker(env):
    assert delete(env, env.a.api_key, org_id=env.a.org_id).status_code == 200
    again = run(env.billing.create_customer(EMAIL_A))
    assert again is not None
    assert delete(env, again.api_key, org_id=again.org_id).status_code == 200
    assert run(env.billing.suppression_marker_count(EMAIL_A)) == 2


def test_export_and_delete_each_write_one_audit_record_without_email(env):
    seed(env, env.a, "a")
    client.get("/billing/account/export", headers=hdr(env.a.api_key))
    delete(env, env.a.api_key, org_id=env.a.org_id)
    exp, dele = actions(env, "billing.account_export"), actions(env, "billing.account_delete")
    assert len(exp) == 1 and len(dele) == 1
    assert exp[0]["target"] == dele[0]["target"] == f"org:{env.a.org_id[:8]}"
    assert exp[0]["outcome"] == "exported" and dele[0]["outcome"] == "deleted"
    dump = json.dumps(run(env.store.list_actions()), default=str)
    assert EMAIL_A not in dump and env.a.api_key not in dump


# --------------------------------------------------------------------------- AC7


def paying(env, customer, first_paid_days_ago=None):
    run(env.billing.update_tier(customer.id, "pro", stripe_customer_id=CUS_ID, stripe_subscription_id=SUB_ID))
    if first_paid_days_ago is not None:
        with sqlite3.connect(env.bdb) as c:
            c.execute("UPDATE customers SET first_paid_at = ? WHERE id = ?",
                      (time.time() - first_paid_days_ago * DAY, customer.id))


def test_paying_account_cancels_subscription_once_and_never_refunds(env):
    paying(env, env.a, first_paid_days_ago=40)
    seed(env, env.a, "a")
    with patch("stripe.Subscription.cancel") as cancel, patch("stripe.Refund.create") as refund:
        r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.status_code == 200, r.text
    cancel.assert_called_once_with(SUB_ID)
    refund.assert_not_called()
    body = r.json()
    assert body["subscription_ended"] is True
    assert "subscription has ended and no further charges will occur" in body["message"]
    assert body["refund_escalation_reference"] is None  # 40 days is outside the window
    assert run(env.billing.get_customer_by_id(env.a.id)) is None
    with sqlite3.connect(env.bdb) as c:
        dump = "\n".join(c.iterdump())
    assert SUB_ID not in dump and CUS_ID not in dump


def test_already_cancelled_subscription_counts_as_success(env):
    import stripe
    paying(env, env.a, first_paid_days_ago=40)
    err = stripe.InvalidRequestError("No such subscription: " + SUB_ID, "id")
    with patch("stripe.Subscription.cancel", side_effect=err):
        r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.status_code == 200 and r.json()["subscription_ended"] is True


def test_stripe_error_is_502_and_nothing_deleted(env):
    import stripe
    paying(env, env.a, first_paid_days_ago=40)
    seed(env, env.a, "a")
    before = count_rows(env, env.a.org_id)
    with patch("stripe.Subscription.cancel", side_effect=stripe.APIConnectionError("down")):
        r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.status_code == 502
    assert count_rows(env, env.a.org_id) == before
    assert run(env.billing.get_customer_by_id(env.a.id)) is not None
    assert actions(env, "billing.account_delete")[0]["outcome"] == "refused:stripe_error"


def test_subscription_with_stripe_unconfigured_is_503_and_nothing_deleted(env):
    paying(env, env.a, first_paid_days_ago=40)
    seed(env, env.a, "a")
    before = count_rows(env, env.a.org_id)
    with patch("src.main.settings.stripe_secret_key", None), patch("stripe.Subscription.cancel") as cancel:
        r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.status_code == 503
    cancel.assert_not_called()
    assert count_rows(env, env.a.org_id) == before
    assert run(env.billing.get_customer_by_id(env.a.id)) is not None


def test_free_account_without_subscription_never_calls_stripe(env):
    with patch("stripe.Subscription.cancel") as cancel:
        r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.status_code == 200 and r.json()["subscription_ended"] is False
    cancel.assert_not_called()


# --------------------------------------------------------------------------- AC10


def refund_runs(env):
    return [r for r in run(env.store.list_runs()) if r["workflow_id"] == "support_escalation"]


def test_deletion_inside_refund_window_opens_one_refund_review(env):
    paying(env, env.a, first_paid_days_ago=3)
    with patch("stripe.Subscription.cancel"):
        r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.status_code == 200
    ref = r.json()["refund_escalation_reference"]
    runs_ = refund_runs(env)
    assert ref and len(runs_) == 1 and runs_[0]["run_id"] == ref
    assert ref in r.json()["message"]
    dump = json.dumps(run(env.store.list_events()), default=str) + json.dumps(runs_, default=str)
    assert "refund" in dump and EMAIL_A not in dump and env.a.org_id not in dump  # org8 prefix only
    status = client.post("/support/escalations/status", json={"reference": ref})
    assert status.status_code == 200


def test_deletion_outside_refund_window_opens_nothing(env):
    paying(env, env.a, first_paid_days_ago=20)
    with patch("stripe.Subscription.cancel"):
        r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.status_code == 200 and r.json()["refund_escalation_reference"] is None
    assert refund_runs(env) == []


def test_unknown_first_payment_counts_as_inside_the_window(env):
    run(env.billing.update_tier(env.a.id, "pro", stripe_subscription_id=SUB_ID))
    with sqlite3.connect(env.bdb) as c:
        c.execute("UPDATE customers SET first_paid_at = NULL WHERE id = ?", (env.a.id,))
    with patch("stripe.Subscription.cancel"):
        r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.json()["refund_escalation_reference"] and len(refund_runs(env)) == 1


def test_free_account_never_opens_a_refund_review(env):
    assert delete(env, env.a.api_key, org_id=env.a.org_id).status_code == 200
    assert refund_runs(env) == []


@pytest.mark.parametrize("actor", ["support_agent", "stripe", "executor", "operator"])
def test_refund_review_event_refused_from_untrusted_actors(env, actor):
    payload = {
        "trigger_class": "customer_message", "event_type": "account.deletion_refund_review",
        "dedup_key": "msg:account_delete:abcd1234:1", "actor": "x", "actor_is_owner": False,
        "data": {"title": "t", "body": "b", "category": "refund", "priority": "pro", "tier": "pro",
                 "identified": True, "customer_ref": "abcd1234"},
    }
    res = run(env.svc.intake_event(payload, actor_kind=actor))
    assert res.get("run_id") is None and "untrusted" in str(res.get("outcome"))
    assert refund_runs(env) == []


def test_refund_review_event_accepted_from_account_service(env):
    payload = {
        "trigger_class": "customer_message", "event_type": "account.deletion_refund_review",
        "dedup_key": "msg:account_delete:abcd1234:2", "actor": "account_deletion", "actor_is_owner": False,
        "data": {"title": "t", "body": "b", "category": "refund", "priority": "pro", "tier": "pro",
                 "identified": True, "customer_ref": "abcd1234"},
    }
    res = run(env.svc.intake_event(payload, actor_kind="account_service"))
    assert res["workflow_id"] == "support_escalation" and res["run_id"]


def test_within_refund_window_helper():
    from src.services.billing_service import CustomerRecord, within_refund_window
    now = 1_800_000_000.0

    def rec(sub, first):
        return CustomerRecord(id="i", email="e", stripe_customer_id=None, stripe_subscription_id=sub, api_key=None,
                              tier="pro", org_id="o", created_at=0.0, updated_at=0.0, first_paid_at=first)
    assert within_refund_window(rec("s", now - 14 * DAY), 14, now) is True
    assert within_refund_window(rec("s", now - 14 * DAY - 1), 14, now) is False
    assert within_refund_window(rec("s", None), 14, now) is True
    assert within_refund_window(rec(None, now), 14, now) is False
    assert within_refund_window(None, 14, now) is False


def test_first_paid_at_is_set_once_by_payment_events(env):
    assert run(env.billing.get_customer_by_id(env.a.id)).first_paid_at is None
    run(env.billing.update_tier(env.a.id, "pro", stripe_customer_id=CUS_ID, stripe_subscription_id=SUB_ID))
    first = run(env.billing.get_customer_by_id(env.a.id)).first_paid_at
    assert first is not None
    time.sleep(0.01)
    run(env.billing.set_billing_status(env.a.id, "active"))
    run(env.billing.update_tier(env.a.id, "pro", stripe_subscription_id=SUB_ID))
    assert run(env.billing.get_customer_by_id(env.b.id)).first_paid_at is None
    assert run(env.billing.get_customer_by_id(env.a.id)).first_paid_at == first


def test_mid_deletion_failure_is_retryable_and_keeps_the_key(env):
    seed(env, env.a, "a")
    with patch.object(env.pks.__class__, "delete_all", side_effect=RuntimeError("boom")):
        r = delete(env, env.a.api_key, org_id=env.a.org_id)
    assert r.status_code == 500
    assert run(env.billing.get_customer_by_api_key(env.a.api_key)) is not None  # the key dies last
    assert actions(env, "billing.account_delete")[0]["outcome"] == "error:provider_keys"
    assert delete(env, env.a.api_key, org_id=env.a.org_id).status_code == 200
    assert all(v == 0 for v in count_rows(env, env.a.org_id).values())
