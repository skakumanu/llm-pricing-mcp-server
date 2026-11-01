"""Compliance audit check catalogue (v1.88.0): every check against fixtures, no network.

Covers the Spec's AC4 (Stripe reconciliation counts, no ids in evidence, not-configured and
permission n/a), AC5 (data-quality regression), AC6 (key-hash migration, read-only), AC7 (Litestream
completeness), AC8 (launch checklist consistency) and AC9 (disabled / repo-side n/a).
"""
import itertools
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import audit_checks as ac  # noqa: E402
from src.services.billing_service import _KEY_PLACEHOLDER_PREFIX  # noqa: E402

NOW = 1_800_000_000.0
STRIPE_KEY = "-".join(["restricted", "audit", "k", "55e"])
ALL_SECRETS = {n: "x" for n in ac.SECRET_NAMES}


def settings(**kw):
    base = dict(legal_pages_live=False, stripe_secret_key=None, stripe_audit_key=None, app_version="0.0.0",
                mcp_api_key="op", audit_stale_price_pct_warn=10.0, audit_escalation_max_age_days=7,
                audit_llm_summary=False, audit_summary_model="claude-haiku-5-5",
                stripe_price_id_pro="price_pro_fixture", stripe_price_id_enterprise=None,
                billing_db_path="billing.db", orchestrator_db_path="orchestrator.db")
    base.update(kw)
    return SimpleNamespace(**base)


def ctx(**kw):
    kw.setdefault("settings", settings())
    kw.setdefault("now", NOW)
    kw.setdefault("env", {})
    return ac.AuditContext(**kw)


def by_id(results):
    return {r["id"]: r for r in results}


async def one(check_id, c):
    spec = next(s for s in ac.CHECK_SPECS if s.id == check_id)
    return await ac._run_one(spec, c, [])


# --------------------------------------------------------------------------- fixtures

def make_billing(path, customers=(), deleted=(), salt=True):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE customers (id TEXT PRIMARY KEY, email TEXT, stripe_customer_id TEXT,
            stripe_subscription_id TEXT, api_key TEXT NOT NULL, tier TEXT DEFAULT 'free', org_id TEXT,
            billing_status TEXT DEFAULT 'active', api_key_hash TEXT);
        CREATE TABLE deleted_accounts (marker_id INTEGER PRIMARY KEY AUTOINCREMENT, email_hash TEXT, deleted_at REAL);
        CREATE TABLE billing_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    for c in customers:
        db.execute("INSERT INTO customers (id, email, stripe_customer_id, stripe_subscription_id, api_key, tier, "
                   "org_id, billing_status, api_key_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                   (c["id"], c["id"] + "@example.invalid", c.get("cus"), c.get("sub"),
                    c.get("api_key", _KEY_PLACEHOLDER_PREFIX + c["id"]), c.get("tier", "free"), "org-" + c["id"],
                    c.get("status", "active"), c.get("hash", "h" * 64)))
    for h, at in deleted:
        db.execute("INSERT INTO deleted_accounts (email_hash, deleted_at) VALUES (?, ?)", (h, at))
    if salt:
        db.execute("INSERT INTO billing_meta (key, value) VALUES ('suppression_salt', 'abc')")
    db.commit()
    db.close()
    return str(path)


def fake_stripe(subs, webhooks=None, error=None):
    async def lister(key):
        assert key == STRIPE_KEY
        if error:
            raise error
        return subs

    async def hooks(key):
        if error:
            raise error
        return webhooks or []
    return lister, hooks


def sub(sid, cus, status="active", price="price_pro_fixture"):
    return {"id": sid, "customer": cus, "status": status, "price_id": price}


class AuthenticationError(Exception):
    """Same class NAME as stripe's, which is how the check recognises a permission refusal."""


# --------------------------------------------------------------------------- catalogue shape

