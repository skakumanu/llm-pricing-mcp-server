"""Document writer through the orchestrator engine (v1.89.0): runs, checkpoint, LLM budget, fallback, store."""
import json
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import docs_catalogue as cat  # noqa: E402
from src.services import docs_sources as dsrc  # noqa: E402
from src.services.docs_service import DocsService  # noqa: E402
from src.services.docs_store import DocsStore  # noqa: E402
from src.services.orchestrator_service import OrchestratorService  # noqa: E402
from src.services.orchestrator_store import OrchestratorStore  # noqa: E402

from tests.test_audit_checks import settings  # noqa: E402
from tests.test_audit_service import Clock, FakeBackend, dq, price  # noqa: E402

AS_OF = "2026-10-31"
WHATS_NEW = """
<!-- v1.89.0 -->
<div class="release latest"><span class="release-date">Nov 2, 2026</span><span class="release-tag tag-feature">Feature</span>
<div class="release-title">Document writer</div>
<div class="section-label">Added</div><ul class="change-list"><li>Four document types; 12 sections tested.</li></ul></div>
<!-- v1.88.0 -->
<div class="release"><span class="release-date">Nov 1, 2026</span><span class="release-tag tag-feature">Feature</span>
<div class="release-title">Compliance audit</div>
<div class="section-label">Added</div><ul class="change-list"><li>Weekly audit runs every Monday at 07:00.</li></ul></div>
"""


def row(name, provider="Anthropic", pin=1.0, pout=5.0, confirmed=True, retired=None, stale=False, kind="provider",
        as_of=AS_OF):
    return SimpleNamespace(model_name=name, provider=provider, cost_per_input_token=pin / 1e6,
                           cost_per_output_token=pout / 1e6, price_as_of=as_of, price_confirmed=confirmed,
                           retired_on=retired, price_is_stale=stale, price_source_kind=kind)


def default_rows():
    return [row("claude-haiku-5-5", pin=0.1, pout=0.5), row("claude-sonnet-5-5", pin=2.0, pout=10.0, kind="registry"),
            row("claude-opus-5-5", pin=4.0, pout=20.0, stale=True),
            row("claude-fable-5-1", pin=10.0, pout=50.0, confirmed=False)]


def fake_sources(rows=None, withheld=(), dq_loader=None, audit=None, **kw):
    async def pricing():
        return rows if rows is not None else default_rows()

    async def withheld_ids():
        return set(withheld)

    async def audit_report():
        return audit

    async def runs(since):
        return [{"workflow_id": "document_generate", "status": "waiting_owner"},
                {"workflow_id": "document_generate", "status": "done"},
                {"workflow_id": "compliance_audit", "status": "done"}]

    async def approvals():
        return 2

    async def budget():
        return {"global": {"spent_usd": 1.5, "ceiling_usd": 150.0, "pct": 1.0},
                "workflows": [{"workflow_id": "document_generate", "spent_usd": 0.25, "monthly_cap_usd": 20.0,
                               "per_run_ceiling_usd": 2.0}]}

    return dsrc.Sources(pricing=pricing, withheld=withheld_ids, data_quality=dq_loader or dq(),
                        whats_new_text=lambda: WHATS_NEW, audit_report=audit_report, budget_view=budget,
                        list_runs=runs, open_approvals=approvals, app_version="1.89.0", **kw)


async def build(tmp_path, backend=None, clock=None, sources=None, **overrides):
    clock = clock or Clock()
    s = settings(anthropic_api_key="k-fixture", agent_llm_provider="anthropic", docs_default_model="claude-haiku-5-5",
                 docs_premium_model="claude-sonnet-5-5", docs_artifact_retention_days=30,
                 docs_data_only_default=False, app_name="LLM Pricing API")
    for k, v in overrides.pop("settings", {}).items():
        setattr(s, k, v)
    ostore = OrchestratorStore(str(tmp_path / "orch.db"))
    await ostore.initialize()
    orch = OrchestratorService(ostore, clock=clock)
    store = DocsStore(str(tmp_path / "orch.db"), retention_days=s.docs_artifact_retention_days)
    await store.initialize()
    svc = DocsService(store, orch, s, clock, sources=sources or fake_sources(),
                      llm_factory=backend.factory if backend else None, price_call=overrides.pop("price_call", price))
    svc.install()
    return SimpleNamespace(svc=svc, orch=orch, store=store, ostore=ostore, clock=clock, settings=s)


