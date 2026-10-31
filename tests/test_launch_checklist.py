"""docs/LAUNCH_CHECKLIST.md is data (v1.86.0, AC18)."""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from scripts import launch_checklist  # noqa: E402

ROWS = launch_checklist.parse()
BY_ID = {r["ID"]: r for r in ROWS}
WEBHOOK_EVENTS = (
    "checkout.session.completed", "customer.subscription.updated", "customer.subscription.deleted",
    "invoice.paid", "invoice.payment_failed", "charge.refunded", "charge.dispute.created",
)


def _write(tmp_path, body):
    path = tmp_path / "LAUNCH_CHECKLIST.md"
    path.write_text(
        "| ID | Item | Status | Owner | Verification | Completed |\n|---|---|---|---|---|---|\n" + body,
        encoding="utf-8")
    return path


def test_checklist_parses_and_validates():
    assert ROWS
    assert launch_checklist.problems(ROWS) == []
    assert launch_checklist.main(["--check"]) == 0


def test_every_row_has_all_columns():
    for row in ROWS:
        for col in launch_checklist.COLUMNS:
            assert row[col], (row["ID"], col)
        assert row["Status"] in ("todo", "in_progress", "done", "n/a")
        assert row["Owner"].isalpha()


@pytest.mark.parametrize("rid,words", launch_checklist.REQUIRED_ITEMS, ids=[r for r, _ in launch_checklist.REQUIRED_ITEMS])
def test_required_items_present_by_id_and_keyword(rid, words):
    text = (BY_ID[rid]["Item"] + " " + BY_ID[rid]["Verification"]).lower()
    for word in words:
        assert word.lower() in text


def test_seven_webhook_event_types_listed():
    verification = BY_ID["LC-06"]["Verification"]
    for event in WEBHOOK_EVENTS:
        assert event in verification
    # the same seven the handler implements
    main_src = (project_root / "src" / "main.py").read_text(encoding="utf-8")
    for event in WEBHOOK_EVENTS:
        assert f'"{event}"' in main_src


def test_push_protection_is_a_manual_owner_item():
    row = BY_ID["LC-17"]
    assert row["Owner"] == "owner" and "manual" in row["Verification"].lower()


def test_restore_drill_references_the_deployment_section():
    assert "docs/DEPLOYMENT.md#restore-from-the-litestream-replica" in BY_ID["LC-10"]["Verification"]


def test_placeholder_item_uses_the_sync_script():
    assert "scripts/sync_legal.py --placeholders" in BY_ID["LC-12"]["Verification"]


def test_launch_items_start_unfinished():
    # Nothing on the list is true until the owner does it; a done row needs a date.
    for row in ROWS:
        if row["Status"] == "done":
            assert row["Completed"] != "-"


def test_ids_are_sequential_and_unique():
    ids = [r["ID"] for r in ROWS]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)


class TestValidatorRejectsBadFiles:
    def test_bad_status(self, tmp_path):
        rows = launch_checklist.parse(_write(tmp_path, "| LC-01 | Entity formed | maybe | owner | proof | - |\n"))
        assert any("Status" in p for p in launch_checklist.problems(rows))

    def test_done_without_date(self, tmp_path):
        rows = launch_checklist.parse(_write(tmp_path, "| LC-01 | Entity formed | done | owner | proof | - |\n"))
        assert any("Completed" in p for p in launch_checklist.problems(rows))

    def test_duplicate_id_and_missing_required(self, tmp_path):
        body = "| LC-01 | Entity formed | todo | owner | proof | - |\n| LC-01 | Again | todo | owner | proof | - |\n"
        found = launch_checklist.problems(launch_checklist.parse(_write(tmp_path, body)))
        assert any("duplicate" in p for p in found)
        assert any("LC-06" in p and "missing" in p for p in found)

    def test_no_table(self, tmp_path):
        path = tmp_path / "x.md"
        path.write_text("nothing here", encoding="utf-8")
        assert launch_checklist.problems(launch_checklist.parse(path)) == ["no checklist table found"]
