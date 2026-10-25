#!/usr/bin/env python3
"""Pick up new registry models as registry-sourced catalogue entries.

A model used to enter the catalogue only when a human edited a provider's
STATIC_PRICING or a provider's live model list happened to expose it. Nothing
watched the reference price registry itself, so for providers whose catalogue
the registry already backs, growth depended on someone noticing.

This script closes that loop. For each registry-backed catalogue provider it
finds every chat model the registry prices under that provider that the
catalogue does not already list, and writes it to
``data/registry_models/<slug>.json`` as a *registry-sourced* entry: the
registry's price, its key, the registry data date, and
``price_source_kind: "registry"`` so no consumer can mistake it for a price
confirmed against the provider's own rate card. Entries already in the file
are re-synced (price changed -> updated and reported; unchanged -> re-dated);
one the registry no longer backs is left alone and ages into stale on its own.

The script never opens a ``*_pricing.py`` file. A curated entry is a human's
statement about a rate card; this is the registry's, kept separately.

Exclusions (each reported as skipped, with its reason): non-chat mode; missing
input or output price; non-chat artefacts by name (embeddings, TTS, ...);
deprecation date already passed; deeper region/tier variants of a shallower
same-provider key; fine-tune templates; dated snapshots beside an undated key;
``-latest`` aliases of an existing key; provider size-bucket pseudo-models;
keys the catalogue already covers (by name or via oracle matching); prices
outside the plausibility band; names the oracle would not resolve back to the
same registry key.

Run weekly by .github/workflows/price-audit.yml with ``--snapshot-only``: the
guard tests the job runs before opening the PR price the catalogue against
the vendored snapshot, so the pickup must read the same registry or every
genuinely new key would fail the guards. New registry data reaches this
script through the (validated, human-merged) snapshot-refresh PR. By hand:

    python scripts/pickup_registry_models.py                       # report only (live registry)
    python scripts/pickup_registry_models.py --apply               # write JSON files
    python scripts/pickup_registry_models.py --markdown            # PR body
    python scripts/pickup_registry_models.py --providers 'Novita,Nebius'
    python scripts/pickup_registry_models.py --snapshot-only       # vendored snapshot, no network
    python scripts/pickup_registry_models.py --registry r.json --out-dir /tmp/x   # tests

Exit status is 0 whether or not anything was written; errors exit non-zero.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.services.base_provider import (  # noqa: E402
    BasePricingProvider,
    registry_models_path,
    render_registry_models_doc,
)
from src.services.price_oracle import (  # noqa: E402
    PLAUSIBLE_MAX_INPUT_PER_1M,
    PLAUSIBLE_MAX_OUTPUT_PER_1M,
    PLAUSIBLE_MIN_INPUT_PER_1M,
    REGISTRY_URL,
    SNAPSHOT_PATH,
    PriceOracle,
    _aliases_for,
    set_price_oracle,
)
from src.services.pricing_aggregator import PROVIDER_SERVICES  # noqa: E402

DEFAULT_OUT_DIR = os.path.join(ROOT, "data", "registry_models")

# Catalogue providers that resell other vendors' models at their own rates, or
# have no stable first-party registry presence. Nothing safe to pick up.
PICKUP_EXCLUDED_PROVIDERS = frozenset({"Hugging Face", "NVIDIA NIM", "PromptQL", "Salesforce"})

# What the workflow runs before it opens the PR; listed in the PR body so a
# reviewer of a GITHUB_TOKEN-created PR (which shows no checks) knows.
GUARD_TESTS = [
    "tests/test_price_oracle.py",
    "tests/test_price_provenance.py",
    "tests/test_pricing_integrity.py",
    "tests/test_data_quality.py",
    "tests/test_registry_pickup.py",
]

# Pseudo-key shapes (OWNER DECISION, FR7). Applied to the key's final slash
# segment — the model id proper — so an organisation segment such as
# "NousResearch" is never mistaken for a non-chat artefact.
DATED_TAIL_RE = re.compile(r"^(.+)-(\d{4}-\d{2}-\d{2}|\d{8}|\d{4})$")
LATEST_TAIL_RE = re.compile(r"^(.+)-latest$")
SIZE_BUCKET_RE = re.compile(r"(?:^|-)up-to-\d+(?:\.\d+)?b$|-\d+(?:\.\d+)?b-\d+(?:\.\d+)?b$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Registry loading
# ---------------------------------------------------------------------------

def load_registry(registry_path: Optional[str], snapshot_only: bool) -> Tuple[Dict[str, Any], str]:
    """Return (raw registry dict, source label).

    ``--registry PATH`` wins (tests), then ``--snapshot-only`` (deterministic
    initial population), else the live registry with the snapshot as fallback —
    the same precedence PriceOracle.load() uses.
    """
    oracle = PriceOracle()
    if registry_path:
        with open(registry_path, encoding="utf-8") as fh:
            return json.load(fh), registry_path
    if snapshot_only:
        raw = oracle._read_snapshot()
        if not raw:
            raise RuntimeError(f"vendored snapshot unreadable at {SNAPSHOT_PATH}")
        return raw, str(SNAPSHOT_PATH)
    raw = asyncio.run(oracle._fetch_remote())
    if raw:
        return raw, REGISTRY_URL
    raw = oracle._read_snapshot()
    if not raw:
        raise RuntimeError("price registry unavailable (no live fetch, no snapshot)")
    return raw, str(SNAPSHOT_PATH)


def build_oracle(raw: Dict[str, Any], source: str) -> PriceOracle:
    oracle = PriceOracle()
    oracle.load_from_dict(raw, source)
    if not oracle.loaded:
        raise RuntimeError(f"registry at {source} holds no priced chat models")
    # The aggregator (used for runtime catalogue names) must see the same data.
    set_price_oracle(oracle)
    return oracle


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

def _registry_providers_in(raw: Dict[str, Any]) -> set:
    return {
        str(v.get("litellm_provider")).lower()
        for v in raw.values()
        if isinstance(v, dict) and v.get("litellm_provider")
    }


def registry_backed_services(
    raw: Dict[str, Any],
    only: Optional[Iterable[str]] = None,
    services: Optional[List[BasePricingProvider]] = None,
) -> List[BasePricingProvider]:
    """Catalogue providers with at least one same-provider registry record.

    *only* restricts to the given display names (case-insensitive). A name that
    is unknown, excluded (``PICKUP_EXCLUDED_PROVIDERS``) or has no same-provider
    registry record raises, so a typo or a misjudged widening of the workflow's
    ``--providers`` fails loudly instead of silently picking up nothing for it.
    Without *only*, excluded and unbacked providers are simply left out.
    """
    if services is None:
        services = [cls() for _, cls in PROVIDER_SERVICES]
    present = _registry_providers_in(raw)
    by_name = {s.provider_name.lower(): s for s in services}
    explicit = only is not None
    if explicit:
        wanted = [n.strip() for n in only if n.strip()]
        unknown = [n for n in wanted if n.lower() not in by_name]
        if unknown:
            raise SystemExit(f"unknown provider(s) in --providers: {unknown}; "
                             f"known: {sorted(s.provider_name for s in services)}")
        services = [by_name[n.lower()] for n in wanted]
    out = []
    for svc in services:
        if svc.provider_name in PICKUP_EXCLUDED_PROVIDERS:
            if explicit:
                raise SystemExit(f"{svc.provider_name!r} is in PICKUP_EXCLUDED_PROVIDERS "
                                 f"(resells other vendors' models); nothing safe to pick up")
            continue
        aliases = _aliases_for(svc.provider_name) or frozenset()
        if aliases & present:
            out.append(svc)
        elif explicit:
            raise SystemExit(f"{svc.provider_name!r} has no same-provider record in the registry "
                             f"(aliases {sorted(aliases)}); check PROVIDER_ALIASES")
    return out


# ---------------------------------------------------------------------------
# Exclusion rules
# ---------------------------------------------------------------------------

def _tail(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def catalogue_name(key: str, aliases: Iterable[str]) -> str:
    """The identifier the provider's API accepts: the key minus its provider prefix."""
    kl = key.lower()
    for alias in sorted(aliases, key=len, reverse=True):
        if kl.startswith(alias + "/"):
            return key[len(alias) + 1:]
    return key