class TestCatalogue:
    def test_ids_unique_and_severities_valid(self):
        assert len(set(ac.CHECK_IDS)) == len(ac.CHECK_IDS)
        assert all(s.severity in ac.SEVERITIES for s in ac.CHECK_SPECS)
        assert ac.CHECK_SPECS[-1].id == "audit.coverage"

    def test_key_placeholder_mirrors_billing_service(self):
        assert ac._KEY_PLACEHOLDER_PREFIX == _KEY_PLACEHOLDER_PREFIX

    def test_webhook_events_are_the_seven_of_the_checklist(self):
        from src.services.launch_checklist import REQUIRED_ITEMS
        lc06 = dict(REQUIRED_ITEMS)["LC-06"]
        assert set(ac.WEBHOOK_EVENTS) == set(lc06) - {"webhook"} and len(ac.WEBHOOK_EVENTS) == 7

    def test_lc_map_is_derived_from_the_specs(self):
        assert "stripe.webhook_endpoint" in ac.LC_MAP["LC-06"]
        assert "billing.key_hash_migration" in ac.LC_MAP["LC-21"]
        assert "launch.checklist_consistency" in ac.LC_MAP["LC-16"]

    def test_parse_disabled(self):
        assert ac.parse_disabled(" a.b, c.d ,,") == frozenset({"a.b", "c.d"})
        assert ac.parse_disabled(None) == frozenset()

    async def test_run_all_never_raises_and_a_broken_check_is_na(self, monkeypatch):
        async def boom(c):
            raise RuntimeError("secret detail must not leak")
        specs = tuple(ac.CheckSpec(s.id, s.title, s.severity, boom if s.id == "legal.live_gate" else s.fn, s.lc,
                                   s.late) for s in ac.CHECK_SPECS)
        monkeypatch.setattr(ac, "CHECK_SPECS", specs)
        results = by_id(await ac.run_all(ctx()))
        assert results["legal.live_gate"]["status"] == "n/a"
        assert results["legal.live_gate"]["evidence"]["reason"] == "error:RuntimeError"
        assert "secret detail" not in json.dumps(results)

    async def test_run_all_order_and_coverage_last(self):
        results = await ac.run_all(ctx())
        assert [r["id"] for r in results] == list(ac.CHECK_IDS)
        assert set(ac.count_statuses(results)) == {"pass", "warn", "fail", "n_a"}


# --------------------------------------------------------------------------- legal

class TestLegal:
    def _docs(self, tmp_path, text):
        d = tmp_path / "legal"
        d.mkdir()
        (d / "terms.md").write_text(text, encoding="utf-8")
        return d

    async def test_clean_docs_pass(self, tmp_path):
        r = await one("legal.placeholders", ctx(legal_docs_dir=self._docs(tmp_path, "All good.")))
        assert r["status"] == "pass" and r["lc"] == ["LC-12"]

    async def test_placeholder_with_flag_on_fails_with_file_and_token(self, tmp_path):
        c = ctx(settings=settings(legal_pages_live=True), legal_docs_dir=self._docs(tmp_path, "Operated by [ENTITY NAME]."))
        r = await one("legal.placeholders", c)
        assert r["status"] == "fail"
        assert r["evidence"]["files"] == {"terms.md": ["[ENTITY NAME]"]}

    async def test_placeholder_with_flag_off_only_warns(self, tmp_path):
        r = await one("legal.placeholders", ctx(legal_docs_dir=self._docs(tmp_path, "Operated by [ENTITY NAME].")))
        assert r["status"] == "warn"

    async def test_live_gate_fails_when_flag_on_and_placeholders_remain(self, tmp_path):
        c = ctx(settings=settings(legal_pages_live=True), legal_docs_dir=self._docs(tmp_path, "[STATE]"))
        r = await one("legal.live_gate", c)
        assert r["status"] == "fail" and "LEGAL_PAGES_LIVE" in r["evidence"]["problem"]

    async def test_live_gate_passes_when_consistent(self, tmp_path):
        r = await one("legal.live_gate", ctx(legal_docs_dir=self._docs(tmp_path, "Clean.")))
        assert r["status"] == "pass" and r["evidence"]["checkout_enabled"] is False


# --------------------------------------------------------------------------- litestream

