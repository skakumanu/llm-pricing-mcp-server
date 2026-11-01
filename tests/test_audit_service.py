"""Compliance audit through the orchestrator engine (v1.88.0): runs, checkpoint, summary, store, safety.

AC1/AC2/AC3 (run outcomes and the owner checkpoint), AC10 (LLM summary spend), AC12 (append-only
store, retention, no write methods referenced), AC13 (per-day dedup).
"""
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import aiosqlite  # noqa: E402

from src.services import audit_checks as ac  # noqa: E402
from src.services.audit_service import AuditService  # noqa: E402
from src.services.audit_store import AuditStore  # noqa: E402
from src.services.orchestrator_service import OrchestratorService  # noqa: E402
from src.services.orchestrator_store import OrchestratorStore  # noqa: E402

from tests.test_audit_checks import ALL_SECRETS, make_billing, settings  # noqa: E402

DAY = 86400
START = 1_800_000_000.0


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t


def dq(confirmed=98.0, withheld=1, never=2):
    async def load():
        return {"success": True, "confirmed_pct": confirmed, "withheld_for_drift": withheld, "never_priced": never,
                "stale_models": 1, "stale_pct": 1.0, "total_models": 100}
    return load


class FakeBackend:
    def __init__(self, text="Fine week. Nothing failed.", usage=None, error=None):
        self.text, self.usage, self.error, self.calls = text, usage or {"input_tokens": 400, "output_tokens": 100}, error, []

    def factory(self, provider, key, model):
        self.calls.append((provider, model))
        return self

    async def complete(self, messages, tools, system=None):
        if self.error:
            raise self.error
        self.last = (messages, tools, system)
        return SimpleNamespace(content=self.text, usage=self.usage)


async def price(model, tokens_in, tokens_out, settings_):
    return 0.001


async def build(tmp_path, clock=None, backend=None, **overrides):
    clock = clock or Clock()
    legal = tmp_path / "legal"
    legal.mkdir(exist_ok=True)
    (legal / "terms.md").write_text("Clean.", encoding="utf-8")
    import src
    s = settings(app_version=src.__version__, billing_db_path=make_billing(tmp_path / "billing.db", [{"id": "c1"}]),
                 anthropic_api_key="k-fixture", agent_llm_provider="anthropic", audit_disabled_checks="")
    for k, v in overrides.pop("settings", {}).items():
        setattr(s, k, v)
    ostore = OrchestratorStore(str(tmp_path / "orch.db"))
    await ostore.initialize()
    orch = OrchestratorService(ostore, clock=clock)
    astore = AuditStore(str(tmp_path / "orch.db"), retention_days=365)
    await astore.initialize()
    audit = AuditService(astore, orch, s, clock, stripe_subscriptions=None, stripe_webhooks=None,
                         data_quality=overrides.pop("data_quality", dq()), legal_docs_dir=legal,
                         litestream_config_path=tmp_path / "none.yml", env=overrides.pop("env", ALL_SECRETS),
                         llm_factory=backend.factory if backend else None, price_call=price)
    audit.install()
    return SimpleNamespace(audit=audit, orch=orch, store=astore, ostore=ostore, clock=clock, settings=s)


def schedule_event(day_key="2026-11-02", repo_checks=None, repo_version=None):
    data = {}
    if repo_checks is not None:
        data["repo_checks"] = repo_checks
    if repo_version:
        data["repo_version"] = repo_version
    return {"trigger_class": "schedule", "event_type": "compliance-audit.requested",
            "dedup_key": f"schedule:compliance-audit:{day_key}", "actor": "schedule", "actor_is_owner": False,
            "data": data}


async def steps(env, run_id):
    return {s["name"]: s for s in await env.ostore.get_steps(run_id)}


# --------------------------------------------------------------------------- AC1 / AC3

async def test_all_pass_run_is_done_with_one_report_and_no_approval(tmp_path):
    env = await build(tmp_path)
    res = await env.orch.intake_event(schedule_event())
    assert res["outcome"] == "created_run" and res["workflow_id"] == "compliance_audit"
    assert res["status"] == "done"
    assert await env.ostore.list_approvals(status=None) == []
    reports = await env.store.list_reports()
    assert len(reports) == 1 and reports[0]["status"] == "pass"
    full = await env.store.get_report(reports[0]["report_id"])
    assert [c["id"] for c in full["checks"]] == list(ac.CHECK_IDS)
    assert full["run_id"] == res["run_id"] and full["trigger_class"] == "schedule"
    assert full["summary_source"] == "deterministic" and "PASS" in full["summary_text"]
    assert full["numbers"]["confirmed_pct"] == 98.0
    run = await env.ostore.get_run(res["run_id"])
    assert run["summary_json"]["report_id"] == full["report_id"]
    st = await steps(env, res["run_id"])
    assert st["review_report"]["io_summary_json"]["output"]["decision"] == "no failing checks; nothing to review"