def is_fine_tune_template(key: str) -> bool:
    return any(seg.startswith("ft:") for seg in key.split("/")) or ":ft-" in key


def deeper_variant_of(key: str, same_provider_keys: Iterable[str]) -> Optional[str]:
    """A shallower same-provider key this one is a region/tier variant of, if any."""
    kl = key.lower()
    depth = kl.count("/")
    tail = _tail(kl)
    for other in same_provider_keys:
        ol = other.lower()
        if ol == kl:
            continue
        if kl.startswith(ol + "/"):
            return other
        if ol.count("/") < depth and _tail(ol) == tail:
            return other
    return None


def dated_snapshot_of(key: str, same_provider_keys_lower: set) -> Optional[str]:
    tail = _tail(key)
    m = DATED_TAIL_RE.match(tail)
    if not m:
        return None
    base_key = key[: len(key) - len(tail)] + m.group(1)
    return base_key if base_key.lower() in same_provider_keys_lower else None


def latest_alias_of(key: str, same_provider_keys_lower: set) -> Optional[str]:
    tail = _tail(key)
    m = LATEST_TAIL_RE.match(tail)
    if not m:
        return None
    prefix = key[: len(key) - len(tail)]
    bases = [m.group(1)]
    if bases[0].endswith("-chat"):
        bases.append(bases[0][: -len("-chat")])
    for base in bases:
        if (prefix + base).lower() in same_provider_keys_lower:
            return prefix + base
    return None


