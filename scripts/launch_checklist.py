#!/usr/bin/env python3
"""Parse and validate docs/LAUNCH_CHECKLIST.md (v1.86.0).

One parser shared by tests/test_launch_checklist.py and the (future) audit agent, so the
checklist is data, not prose. The table columns are fixed:

    | ID | Item | Status | Owner | Verification | Completed |

    python scripts/launch_checklist.py            # print a one-line summary per status
    python scripts/launch_checklist.py --check    # exit 1 if the file is malformed or incomplete
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = PROJECT_ROOT / "docs" / "LAUNCH_CHECKLIST.md"

COLUMNS = ["ID", "Item", "Status", "Owner", "Verification", "Completed"]
STATUSES = ("todo", "in_progress", "done", "n/a")
_ID = re.compile(r"^LC-\d{2}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# (ID, keywords that must all appear, case-insensitively, in that row's Item + Verification).
REQUIRED_ITEMS = [
    ("LC-01", ["entity formed"]),
    ("LC-02", ["stripe account"]),
    ("LC-03", ["payout"]),
    ("LC-04", ["stripe tax"]),
    ("LC-05", ["price ids"]),
    ("LC-06", ["webhook"] + ["checkout.session.completed", "customer.subscription.updated",
                              "customer.subscription.deleted", "invoice.paid",
                              "invoice.payment_failed", "charge.refunded",
                              "charge.dispute.created"]),
    ("LC-07", ["secrets"]),
    ("LC-08", ["backed up outside fly"]),
    ("LC-09", ["replication running", "litestream"]),
    ("LC-10", ["restore drill", "restore-from-the-litestream-replica"]),
    ("LC-11", ["lawyer"]),
    ("LC-12", ["placeholders replaced"]),
    ("LC-13", ["legal_pages_live"]),
    ("LC-14", ["pilot"]),
    ("LC-15", ["support expectations", "best effort"]),
    ("LC-16", ["audit report"]),
    ("LC-17", ["push protection", "manual"]),
    ("LC-18", ["dependabot"]),
    ("LC-19", ["codeql"]),
    ("LC-20", ["security statement"]),
]


def parse(path: Path = CHECKLIST) -> List[Dict[str, str]]:
    """Return one dict per table row, keyed by the COLUMNS names (lower-cased status)."""
    rows: List[Dict[str, str]] = []
    header_seen = False
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells == COLUMNS:
            header_seen = True
            continue
        if not header_seen or set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(COLUMNS, cells))
        row["_cells"] = str(len(cells))
        rows.append(row)
    return rows


def problems(rows: List[Dict[str, str]]) -> List[str]:
    """Structural and completeness problems; an empty list means the checklist is valid."""
    out: List[str] = []
    if not rows:
        return ["no checklist table found"]
    seen = set()
    for row in rows:
        rid = row.get("ID", "?")
        if row.get("_cells") != str(len(COLUMNS)):
            out.append(f"{rid}: expected {len(COLUMNS)} columns, found {row.get('_cells')}")
        if not _ID.match(rid):
            out.append(f"{rid}: ID must look like LC-01")
        if rid in seen:
            out.append(f"{rid}: duplicate ID")
        seen.add(rid)
        if row.get("Status") not in STATUSES:
            out.append(f"{rid}: Status must be one of {', '.join(STATUSES)}")
        for col in ("Item", "Owner", "Verification"):
            if not row.get(col):
                out.append(f"{rid}: {col} is empty")
        done = row.get("Status") == "done"
        completed = row.get("Completed", "")
        if done and not _DATE.match(completed):
            out.append(f"{rid}: a done item needs a YYYY-MM-DD Completed date")
        if not done and completed not in ("-", "") and not _DATE.match(completed):
            out.append(f"{rid}: Completed must be a date or '-'")
    by_id = {r["ID"]: r for r in rows}
    for rid, words in REQUIRED_ITEMS:
        row = by_id.get(rid)
        if row is None:
            out.append(f"{rid}: required item missing")
            continue
        text = (row.get("Item", "") + " " + row.get("Verification", "")).lower()
        for word in words:
            if word.lower() not in text:
                out.append(f"{rid}: required keyword '{word}' missing")
    return out


def main(argv: List[str]) -> int:
    rows = parse()
    found = problems(rows)
    if "--check" in argv:
        for p in found:
            print(p)
        print("launch checklist valid" if not found else f"{len(found)} problem(s)")
        return 1 if found else 0
    counts = Counter(r.get("Status") for r in rows)
    print(", ".join(f"{s}: {counts.get(s, 0)}" for s in STATUSES))
    for p in found:
        print(p)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
