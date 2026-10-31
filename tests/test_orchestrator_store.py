"""Orchestrator store (v1.83.0): schema, dedup under concurrency, retention, append-only actions, spend.

Keys and ids are built at runtime so no secret-shaped literal lands in a commit.
"""
import asyncio
import inspect
import re
import sqlite3
import sys
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import orchestrator_store as store_mod  # noqa: E402
from src.services.orchestrator_store import (  # noqa: E402
    ACTION_COLUMNS, RUN_COLUMNS, OrchestratorStore, new_run_id,
)
from src.services.orchestrator_workflows import RUN_ID_RE  # noqa: E402

TABLES = {"orch_runs", "orch_steps", "orch_events", "orch_approvals", "orch_actions", "orch_spend", "orch_settings"}
DAY = 86400


def _run(coro):
    return asyncio.run(coro)


def _run_row(run_id, dedup_key, created_at=None, **extra):
    now = time.time() if created_at is None else created_at
    row = {
        "run_id": run_id, "workflow_id": "github_accepted_bug", "trigger_class": "github_event",
        "dedup_key": dedup_key, "mode": "supervised", "status": "active", "result": None,
        "public_summary": "x", "requester": "github:owner", "source_ref": None, "instruction_like": False,
        "created_at": now, "updated_at": now, "finished_at": None, "step_count": 0, "max_steps": 20,
        "deadline_at": now + DAY, "cost_usd": 0.0, "parked_reason": None,
        "provenance_json": {"requester": "github:owner"}, "summary_json": {"inputs": {"issue_number": 1}},
        "owner_note": None,
    }
    row.update(extra)
    return row


@pytest.fixture
def store(tmp_path):
    s = OrchestratorStore(str(tmp_path / "orch.db"), run_retention_days=365, io_retention_days=90)
    _run(s.initialize())
    return s


class TestSchema:
    def test_seven_tables_exist(self, store):
        con = sqlite3.connect(store.db_path)
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert TABLES <= names

    def test_dedup_key_is_unique_not_null(self, store):
        con = sqlite3.connect(store.db_path)
        ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='orch_runs'").fetchone()[0]
        con.close()
        assert re.search(r"dedup_key TEXT NOT NULL UNIQUE", ddl)

    def test_run_id_format(self):
        ids = {new_run_id() for _ in range(200)}
        assert len(ids) == 200
        for rid in ids:
            assert RUN_ID_RE.match(rid), rid

    def test_initialize_is_idempotent(self, store):
        _run(store.initialize())
        _run(store.initialize())


class TestDedup:
    def test_concurrent_inserts_yield_one_run(self, store):
        key = "gh:issue:77:accepted"

        async def go():
            rows = [_run_row(new_run_id(), key) for _ in range(12)]
            return await asyncio.gather(*(store.create_run_or_attach(r) for r in rows))

        results = _run(go())
        created = [r for r, c in results if c]
        assert len(created) == 1
        assert len({r["run_id"] for r, _ in results}) == 1
        assert len(_run(store.list_runs())) == 1

    def test_duplicate_attach_returns_existing_row(self, store):
        first, created = _run(store.create_run_or_attach(_run_row("orc_aaaaaaaaaaaa", "gh:issue:1:accepted")))
        second, created2 = _run(store.create_run_or_attach(_run_row("orc_bbbbbbbbbbbb", "gh:issue:1:accepted")))
        assert created and not created2
        assert second["run_id"] == "orc_aaaaaaaaaaaa"
        assert _run(store.get_run("orc_bbbbbbbbbbbb")) is None

    def test_duplicate_event_row_is_visible(self, store):
        _run(store.create_run_or_attach(_run_row("orc_cccccccccccc", "gh:issue:2:accepted")))
        _run(store.insert_event({"run_id": "orc_cccccccccccc", "dedup_key": "gh:issue:2:accepted",
                                 "trigger_class": "github_event", "event_type": "issues.labeled",
                                 "duplicate": True, "outcome": "attached_duplicate"}))
        events = _run(store.list_events("orc_cccccccccccc"))
        assert len(events) == 1 and events[0]["duplicate"] is True
        assert events[0]["outcome"] == "attached_duplicate"