def is_size_bucket(key: str) -> bool:
    return bool(SIZE_BUCKET_RE.search(_tail(key)))


def _fmt_per_1m(per_1k: float) -> str:
    return f"{per_1k * 1000:.4f}"


# ---------------------------------------------------------------------------
# Per-provider planning
# ---------------------------------------------------------------------------

def _empty_doc(svc: BasePricingProvider, aliases: Iterable[str], present: set) -> Dict[str, Any]:
    return {
        "provider": svc.provider_name,
        "registry_provider": ",".join(sorted(a for a in aliases if a in present)),
        "generated_by": "scripts/pickup_registry_models.py",
        "models": {},
    }


def read_doc(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict) or not isinstance(doc.get("models"), dict):
        raise RuntimeError(f"{path}: not a registry-models document")
    return doc


def render_doc(doc: Dict[str, Any]) -> bytes:
    """The shared on-disk encoding (see base_provider.render_registry_models_doc)."""
    return render_registry_models_doc(doc)


def _round(x: float) -> float:
    return round(x, 12)


def plan_provider(
    svc: BasePricingProvider,
    raw: Dict[str, Any],
    oracle: PriceOracle,
    out_dir: str,
    date: str,
) -> Dict[str, Any]:
    """Decide what the provider's registry-models file should contain. Pure: writes nothing."""
    provider = svc.provider_name
    aliases = _aliases_for(provider) or frozenset()
    present = _registry_providers_in(raw)
    path = str(registry_models_path(provider, out_dir))
    svc.REGISTRY_MODELS_DIR = out_dir  # the runtime merge must read the same file we write
    doc = read_doc(path) or _empty_doc(svc, aliases, present)
    models: Dict[str, Dict[str, Any]] = doc["models"]

    # (1) what the catalogue already has for this provider
    static = getattr(svc, "STATIC_PRICING", None) or {}
    runtime, _ = asyncio.run(svc.get_pricing_with_status())
    existing_names: Dict[str, str] = {}
    for name in list(static) + list(models) + [m.model_name for m in runtime]:
        existing_names.setdefault(name.lower(), name)
    covered_keys: Dict[str, str] = {}
    for lower, name in existing_names.items():
        hit = oracle.lookup(name, provider, require_provider_match=True)
        if hit is not None:
            covered_keys.setdefault(hit.model_key.lower(), name)
    for name, entry in models.items():
        rk = entry.get("registry_key")
        if rk:
            covered_keys.setdefault(rk.lower(), name)

    # same-provider raw entries, in sorted key order for a deterministic report
    raw_same = {
        k: v for k, v in raw.items()
        if isinstance(v, dict) and str(v.get("litellm_provider", "")).lower() in aliases
    }
    chat_keys = [k for k in oracle.records.values() if k.provider and k.provider.lower() in aliases]
    same_keys = [r.model_key for r in chat_keys]
    same_keys_lower = {k.lower() for k in same_keys}

    skipped: List[Dict[str, str]] = []
    candidates: Dict[str, List[Tuple[str, Any]]] = {}  # name.lower() -> [(key, rec)]

    def skip(key: str, reason: str) -> None:
        skipped.append({"registry_key": key, "reason": reason})

    for key in sorted(raw_same):
        v = raw_same[key]
        mode = v.get("mode")
        if mode not in (None, "chat"):
            skip(key, f"mode is '{mode}', not chat")
            continue
        if v.get("input_cost_per_token") is None or v.get("output_cost_per_token") is None:
            skip(key, "missing input or output price")
            continue
        rec = oracle.records.get(key.lower())
        if rec is None:
            skip(key, "not parseable as a priced chat record")
            continue
        tail = _tail(key).lower()
        tok = next((t for t in BasePricingProvider._DISCOVERY_EXCLUDE if t in tail), None)
        if tok:
            skip(key, f"non-chat artefact by name ('{tok}')")
            continue
        if rec.deprecation_date and rec.deprecation_date <= date:
            skip(key, f"deprecated on {rec.deprecation_date}")
            continue
        shallower = deeper_variant_of(key, same_keys)
        if shallower:
            skip(key, f"deeper region/tier variant of {shallower}")
            continue
        if is_fine_tune_template(key):
            skip(key, "fine-tune template key")
            continue
        base = dated_snapshot_of(key, same_keys_lower)
        if base:
            skip(key, f"dated snapshot of {base}")
            continue
        base = latest_alias_of(key, same_keys_lower)
        if base:
            skip(key, f"'-latest' alias of {base}")
            continue
        if is_size_bucket(key):
            skip(key, "provider size-bucket pseudo-model")
            continue
        if key.lower() in covered_keys:
            skip(key, f"already in catalogue as '{covered_keys[key.lower()]}'")
            continue
        inp_1m = rec.input_per_1k * 1000
        out_1m = rec.output_per_1k * 1000
        in_band = (PLAUSIBLE_MIN_INPUT_PER_1M <= inp_1m <= PLAUSIBLE_MAX_INPUT_PER_1M
                   and 0 <= out_1m <= PLAUSIBLE_MAX_OUTPUT_PER_1M)
        if not in_band:
            skip(key, f"outside plausibility band (input ${inp_1m:.4f}/1M, output ${out_1m:.4f}/1M)")
            continue
        name = catalogue_name(key, aliases)
        if name.lower() in existing_names:
            skip(key, f"name already in catalogue ('{existing_names[name.lower()]}')")
            continue
        candidates.setdefault(name.lower(), []).append((key, rec))

    # (3) one key per name — shallowest wins — and the oracle must round-trip
    added: List[Dict[str, Any]] = []
    for name_lower, options in sorted(candidates.items()):
        options.sort(key=lambda kr: (kr[0].count("/"), kr[0]))
        key, rec = options[0]
        for other_key, _ in options[1:]:
            skip(other_key, f"duplicate catalogue name (kept {key})")
        name = catalogue_name(key, aliases)
        hit = oracle.lookup(name, provider, require_provider_match=True)
        if hit is None or hit.model_key.lower() != key.lower():
            skip(key, f"oracle resolves '{name}' to {hit.model_key if hit else 'nothing'}, not this key")
            continue
        entry = {
            "input": _round(rec.input_per_1k),
            "output": _round(rec.output_per_1k),
            "context_window": rec.context_window,
            "supports_vision": bool(rec.supports_vision),
            "supports_function_calling": bool(rec.supports_function_calling),
            "price_source_kind": "registry",
            "registry_key": key,
            "price_as_of": date,
            "added_on": date,
        }
        models[name] = entry
        added.append({"provider": provider, "model": name, "registry_key": key,
                      "input_per_1k": entry["input"], "output_per_1k": entry["output"]})

    # (5) existing entries: re-sync while the registry still backs them
    changes: List[Dict[str, Any]] = []
    redated: List[str] = []
    unbacked: List[Dict[str, Any]] = []
    added_names = {a["model"] for a in added}
    for name in sorted(models):
        if name in added_names:
            continue
        entry = models[name]
        rk = entry.get("registry_key") or ""
        rec = oracle.records.get(rk.lower())
        if rec is None or (rec.deprecation_date and rec.deprecation_date <= date):
            unbacked.append({"provider": provider, "model": name, "registry_key": rk,
                             "price_as_of": entry.get("price_as_of"),
                             "reason": "record gone from registry" if rec is None
                             else f"deprecated on {rec.deprecation_date}"})
            continue
        new_in, new_out = _round(rec.input_per_1k), _round(rec.output_per_1k)
        old_in, old_out = entry.get("input"), entry.get("output")
        if old_in != new_in or old_out != new_out:
            changes.append({"provider": provider, "model": name, "registry_key": rk,
                            "old_input_per_1k": old_in, "new_input_per_1k": new_in,
                            "old_output_per_1k": old_out, "new_output_per_1k": new_out})
            entry["input"], entry["output"] = new_in, new_out
        else:
            redated.append(name)
        entry["context_window"] = rec.context_window
        entry["supports_vision"] = bool(rec.supports_vision)
        entry["supports_function_calling"] = bool(rec.supports_function_calling)
        entry["price_source_kind"] = "registry"
        entry["price_as_of"] = date

    rendered = render_doc(doc)
    before = open(path, "rb").read() if os.path.exists(path) else None
    return {
        "provider": provider,
        "file": os.path.relpath(path, ROOT) if path.startswith(ROOT) else path,
        "path": path,
        "doc": doc,
        "rendered": rendered,
        "changed": rendered != before,
        "registry_chat_records": len(same_keys),
        "catalogue_before": len(runtime),
        "catalogue_after": len(runtime) + len(added),
        "added": added,
        "changes": changes,
        "redated": redated,
        "unbacked": unbacked,
        "skipped": skipped,
    }


