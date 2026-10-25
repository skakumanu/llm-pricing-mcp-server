#!/usr/bin/env python3
"""Find — and optionally remove — catalogue models their provider has shut down.

Two signals, reported separately because they carry different weight:

1. **Retired** (strong): the registry's same-provider entry carries a
   ``deprecation_date`` that has passed. The oracle already withholds these
   from cost math (``withhold_retired``); this script deletes the
   STATIC_PRICING entry once the date is at least ``--grace-days`` old, so a
   registry mistake has time to be corrected upstream before we act on it.
2. **Candidates** (weak): the model has no registry entry under *any*
   provider. Often retired (Gemini 1.0/1.5, Jurassic-2 were found this way),
   sometimes just absent from the registry. Listed for a human; never deleted.

Run weekly by .github/workflows/price-audit.yml, which opens a PR with the
deletions and puts both lists in the rolling audit issue. By hand:

    python scripts/retire_models.py                    # report
    python scripts/retire_models.py --apply            # delete retired entries
    python scripts/retire_models.py --markdown         # issue/PR body
"""
import argparse
import asyncio
import glob
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.services.base_provider import registry_models_path, render_registry_models_doc  # noqa: E402
from src.services.price_oracle import get_price_oracle  # noqa: E402
from src.services.pricing_aggregator import PricingAggregatorService  # noqa: E402

SERVICES = os.path.join(ROOT, "src", "services")


def remove_registry_entry(provider: str, model: str) -> Optional[str]:
    """Delete *model* from the provider's registry-models JSON; return the path, or None if absent.

    Registry-sourced entries (v1.75.0) live in data/registry_models/<slug>.json
    rather than STATIC_PRICING. The file is re-encoded through the same
    ``render_registry_models_doc`` the pickup uses, so the diff is one block.
    """
    path = registry_models_path(provider)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as fh:
        doc = json.load(fh)
    models = doc.get("models") if isinstance(doc, dict) else None
    if not isinstance(models, dict) or model not in models:
        return None
    del models[model]
    path.write_bytes(render_registry_models_doc(doc))
    return str(path)


def provider_file(provider: str, model: str) -> Optional[str]:
    hits = []
    for path in glob.glob(os.path.join(SERVICES, "*_pricing.py")):
        text = open(path, encoding="utf-8").read()
        if f'"{provider}"' in text and re.search(r'^\s*"' + re.escape(model) + r'":\s*\{', text, re.M):
            hits.append(path)
    return hits[0] if len(hits) == 1 else None


def remove_entry(text: str, key: str) -> Optional[str]:
    """Delete one top-level STATIC_PRICING entry (and its leading comment lines)."""
    m = re.search(r'^([ \t]*)"' + re.escape(key) + r'":\s*\{[^\n]*\n', text, re.M)
    if not m:
        return None
    indent = m.group(1)
    end = re.compile(r'^' + re.escape(indent) + r'\},?[ \t]*\n', re.M).search(text, m.end())
    if not end:
        return None
    start = m.start()
    while True:
        prev_nl = text.rfind("\n", 0, start - 1)
        line = text[prev_nl + 1:start]
        if line.strip().startswith("#") and line.startswith(indent):
            start = prev_nl + 1
        else:
            break
    return text[:start] + text[end.end():]


async def scan(grace_days: int, today: Optional[str] = None) -> Dict[str, List[Dict]]:
    oracle = get_price_oracle()
    await oracle.load()
    if not oracle.loaded:
        raise RuntimeError("price registry unavailable (no live fetch, no snapshot)")
    today_d = datetime.fromisoformat(today).date() if today else datetime.now(UTC).date()
    cutoff = (today_d - timedelta(days=grace_days)).isoformat()
    models, _ = await PricingAggregatorService().get_all_pricing_async(include_unconfirmed=True)
    retired, scheduled, candidates = [], [], []
    for m in models:
        if m.pricing_model != "per_token":
            continue
        rec = oracle.lookup(m.model_name, m.provider, require_provider_match=True)
        row = {"provider": m.provider, "model": m.model_name,
               "price_source_kind": getattr(m, "price_source_kind", "provider")}
        if rec is not None:
            if rec.deprecation_date:
                row.update({"registry_key": rec.model_key, "deprecation_date": rec.deprecation_date})
                if rec.deprecation_date <= today_d.isoformat():
                    row["past_grace"] = rec.deprecation_date <= cutoff
                    retired.append(row)
                else:
                    scheduled.append(row)
        elif oracle.lookup(m.model_name, None) is None:
            row["price_confirmed"] = m.price_confirmed
            candidates.append(row)
    for lst in (retired, scheduled, candidates):
        lst.sort(key=lambda r: (r["provider"], r["model"]))
    return {"today": today_d.isoformat(), "grace_days": grace_days,
            "retired": retired, "scheduled": scheduled, "candidates": candidates}