def llm_answer(summary="Haiku is the cheapest option.", notes=None):
    return json.dumps({"summary": summary, "notes": notes or {}})


async def spend_rows(env):
    return await env.ostore.spend_by_workflow(__import__("src.services.orchestrator_service", fromlist=["_period"])._period(env.clock()))


DECK = dict(doc_type="pricing_comparison_deck", params={})


async def gen(env, **kw):
    args = dict(DECK)
    args.update(kw)
    return await env.svc.generate(args.pop("doc_type"), args.pop("params", {}), **args)


# --------------------------------------------------------------------------- AC1 / AC8

async def test_deck_run_waits_at_publish_checkpoint_with_all_slides(tmp_path):
    backend = FakeBackend(text=llm_answer())
    env = await build(tmp_path, backend)
    res = await gen(env)
    assert res["status"] == "waiting_owner" and res["approval"]["kind"] == "publish"
    assert res["artifact_status"] == "draft" and res["llm_mode"] == "llm" and res["corrections"] == 0
    assert res["outline"] == ["title", "executive_summary", "agenda", "price_table", "cost_ranking", "cost_chart",
                              "methodology", "sources", "next_steps"]
    row_ = await env.store.get(res["artifact_id"])
    assert all(s["notes"].strip() for s in row_["document"]["sections"])
    approvals = await env.ostore.list_approvals(status="waiting")
    art = approvals[0]["artifact_summary_json"]
    assert art["artifact_id"] == res["artifact_id"] and art["doc_type"] == "pricing_comparison_deck"
    assert art["corrections"] == 0 and art["llm_mode"] == "llm" and art["title"]


async def test_approve_publishes_and_reject_discards_and_both_download(tmp_path):
    env = await build(tmp_path, FakeBackend(text=llm_answer()))
    a = await gen(env)
    b = await gen(env)
    for res, decision in ((a, "approve"), (b, "reject")):
        approval = (await env.ostore.list_approvals(status="waiting", run_id=res["run_id"]))[0]
        await env.orch.decide_approval(approval["approval_id"], decision)
    ra = await env.svc.get_artifact(a["artifact_id"])
    rb = await env.svc.get_artifact(b["artifact_id"])
    assert ra["status"] == "published" and rb["status"] == "discarded"
    body_a, media, name = await env.svc.render(ra, "html")
    body_b, _, _ = await env.svc.render(rb, "md")
    assert "DRAFT" not in body_a and "DISCARDED" in body_b
    assert media.startswith("text/html") and re.fullmatch(r"pricing-comparison-deck-\d{8}-doc_\w+\.html", name)
    run_a = await env.ostore.get_run(a["run_id"])
    assert run_a["status"] == "done"


# --------------------------------------------------------------------------- AC2 / AC3 / AC4

async def test_untraceable_number_is_removed_and_counted(tmp_path):
    env = await build(tmp_path, FakeBackend(text=llm_answer("Haiku is cheap. It saves 73% versus the rest.")))
    res = await gen(env)
    assert res["corrections"] == 1 and res["manifest"]["corrections_count"] == 1
    doc = (await env.store.get(res["artifact_id"]))["document"]
    summary = doc["sections"][1]["paragraphs"][0]
    assert "73" not in summary and "Haiku is cheap." in summary


async def test_clean_draft_reports_zero_corrections(tmp_path):
    env = await build(tmp_path, FakeBackend(text=llm_answer("Four models were compared.")))
    res = await gen(env)
    assert res["corrections"] == 0
    doc = (await env.store.get(res["artifact_id"]))["document"]
    assert doc["sections"][1]["paragraphs"] == ["Four models were compared."]