def apply_plan(plan: Dict[str, Any]) -> bool:
    """Write the planned document. Returns whether the file's bytes changed."""
    if not plan["changed"]:
        return False
    os.makedirs(os.path.dirname(plan["path"]), exist_ok=True)
    with open(plan["path"], "wb") as fh:
        fh.write(plan["rendered"])
    return True


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _group(rows: List[Dict[str, Any]], key: str = "provider") -> Dict[str, List[Dict[str, Any]]]:
    by: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r[key], []).append(r)
    return by


def _md(text: Any) -> str:
    """Neutralise Markdown in a string copied from the third-party registry.

    Registry keys, ``mode`` values and dates are interpolated into a PR body a
    maintainer is asked to merge; upstream data must render as text, never as
    a table break, a code-span escape or a live link.
    """
    s = str(text)
    for ch, rep in (("\\", "\\\\"), ("|", "\\|"), ("`", "\\`"), ("[", "\\["), ("]", "\\]"),
                    ("<", "&lt;"), (">", "&gt;")):
        s = s.replace(ch, rep)
    return s.replace("\r", " ").replace("\n", " ")


def _code(text: Any) -> str:
    """A code span for a registry-derived string that cannot break out of it."""
    return "`" + _md(text) + "`"


def markdown(report: Dict[str, Any]) -> str:
    plans = report["providers"]
    added = [a for p in plans for a in p["added"]]
    changes = [c for p in plans for c in p["changes"]]
    redated = [(p["provider"], n) for p in plans for n in p["redated"]]
    unbacked = [u for p in plans for u in p["unbacked"]]
    skipped = [(p["provider"], s) for p in plans for s in p["skipped"]]
    out: List[str] = []

    out.append(f"## Added ({len(added)})")
    out.append("Registry-sourced entries: the registry's price, labelled `price_source_kind: registry` "
               "with the registry key and date. Usable in estimates and comparisons; never counted as "
               "provider-confirmed.\n")
    if added:
        for prov, rows in sorted(_group(added).items()):
            out.append(f"### {prov} ({len(rows)})")
            out.append("| Model | Registry key | Input $/1M | Output $/1M |")
            out.append("|---|---|---|---|")
            for a in rows:
                out.append(f"| {_code(a['model'])} | {_code(a['registry_key'])} | {_fmt_per_1m(a['input_per_1k'])} | "
                           f"{_fmt_per_1m(a['output_per_1k'])} |")
            out.append("")
    else:
        out.append("_none_\n")

    out.append(f"## Price changes ({len(changes)})")
    if changes:
        out.append("| Provider | Model | Registry key | Input $/1M (old → new) | Output $/1M (old → new) |")
        out.append("|---|---|---|---|---|")
        for c in changes:
            out.append(f"| {c['provider']} | {_code(c['model'])} | {_code(c['registry_key'])} | "
                       f"{_fmt_per_1m(c['old_input_per_1k'] or 0)} → {_fmt_per_1m(c['new_input_per_1k'])} | "
                       f"{_fmt_per_1m(c['old_output_per_1k'] or 0)} → {_fmt_per_1m(c['new_output_per_1k'])} |")
        out.append("")
    else:
        out.append("_none_\n")

    out.append(f"## Re-dated ({len(redated)})")
    out.append(f"Registry still carries the same price; `price_as_of` set to {report['date']}.\n")
    if redated:
        by: Dict[str, int] = {}
        for prov, _ in redated:
            by[prov] = by.get(prov, 0) + 1
        for prov, n in sorted(by.items()):
            out.append(f"- **{prov}**: {n}")
        out.append("")
    else:
        out.append("_none_\n")

    out.append(f"## No longer backed ({len(unbacked)})")
    out.append("The registry no longer carries (or has deprecated) these. Left untouched with their last "
               "date; they age into stale under the 90-day rule and retire_models.py handles deprecations.\n")
    if unbacked:
        out.append("| Provider | Model | Registry key | Last dated | Why |")
        out.append("|---|---|---|---|---|")
        for u in unbacked:
            out.append(f"| {u['provider']} | {_code(u['model'])} | {_code(u['registry_key'])} | "
                       f"{_md(u['price_as_of'])} | {_md(u['reason'])} |")
        out.append("")
    else:
        out.append("_none_\n")

    out.append(f"## Skipped ({len(skipped)})")
    if skipped:
        by_prov: Dict[str, List[Dict[str, str]]] = {}
        for prov, s in skipped:
            by_prov.setdefault(prov, []).append(s)
        for prov, rows in sorted(by_prov.items()):
            out.append(f"### {prov} ({len(rows)})")
            out.append("| Registry key | Reason |")
            out.append("|---|---|")
            for s in rows:
                out.append(f"| {_code(s['registry_key'])} | {_md(s['reason'])} |")
            out.append("")
    else:
        out.append("_none_\n")

    out.append("## Guards run")
    out.append("Registry: " + _code(report["registry_source"]) + ". Only `data/registry_models/*.json` is written; "
               "no `src/services/*_pricing.py` is touched. Before the PR is opened the job runs:\n")
    for t in report["guards"]:
        out.append(f"- `{t}`")
    return "\n".join(out)


