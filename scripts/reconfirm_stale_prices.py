#!/usr/bin/env python3
"""Reconfirm stale curated prices that still match the reference registry.

A curated price older than PRICE_STALE_AFTER_DAYS is reported as stale by
get_data_quality. If the price still agrees with the registry's same-provider
entry on both input and output (within the drift threshold), the price is
right and only its confirmation date is old — so stamp today's date on that
one STATIC_PRICING entry. Prices with no first-party registry entry are left
stale: there is no honest way to reconfirm them here.

Run weekly by .github/workflows/price-audit.yml, which opens a PR with the
result; a human merges it. Also usable by hand:

    python scripts/reconfirm_stale_prices.py            # report only
    python scripts/reconfirm_stale_prices.py --apply    # edit provider files
    python scripts/reconfirm_stale_prices.py --json     # machine-readable report

Exit status is 0 whether or not anything was stamped; errors exit non-zero.
"""
import argparse
import asyncio
import glob
import json
import os
import re
import sys
from datetime import UTC, datetime
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.services.price_oracle import DRIFT_THRESHOLD_PCT, get_price_oracle  # noqa: E402
from src.services.pricing_aggregator import PricingAggregatorService  # noqa: E402

SERVICES = os.path.join(ROOT, "src", "services")


def provider_file(provider: str, model: str) -> Optional[str]:
    """The one provider file that defines *model* for *provider*.

    The same key ("llama3.1-8b") can exist in several files; only the file
    whose provider string matches is the one the registry confirmed.
    """
    hits = []
    for path in glob.glob(os.path.join(SERVICES, "*_pricing.py")):
        text = open(path, encoding="utf-8").read()
        if f'"{provider}"' not in text:
            continue
        if re.search(r'^\s*"' + re.escape(model) + r'":\s*\{', text, re.M):
            hits.append(path)
    return hits[0] if len(hits) == 1 else None


def stamp(path: str, model: str, date: str) -> bool:
    """Set (or insert) a per-model price_as_of in a STATIC_PRICING entry."""
    text = open(path, encoding="utf-8").read()
    m = re.search(r'^(\s*)"' + re.escape(model) + r'":\s*\{\s*\n', text, re.M)
    if not m:
        return False
    indent = m.group(1) + "    "
    start = m.end()
    end = re.compile(r'^' + re.escape(m.group(1)) + r'\},?\s*$', re.M).search(text, start)
    if not end:
        return False
    block = text[start:end.start()]
    if re.search(r'"price_as_of":\s*"[^"]+"', block):
        new_block = re.sub(r'("price_as_of":\s*")[^"]+(")', r'\g<1>' + date + r'\2', block, count=1)
    else:
        out_line = re.search(r'^.*"output":.*\n', block, re.M)
        if not out_line:
            return False
        new_block = block[:out_line.end()] + f'{indent}"price_as_of": "{date}",\n' + block[out_line.end():]
    if new_block == block:
        return False
    open(path, "w", encoding="utf-8").write(text[:start] + new_block + text[end.start():])
    return True


async def classify(threshold_pct: float) -> Tuple[List[Dict], List[Dict]]:
    """Split stale confirmed per-token models into (reconfirmable, not)."""
    oracle = get_price_oracle()
    await oracle.load()
    if not oracle.loaded:
        raise RuntimeError("price registry unavailable (no live fetch, no snapshot)")
    models, _ = await PricingAggregatorService().get_all_pricing_async(include_unconfirmed=True)
    stale = [m for m in models if m.pricing_model == "per_token" and m.price_confirmed and m.price_is_stale]
    ok, no = [], []
    for m in stale:
        rec = oracle.lookup(m.model_name, m.provider, require_provider_match=True)
        row = {"provider": m.provider, "model": m.model_name, "price_as_of": m.price_as_of,
               "ours_in": m.cost_per_input_token * 1e6, "ours_out": m.cost_per_output_token * 1e6}
        if rec is None:
            row["reason"] = "no first-party registry entry"
            no.append(row)
            continue
        reg_in, reg_out = rec.input_per_1k * 1000, rec.output_per_1k * 1000
        row.update({"reg_in": reg_in, "reg_out": reg_out, "registry_key": rec.model_key})
        d_in = abs(row["ours_in"] - reg_in) / reg_in * 100 if reg_in else 999
        d_out = abs(row["ours_out"] - reg_out) / reg_out * 100 if reg_out else (0 if row["ours_out"] == 0 else 999)
        if d_in < threshold_pct and d_out < threshold_pct:
            ok.append(row)
        else:
            row["reason"] = f"registry disagrees (in {d_in:.1f}%, out {d_out:.1f}%)"
            no.append(row)
    return ok, no


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="edit provider files in place")
    ap.add_argument("--json", action="store_true", help="print a JSON report instead of text")
    ap.add_argument("--date", default=datetime.now(UTC).date().isoformat(), help="date to stamp (default: today UTC)")
    ap.add_argument("--threshold-pct", type=float, default=DRIFT_THRESHOLD_PCT)
    args = ap.parse_args()

    ok, no = asyncio.run(classify(args.threshold_pct))
    stamped, skipped = [], []
    for row in ok:
        path = provider_file(row["provider"], row["model"])
        if path is None:
            row["reason"] = "could not locate a unique STATIC_PRICING entry"
            skipped.append(row)
            continue
        row["file"] = os.path.relpath(path, ROOT)
        if args.apply:
            if stamp(path, row["model"], args.date):
                stamped.append(row)
            else:
                row["reason"] = "stamp failed (entry shape not recognised)"
                skipped.append(row)
        else:
            stamped.append(row)

    report = {"date": args.date, "applied": args.apply, "stale_total": len(ok) + len(no),
              "reconfirmable": len(ok), "stamped": stamped, "skipped": skipped, "left_stale": no}
    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 0

    verb = "Stamped" if args.apply else "Would stamp"
    print(f"Stale confirmed prices: {report['stale_total']}; reconfirmable against the registry: {len(ok)}")
    print(f"{verb} price_as_of={args.date} on {len(stamped)} entr{'y' if len(stamped) == 1 else 'ies'}:")
    for r in stamped:
        print(f"  {r['provider']:14s} {r['model']:50s} {r.get('file', '')}")
    if skipped:
        print(f"Skipped {len(skipped)}:")
        for r in skipped:
            print(f"  {r['provider']:14s} {r['model']:50s} {r['reason']}")
    print(f"Left stale (no honest way to reconfirm): {len(no)}")
    by = {}
    for r in no:
        by.setdefault(r["provider"], []).append(r)
    for p, rows in sorted(by.items()):
        disagree = [r for r in rows if r["reason"].startswith("registry disagrees")]
        note = ""
        if disagree:
            note = f"  ({len(disagree)} disagree with the registry — check_price_drift will withhold these)"
        print(f"  {p:14s} {len(rows):3d}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
