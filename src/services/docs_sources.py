"""Document writer data adapters (v1.89.0): every figure a document prints is gathered here, from a product
data call made during the generation, and recorded in the provenance manifest.

Sources (read-only): the pricing aggregator, the price oracle's drift check, ``get_data_quality``, the
What's New release record, the audit store and the orchestrator store. Never billing.db, conversations,
customer keys, Stripe or live customer usage. The loaders are injected into ``Sources`` so tests use fakes.
"""
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from src.services import whats_new
from src.services.docs_verify import scrub_deep
from src.services.docs_catalogue import (DEFAULT_WINDOW_DAYS, PRICED_STATUSES, Figure, Manifest, ParamError,
                                         fmt_int)

PRICING_CALL = "PricingAggregatorService.get_all_pricing_async"
DRIFT_CALL = "CheckPriceDriftTool.execute"
DQ_CALL = "GetDataQualityTool.execute"
WHATS_NEW_CALL = "static/whats-new release record"
AUDIT_CALL = "AuditStore.latest_report"
BUDGET_CALL = "OrchestratorService.budget_view"
RUNS_CALL = "OrchestratorStore.list_runs"
APPROVALS_CALL = "OrchestratorStore.list_approvals"

DQ_FIELDS = (("total_models", "dq_total_models", "models", "Models in the catalogue"),
             ("confirmed_pct", "dq_confirmed_pct", "percent", "Confirmed prices"),
             ("provider_confirmed", "dq_provider_confirmed", "models", "Provider-confirmed prices"),
             ("registry_sourced", "dq_registry_sourced", "models", "Registry-sourced prices"),
             ("withheld_for_drift", "dq_withheld_for_drift", "models", "Withheld for drift"),
             ("never_priced", "dq_never_priced", "models", "Never priced"),
             ("stale_models", "dq_stale_models", "models", "Stale prices"),
             ("retired_listed", "dq_retired_listed", "models", "Retired, listed"))


def utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _call(m: Manifest, tool: str, scope: str, now: float) -> None:
    m.calls.append({"tool": tool, "scope": scope, "at": utc_iso(now)})


# --------------------------------------------------------------------------- default loaders (real services)

async def load_pricing() -> List[Any]:
    from src.services.pricing_aggregator import get_pricing_aggregator
    rows, _ = await (await get_pricing_aggregator()).get_all_pricing_async(include_unconfirmed=True)
    return list(rows)


async def load_withheld() -> Set[str]:
    """Lower-cased names of models the price oracle currently withholds for drift."""
    from mcp.tools.check_price_drift import CheckPriceDriftTool
    result = await CheckPriceDriftTool().execute({"limit": 200})
    if not result.get("success"):
        return set()
    return {str(f.get("model_name", "")).lower() for f in result.get("findings", [])}


async def load_data_quality() -> Dict[str, Any]:
    from mcp.tools.get_data_quality import GetDataQualityTool
    return await GetDataQualityTool().execute({})


@dataclass
class Sources:
    pricing: Callable[[], Awaitable[List[Any]]] = load_pricing
    withheld: Callable[[], Awaitable[Set[str]]] = load_withheld
    data_quality: Callable[[], Awaitable[Dict[str, Any]]] = load_data_quality
    whats_new_text: Optional[Callable[[], str]] = None
    audit_report: Optional[Callable[[], Awaitable[Optional[Dict[str, Any]]]]] = None
    budget_view: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None
    list_runs: Optional[Callable[[float], Awaitable[List[Dict[str, Any]]]]] = None
    open_approvals: Optional[Callable[[], Awaitable[int]]] = None
    app_version: str = ""


def price_status(p: Any, withheld: Set[str]) -> str:
    """FR5 label for one catalogue row."""
    if getattr(p, "retired_on", None):
        return "retired"
    if not getattr(p, "price_confirmed", True):
        return "withheld" if str(p.model_name).lower() in withheld else "unpriced"
    if getattr(p, "price_is_stale", False):
        return "stale"
    if getattr(p, "price_source_kind", "provider") == "registry":
        return "registry-sourced"
    return "provider-confirmed"