async def test_warn_only_run_finishes_without_approval(tmp_path):
    env = await build(tmp_path, env={n: "x" for n in ac.SECRET_NAMES if n != "STRIPE_AUDIT_KEY"})
    res = await env.orch.intake_event(schedule_event())
    assert res["status"] == "done"
    report = (await env.store.list_reports())[0]
    assert report["status"] == "warn" and report["counts"]["warn"] >= 1 and report["counts"]["fail"] == 0
    assert await env.ostore.list_approvals(status=None) == []


# --------------------------------------------------------------------------- AC2

def failing_settings():
    return {"settings": {"legal_pages_live": True}}


def placeholder(tmp_path, text="Operated by [ENTITY NAME]."):
    (tmp_path / "legal" / "terms.md").write_text(text, encoding="utf-8")


async def test_failing_check_persists_report_and_waits_on_an_audit_flag_approval(tmp_path):
    env = await build(tmp_path, **failing_settings())
    placeholder(tmp_path)
    res = await env.orch.intake_event(schedule_event())
    assert res["status"] == "waiting_owner"
    report = (await env.store.list_reports())[0]
    assert report["status"] == "fail"
    approvals = await env.ostore.list_approvals(status="waiting")
    assert len(approvals) == 1 and approvals[0]["kind"] == "audit_flag"
    art = approvals[0]["artifact_summary_json"]
    assert art["report_id"] == report["report_id"]
    assert "legal.placeholders" in art["failed_outcome"] and "legal.live_gate" in art["failed_outcome"]
    assert art["failed_step"] == "run_checks" and art["blocked_step"] == "review_report"
    st = await steps(env, res["run_id"])
    assert st["run_checks"]["status"] == "failed" and st["run_checks"]["outcome"].startswith("gate_failed:")
    assert st["executive_summary"]["status"] == "done"  # the summary still ran
    assert (await env.store.get_report(report["report_id"]))["summary_text"].startswith("Compliance audit")


async def test_approving_completes_the_run(tmp_path):
    env = await build(tmp_path, **failing_settings())
    placeholder(tmp_path, "[STATE]")
    res = await env.orch.intake_event(schedule_event())
    approval = (await env.ostore.list_approvals(status="waiting"))[0]
    await env.orch.decide_approval(approval["approval_id"], "approve")
    run = await env.ostore.get_run(res["run_id"])
    assert run["status"] == "done" and run["result"] == "done"
    st = await steps(env, res["run_id"])
    assert st["review_report"]["status"] == "done"
    assert st["review_report"]["io_summary_json"]["output"]["decision"] == "report reviewed by owner"
    assert len(await env.store.list_reports()) == 1  # approving never re-runs the audit


async def test_rejecting_closes_the_run(tmp_path):
    env = await build(tmp_path, **failing_settings())
    placeholder(tmp_path, "[STATE]")
    res = await env.orch.intake_event(schedule_event())
    approval = (await env.ostore.list_approvals(status="waiting"))[0]
    await env.orch.decide_approval(approval["approval_id"], "reject", note="known, tracked in LC-12")
    run = await env.ostore.get_run(res["run_id"])
    assert run["status"] == "closed" and run["result"] == "deliberately_not_done"


async def test_fail_without_the_engine_change_would_not_open_a_second_approval_on_replay(tmp_path):
    env = await build(tmp_path, **failing_settings())
    placeholder(tmp_path, "[STATE]")
    res = await env.orch.intake_event(schedule_event())
    await env.orch.advance(res["run_id"])
    assert len(await env.ostore.list_approvals(status=None)) == 1


# --------------------------------------------------------------------------- AC13

async def test_two_schedule_events_on_one_day_attach_to_one_run(tmp_path):
    env = await build(tmp_path)
    first = await env.orch.intake_event(schedule_event("2026-11-02"))
    second = await env.orch.intake_event(schedule_event("2026-11-02"))
    assert second["duplicate"] is True and second["run_id"] == first["run_id"]
    assert len(await env.store.list_reports()) == 1
    env.clock.t += DAY
    third = await env.orch.intake_event(schedule_event("2026-11-03"))
    assert third["run_id"] != first["run_id"] and len(await env.store.list_reports()) == 2