async def test_unpriced_model_is_not_available_and_excluded(tmp_path):
    env = await build(tmp_path, FakeBackend(text=llm_answer()))
    res = await gen(env)
    row_ = await env.store.get(res["artifact_id"])
    doc = row_["document"]
    prices = {r[0]: r for r in next(s for s in doc["sections"] if s["id"] == "price_table")["table"]["rows"]}
    assert prices["claude-fable-5-1"][2] == "price not available" and prices["claude-fable-5-1"][4] == "unpriced"
    assert prices["claude-haiku-5-5"][5] == AS_OF and prices["claude-sonnet-5-5"][4] == "registry-sourced"
    assert prices["claude-opus-5-5"][4] == "stale"
    ranking = next(s for s in doc["sections"] if s["id"] == "cost_ranking")["table"]["rows"]
    assert [r[0] for r in ranking] == ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    chart = next(s for s in doc["sections"] if s["id"] == "cost_chart")["chart"]
    assert [b["label"] for b in chart["bars"]] == [r[0] for r in ranking]


async def test_data_quality_section_equals_snapshot(tmp_path):
    env = await build(tmp_path, sources=fake_sources(dq_loader=dq(confirmed=97.5, withheld=3, never=4)))
    res = await gen(env, data_only=True)
    row_ = await env.store.get(res["artifact_id"])
    dq_rows = dict(next(s for s in row_["document"]["sections"] if s["id"] == "methodology")["table"]["rows"])
    assert dq_rows["Confirmed prices"] == "97.5%" and dq_rows["Withheld for drift"] == "3"
    assert dq_rows["Never priced"] == "4"
    assert row_["manifest"]["data_quality"]["confirmed_pct"] == 97.5


# --------------------------------------------------------------------------- AC6 / AC7

async def test_data_only_request_makes_no_llm_call_and_is_labelled(tmp_path):
    backend = FakeBackend(text=llm_answer())
    env = await build(tmp_path, backend)
    res = await gen(env, data_only=True)
    assert backend.calls == [] and res["llm_mode"] == "data_only:requested"
    row_ = await env.store.get(res["artifact_id"])
    assert row_["document"]["label"].startswith("Data-only edition")
    assert "Data-only edition" in row_["document"]["footer"]
    assert await env.ostore.spend_total(period=__import__("src.services.orchestrator_service", fromlist=["_period"])._period(env.clock())) == 0


async def test_backend_failure_falls_back_and_records_reason(tmp_path):
    backend = FakeBackend(error=ConnectionError("down"))
    env = await build(tmp_path, backend)
    res = await gen(env)
    assert res["llm_mode"] == "data_only:error:ConnectionError" and res["artifact_status"] == "draft"
    assert (await env.store.get(res["artifact_id"]))["llm"]["reason"] == "error:ConnectionError"


async def test_unparseable_answer_falls_back_but_records_spend(tmp_path):
    env = await build(tmp_path, FakeBackend(text="not json at all"))
    res = await gen(env)
    assert res["llm_mode"] == "data_only:unusable_response"
    assert (await env.ostore.get_run(res["run_id"]))["cost_usd"] == pytest.approx(0.001)


async def test_exhausted_monthly_budget_completes_data_only_without_call(tmp_path):
    backend = FakeBackend(text=llm_answer())
    env = await build(tmp_path, backend)
    await env.orch.update_settings({"budget.workflow_monthly_usd.document_generate": 0.01}, updated_by="test")
    first = await gen(env)  # consumes spend
    assert first["llm_mode"] == "llm"
    await env.orch.record_agent_spend("document_generate", "document_agent", "x", "m", 1, 1, 1.0)
    calls_before = len(backend.calls)
    res = await gen(env)
    assert len(backend.calls) == calls_before
    assert res["llm_mode"].startswith("data_only:budget") and res["artifact_status"] == "draft"


async def test_successful_call_records_exactly_one_spend_row_with_model(tmp_path):
    env = await build(tmp_path, FakeBackend(text=llm_answer()))
    res = await gen(env, premium=True)
    async with __import__("aiosqlite").connect(str(tmp_path / "orch.db")) as db:
        async with db.execute("SELECT workflow_id, model, tokens_in, tokens_out FROM orch_spend WHERE run_id = ?",
                              (res["run_id"],)) as cur:
            rows = await cur.fetchall()
    assert rows == [("document_generate", "claude-sonnet-5-5", 400, 100)]