# --------------------------------------------------------------------------- shared: data quality

async def _gather_data_quality(src: Sources, m: Manifest, now: float) -> None:
    dq = await src.data_quality()
    _call(m, DQ_CALL, "catalogue data-quality snapshot", now)
    if not isinstance(dq, dict) or not dq.get("success", True):
        raise RuntimeError("data quality snapshot unavailable")
    snap: Dict[str, Any] = {}
    for key, fid, unit, label in DQ_FIELDS:
        if isinstance(dq.get(key), (int, float)) and not isinstance(dq.get(key), bool):
            snap[key] = dq[key]
            m.add(Figure(fid, float(dq[key]), unit, label, DQ_CALL))
    for key in ("registry_fetched", "stale_pct"):
        if dq.get(key) is not None:
            snap[key] = dq[key]
    if dq.get("registry_fetched"):
        m.dates.append(str(dq["registry_fetched"])[:10])
    m.data_quality = snap


# --------------------------------------------------------------------------- pricing documents

def _find_row(rows: List[Any], name: str) -> Optional[Any]:
    matches = [r for r in rows if str(r.model_name).lower() == name.lower()]
    if not matches:
        return None
    usable = [r for r in matches if getattr(r, "price_confirmed", True) and not getattr(r, "retired_on", None)]
    return (usable or matches)[0]


async def gather_pricing(params: Dict[str, Any], src: Sources, m: Manifest, now: float) -> None:
    rows = await src.pricing()
    _call(m, PRICING_CALL, "all providers, unconfirmed models included", now)
    withheld = await src.withheld()
    _call(m, DRIFT_CALL, "models currently withheld for price drift", now)
    wl_in, wl_out = int(params["input_tokens"]), int(params["output_tokens"])
    m.add(Figure("wl_in", float(wl_in), "tokens", "Workload input tokens", "request parameter"))
    m.add(Figure("wl_out", float(wl_out), "tokens", "Workload output tokens", "request parameter"))
    models: List[Dict[str, Any]] = []
    for i, name in enumerate(params["model_names"]):
        row = _find_row(rows, name)
        m.names.append(name)
        if row is None:  # not in the catalogue at all: an unpriced model, never a guessed price
            models.append({"model": name, "provider": "", "priced": False, "status": "unpriced"})
            continue
        status = price_status(row, withheld)
        provider = str(row.provider)
        m.names.append(provider)
        entry: Dict[str, Any] = {"model": row.model_name, "provider": provider, "priced": False, "status": status}
        if status in PRICED_STATUSES:
            per_in, per_out = float(row.cost_per_input_token) * 1e6, float(row.cost_per_output_token) * 1e6
            as_of = getattr(row, "price_as_of", None)
            common = dict(source_call=PRICING_CALL, price_as_of=as_of, status=status, model=row.model_name,
                          provider=provider)
            m.add(Figure(f"in_{i}", per_in, "USD per 1M input tokens", f"{row.model_name} input price", **common))
            m.add(Figure(f"out_{i}", per_out, "USD per 1M output tokens", f"{row.model_name} output price",
                         **common))
            cost = wl_in * float(row.cost_per_input_token) + wl_out * float(row.cost_per_output_token)
            m.add(Figure(f"cost_{i}", cost, "USD workload cost", f"{row.model_name} workload cost", **common))
            entry.update(priced=True, fin=f"in_{i}", fout=f"out_{i}", cost=f"cost_{i}")
            if as_of:
                m.dates.append(str(as_of)[:10])
        models.append(entry)
    n_priced = sum(1 for e in models if e["priced"])
    m.add(Figure("n_models", float(len(models)), "models", "Models compared", PRICING_CALL))
    m.add(Figure("n_priced", float(n_priced), "models", "Models with a usable price", PRICING_CALL))
    m.meta["models"] = models
    await _gather_data_quality(src, m, now)