async def test_owner_request_uses_its_own_id_and_may_run_the_same_day(tmp_path):
    env = await build(tmp_path)
    await env.orch.intake_event(schedule_event())
    a = await env.audit.run_now("audit-first-click")
    b = await env.audit.run_now("audit-second-click")
    again = await env.audit.run_now("audit-first-click")
    assert a["run_id"] != b["run_id"] and again["run_id"] == a["run_id"] and again["duplicate"] is True
    assert a["report_id"] and a["report_status"] == "pass" and b["report_id"] != a["report_id"]
    assert len(await env.store.list_reports()) == 3


async def test_repo_checks_from_the_workflow_are_used_and_bounded(tmp_path):
    env = await build(tmp_path)
    ev = schedule_event(repo_checks={"repo.dependabot": "pass", "repo.codeql": "fail",
                                     **{f"x{i}": "pass" for i in range(80)}}, repo_version="9.9.9-extra-long-" * 5)
    res = await env.orch.intake_event(ev)
    report = await env.store.get_report((await env.store.list_reports())[0]["report_id"])
    rows = {c["id"]: c for c in report["checks"]}
    assert rows["repo.dependabot"]["status"] == "pass" and rows["repo.codeql"]["status"] == "fail"
    assert report["config"]["repo_checks_supplied"] is True
    assert res["status"] == "waiting_owner"
    inputs = (await env.ostore.get_run(res["run_id"]))["summary_json"]["inputs"]
    assert len(inputs["repo_checks"]) == 30 and len(inputs["repo_version"]) <= 20


async def test_regression_between_two_reports_fails_and_baseline_is_first(tmp_path):
    env = await build(tmp_path, data_quality=dq(98.1))
    await env.orch.intake_event(schedule_event("2026-11-02"))
    first = await env.store.latest_report()
    assert {c["id"]: c for c in first["checks"]}["data.quality_regression"]["evidence"]["reason"] == "baseline recorded"
    env.clock.t += DAY
    env.audit._data_quality = dq(97.9)
    res = await env.orch.intake_event(schedule_event("2026-11-03"))
    latest = await env.store.latest_report()
    assert {c["id"]: c for c in latest["checks"]}["data.quality_regression"]["status"] == "fail"
    assert latest["numbers"]["confirmed_pct"] == 97.9 and res["status"] == "waiting_owner"
    assert await env.store.previous_numbers(before_ts=env.clock.t + 1) == latest["numbers"]


# --------------------------------------------------------------------------- AC10

async def spend_rows(env):
    async with aiosqlite.connect(env.ostore.db_path) as db:
        async with db.execute("SELECT workflow_id, model, tokens_in, tokens_out, cost_usd FROM orch_spend") as cur:
            return await cur.fetchall()


async def test_llm_off_is_deterministic_and_writes_no_spend(tmp_path):
    backend = FakeBackend()
    env = await build(tmp_path, backend=backend)
    await env.orch.intake_event(schedule_event())
    assert (await env.store.latest_report())["summary_source"] == "deterministic"
    assert await spend_rows(env) == [] and backend.calls == []


async def test_llm_on_records_exactly_one_spend_row_under_compliance_audit(tmp_path):
    backend = FakeBackend()
    env = await build(tmp_path, backend=backend, settings={"audit_llm_summary": True})
    res = await env.orch.intake_event(schedule_event())
    report = await env.store.latest_report()
    assert report["summary_source"] == "llm" and report["summary_text"] == "Fine week. Nothing failed."
    assert await spend_rows(env) == [("compliance_audit", "claude-haiku-5-5", 400, 100, 0.001)]
    assert backend.calls == [("anthropic", "claude-haiku-5-5")]
    messages, tools, system = backend.last
    assert tools == [] and "legal advice" in system and len(messages) == 1
    assert "legal.placeholders | pass" in messages[0]["content"]  # the redacted check table, nothing else
    assert (await env.ostore.get_run(res["run_id"]))["cost_usd"] == pytest.approx(0.001)