def apply_removals(report: Dict) -> List[Dict]:
    removed = []
    by_file: Dict[str, List[Dict]] = {}
    for row in report["retired"]:
        if not row.get("past_grace"):
            continue
        # A registry-sourced row lives in the provider's JSON file, not in a
        # *_pricing.py. A registry-labelled STATIC_PRICING entry (or a runtime
        # gap-fill) reports the same kind but is not in the JSON, so fall back.
        if row.get("price_source_kind") == "registry":
            json_path = remove_registry_entry(row["provider"], row["model"])
            if json_path is not None:
                row["file"] = os.path.relpath(json_path, ROOT)
                removed.append(row)
                continue
        path = provider_file(row["provider"], row["model"])
        if path is None:
            row["skipped"] = "could not locate a unique STATIC_PRICING entry"
            continue
        by_file.setdefault(path, []).append(row)
    for path, rows in by_file.items():
        text = open(path, encoding="utf-8").read()
        for row in rows:
            new = remove_entry(text, row["model"])
            if new is None:
                row["skipped"] = "entry shape not recognised"
                continue
            text = new
            row["file"] = os.path.relpath(path, ROOT)
            removed.append(row)
        open(path, "w", encoding="utf-8").write(text)
    return removed


def markdown(report: Dict, removed: Optional[List[Dict]]) -> str:
    out = []
    r, s, c = report["retired"], report["scheduled"], report["candidates"]
    out.append(f"### Retired models ({len(r)})")
    out.append("Same-provider registry entry carries a deprecation date that has passed. "
               "These are already withheld from cost math; entries past the "
               f"{report['grace_days']}-day grace period are removed by the retirement PR.\n")
    if r:
        out.append("| Provider | Model | Registry key | Retired on | Removed |")
        out.append("|---|---|---|---|---|")
        gone = {(x["provider"], x["model"]) for x in (removed or [])}
        for x in r:
            if (x["provider"], x["model"]) in gone:
                flag = "yes"
            else:
                flag = "pending" if x["past_grace"] else "after grace"
            out.append(f"| {x['provider']} | `{x['model']}` | `{x['registry_key']}` | "
                       f"{x['deprecation_date']} | {flag} |")
    else:
        out.append("_none_")
    out.append(f"\n### Scheduled retirements ({len(s)})")
    if s:
        out.append("| Provider | Model | Retires on |")
        out.append("|---|---|---|")
        for x in s:
            out.append(f"| {x['provider']} | `{x['model']}` | {x['deprecation_date']} |")
    else:
        out.append("_none_")
    out.append(f"\n### Retirement candidates — no registry entry under any provider ({len(c)})")
    out.append("Weak signal: verify against the provider's own model list before removing. "
               "Never deleted automatically.\n")
    if c:
        by: Dict[str, List[str]] = {}
        for x in c:
            by.setdefault(x["provider"], []).append(x["model"])
        for p, names in sorted(by.items()):
            out.append(f"- **{p}**: " + ", ".join(f"`{n}`" for n in names))
    else:
        out.append("_none_")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="delete retired entries past the grace period")
    ap.add_argument("--grace-days", type=int, default=30)
    ap.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD), for tests")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    report = asyncio.run(scan(args.grace_days, args.today))
    removed = apply_removals(report) if args.apply else None
    if removed is not None:
        report["removed"] = removed
    if args.json:
        print(json.dumps(report, indent=1, default=str))
    elif args.markdown:
        print(markdown(report, removed))
    else:
        print(f"retired: {len(report['retired'])} | scheduled: {len(report['scheduled'])} | "
              f"candidates (no registry entry): {len(report['candidates'])}")
        for x in report["retired"]:
            print(f"  RETIRED   {x['provider']:14s} {x['model']:50s} {x['deprecation_date']}"
                  + ("  [past grace]" if x["past_grace"] else ""))
        for x in report["scheduled"]:
            print(f"  SCHEDULED {x['provider']:14s} {x['model']:50s} {x['deprecation_date']}")
        if removed is not None:
            print(f"removed {len(removed)} entr{'y' if len(removed) == 1 else 'ies'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