# --------------------------------------------------------------------------- release notes

def _parse_range(spec: str, versions: List[str]) -> List[str]:
    """Versions (oldest first) selected by 'latest', 'X.Y.Z' or 'A..B'."""
    spec = spec.strip().lstrip("v")
    ordered = sorted(set(versions), key=whats_new.version_key)
    if not ordered:
        raise ParamError("version_range", "the What's New record has no entries")
    if spec == "latest":
        return [ordered[-1]]
    parts = [p.lstrip("v") for p in spec.split("..")]
    if len(parts) > 2 or not all(re.fullmatch(r"\d+\.\d+\.\d+", p) for p in parts):
        raise ParamError("version_range", "use 'latest', 'X.Y.Z' or 'A.B.C..X.Y.Z'")
    lo, hi = parts[0], parts[-1]
    if whats_new.version_key(lo) > whats_new.version_key(hi):
        raise ParamError("version_range", "the range start is after its end")
    chosen = [v for v in ordered if whats_new.version_key(lo) <= whats_new.version_key(v) <= whats_new.version_key(hi)]
    if not chosen:
        raise ParamError("version_range", "no What's New entry in that range")
    return chosen


async def gather_release(params: Dict[str, Any], src: Sources, m: Manifest, now: float) -> None:
    text = src.whats_new_text() if src.whats_new_text else whats_new.read_file()
    chosen = _parse_range(str(params["version_range"]), whats_new.list_versions(text))
    entries = []
    for v in chosen:
        block = whats_new.extract_entry(text, v)
        entry = {"version": v, "title": whats_new.entry_title(block) or f"v{v}", "date": whats_new.entry_date(block),
                 "tag": whats_new.entry_tag(block), "sections": whats_new.entry_sections(block),
                 "stats": whats_new.entry_stats(block)}
        entries.append(entry)
        m.source_texts.append(f"v{v} {entry['title']} {entry['date']}")
        for _label, items in entry["sections"]:
            m.source_texts.extend(items)
        m.source_texts.extend(entry["stats"])
    _call(m, WHATS_NEW_CALL, "selected release entries only", now)
    m.add(Figure("n_releases", float(len(entries)), "releases", "Releases covered", WHATS_NEW_CALL))
    m.meta.update(entries=entries, first_version=chosen[0], last_version=chosen[-1])


# --------------------------------------------------------------------------- internal status

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