async def test_budget_refusal_keeps_the_template_and_makes_no_call(tmp_path):
    backend = FakeBackend()
    env = await build(tmp_path, backend=backend, settings={"audit_llm_summary": True})
    await env.ostore.set_setting("budget.workflow_monthly_usd.compliance_audit", 0.0001)
    await env.ostore.record_spend({"run_id": "orc_seed00000001", "step_id": None, "workflow_id": "compliance_audit",
                                   "agent": "audit_agent", "model": "m", "at": env.clock.t, "period": "2027-01",
                                   "tokens_in": 1, "tokens_out": 1, "cost_usd": 1.0})
    res = await env.orch.intake_event(schedule_event())
    report = await env.store.latest_report()
    assert res["status"] == "done"  # report-only run is never stuck on the budget
    assert report["summary_source"] == "llm_skipped:budget:workflow_monthly_cap"
    assert report["summary_text"].startswith("Compliance audit") and backend.calls == []
    assert len(await spend_rows(env)) == 1  # only the seeded row


async def test_backend_error_and_missing_key_keep_the_template(tmp_path):
    env = await build(tmp_path, backend=FakeBackend(error=ConnectionError("down")),
                      settings={"audit_llm_summary": True})
    await env.orch.intake_event(schedule_event())
    assert (await env.store.latest_report())["summary_source"] == "llm_skipped:error:ConnectionError"
    assert await spend_rows(env) == []
    second = tmp_path / "second"
    second.mkdir()
    env2 = await build(second, backend=FakeBackend(), settings={"audit_llm_summary": True, "anthropic_api_key": None})
    await env2.orch.intake_event(schedule_event())
    assert (await env2.store.latest_report())["summary_source"] == "llm_skipped:no_api_key"


async def test_default_model_is_haiku_5_5():
    from src.config.settings import Settings
    f = Settings.model_fields
    assert f["audit_summary_model"].default == "claude-haiku-5-5"
    assert f["audit_llm_summary"].default is False and f["stripe_audit_key"].default is None
    assert f["audit_report_retention_days"].default == 365 and f["audit_escalation_max_age_days"].default == 7
    assert f["audit_stale_price_pct_warn"].default == 10.0 and f["audit_disabled_checks"].default == ""


# --------------------------------------------------------------------------- orchestrator-backed checks

async def test_weekly_audit_and_escalation_checks_read_the_orchestrator(tmp_path):
    env = await build(tmp_path)
    await env.orch.intake_event({"trigger_class": "schedule", "event_type": "price-audit.completed",
                                 "dedup_key": "schedule:price-audit:2026-W44", "actor": "schedule",
                                 "data": {"jobs": {"audit": "failure"}}})
    await env.orch.intake_event(schedule_event())
    report = await env.store.latest_report()
    rows = {c["id"]: c for c in report["checks"]}
    assert rows["data.weekly_audit_ran"]["status"] == "fail"  # the parked weekly audit
    assert rows["orch.open_escalations_age"]["status"] == "pass"
    env.clock.t += 10 * DAY
    await env.orch.intake_event({"trigger_class": "schedule", "event_type": "price-audit.completed",
                                 "dedup_key": "schedule:price-audit:2026-W45", "actor": "schedule",
                                 "data": {"jobs": {"audit": "success"}}})
    env.clock.t += 9 * DAY
    await env.orch.intake_event(schedule_event("2026-11-20"))
    rows = {c["id"]: c for c in (await env.store.latest_report())["checks"]}
    assert rows["data.weekly_audit_ran"]["status"] == "warn"  # ran, but 9 days ago


# --------------------------------------------------------------------------- AC12