class TestLitestream:
    def _settings(self):
        return settings(billing_db_path="/app/data/billing.db", orchestrator_db_path="/app/data/orchestrator.db",
                        pricing_history_db_path="/app/data/pricing_history.db", conversation_db_path=None)

    async def test_missing_yml_is_na(self, tmp_path):
        r = await one("backup.litestream_config", ctx(litestream_config_path=tmp_path / "nope.yml"))
        assert r["status"] == "n/a" and "not present" in r["evidence"]["reason"]

    async def test_setting_missing_from_yml_fails_naming_the_setting(self, tmp_path):
        yml = tmp_path / "l.yml"
        yml.write_text("dbs:\n  - path: /app/data/billing.db\n  - path: /app/data/orchestrator.db\n", encoding="utf-8")
        r = await one("backup.litestream_config", ctx(settings=self._settings(), litestream_config_path=yml,
                                                      env=ALL_SECRETS))
        assert r["status"] == "fail" and r["evidence"]["missing_settings"] == ["pricing_history_db_path"]

    async def test_shipped_config_passes(self):
        s = settings(**{k: "/app/data/" + Path(v).name for k, v in {
            "billing_db_path": "billing.db", "orchestrator_db_path": "orchestrator.db",
            "pricing_history_db_path": "pricing_history.db", "conversation_db_path": "conversations.db"}.items()})
        c = ctx(settings=s, litestream_config_path=project_root / "litestream.yml",
                env={"LITESTREAM_REPLICA_URL": "s3://bucket/x"})
        r = await one("backup.litestream_config", c)
        assert r["status"] == "pass", r["evidence"]

    async def test_replica_url_unset_warns_without_leaking_values(self):
        s = settings(**{k: "/app/data/" + Path(v).name for k, v in {
            "billing_db_path": "billing.db", "orchestrator_db_path": "orchestrator.db",
            "pricing_history_db_path": "pricing_history.db", "conversation_db_path": "conversations.db"}.items()})
        r = await one("backup.litestream_config", ctx(settings=s, litestream_config_path=project_root / "litestream.yml"))
        assert r["status"] == "warn" and r["evidence"]["LITESTREAM_REPLICA_URL"] == "unset"

    async def test_local_relative_paths_are_na(self):
        r = await one("backup.litestream_config", ctx(litestream_config_path=project_root / "litestream.yml"))
        assert r["status"] == "n/a" and "local run" in r["evidence"]["reason"]


# --------------------------------------------------------------------------- secrets and release

class TestSecretsAndRelease:
    async def test_names_only_never_values(self):
        env = dict(ALL_SECRETS, STRIPE_SECRET_KEY="sk_live_super_secret_value")
        r = await one("secrets.presence", ctx(env=env))
        assert r["status"] == "pass"
        assert "super_secret" not in json.dumps(r)
        assert r["evidence"]["secrets"]["STRIPE_SECRET_KEY"] == "set"

    async def test_missing_operator_key_fails_other_missing_warns(self):
        env = {n: "x" for n in ac.SECRET_NAMES if n != "MCP_API_KEY"}
        assert (await one("secrets.presence", ctx(settings=settings(mcp_api_key=None), env=env)))["status"] == "fail"
        env2 = {n: "x" for n in ac.SECRET_NAMES if n != "STRIPE_AUDIT_KEY"}
        r = await one("secrets.presence", ctx(env=env2))
        assert r["status"] == "warn" and r["evidence"]["secrets"]["STRIPE_AUDIT_KEY"] == "unset"

    async def test_secret_str_in_settings_counts_as_set(self):
        env = {n: "x" for n in ac.SECRET_NAMES if n != "STRIPE_AUDIT_KEY"}
        r = await one("secrets.presence", ctx(settings=settings(stripe_audit_key=SecretStr("k")), env=env))
        assert r["status"] == "pass"

    async def test_version_consistency_passes_in_repo_and_fails_on_drift(self, tmp_path):
        import src
        c = ctx(settings=settings(app_version=src.__version__))
        assert (await one("release.version_consistency", c))["status"] == "pass"
        page = tmp_path / "wn.html"
        page.write_text('<span class="version-badge">v0.0.1</span>', encoding="utf-8")
        r = await one("release.version_consistency", ctx(settings=settings(app_version=src.__version__),
                                                         whats_new_path=page))
        assert r["status"] == "fail" and r["evidence"]["versions"]["whats-new top entry"] == "0.0.1"

    async def test_deployed_vs_repo(self):
        import src
        assert (await one("release.deployed_matches_repo", ctx()))["status"] == "n/a"
        assert (await one("release.deployed_matches_repo", ctx(repo_version=src.__version__)))["status"] == "pass"
        assert (await one("release.deployed_matches_repo", ctx(repo_version="999.0.0")))["status"] == "warn"
        assert (await one("release.deployed_matches_repo", ctx(repo_version="0.0.1")))["status"] == "fail"