class TestRetention:
    def test_rows_older_than_run_retention_are_deleted(self, store):
        old = time.time() - 400 * DAY
        _run(store.create_run_or_attach(_run_row("orc_dddddddddddd", "gh:issue:3:accepted", created_at=old)))
        _run(store.insert_steps([{"step_id": "orc_dddddddddddd:1", "run_id": "orc_dddddddddddd", "seq": 1,
                                  "name": "intake", "kind": "deterministic", "agent": "orchestrator",
                                  "status": "done", "attempt": 1, "max_attempts": 3, "started_at": old,
                                  "finished_at": old, "timeout_s": 30, "outcome": "ok", "artifact_hash": None,
                                  "external_ref": None, "io_summary_json": {"output": 1}, "cost_usd": 0.0,
                                  "error_code": None}]))
        _run(store.append_action({"run_id": "orc_dddddddddddd", "at": old, "action": "run.created", "outcome": "x"}))
        _run(store.create_run_or_attach(_run_row("orc_eeeeeeeeeeee", "gh:issue:4:accepted")))
        counts = _run(store.purge_expired())
        assert counts["orch_runs"] == 1 and counts["orch_steps"] == 1 and counts["orch_actions"] == 1
        assert _run(store.get_run("orc_dddddddddddd")) is None
        assert _run(store.get_run("orc_eeeeeeeeeeee")) is not None

    def test_purge_removes_steps_that_never_started_with_their_run(self, store):
        old = time.time() - 400 * DAY
        _run(store.create_run_or_attach(_run_row("orc_oooooooooooo", "gh:issue:30:accepted", created_at=old)))
        base = {"run_id": "orc_oooooooooooo", "kind": "deterministic", "agent": "orchestrator", "max_attempts": 3,
                "timeout_s": 30, "artifact_hash": None, "external_ref": None, "cost_usd": 0.0}
        _run(store.insert_steps([
            {**base, "step_id": "orc_oooooooooooo:1", "seq": 1, "name": "intake", "status": "failed", "attempt": 3,
             "started_at": old, "finished_at": old, "outcome": "boom", "io_summary_json": None, "error_code": "x"},
            # Never started: started_at is NULL, which `< cutoff` never matches.
            {**base, "step_id": "orc_oooooooooooo:2", "seq": 2, "name": "annotate", "status": "pending", "attempt": 0,
             "started_at": None, "finished_at": None, "outcome": None, "io_summary_json": {"gate_override": "apr"},
             "error_code": None},
        ]))
        counts = _run(store.purge_expired())
        assert counts["orch_runs"] == 1 and counts["orch_steps"] == 2
        assert _run(store.get_run("orc_oooooooooooo")) is None
        assert _run(store.get_steps("orc_oooooooooooo")) == []

    def test_io_fields_nulled_after_io_retention_but_row_kept(self, store):
        mid = time.time() - 100 * DAY
        _run(store.create_run_or_attach(_run_row("orc_ffffffffffff", "gh:issue:5:accepted", created_at=mid,
                                                 owner_note="note")))
        _run(store.insert_steps([{"step_id": "orc_ffffffffffff:1", "run_id": "orc_ffffffffffff", "seq": 1,
                                  "name": "intake", "kind": "deterministic", "agent": "orchestrator",
                                  "status": "done", "attempt": 1, "max_attempts": 3, "started_at": mid,
                                  "finished_at": mid, "timeout_s": 30, "outcome": "ok", "artifact_hash": None,
                                  "external_ref": None, "io_summary_json": {"output": {"a": 1}}, "cost_usd": 0.0,
                                  "error_code": None}]))
        _run(store.purge_expired())
        run = _run(store.get_run("orc_ffffffffffff"))
        assert run is not None and run["summary_json"] is None and run["owner_note"] is None
        assert run["provenance_json"] == {"requester": "github:owner"}  # decisions/provenance kept 12 months
        step = _run(store.get_step("orc_ffffffffffff:1"))
        assert step is not None and step["io_summary_json"] is None and step["outcome"] == "ok"

    def test_recent_rows_untouched(self, store):
        _run(store.create_run_or_attach(_run_row("orc_gggggggggggg", "gh:issue:6:accepted")))
        _run(store.purge_expired())
        run = _run(store.get_run("orc_gggggggggggg"))
        assert run["summary_json"] == {"inputs": {"issue_number": 1}}