async def test_retention_purge_removes_old_reports_and_orphan_check_rows(tmp_path):
    env = await build(tmp_path)
    await env.orch.intake_event(schedule_event("2026-11-02"))
    old = (await env.store.list_reports())[0]
    env.clock.t += 400 * DAY
    await env.orch.intake_event(schedule_event("2027-12-07"))
    await env.store.purge_expired(now=env.clock.t)  # idempotent: the second run already purged opportunistically
    reports = await env.store.list_reports()
    assert all(r["report_id"] != old["report_id"] for r in reports) and len(reports) == 1
    async with aiosqlite.connect(env.ostore.db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM audit_checks WHERE report_id = ?", (old["report_id"],)) as cur:
            assert (await cur.fetchone())[0] == 0
        async with db.execute("SELECT COUNT(*) FROM audit_checks") as cur:
            assert (await cur.fetchone())[0] == len(ac.CHECK_IDS)


async def test_orphan_check_rows_without_a_report_are_purged(tmp_path):
    env = await build(tmp_path)
    await env.store.save_report({"run_id": "orc_x", "status": "pass", "created_at": env.clock.t}, [])
    async with aiosqlite.connect(env.ostore.db_path) as db:
        await db.execute("INSERT INTO audit_checks (row_id, report_id, seq, check_id, status) "
                         "VALUES ('chk_1', 'aud_gone', 1, 'x', 'pass')")
        await db.commit()
    assert (await env.store.purge_expired(now=env.clock.t))["audit_checks"] == 1


async def test_set_summary_is_the_only_update_and_is_redacted(tmp_path):
    env = await build(tmp_path)
    await env.orch.intake_event(schedule_event())
    rid = (await env.store.list_reports())[0]["report_id"]
    await env.store.set_summary(rid, "contact someone" + "@" + "example.org please", "llm")
    r = await env.store.get_report(rid)
    assert "@" not in r["summary_text"] and r["summary_source"] == "llm"


def test_store_exposes_no_update_or_delete_beyond_set_summary_and_purge():
    src = (project_root / "src" / "services" / "audit_store.py").read_text(encoding="utf-8")
    updates = re.findall(r"\bUPDATE\s+(audit_\w+)", src)
    deletes = re.findall(r"\bDELETE\s+FROM\s+(audit_\w+)", src)
    assert updates == ["audit_reports"]  # set_summary only
    assert sorted(deletes) == ["audit_checks", "audit_reports"]  # purge_expired only
    for m in re.finditer(r"async def (\w+)", src):
        if m.group(1) not in {"initialize", "save_report", "set_summary", "purge_expired", "list_reports", "get_report",
                              "latest_report", "get_report_by_run", "previous_numbers", "first_seen",
                              "init_audit_store"}:
            pytest.fail(f"unexpected store method {m.group(1)}")


def test_audit_modules_reference_no_write_method_of_billing_stripe_or_orchestrator_settings():
    forbidden = [r"update_tier", r"set_billing_status", r"delete_customer", r"update_settings",
                 r"stripe\.\w+\.(create|modify|delete|update)\b", r"\.(modify|delete)\(", r"\bapi_key\s*=\s*['\"]",
                 r"stripe\.api_key\s*="]
    for name in ("audit_checks.py", "audit_service.py", "audit_store.py"):
        text = (project_root / "src" / "services" / name).read_text(encoding="utf-8")
        for pat in forbidden:
            assert not re.search(pat, text), f"{name} matches {pat}"
    checks_src = (project_root / "src" / "services" / "audit_checks.py").read_text(encoding="utf-8")
    assert "mode=ro" in checks_src and "query_only" in checks_src
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b", checks_src.replace("CREATE TABLE", ""))


async def test_orchestrator_core_does_not_import_audit_code():
    text = (project_root / "src" / "services" / "orchestrator_service.py").read_text(encoding="utf-8")
    assert "audit_service" not in text and "audit_checks" not in text and "audit_store" not in text


async def test_evidence_is_redacted_before_storage(tmp_path):
    env = await build(tmp_path)
    leaked = "cus_" + "Z" * 14
    rid = await env.store.save_report(
        {"run_id": "orc_y", "status": "pass", "created_at": env.clock.t},
        [{"id": "x.y", "status": "pass", "severity": "info", "title": "t",
          "evidence": {"note": f"mail a{'@'}b.org {leaked}", "nested": [leaked]}, "lc": []}])
    ev = (await env.store.get_report(rid))["checks"][0]["evidence"]
    assert "@" not in str(ev) and "cus_" not in str(ev)


async def test_engine_gate_failure_without_override_is_a_waiting_run_not_failed(tmp_path):
    """The gate-failed step alone no longer turns an overridden run into 'failed' (v1.88.0)."""
    env = await build(tmp_path, **failing_settings())
    placeholder(tmp_path)
    res = await env.orch.intake_event(schedule_event())
    assert (await env.ostore.get_run(res["run_id"]))["status"] == "waiting_owner"
    # a hard failure (a step that failed for any other reason) still fails the run
    sql = sqlite3.connect(env.ostore.db_path)
    sql.execute("UPDATE orch_steps SET outcome = 'rejected by owner' WHERE name = 'run_checks'")
    sql.commit()
    sql.close()
    approval = (await env.ostore.list_approvals(status="waiting"))[0]
    await env.orch.decide_approval(approval["approval_id"], "approve")
    assert (await env.ostore.get_run(res["run_id"]))["status"] == "failed"