# --------------------------------------------------------------------------- data quality (AC5)

def dq_loader(confirmed=98.0, withheld=1, never=2, stale=3, stale_pct=3.0):
    async def load():
        return {"success": True, "confirmed_pct": confirmed, "withheld_for_drift": withheld, "never_priced": never,
                "stale_models": stale, "stale_pct": stale_pct, "total_models": 100}
    return load


class TestDataQuality:
    PREV = {"confirmed_pct": 98.1, "withheld_for_drift": 1, "never_priced": 2}

    async def test_drop_of_point_two_fails(self):
        r = await one("data.quality_regression", ctx(load_data_quality=dq_loader(97.9), previous_numbers=self.PREV))
        assert r["status"] == "fail" and "confirmed_pct dropped" in r["evidence"]["problems"]

    async def test_equal_or_higher_passes(self):
        for pct in (98.1, 99.0):
            r = await one("data.quality_regression", ctx(load_data_quality=dq_loader(pct), previous_numbers=self.PREV))
            assert r["status"] == "pass"

    async def test_drop_below_threshold_passes(self):
        r = await one("data.quality_regression", ctx(load_data_quality=dq_loader(98.05), previous_numbers=self.PREV))
        assert r["status"] == "pass"

    async def test_rise_in_withheld_or_never_priced_fails(self):
        r = await one("data.quality_regression", ctx(load_data_quality=dq_loader(98.1, withheld=2), previous_numbers=self.PREV))
        assert r["status"] == "fail" and "withheld_for_drift rose" in r["evidence"]["problems"]
        r = await one("data.quality_regression", ctx(load_data_quality=dq_loader(98.1, never=3), previous_numbers=self.PREV))
        assert r["status"] == "fail" and "never_priced rose" in r["evidence"]["problems"]

    async def test_first_run_is_baseline_and_numbers_are_recorded(self):
        c = ctx(load_data_quality=dq_loader(98.1))
        r = await one("data.quality_regression", c)
        assert r["status"] == "n/a" and r["evidence"]["reason"] == "baseline recorded"
        assert c.numbers["confirmed_pct"] == 98.1 and c.numbers["stale_pct"] == 3.0

    async def test_unavailable_source_is_na_not_fail(self):
        async def broken():
            return {"success": False, "error": "x"}
        r = await one("data.quality_regression", ctx(load_data_quality=broken))
        assert r["status"] == "n/a"
        assert (await one("data.quality_regression", ctx()))["status"] == "n/a"

    async def test_stale_prices_threshold(self):
        assert (await one("data.stale_prices", ctx(load_data_quality=dq_loader(stale_pct=10.0))))["status"] == "pass"
        r = await one("data.stale_prices", ctx(load_data_quality=dq_loader(stale_pct=12.5)))
        assert r["status"] == "warn" and r["evidence"]["threshold_pct"] == 10.0


# --------------------------------------------------------------------------- billing (AC6)