async def test_default_model_is_haiku(tmp_path):
    backend = FakeBackend(text=llm_answer())
    env = await build(tmp_path, backend)
    await gen(env)
    assert backend.calls == [("anthropic", "claude-haiku-5-5")]


async def test_missing_api_key_is_data_only(tmp_path):
    env = await build(tmp_path, FakeBackend(text=llm_answer()), settings={"anthropic_api_key": None})
    assert (await gen(env))["llm_mode"] == "data_only:no_api_key"


# --------------------------------------------------------------------------- AC10 / failures

async def test_gather_failure_parks_run_and_stores_no_document(tmp_path):
    async def boom():
        raise RuntimeError("source down")
    src = fake_sources()
    src.pricing = boom
    env = await build(tmp_path, sources=src)
    res = await gen(env)
    assert res["status"] != "waiting_owner" and res["artifact_status"] == "failed"
    row_ = await env.store.get(res["artifact_id"])
    assert row_["document"] is None and "gather failed" in row_["error"]
    assert (await env.ostore.get_run(res["run_id"]))["parked_reason"].startswith("blocked:gather")


async def test_structural_validation_failure_stores_no_artifact(tmp_path, monkeypatch):
    env = await build(tmp_path, FakeBackend(text=llm_answer()))
    orig = cat.build_document

    def broken(*a, **k):
        doc = orig(*a, **k)
        doc["sections"] = [s for s in doc["sections"] if s["id"] != "executive_summary"]
        return doc
    monkeypatch.setattr(cat, "build_document", broken)
    res = await gen(env)
    assert res["artifact_status"] == "failed" and "executive_summary" in (res["failure"] or "")
    row_ = await env.store.get(res["artifact_id"])
    assert row_["document"] is None and row_["manifest"] == {} and row_["llm"] is None
    assert res["approval"] is None


# --------------------------------------------------------------------------- AC9 / AC13

async def test_artifact_rows_never_contain_pii_tokens(tmp_path):
    rows = default_rows() + [row("sk-" + "a" * 24 + "@x.io", provider="cus_" + "b" * 12)]
    env = await build(tmp_path, FakeBackend(text=llm_answer("Mail me at a@b.io with sk-" + "c" * 24 + ".")),
                      sources=fake_sources(rows))
    res = await gen(env, params={"model_names": ["claude-haiku-5-5", "sk-" + "a" * 24 + "@x.io"]})
    assert res["artifact_id"]
    con = sqlite3.connect(str(tmp_path / "orch.db"))
    dump = " ".join(str(v) for r in con.execute("SELECT * FROM doc_artifacts") for v in r)
    con.close()
    for token in ("@", "cus_", "sub_"):
        assert token not in dump
    assert not re.search(r"\bsk-", dump)


async def test_deterministic_structure_in_data_only_mode(tmp_path):
    env = await build(tmp_path)
    a = await gen(env, data_only=True)
    b = await gen(env, data_only=True)
    da = (await env.store.get(a["artifact_id"]))["document"]
    db = (await env.store.get(b["artifact_id"]))["document"]
    assert [s["id"] for s in da["sections"]] == [s["id"] for s in db["sections"]]
    assert [s["table"] for s in da["sections"]] == [s["table"] for s in db["sections"]]


# --------------------------------------------------------------------------- AC11 / AC12

async def test_release_brief_contains_only_record_facts(tmp_path):
    env = await build(tmp_path, FakeBackend(text=llm_answer("Two releases shipped. Over 900 tests were added.")))
    res = await gen(env, doc_type="release_notes_brief", params={"version_range": "1.88.0..1.89.0"})
    assert res["corrections"] >= 1  # "900" is not in the record
    doc = (await env.store.get(res["artifact_id"]))["document"]
    bullets = [b for s in doc["sections"] if s["id"].startswith("release_") for b in s["bullets"]]
    assert "Added: Four document types; 12 sections tested." in bullets
    assert "Added: Weekly audit runs every Monday at 07:00." in bullets
    assert all(m.group(0) in WHATS_NEW for b in bullets for m in [re.search(r"\d+", b)] if m)
    assert doc["title"] == "Release notes brief: v1.88.0 to v1.89.0"
    assert not any(f.id.startswith(("in_", "cost_")) for f in cat.Manifest.from_dict(
        (await env.store.get(res["artifact_id"]))["manifest"]).figures)