def text_summary(report: Dict[str, Any]) -> str:
    lines = []
    verb = "Wrote" if report["applied"] else "Would write"
    for p in report["providers"]:
        lines.append(f"{p['provider']:18s} registry chat records {p['registry_chat_records']:4d} | "
                     f"catalogue {p['catalogue_before']:4d} -> {p['catalogue_after']:4d} | "
                     f"added {len(p['added']):3d} | changed {len(p['changes']):3d} | re-dated {len(p['redated']):3d} | "
                     f"unbacked {len(p['unbacked']):3d} | skipped {len(p['skipped']):3d}"
                     + ("" if p["changed"] else "  [no change]"))
    n_changed = sum(1 for p in report["providers"] if p["changed"])
    lines.append(f"{verb} {n_changed} file(s) under {report['out_dir']} (date {report['date']}).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    *,
    registry: Optional[str] = None,
    snapshot_only: bool = False,
    providers: Optional[List[str]] = None,
    out_dir: str = DEFAULT_OUT_DIR,
    date: Optional[str] = None,
    apply: bool = False,
    services: Optional[List[BasePricingProvider]] = None,
) -> Dict[str, Any]:
    """Programmatic form of main(); tests call this with a stub provider."""
    date = date or datetime.now(UTC).date().isoformat()
    raw, source = load_registry(registry, snapshot_only)
    oracle = build_oracle(raw, source)
    svcs = registry_backed_services(raw, providers, services=services)
    plans = []
    for svc in svcs:
        plan = plan_provider(svc, raw, oracle, out_dir, date)
        if apply:
            apply_plan(plan)
        plans.append(plan)
    return {
        "date": date,
        "applied": apply,
        "out_dir": out_dir,
        "registry_source": source,
        "guards": GUARD_TESTS,
        "providers": plans,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write data/registry_models/<slug>.json files")
    ap.add_argument("--json", action="store_true", help="print a JSON report instead of text")
    ap.add_argument("--markdown", action="store_true", help="print the PR body")
    ap.add_argument("--providers", default=None,
                    help="comma-separated display names (default: every registry-backed catalogue provider)")
    ap.add_argument("--date", default=None, help="date to stamp (YYYY-MM-DD; default: today UTC)")
    ap.add_argument("--registry", default=None, help="local registry JSON to pick from (tests)")
    ap.add_argument("--snapshot-only", action="store_true", help="use the vendored snapshot, no network")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    args = ap.parse_args()

    providers = args.providers.split(",") if args.providers else None
    report = run(registry=args.registry, snapshot_only=args.snapshot_only, providers=providers,
                 out_dir=os.path.abspath(args.out_dir), date=args.date, apply=args.apply)

    if args.json:
        slim = dict(report)
        slim["providers"] = [{k: v for k, v in p.items() if k not in ("doc", "rendered")} for p in report["providers"]]
        print(json.dumps(slim, indent=1, default=str))
    elif args.markdown:
        print(markdown(report))
    else:
        print(text_summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