class TestBilling:
    async def test_unhashed_row_fails_with_count_one(self, tmp_path):
        path = make_billing(tmp_path / "b.db", [{"id": "c1"}, {"id": "c2", "api_key": "legacy-plain", "hash": None}])
        r = await one("billing.key_hash_migration", ctx(billing_db_path=path))
        assert r["status"] == "fail" and r["evidence"]["unhashed"] == 1 and r["lc"] == ["LC-21"]

    async def test_all_hashed_passes(self, tmp_path):
        path = make_billing(tmp_path / "b.db", [{"id": "c1"}, {"id": "c2"}])
        r = await one("billing.key_hash_migration", ctx(billing_db_path=path))
        assert r["status"] == "pass" and r["evidence"]["unhashed"] == 0

    async def test_the_database_is_never_modified(self, tmp_path):
        path = make_billing(tmp_path / "b.db", [{"id": "c1", "api_key": "legacy", "hash": None}])
        before = Path(path).read_bytes()
        await one("billing.key_hash_migration", ctx(billing_db_path=path))
        await one("billing.deleted_markers", ctx(billing_db_path=path))
        assert Path(path).read_bytes() == before

    async def test_connection_is_read_only(self, tmp_path):
        path = make_billing(tmp_path / "b.db", [{"id": "c1"}])
        db = await ac._open_billing_ro(ctx(billing_db_path=path))
        try:
            with pytest.raises(sqlite3.OperationalError):
                await db.execute("DELETE FROM customers")
        finally:
            await db.close()
        assert "mode=ro" in ac._billing_uri(path)

    async def test_missing_database_is_na(self, tmp_path):
        r = await one("billing.key_hash_migration", ctx(billing_db_path=str(tmp_path / "none.db")))
        assert r["status"] == "n/a"

    async def test_deleted_markers(self, tmp_path):
        good = make_billing(tmp_path / "g.db", deleted=[("a" * 64, 1.0)])
        assert (await one("billing.deleted_markers", ctx(billing_db_path=good)))["status"] == "pass"
        bad = make_billing(tmp_path / "bad.db", deleted=[("", 1.0)])
        r = await one("billing.deleted_markers", ctx(billing_db_path=bad))
        assert r["status"] == "fail" and r["evidence"]["malformed"] == 1
        nosalt = make_billing(tmp_path / "ns.db", deleted=[("a" * 64, 1.0)], salt=False)
        assert (await one("billing.deleted_markers", ctx(billing_db_path=nosalt)))["status"] == "fail"
        fresh = make_billing(tmp_path / "f.db", salt=False)  # no deletions yet: no salt is fine
        assert (await one("billing.deleted_markers", ctx(billing_db_path=fresh)))["status"] == "pass"


# --------------------------------------------------------------------------- stripe (AC4)

_counter = itertools.count()
STRIPE_IDS = ("cus_", "sub_", "@")


def stripe_ctx(tmp_path, customers, subs, webhooks=None, error=None):
    lister, hooks = fake_stripe(subs, webhooks, error)
    return ctx(settings=settings(stripe_audit_key=SecretStr(STRIPE_KEY)),
               billing_db_path=make_billing(tmp_path / f"b{next(_counter)}.db", customers),
               stripe_list_subscriptions=lister, stripe_list_webhook_endpoints=hooks)