async def gather_status(params: Dict[str, Any], src: Sources, m: Manifest, now: float) -> None:
    days = int(params.get("window_days", DEFAULT_WINDOW_DAYS))
    m.add(Figure("window_days", float(days), "days", "Run window", "request parameter"))
    report = await src.audit_report() if src.audit_report else None
    _call(m, AUDIT_CALL, "latest report, counts and check ids only", now)
    if report:
        counts = report.get("counts") or {}
        for fid, key in (("audit_pass", "pass"), ("audit_warn", "warn"), ("audit_fail", "fail"),
                         ("audit_na", "n_a")):
            m.add(Figure(fid, float(counts.get(key, 0) or 0), "checks", f"Audit checks: {key}", AUDIT_CALL))
        date = utc_iso(float(report.get("created_at") or now))[:10]
        m.dates.append(date)
        m.meta["audit"] = {"report_id": str(report.get("report_id")), "status": str(report.get("status")),
                           "date": date,
                           "failing": [str(c.get("id")) for c in report.get("checks", []) if c.get("status") == "fail"]}
    bud = await src.budget_view() if src.budget_view else {}
    _call(m, BUDGET_CALL, "monthly spend against the caps", now)
    g = (bud or {}).get("global")
    if g:
        m.add(Figure("bud_spent", float(g.get("spent_usd") or 0), "USD", "Global spend this month", BUDGET_CALL))
        m.add(Figure("bud_ceiling", float(g.get("ceiling_usd") or 0), "USD", "Global monthly ceiling", BUDGET_CALL))
        m.add(Figure("bud_pct", float(g.get("pct") or 0), "percent", "Global ceiling used", BUDGET_CALL))
        m.meta["budget_global"] = True
    brows = []
    for w in (bud or {}).get("workflows", []):
        wid = str(w.get("workflow_id"))
        key = _slug(wid)
        m.add(Figure(f"spent_{key}", float(w.get("spent_usd") or 0), "USD", f"{wid} spend", BUDGET_CALL))
        row = {"workflow_id": wid, "spent": f"spent_{key}", "cap": None, "run_cap": None}
        if w.get("monthly_cap_usd"):
            m.add(Figure(f"cap_{key}", float(w["monthly_cap_usd"]), "USD", f"{wid} monthly cap", BUDGET_CALL))
            row["cap"] = f"cap_{key}"
        if w.get("per_run_ceiling_usd"):
            m.add(Figure(f"runcap_{key}", float(w["per_run_ceiling_usd"]), "USD", f"{wid} per-run ceiling",
                         BUDGET_CALL))
            row["run_cap"] = f"runcap_{key}"
        brows.append(row)
    m.meta["budget_rows"] = brows
    runs = await src.list_runs(now - days * 86400) if src.list_runs else []
    _call(m, RUNS_CALL, "run counts by workflow and status", now)
    tally: Dict[tuple, int] = {}
    for r in runs:
        k = (str(r.get("workflow_id")), str(r.get("status")))
        tally[k] = tally.get(k, 0) + 1
    rrows = []
    for (wid, status), n in sorted(tally.items()):
        fid = f"runs_{_slug(wid)}_{_slug(status)}"
        m.add(Figure(fid, float(n), "runs", f"{wid} runs, {status}", RUNS_CALL))
        rrows.append({"workflow_id": wid, "status": status, "fig": fid})
    m.meta["run_rows"] = rrows
    open_n = await src.open_approvals() if src.open_approvals else 0
    _call(m, APPROVALS_CALL, "number of approvals waiting", now)
    m.add(Figure("approvals_open", float(open_n), "approvals", "Approvals waiting", APPROVALS_CALL))
    await _gather_data_quality(src, m, now)


# --------------------------------------------------------------------------- entry point

_GATHERERS = {
    "release_notes_brief": gather_release,
    "pricing_comparison_deck": gather_pricing,
    "pricing_one_pager": gather_pricing,
    "internal_status_report": gather_status,
}


async def gather(doc_type: str, params: Dict[str, Any], src: Sources, now: Optional[float] = None) -> Manifest:
    """Make this generation's product data calls and return the provenance manifest (redacted)."""
    now = time.time() if now is None else now
    m = Manifest(generated_at=utc_iso(now))
    m.dates.append(m.generated_at[:10])
    m.meta["doc_type"] = doc_type
    m.meta["app_version"] = src.app_version
    await _GATHERERS[doc_type](params, src, m, now)
    return Manifest.from_dict(scrub_deep(m.to_dict()))


def llm_table(m: Manifest, limit: int = 8000) -> str:
    """The only content a model sees: the redacted manifest as a plain table (no free customer text)."""
    lines = ["figure | value | unit | label | status | price_as_of"]
    for f in m.figures:
        lines.append(f"{f.id} | {fmt_value(f)} | {f.unit} | {f.label} | {f.status or ''} | {f.price_as_of or ''}")
    for e in m.meta.get("entries", []):
        lines.append(f"release v{e['version']}: {e['title']}")
        for _label, items in e["sections"]:
            lines.extend("- " + it for it in items)
    return "\n".join(lines)[:limit]


def fmt_value(f: Figure) -> str:
    from src.services.docs_catalogue import fmt_fig
    return fmt_fig(f)


__all__ = ("Sources", "gather", "price_status", "llm_table", "fmt_int")