async def test_release_brief_latest_and_bad_range(tmp_path):
    env = await build(tmp_path)
    res = await gen(env, doc_type="release_notes_brief", params={}, data_only=True)
    assert (await env.store.get(res["artifact_id"]))["document"]["title"] == "Release notes brief: v1.89.0"
    bad = await gen(env, doc_type="release_notes_brief", params={"version_range": "9.0.0"}, data_only=True)
    assert bad["artifact_id"] is None and "version_range" in bad["failure"]
    assert (await env.ostore.get_run(bad["run_id"]))["status"] != "done"


async def test_status_report_has_audit_budget_runs_and_no_pii(tmp_path):
    audit = {"report_id": "aud_abc12345", "status": "warn", "created_at": Clock()(),
             "counts": {"pass": 12, "warn": 1, "fail": 0, "n_a": 2},
             "checks": [{"id": "legal.placeholders", "status": "warn"}]}
    env = await build(tmp_path, sources=fake_sources(audit=audit))
    res = await gen(env, doc_type="internal_status_report", params={}, data_only=True)
    doc = (await env.store.get(res["artifact_id"]))["document"]
    text = json.dumps(doc)
    assert "aud_abc12345" in text and "$150.00" in text and "document_generate" in text
    assert "@" not in text and not re.search(r"\b(cus|sub)_", text)
    audit_rows = dict(next(s for s in doc["sections"] if s["id"] == "audit")["table"]["rows"])
    assert audit_rows["Passed"] == "12" and audit_rows["Failed"] == "0"


# --------------------------------------------------------------------------- AC14 / store / safety

async def test_retention_purge_removes_old_artifacts(tmp_path):
    env = await build(tmp_path, FakeBackend(text=llm_answer()))
    res = await gen(env)
    assert await env.store.purge_expired(now=env.clock() + 29 * 86400) == 0
    assert await env.store.purge_expired(now=env.clock() + 31 * 86400) == 1
    assert await env.store.get(res["artifact_id"]) is None


async def test_invalid_params_raise_before_a_run_exists(tmp_path):
    env = await build(tmp_path)
    with pytest.raises(cat.ParamError):
        await gen(env, params={"nope": 1})
    with pytest.raises(cat.ParamError):
        await env.svc.generate("sales_one_pager", {})
    assert await env.ostore.list_runs() == []


async def test_generations_are_logged_as_action_records(tmp_path):
    env = await build(tmp_path, FakeBackend(text=llm_answer()))
    res = await gen(env)
    row_ = await env.store.get(res["artifact_id"])
    await env.svc.render(row_, "html")
    actions = [a["action"] for a in await env.ostore.list_actions(run_id=res["run_id"])]
    assert "docs.generate" in actions and "docs.download" in actions


def test_store_has_one_update_and_one_delete():
    src = (project_root / "src" / "services" / "docs_store.py").read_text(encoding="utf-8")
    assert len(re.findall(r"\bUPDATE doc_artifacts\b", src)) == 1
    assert len(re.findall(r"\bDELETE FROM\b", src)) == 1


def test_docs_modules_reference_no_write_methods_of_other_systems():
    banned = ("update_tier", "set_billing_status", "delete_customer", "stripe.", "update_settings", "request_action",
              "billing_db", "conversations")
    for name in ("docs_catalogue", "docs_sources", "docs_render", "docs_verify", "docs_store", "docs_service"):
        src = (project_root / "src" / "services" / f"{name}.py").read_text(encoding="utf-8")
        code = re.sub(r'"""(?:.|\n)*?"""', "", src)
        code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
        for word in banned:
            assert word not in code, f"{name} references {word}"


def test_orchestrator_does_not_import_docs_code():
    src = (project_root / "src" / "services" / "orchestrator_service.py").read_text(encoding="utf-8")
    assert "docs_" not in src.replace("document_generate", "")


async def test_configured_models_resolve_in_catalogue():
    from src.services.pricing_aggregator import get_pricing_aggregator
    agg = await get_pricing_aggregator()
    for model in ("claude-haiku-5-5", "claude-sonnet-5-5"):
        assert await agg.find_model_pricing(model) is not None