class TestStripe:
    CUSTOMERS = [
        {"id": "aaaaaaaa1", "cus": "cus_A1", "sub": "sub_A1", "tier": "pro"},             # consistent
        {"id": "bbbbbbbb2", "cus": "cus_B2", "sub": "sub_B2", "tier": "pro"},             # sub canceled -> mismatch
        {"id": "cccccccc3", "cus": "cus_C3", "sub": "sub_C3", "tier": "free"},            # paying, local free -> mismatch
        {"id": "dddddddd4", "cus": "cus_D4", "sub": "sub_D4", "tier": "pro", "status": "active"},  # stripe past_due
        {"id": "eeeeeeee5", "cus": "cus_E5", "sub": "sub_E5", "tier": "pro", "status": "past_due"},  # local past_due
        {"id": "ffffffff6", "cus": "cus_SHARED", "tier": "free"},
        {"id": "gggggggg7", "cus": "cus_SHARED", "tier": "free"},
    ]
    SUBS = [
        sub("sub_A1", "cus_A1"), sub("sub_B2", "cus_B2", "canceled"), sub("sub_C3", "cus_C3"),
        sub("sub_D4", "cus_D4", "past_due"), sub("sub_E5", "cus_E5"),
        sub("sub_ORPHAN", "cus_NOBODY"), sub("sub_ORPHAN2", "cus_NOBODY2", "trialing"),
        sub("sub_OLD", "cus_OLD", "canceled"),  # canceled with no local customer is not an orphan
    ]

    async def results(self, tmp_path):
        c = stripe_ctx(tmp_path, self.CUSTOMERS, self.SUBS)
        return {cid: await one(cid, c) for cid in (
            "stripe.reconciliation", "stripe.orphan_subscriptions", "stripe.tier_mismatch",
            "stripe.past_due_mismatch", "stripe.shared_ids")}

    async def test_exact_counts(self, tmp_path):
        r = await self.results(tmp_path)
        assert r["stripe.orphan_subscriptions"]["status"] == "fail"
        assert r["stripe.orphan_subscriptions"]["evidence"]["orphan_subscriptions"] == 2
        assert r["stripe.tier_mismatch"]["evidence"]["tier_mismatches"] == 2
        assert sorted(r["stripe.tier_mismatch"]["evidence"]["customers"]) == ["bbbbbbbb", "cccccccc"]
        pd = r["stripe.past_due_mismatch"]
        assert pd["status"] == "fail"
        assert pd["evidence"]["stripe_past_due_local_active"] == 1 and pd["evidence"]["local_past_due_stripe_active"] == 1
        assert r["stripe.shared_ids"]["status"] == "fail" and r["stripe.shared_ids"]["evidence"]["shared_ids"] == 1
        assert r["stripe.reconciliation"]["status"] == "pass"
        assert r["stripe.reconciliation"]["evidence"]["stripe_subscriptions"] == len(self.SUBS)

    async def test_evidence_contains_no_emails_or_stripe_ids(self, tmp_path):
        text = json.dumps(await self.results(tmp_path))
        for token in STRIPE_IDS:
            assert token not in text, token

    async def test_clean_state_passes_everything(self, tmp_path):
        c = stripe_ctx(tmp_path, [{"id": "aaaaaaaa1", "cus": "cus_A1", "sub": "sub_A1", "tier": "pro"},
                                  {"id": "bbbbbbbb2", "tier": "free"}], [sub("sub_A1", "cus_A1")])
        for cid in ("stripe.orphan_subscriptions", "stripe.tier_mismatch", "stripe.past_due_mismatch",
                    "stripe.shared_ids"):
            assert (await one(cid, c))["status"] == "pass", cid

    async def test_price_id_mapping_to_another_tier_is_a_mismatch(self, tmp_path):
        c = stripe_ctx(tmp_path, [{"id": "aaaaaaaa1", "cus": "cus_A1", "sub": "sub_A1", "tier": "enterprise"}],
                       [sub("sub_A1", "cus_A1", price="price_pro_fixture")])
        assert (await one("stripe.tier_mismatch", c))["evidence"]["tier_mismatches"] == 1

    async def test_local_past_due_with_stripe_active_alone_is_a_warn(self, tmp_path):
        c = stripe_ctx(tmp_path, [{"id": "eeeeeeee5", "cus": "cus_E5", "sub": "sub_E5", "tier": "pro",
                                   "status": "past_due"}], [sub("sub_E5", "cus_E5")])
        assert (await one("stripe.past_due_mismatch", c))["status"] == "warn"

    async def test_unset_key_makes_every_stripe_check_na_not_configured(self, tmp_path):
        c = ctx(billing_db_path=make_billing(tmp_path / "b.db", self.CUSTOMERS))
        for cid in [s.id for s in ac.CHECK_SPECS if s.id.startswith("stripe.")]:
            r = await one(cid, c)
            assert r["status"] == "n/a" and r["evidence"]["reason"] == "not configured", cid

    async def test_permission_error_is_na_key_lacks_permission(self, tmp_path):
        c = stripe_ctx(tmp_path, self.CUSTOMERS, self.SUBS, error=AuthenticationError("restricted"))
        for cid in ("stripe.reconciliation", "stripe.orphan_subscriptions", "stripe.tier_mismatch",
                    "stripe.past_due_mismatch", "stripe.shared_ids", "stripe.webhook_endpoint"):
            r = await one(cid, c)
            assert r["status"] == "n/a" and r["evidence"]["reason"] == "key lacks permission", cid

    async def test_other_stripe_errors_are_a_single_error_row_each(self, tmp_path):
        calls = []

        async def flaky(key):
            calls.append(1)
            raise ConnectionError("down")
        c = stripe_ctx(tmp_path, self.CUSTOMERS, [])
        c.stripe_list_subscriptions = flaky
        r1 = await one("stripe.orphan_subscriptions", c)
        r2 = await one("stripe.shared_ids", c)
        assert r1["evidence"]["reason"] == r2["evidence"]["reason"] == "error:ConnectionError"
        assert len(calls) == 1  # Stripe is asked once per audit

    async def test_webhook_endpoint_seven_events(self, tmp_path):
        ok = [{"status": "enabled", "enabled_events": list(ac.WEBHOOK_EVENTS)}]
        r = await one("stripe.webhook_endpoint", stripe_ctx(tmp_path, [], [], webhooks=ok))
        assert r["status"] == "pass" and r["lc"] == ["LC-06"]
        star = [{"status": "enabled", "enabled_events": ["*"]}]
        assert (await one("stripe.webhook_endpoint", stripe_ctx(tmp_path, [], [], webhooks=star)))["status"] == "pass"
        short = [{"status": "enabled", "enabled_events": list(ac.WEBHOOK_EVENTS[:5])}]
        r = await one("stripe.webhook_endpoint", stripe_ctx(tmp_path, [], [], webhooks=short))
        assert r["status"] == "fail" and r["evidence"]["missing_events"] == list(ac.WEBHOOK_EVENTS[5:])
        disabled = [{"status": "disabled", "enabled_events": list(ac.WEBHOOK_EVENTS)}]
        assert (await one("stripe.webhook_endpoint", stripe_ctx(tmp_path, [], [], webhooks=disabled)))["status"] == "fail"
        assert (await one("stripe.webhook_endpoint", stripe_ctx(tmp_path, [], [], webhooks=[])))["status"] == "fail"

    async def test_adapter_uses_a_per_call_key_and_never_sets_the_global_one(self, monkeypatch):
        import stripe
        from src.services import audit_service as svc
        seen = {}

        class Page:
            def auto_paging_iter(self):
                yield {"id": "sub_1", "customer": {"id": "cus_1"}, "status": "active",
                       "items": {"data": [{"price": {"id": "price_x"}}]}}

        def fake_list(**kw):
            seen.update(kw)
            return Page()
        monkeypatch.setattr(stripe.Subscription, "list", staticmethod(fake_list))
        before = stripe.api_key
        out = await svc.stripe_list_subscriptions(STRIPE_KEY)
        assert seen["api_key"] == STRIPE_KEY and stripe.api_key == before
        assert out == [{"id": "sub_1", "customer": "cus_1", "status": "active", "price_id": "price_x"}]