class TestAppendOnlyActions:
    def test_module_has_no_update_or_delete_for_actions(self):
        src = inspect.getsource(store_mod)
        assert "UPDATE orch_actions" not in src
        assert "DELETE FROM orch_actions" not in src
        for name in dir(OrchestratorStore):
            assert not re.match(r"(update|delete|remove)_action", name), name

    def test_only_purge_touches_actions_destructively(self):
        src = inspect.getsource(store_mod)
        # The one generic DELETE is the retention purge over _TIME_COLUMN.
        assert src.count("DELETE FROM {table}") == 1

    def test_append_and_list_and_count(self, store):
        for i in range(3):
            _run(store.append_action({"run_id": "orc_hhhhhhhhhhhh", "action": "step.done", "outcome": f"o{i}",
                                      "at": 1000 + i}))
        _run(store.append_action({"run_id": "orc_hhhhhhhhhhhh", "action": "run.parked", "outcome": "x", "at": 1003}))
        rows = _run(store.list_actions(run_id="orc_hhhhhhhhhhhh"))
        assert [r["outcome"] for r in rows[:3]] == ["o0", "o1", "o2"]
        assert _run(store.count_actions("orc_hhhhhhhhhhhh", "step.done")) == 3
        assert _run(store.count_actions(None, "run.parked")) == 1
        assert set(ACTION_COLUMNS) <= set(rows[0])


class TestSpendAndSettings:
    def test_spend_sums_per_period_workflow_and_run(self, store):
        for wf, run, cost, at in (("a", "r1", 1.0, 0), ("a", "r2", 2.0, 0), ("b", "r3", 4.0, 0)):
            _run(store.record_spend({"run_id": run, "workflow_id": wf, "cost_usd": cost, "at": time.time() + at}))
        period = time.strftime("%Y-%m", time.gmtime())
        assert _run(store.spend_total(period=period)) == pytest.approx(7.0)
        assert _run(store.spend_total(period=period, workflow_id="a")) == pytest.approx(3.0)
        assert _run(store.spend_total(run_id="r3")) == pytest.approx(4.0)
        assert _run(store.spend_total(period="1999-01")) == 0.0
        assert _run(store.spend_by_workflow(period)) == {"a": pytest.approx(3.0), "b": pytest.approx(4.0)}

    def test_settings_upsert_and_default(self, store):
        assert _run(store.get_setting("global_pause", False)) is False
        _run(store.set_setting("global_pause", True))
        _run(store.set_setting("global_pause", False))
        assert _run(store.get_setting("global_pause")) is False
        _run(store.set_setting("budget.global_monthly_usd", 150))
        assert _run(store.all_settings()) == {"global_pause": False, "budget.global_monthly_usd": 150}


class TestApprovalsAndExport:
    def test_find_approval_binds_run_step_hash_recipient(self, store):
        base = {"run_id": "orc_iiiiiiiiiiii", "step_id": "orc_iiiiiiiiiiii:4", "kind": "outbound",
                "artifact_hash": "h1", "recipient": "issue:#1"}
        aid = _run(store.insert_approval(base))
        found = _run(store.find_approval("orc_iiiiiiiiiiii", "orc_iiiiiiiiiiii:4", "h1", "issue:#1"))
        assert found["approval_id"] == aid
        assert _run(store.find_approval("orc_iiiiiiiiiiii", "orc_iiiiiiiiiiii:4", "h2", "issue:#1")) is None
        assert _run(store.find_approval("orc_iiiiiiiiiiii", "orc_iiiiiiiiiiii:4", "h1", "issue:#2")) is None
        assert _run(store.find_approval("orc_jjjjjjjjjjjj", "orc_iiiiiiiiiiii:4", "h1", "issue:#1")) is None

    def test_csv_neutralises_formula_prefixes_and_serialises_json(self, store):
        rows = [{"run_id": "=HYPERLINK(1)", "owner_note": "+x", "summary_json": {"a": 1}, "cost_usd": -0.5}]
        text = OrchestratorStore.to_csv(rows, RUN_COLUMNS)
        assert "'=HYPERLINK(1)" in text and "'+x" in text
        assert '"{""a"":1}"' in text
        assert ",-0.5," in text  # numbers are left alone

    def test_list_runs_filters(self, store):
        now = time.time()
        _run(store.create_run_or_attach(_run_row("orc_kkkkkkkkkkkk", "gh:issue:8:accepted", status="parked")))
        _run(store.create_run_or_attach(_run_row("orc_llllllllllll", "gh:issue:9:accepted", status="done",
                                                 workflow_id="weekly_price_audit", trigger_class="schedule")))
        assert [r["run_id"] for r in _run(store.list_runs(status="parked"))] == ["orc_kkkkkkkkkkkk"]
        assert [r["run_id"] for r in _run(store.list_runs(trigger_class="schedule"))] == ["orc_llllllllllll"]
        assert len(_run(store.list_runs(since=now - 5))) == 2
        assert _run(store.list_runs(until=now - 5)) == []
        assert len(_run(store.list_runs_by_status(("parked", "done")))) == 2
