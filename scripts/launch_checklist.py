#!/usr/bin/env python3
"""Parse and validate docs/LAUNCH_CHECKLIST.md (v1.86.0).

The parser itself moved to src/services/launch_checklist.py in v1.88.0 (the audit agent runs in the
production image, which has no scripts/); this file is the CLI and re-exports the names so existing
imports (tests/test_launch_checklist.py) keep working.

    python scripts/launch_checklist.py            # print a one-line summary per status
    python scripts/launch_checklist.py --check    # exit 1 if the file is malformed or incomplete
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.launch_checklist import (  # noqa: E402,F401
    CHECKLIST, COLUMNS, REQUIRED_ITEMS, STATUSES, parse, problems,
)


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