# --------------------------------------------------------------------------- orchestrator-backed

class TestOrchestratorBacked:
    async def test_na_without_orchestrator(self):
        for cid in ("data.weekly_audit_ran", "orch.budget_vs_spend", "orch.open_escalations_age"):
            assert (await one(cid, ctx()))["status"] == "n/a", cid


# --------------------------------------------------------------------------- repo-side, disabled (AC9)

class TestRepoSideAndDisabled:
    REPO_IDS = [s.id for s in ac.CHECK_SPECS if s.id.startswith("repo.")]

    async def test_absent_repo_checks_are_na(self):
        for cid in self.REPO_IDS:
            r = await one(cid, ctx())
            assert r["status"] == "n/a" and r["evidence"]["reason"].startswith("repo-side check not supplied"), cid

    async def test_supplied_results(self):
        c = ctx(repo_checks={"repo.dependabot": "pass", "repo.codeql": "fail", "repo.forbidden_tracked_files": "fail",
                             "repo.version_tests": "weird"})
        assert (await one("repo.dependabot", c))["status"] == "pass"
        assert (await one("repo.codeql", c))["status"] == "fail"
        forbidden = await one("repo.forbidden_tracked_files", c)
        assert forbidden["status"] == "fail" and forbidden["severity"] == "critical"
        assert (await one("repo.version_tests", c))["status"] == "n/a"
        assert (await one("repo.replication_check", c))["status"] == "n/a"  # not in the dict

    async def test_disabled_checks_are_na_disabled_and_listed_in_coverage(self):
        c = ctx(disabled=frozenset({"secrets.presence", "data.stale_prices"}))
        results = by_id(await ac.run_all(c))
        for cid in ("secrets.presence", "data.stale_prices"):
            assert results[cid]["status"] == "n/a" and results[cid]["evidence"]["reason"] == "disabled"
        cov = results["audit.coverage"]["evidence"]
        assert cov["disabled"] == ["data.stale_prices", "secrets.presence"]
        assert cov["na_reasons"]["secrets.presence"] == "disabled"
        assert cov["repo_checks_supplied"] is False and cov["stripe_configured"] is False
        assert sum(cov["counts"].values()) == cov["checks_run"] == len(ac.CHECK_IDS) - 1


