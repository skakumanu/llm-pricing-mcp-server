"""Document writer data adapters (v1.89.0): status labels, release record parser move, source fidelity."""
import subprocess
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import docs_catalogue as cat  # noqa: E402
from src.services import docs_sources as dsrc  # noqa: E402
from src.services import whats_new  # noqa: E402

from tests.test_docs_service import WHATS_NEW, fake_sources, row  # noqa: E402

NOW = 1_800_000_000.0


@pytest.mark.parametrize("kwargs,withheld,expected", [
    ({}, (), "provider-confirmed"),
    ({"kind": "registry"}, (), "registry-sourced"),
    ({"stale": True}, (), "stale"),
    ({"confirmed": False}, ("m",), "withheld"),
    ({"confirmed": False}, (), "unpriced"),
    ({"retired": "2026-01-01", "confirmed": False}, ("m",), "retired"),
])
def test_price_status_labels(kwargs, withheld, expected):
    assert dsrc.price_status(row("m", **kwargs), set(withheld)) == expected


async def test_only_priced_statuses_get_figures_and_totals():
    rows = [row("a"), row("b", confirmed=False), row("c", retired="2026-01-01", confirmed=False)]
    params, _ = cat.validate_params("pricing_one_pager", {"model_names": ["a", "b", "c", "ghost"]})
    m = await dsrc.gather("pricing_one_pager", params, fake_sources(rows, withheld=("b",)), now=NOW)
    statuses = {e["model"]: e["status"] for e in m.meta["models"]}
    assert statuses == {"a": "provider-confirmed", "b": "withheld", "c": "retired", "ghost": "unpriced"}
    assert m.get("n_models").value == 4 and m.get("n_priced").value == 1
    assert [f.id for f in m.figures if f.id.startswith(("in_", "cost_"))] == ["in_0", "cost_0"]
    assert m.get("in_0").price_as_of == "2026-10-31" and m.get("in_0").status == "provider-confirmed"


async def test_cost_is_workload_times_price():
    params, _ = cat.validate_params("pricing_one_pager", {"model_names": ["claude-haiku-5-5", "claude-opus-5-5"],
                                                          "input_tokens": 2_000_000, "output_tokens": 1_000_000})
    m = await dsrc.gather("pricing_one_pager", params, fake_sources(), now=NOW)
    assert m.get("cost_0").value == pytest.approx(2 * 0.1 + 1 * 0.5)


async def test_data_quality_snapshot_is_recorded_as_read():
    from tests.test_audit_service import dq
    params, _ = cat.validate_params("pricing_one_pager", {})
    m = await dsrc.gather("pricing_one_pager", params, fake_sources(dq_loader=dq(confirmed=91.0, withheld=2, never=3)),
                          now=NOW)
    assert m.data_quality["confirmed_pct"] == 91.0 and m.get("dq_withheld_for_drift").value == 2
    assert [c["tool"] for c in m.calls][:2] == [dsrc.PRICING_CALL, dsrc.DRIFT_CALL] and dsrc.DQ_CALL in [c["tool"] for c in m.calls]


async def test_failed_data_quality_snapshot_fails_the_gather():
    async def bad():
        return {"success": False}
    params, _ = cat.validate_params("pricing_one_pager", {})
    with pytest.raises(RuntimeError):
        await dsrc.gather("pricing_one_pager", params, fake_sources(dq_loader=bad), now=NOW)


async def test_release_brief_uses_only_the_release_record():
    params, _ = cat.validate_params("release_notes_brief", {"version_range": "1.88.0..1.89.0"})
    m = await dsrc.gather("release_notes_brief", params, fake_sources(), now=NOW)
    assert [f.id for f in m.figures] == ["n_releases"] and [c["tool"] for c in m.calls] == [dsrc.WHATS_NEW_CALL]
    assert [e["version"] for e in m.meta["entries"]] == ["1.88.0", "1.89.0"]
    assert any("Four document types" in t for t in m.source_texts)


@pytest.mark.parametrize("spec,expected", [("latest", ["1.89.0"]), ("1.88.0", ["1.88.0"]), ("v1.88.0..v1.89.0", ["1.88.0", "1.89.0"])])
def test_version_range_parsing(spec, expected):
    assert dsrc._parse_range(spec, whats_new.list_versions(WHATS_NEW)) == expected


@pytest.mark.parametrize("spec", ["1.89.0..1.88.0", "9.9.9", "banana", "1.0.0..2.0.0.0"])
def test_bad_version_ranges_are_refused(spec):
    with pytest.raises(cat.ParamError):
        dsrc._parse_range(spec, whats_new.list_versions(WHATS_NEW))


async def test_status_report_holds_counts_and_ids_only():
    audit = {"report_id": "aud_x1", "status": "pass", "created_at": NOW, "counts": {"pass": 3}, "checks": [],
             "summary_text": "contact owner@example.com"}
    params, _ = cat.validate_params("internal_status_report", {})
    m = await dsrc.gather("internal_status_report", params, fake_sources(audit=audit), now=NOW)
    assert "summary_text" not in str(m.to_dict()) and "example.com" not in str(m.to_dict())
    assert m.get("approvals_open").value == 2 and m.get("audit_pass").value == 3


def test_whats_new_parser_moved_and_script_still_reexports_it():
    from scripts import release_notes_from_whats_new as script
    assert script.extract_entry is whats_new.extract_entry and script.entry_title is whats_new.entry_title
    out = subprocess.run([sys.executable, str(project_root / "scripts" / "release_notes_from_whats_new.py"), "1.89.0",
                          "--title"], capture_output=True, text=True, check=True, cwd=project_root)  # nosec B603
    assert out.stdout.strip() == "Document writer: decks and one-pagers with provenance, owner-reviewed"
