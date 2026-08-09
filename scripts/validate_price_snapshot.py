#!/usr/bin/env python3
"""Validate the vendored price-registry snapshot.

Why this is a standalone script rather than only a test
-------------------------------------------------------
The weekly audit opens its snapshot-refresh PR using ``GITHUB_TOKEN``, and
GitHub deliberately does not trigger workflow runs for events created with that
token. The PR therefore arrives with **no CI**, which is a bad way to receive a
data change — silently-wrong pricing data is exactly the failure this repo has
already had once.

So the job that produces the file validates it before opening the PR, and the
same checks run in the normal test suite against the committed snapshot. The
data is verified either way, without depending on GitHub's trigger semantics or
on a long-lived personal access token.

Exit code 0 = usable, 1 = reject. Prints a Markdown summary on stdout so the
workflow can paste it straight into the PR body.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REQUIRED_FIELDS = ("input_cost_per_token", "output_cost_per_token")

# A healthy refresh nudges a handful of models. Anything wilder means the
# upstream format changed or the fetch was truncated — refuse rather than
# quietly replace every price in the catalogue.
MIN_MODELS = 500
MAX_REMOVED_FRACTION = 0.10
MAX_REPRICED_FRACTION = 0.25


def load(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object, got {type(data).__name__}")
    return data


def check_shape(snapshot: Dict[str, Any]) -> List[str]:
    """Structural problems that make the snapshot unusable."""
    errors: List[str] = []

    if len(snapshot) < MIN_MODELS:
        errors.append(
            f"only {len(snapshot)} models (minimum {MIN_MODELS}) — likely a truncated fetch"
        )

    malformed, negative = [], []
    for key, rec in snapshot.items():
        if not isinstance(rec, dict):
            malformed.append(key)
            continue
        for field in REQUIRED_FIELDS:
            if field not in rec:
                malformed.append(f"{key} (missing {field})")
                break
            value = rec[field]
            if not isinstance(value, (int, float)):
                malformed.append(f"{key} ({field} is {type(value).__name__})")
                break
            if value < 0:
                negative.append(f"{key} ({field}={value})")
                break

    if malformed:
        errors.append(f"{len(malformed)} malformed entr{'y' if len(malformed) == 1 else 'ies'}: "
                      + ", ".join(malformed[:5]))
    if negative:
        errors.append(f"{len(negative)} negative price(s): " + ", ".join(negative[:5]))

    return errors


def diff(old: Dict[str, Any], new: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    repriced = sorted(
        k for k in set(old) & set(new)
        if old[k].get("input_cost_per_token") != new[k].get("input_cost_per_token")
        or old[k].get("output_cost_per_token") != new[k].get("output_cost_per_token")
    )
    return added, removed, repriced


def check_movement(old: Dict[str, Any], removed: List[str], repriced: List[str]) -> List[str]:
    """Changes too large to be a routine upstream update."""
    errors: List[str] = []
    if not old:
        return errors

    removed_frac = len(removed) / len(old)
    if removed_frac > MAX_REMOVED_FRACTION:
        errors.append(
            f"{len(removed)} models removed ({removed_frac:.0%} of the catalogue, "
            f"limit {MAX_REMOVED_FRACTION:.0%}) — upstream format change or bad fetch"
        )

    repriced_frac = len(repriced) / len(old)
    if repriced_frac > MAX_REPRICED_FRACTION:
        errors.append(
            f"{len(repriced)} models repriced ({repriced_frac:.0%} of the catalogue, "
            f"limit {MAX_REPRICED_FRACTION:.0%}) — suspiciously broad, review by hand"
        )

    return errors


def summarise(old: Dict[str, Any], new: Dict[str, Any],
              added: List[str], removed: List[str], repriced: List[str]) -> str:
    lines = [
        "| Metric | Value |",
        "|---|---|",
        f"| Models | {len(old)} → {len(new)} |",
        f"| Added | {len(added)} |",
        f"| Removed | {len(removed)} |",
        f"| Repriced | {len(repriced)} |",
    ]

    def price_row(key: str) -> str:
        o_in = (old[key].get("input_cost_per_token") or 0) * 1e6
        n_in = (new[key].get("input_cost_per_token") or 0) * 1e6
        o_out = (old[key].get("output_cost_per_token") or 0) * 1e6
        n_out = (new[key].get("output_cost_per_token") or 0) * 1e6
        return f"| `{key}` | ${o_in:.3f} / ${o_out:.3f} | ${n_in:.3f} / ${n_out:.3f} |"

    if repriced:
        lines += ["", "**Repriced** (per 1M in/out)", "",
                  "| Model | Before | After |", "|---|---|---|"]
        lines += [price_row(k) for k in repriced[:20]]
        if len(repriced) > 20:
            lines.append(f"| _…and {len(repriced) - 20} more_ | | |")

    if added:
        shown = ", ".join(f"`{k}`" for k in added[:15])
        more = f" _(+{len(added) - 15} more)_" if len(added) > 15 else ""
        lines += ["", f"**Added**: {shown}{more}"]

    if removed:
        shown = ", ".join(f"`{k}`" for k in removed[:15])
        more = f" _(+{len(removed) - 15} more)_" if len(removed) > 15 else ""
        lines += ["", f"**Removed**: {shown}{more}"]

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="Snapshot to validate")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="Previous snapshot to diff against (enables movement checks)")
    args = parser.parse_args()

    try:
        new = load(args.snapshot)
    except Exception as e:
        print(f"REJECT: cannot read {args.snapshot}: {e}")
        return 1

    errors = check_shape(new)
    added: List[str] = []
    removed: List[str] = []
    repriced: List[str] = []
    old: Dict[str, Any] = {}

    if args.baseline and args.baseline.exists():
        try:
            old = load(args.baseline)
            added, removed, repriced = diff(old, new)
            errors += check_movement(old, removed, repriced)
        except Exception as e:
            print(f"REJECT: cannot read baseline {args.baseline}: {e}")
            return 1

    if errors:
        print("REJECT: snapshot failed validation\n")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"OK: {len(new)} models validated\n")
    if old:
        print(summarise(old, new, added, removed, repriced))
    return 0


if __name__ == "__main__":
    sys.exit(main())