# --------------------------------------------------------------------------- launch checklist (AC8)

CHECKLIST_HEAD = "| ID | Item | Status | Owner | Verification | Completed |\n|---|---|---|---|---|---|\n"


def checklist(tmp_path, statuses):
    """The real checklist with the given {LC id: status} overrides (a done row gets a date)."""
    lines = []
    for line in (project_root / "docs" / "LAUNCH_CHECKLIST.md").read_text(encoding="utf-8").splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if line.startswith("| LC-") and cells[0] in statuses:
            cells[2] = statuses[cells[0]]
            cells[5] = "2026-11-01" if statuses[cells[0]] == "done" else "-"
            line = "| " + " | ".join(cells) + " |"
        lines.append(line)
    path = tmp_path / "LAUNCH_CHECKLIST.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class TestChecklistConsistency:
    async def _run(self, tmp_path, statuses, results):
        spec = next(s for s in ac.CHECK_SPECS if s.id == "launch.checklist_consistency")
        return await ac._run_one(spec, ctx(checklist_path=checklist(tmp_path, statuses)), results)

    def row(self, cid, status):
        return {"id": cid, "status": status, "evidence": {}}

    async def test_done_row_with_failing_mapped_check_fails_naming_the_lc_id(self, tmp_path):
        r = await self._run(tmp_path, {"LC-12": "done"}, [self.row("legal.placeholders", "fail")])
        assert r["status"] == "fail"
        assert {"lc": "LC-12", "checks": ["legal.placeholders"]} in r["evidence"]["claimed_done_not_verified"]

    async def test_done_row_contradicted_by_a_warn_for_expected_before_launch_checks(self, tmp_path):
        r = await self._run(tmp_path, {"LC-07": "done"}, [self.row("secrets.presence", "warn")])
        assert r["status"] == "fail"
        r = await self._run(tmp_path, {"LC-18": "done"}, [self.row("repo.dependabot", "n/a")])
        assert r["status"] == "pass"  # not verified in this run is not a contradiction

    async def test_done_row_with_passing_check_is_fine_and_todo_ready_is_listed(self, tmp_path):
        r = await self._run(tmp_path, {"LC-12": "done"}, [self.row("legal.placeholders", "pass")])
        assert r["status"] == "pass"
        r = await self._run(tmp_path, {}, [self.row("legal.placeholders", "pass")])
        assert "LC-12" in r["evidence"]["ready_to_mark_done"]

    async def test_structural_problems_fail(self, tmp_path):
        path = tmp_path / "bad.md"
        path.write_text(CHECKLIST_HEAD + "| LC-01 | Entity formed | maybe | owner | proof | - |\n", encoding="utf-8")
        spec = next(s for s in ac.CHECK_SPECS if s.id == "launch.checklist_consistency")
        r = await ac._run_one(spec, ctx(checklist_path=path), [])
        assert r["status"] == "fail" and r["evidence"]["structural_problem_count"] > 0

    async def test_missing_file_is_na_and_real_file_passes(self, tmp_path):
        spec = next(s for s in ac.CHECK_SPECS if s.id == "launch.checklist_consistency")
        assert (await ac._run_one(spec, ctx(checklist_path=tmp_path / "x.md"), []))["status"] == "n/a"
        assert (await ac._run_one(spec, ctx(), []))["status"] == "pass"


def test_overall_status_and_counts():
    rows = [{"status": "pass"}, {"status": "n/a"}]
    assert ac.overall_status(rows) == "pass"
    assert ac.overall_status(rows + [{"status": "warn"}]) == "warn"
    assert ac.overall_status(rows + [{"status": "warn"}, {"status": "fail"}]) == "fail"
    assert ac.count_statuses(rows) == {"pass": 1, "warn": 0, "fail": 0, "n_a": 1}
