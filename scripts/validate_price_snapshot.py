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

# Upstream sometimes drops a whole provider namespace at once (Oct 2026: every
# `openrouter/*` entry, 486 keys, 15% of the file) — an editorial change, not a
# bad fetch. Keys of a provider that disappears *entirely* are discounted from
# the removal fraction, up to a few providers per refresh, and listed in the
# summary for the human merging the PR. Two guards stay absolute: a removal of
# more than MAX_TOTAL_REMOVED_FRACTION is rejected whatever its shape, and a
# provider the catalogue itself is priced from can never be discounted.
MAX_TOTAL_REMOVED_FRACTION = 0.40
MAX_WHOLESALE_PROVIDERS = 5
PROTECTED_PROVIDERS = frozenset({
    "openai", "azure", "anthropic", "gemini", "vertex_ai", "bedrock", "bedrock_converse",
    "mistral", "groq", "together_ai", "fireworks_ai", "deepseek", "xai", "cohere",
    "deepinfra", "novita", "nebius", "databricks", "vercel_ai_gateway",
})


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


def wholesale_removed_providers(old: Dict[str, Any], removed: List[str]) -> Dict[str, int]:
    """Providers whose every key in *old* is gone from the new snapshot -> key count."""
    removed_set = set(removed)
    per_provider: Dict[str, List[str]] = {}
    for key, rec in old.items():
        prov = (rec.get("litellm_provider") if isinstance(rec, dict) else None) or ""
        per_provider.setdefault(prov, []).append(key)
    return {
        prov: len(keys)
        for prov, keys in per_provider.items()
        if prov and all(k in removed_set for k in keys)
    }


def check_movement(old: Dict[str, Any], removed: List[str], repriced: List[str]) -> Tuple[List[str], List[str]]:
    """Changes too large to be a routine upstream update -> (errors, notes)."""
    errors: List[str] = []
    notes: List[str] = []
    if not old:
        return errors, notes

    total_frac = len(removed) / len(old)
    if total_frac > MAX_TOTAL_REMOVED_FRACTION:
        errors.append(
            f"{len(removed)} models removed ({total_frac:.0%} of the catalogue, "
            f"hard limit {MAX_TOTAL_REMOVED_FRACTION:.0%}) — upstream format change or bad fetch"
        )
        return errors, notes

    wholesale = wholesale_removed_providers(old, removed)
    protected = sorted(p for p in wholesale if p in PROTECTED_PROVIDERS)
    if protected:
        errors.append(
            "provider(s) the catalogue is priced from vanished entirely: "
            + ", ".join(f"{p} ({wholesale[p]} keys)" for p in protected)
            + " — bad fetch, not an editorial change"
        )
        return errors, notes

    discounted = 0
    if wholesale and len(wholesale) <= MAX_WHOLESALE_PROVIDERS:
        discounted = sum(wholesale.values())
        notes.append(
            "provider namespace(s) removed upstream entirely (discounted from the removal limit): "
            + ", ".join(f"`{p}` ({n} keys)" for p, n in sorted(wholesale.items(), key=lambda kv: -kv[1]))
        )

    residual = len(removed) - discounted
    removed_frac = residual / len(old)
    if removed_frac > MAX_REMOVED_FRACTION:
        errors.append(
            f"{residual} models removed ({removed_frac:.0%} of the catalogue, "
            f"limit {MAX_REMOVED_FRACTION:.0%}) — upstream format change or bad fetch"
        )

    repriced_frac = len(repriced) / len(old)
    if repriced_frac > MAX_REPRICED_FRACTION:
        errors.append(
            f"{len(repriced)} models repriced ({repriced_frac:.0%} of the catalogue, "
            f"limit {MAX_REPRICED_FRACTION:.0%}) — suspiciously broad, review by hand"
        )

    return errors, notes


def summarise(old: Dict[str, Any], new: Dict[str, Any],
              added: List[str], removed: List[str], repriced: List[str],
              notes: List[str] = ()) -> str:
    lines = [
        "| Metric | Value |",
        "|---|---|",
        f"| Models | {len(old)} → {len(new)} |",
        f"| Added | {len(added)} |",
        f"| Removed | {len(removed)} |",
        f"| Repriced | {len(repriced)} |",
    ]
    for note in notes:
        lines += ["", f"**Note**: {note}"]

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
    notes: List[str] = []
    old: Dict[str, Any] = {}

    if args.baseline and args.baseline.exists():
        try:
            old = load(args.baseline)
            added, removed, repriced = diff(old, new)
            movement_errors, notes = check_movement(old, removed, repriced)
            errors += movement_errors
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
        print(summarise(old, new, added, removed, repriced, notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
