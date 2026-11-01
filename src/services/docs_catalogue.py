"""Document writer catalogue (v1.89.0): the closed set of document types, their parameters and defaults,
the provenance manifest model and the deterministic outline/template builders.

Closed catalogue (owner decision): ``release_notes_brief``, ``pricing_comparison_deck``,
``pricing_one_pager`` and ``internal_status_report``. Anything else is refused before a run exists.

Deferred, on purpose (recorded here so the reason survives):
- cost-savings report: needs the caller's own-org usage, i.e. live customer data (phase 2);
- sales one-pager, customer changelog email draft: outbound-adjacent, held until the legal pages are live;
- README/landing refresh proposals: repository edits belong to the SDLC pipeline, not an artefact store;
- PPTX/DOCX/PDF binary export: a later renderer; v1 ships printable HTML and Markdown only.

The structured Document is a plain dict (JSON-storable). Every number printed in it is formatted from a
manifest Figure by the helpers below, so the renderers cannot disagree with the manifest; docs_verify
cross-checks the finished text anyway.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

DOC_TYPE_IDS: Tuple[str, ...] = ("release_notes_brief", "pricing_comparison_deck", "pricing_one_pager",
                                 "internal_status_report")

# The four Anthropic models this repo's own sessions run on (CLAUDE.md "Model Selection").
DEFAULT_MODEL_NAMES: Tuple[str, ...] = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5",
                                        "claude-fable-5-1")
DEFAULT_INPUT_TOKENS = 1_000_000
DEFAULT_OUTPUT_TOKENS = 150_000
DEFAULT_AUDIENCE = "Owner"
DEFAULT_WINDOW_DAYS = 30
NOT_AVAILABLE = "price not available"

PRICED_STATUSES = frozenset({"provider-confirmed", "registry-sourced", "stale"})
STATUS_LABELS = ("provider-confirmed", "registry-sourced", "stale", "withheld", "unpriced", "retired")


class ParamError(ValueError):
    """A request parameter is unknown, mistyped or out of bounds (HTTP 422 with the field name)."""

    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(f"{field_name}: {message}")
        self.field = field_name
        self.message = message


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str                 # 'string' | 'integer' | 'string_list'
    default: Any
    description: str
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    min_items: Optional[int] = None
    max_items: Optional[int] = None


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    kind: str                 # 'title' | 'summary' | 'agenda' | 'table' | 'chart' | 'data_quality' | 'sources' | ...
    required: bool = True


@dataclass(frozen=True)
class DocType:
    id: str
    title: str
    kind: str                 # 'deck' | 'document'
    description: str
    params: Tuple[ParamSpec, ...]
    sections: Tuple[SectionSpec, ...]
    data_sources: Tuple[str, ...]


_AUDIENCE = ParamSpec("audience", "string", DEFAULT_AUDIENCE, "Who the document is for (shown on the title)",
                      minimum=1, maximum=60)
_PRICING_PARAMS = (
    ParamSpec("model_names", "string_list", list(DEFAULT_MODEL_NAMES), "Models to compare (2 to 10)",
              min_items=2, max_items=10, maximum=80),
    ParamSpec("input_tokens", "integer", DEFAULT_INPUT_TOKENS, "Workload input tokens used for cost",
              minimum=1, maximum=10 ** 12),
    ParamSpec("output_tokens", "integer", DEFAULT_OUTPUT_TOKENS, "Workload output tokens used for cost",
              minimum=0, maximum=10 ** 12),
    _AUDIENCE,
)

DOC_TYPES: Dict[str, DocType] = {
    "release_notes_brief": DocType(
        "release_notes_brief", "Release notes brief", "document",
        "A short brief of one release or a version range, built only from the What's New record.",
        (ParamSpec("version_range", "string", "latest",
                   "'latest', one version ('1.88.0') or a range ('1.86.0..1.88.0')", minimum=1, maximum=40),
         _AUDIENCE),
        (SectionSpec("executive_summary", "Executive summary", "summary"),
         SectionSpec("releases_overview", "Releases covered", "table"),
         SectionSpec("sources", "Sources", "sources"),
         SectionSpec("freshness", "Freshness", "freshness")),
        ("static/whats-new release record",)),
    "pricing_comparison_deck": DocType(
        "pricing_comparison_deck", "Pricing comparison deck", "deck",
        "A slide deck comparing model prices and the cost of a stated workload, with price status labels.",
        _PRICING_PARAMS,
        (SectionSpec("title", "Title", "title"),
         SectionSpec("executive_summary", "Executive summary", "summary"),
         SectionSpec("agenda", "Agenda", "agenda"),
         SectionSpec("price_table", "Model prices", "table"),
         SectionSpec("cost_ranking", "Cost at your workload", "table"),
         SectionSpec("cost_chart", "Cost comparison chart", "chart"),
         SectionSpec("methodology", "Methodology and data quality", "data_quality"),
         SectionSpec("sources", "Sources", "sources"),
         SectionSpec("next_steps", "Next steps", "next_steps")),
        ("pricing aggregator", "price oracle drift check", "get_data_quality")),
    "pricing_one_pager": DocType(
        "pricing_one_pager", "Pricing one-pager", "document",
        "The same pricing data as the deck in a one-page document layout.",
        _PRICING_PARAMS,
        (SectionSpec("executive_summary", "Executive summary", "summary"),
         SectionSpec("price_table", "Model prices", "table"),
         SectionSpec("cost_ranking", "Cost at your workload", "table"),
         SectionSpec("cost_chart", "Cost comparison chart", "chart"),
         SectionSpec("data_quality", "Data quality", "data_quality"),
         SectionSpec("sources", "Sources", "sources"),
         SectionSpec("freshness", "Freshness", "freshness")),
        ("pricing aggregator", "price oracle drift check", "get_data_quality")),
    "internal_status_report": DocType(
        "internal_status_report", "Internal status report", "document",
        "Operator-only status: latest compliance audit, orchestrator budget and run counts. Counts and ids only.",
        (ParamSpec("window_days", "integer", DEFAULT_WINDOW_DAYS, "Days of orchestrator runs to count",
                   minimum=1, maximum=90), _AUDIENCE),
        (SectionSpec("executive_summary", "Executive summary", "summary"),
         SectionSpec("audit", "Compliance audit", "table"),
         SectionSpec("budget", "Orchestrator budget", "table"),
         SectionSpec("runs", "Orchestrator runs", "table"),
         SectionSpec("data_quality", "Data quality", "data_quality"),
         SectionSpec("sources", "Sources", "sources"),
         SectionSpec("freshness", "Freshness", "freshness")),
        ("audit store", "orchestrator store", "get_data_quality")),
}


def get_type(doc_type: str) -> DocType:
    if doc_type not in DOC_TYPES:
        raise ParamError("doc_type", "not in the catalogue (" + ", ".join(DOC_TYPE_IDS) + ")")
    return DOC_TYPES[doc_type]


def required_section_ids(doc_type: str) -> List[str]:
    return [s.id for s in get_type(doc_type).sections if s.required]


def describe_types() -> List[Dict[str, Any]]:
    """GET /documents/types: the catalogue with parameters, defaults and sections."""
    out = []
    for t in DOC_TYPES.values():
        out.append({
            "doc_type": t.id, "title": t.title, "kind": t.kind, "description": t.description,
            "formats": ["html", "markdown"], "data_sources": list(t.data_sources),
            "params": [{k: v for k, v in asdict(p).items() if v is not None} for p in t.params],
            "defaults": {p.name: p.default for p in t.params},
            "sections": [{"id": s.id, "title": s.title} for s in t.sections],
        })
    return out


def validate_params(doc_type: str, params: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """Return (resolved params with defaults applied, names that fell back to the default)."""
    t = get_type(doc_type)
    params = params or {}
    if not isinstance(params, dict):
        raise ParamError("params", "must be an object")
    known = {p.name: p for p in t.params}
    for key in params:
        if key not in known:
            raise ParamError(str(key)[:60], "unknown parameter for " + doc_type)
    resolved: Dict[str, Any] = {}
    defaulted: List[str] = []
    for spec in t.params:
        if spec.name not in params or params[spec.name] is None:
            resolved[spec.name] = list(spec.default) if isinstance(spec.default, list) else spec.default
            defaulted.append(spec.name)
            continue
        value = params[spec.name]
        if spec.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ParamError(spec.name, "must be an integer")
            if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
                raise ParamError(spec.name, f"must be between {spec.minimum} and {spec.maximum}")
        elif spec.type == "string":
            if not isinstance(value, str) or not value.strip():
                raise ParamError(spec.name, "must be a non-empty string")
            value = value.strip()
            if spec.maximum is not None and len(value) > spec.maximum:
                raise ParamError(spec.name, f"must be at most {spec.maximum} characters")
        else:
            if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
                raise ParamError(spec.name, "must be a list of non-empty strings")
            value = list(dict.fromkeys(x.strip() for x in value))  # de-duplicated, order kept
            if spec.min_items is not None and len(value) < spec.min_items \
                    or spec.max_items is not None and len(value) > spec.max_items:
                raise ParamError(spec.name, f"needs {spec.min_items} to {spec.max_items} distinct entries")
            if spec.maximum is not None and any(len(x) > spec.maximum for x in value):
                raise ParamError(spec.name, f"entries must be at most {spec.maximum} characters")
        resolved[spec.name] = value
    return resolved, defaulted


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

@dataclass
class Figure:
    id: str
    value: float
    unit: str
    label: str
    source_call: str
    price_as_of: Optional[str] = None
    status: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None


@dataclass
class Manifest:
    generated_at: str                                   # ISO UTC, set once at gather
    figures: List[Figure] = field(default_factory=list)
    calls: List[Dict[str, str]] = field(default_factory=list)
    data_quality: Dict[str, Any] = field(default_factory=dict)
    source_texts: List[str] = field(default_factory=list)   # verbatim record text numbers may come from
    names: List[str] = field(default_factory=list)          # model/provider names (digits inside are identifiers)
    dates: List[str] = field(default_factory=list)          # ISO dates that may appear in the text
    meta: Dict[str, Any] = field(default_factory=dict)

    def add(self, fig: Figure) -> Figure:
        self.figures.append(fig)
        return fig

    def get(self, fig_id: str) -> Figure:
        for f in self.figures:
            if f.id == fig_id:
                return f
        raise KeyError(fig_id)

    def has(self, fig_id: str) -> bool:
        return any(f.id == fig_id for f in self.figures)

    def to_dict(self) -> Dict[str, Any]:
        return {"generated_at": self.generated_at, "figures": [asdict(f) for f in self.figures],
                "calls": list(self.calls), "data_quality": dict(self.data_quality),
                "source_texts": list(self.source_texts), "names": list(self.names), "dates": list(self.dates),
                "meta": dict(self.meta)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Manifest":
        return cls(generated_at=d.get("generated_at", ""), figures=[Figure(**f) for f in d.get("figures", [])],
                   calls=list(d.get("calls", [])), data_quality=dict(d.get("data_quality", {})),
                   source_texts=list(d.get("source_texts", [])), names=list(d.get("names", [])),
                   dates=list(d.get("dates", [])), meta=dict(d.get("meta", {})))


# ---------------------------------------------------------------------------
# Formatting (the only place numbers become text)
# ---------------------------------------------------------------------------

def fmt_usd(v: float) -> str:
    return f"${v:,.2f}" if abs(v) >= 0.01 or v == 0 else f"${v:.4f}"


def fmt_int(v: float) -> str:
    return f"{int(round(v)):,}"


def fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


def fmt_fig(f: Figure) -> str:
    if f.unit.startswith("USD"):
        return fmt_usd(f.value)
    if f.unit == "percent":
        return fmt_pct(f.value)
    return fmt_int(f.value)


def _fv(m: Manifest, fig_id: str) -> str:
    return fmt_fig(m.get(fig_id))


def date_of(iso: str) -> str:
    return (iso or "")[:10]


# ---------------------------------------------------------------------------
# Document construction
# ---------------------------------------------------------------------------

def section(sid: str, title: str, kind: str = "body", *, paragraphs: Optional[List[str]] = None,
            bullets: Optional[List[str]] = None, table: Optional[Dict[str, Any]] = None,
            chart: Optional[Dict[str, Any]] = None, notes: str = "") -> Dict[str, Any]:
    return {"id": sid, "title": title, "kind": kind, "paragraphs": list(paragraphs or []),
            "bullets": list(bullets or []), "table": table, "chart": chart, "notes": notes, "extra_tables": []}


def table(columns: List[str], rows: List[List[str]], caption: str = "") -> Dict[str, Any]:
    return {"columns": columns, "rows": rows, "caption": caption}


def priced_models(m: Manifest) -> List[Dict[str, Any]]:
    return list(m.meta.get("models", []))


def _llm_label(llm_mode: str, model: Optional[str], reason: Optional[str]) -> str:
    if llm_mode == "llm" and model:
        return f"Narrative drafted by {model}; every figure is checked against the provenance manifest."
    return f"Data-only edition (no narrative model was used): {reason or 'requested'}"


def build_document(doc_type: str, params: Dict[str, Any], defaulted: List[str], manifest: Manifest,
                   prose: Optional[Dict[str, Any]], llm: Dict[str, Any], product: str, version: str
                   ) -> Dict[str, Any]:
    """Deterministic document for ``doc_type``. ``prose`` (already verified) only overlays the executive
    summary paragraph and per-section speaker notes; the section list, order and tables never change."""
    t = get_type(doc_type)
    builder = _BUILDERS[doc_type]
    secs, footnotes, freshness = builder(params, defaulted, manifest)
    prose = prose or {}
    summary_text = (prose.get("summary") or "").strip()
    notes = prose.get("notes") or {}
    for s in secs:
        if s["id"] == "executive_summary" and summary_text:
            s["paragraphs"] = [summary_text]
        if t.kind == "deck":
            override = (notes.get(s["id"]) or "").strip() if isinstance(notes, dict) else ""
            if override:
                s["notes"] = override
            if not s["notes"].strip():
                s["notes"] = f"Walk through {s['title'].lower()} and confirm the data date before presenting."
    date = date_of(manifest.generated_at)
    mode = llm.get("mode", "data_only")
    label = _llm_label(mode, llm.get("model"), llm.get("reason"))
    title = _title_for(doc_type, params, manifest)
    return {
        "doc_type": doc_type, "kind": t.kind, "title": title,
        "subtitle": f"Prepared for {params.get('audience', DEFAULT_AUDIENCE)} on {date} (UTC)",
        "audience": params.get("audience", DEFAULT_AUDIENCE), "generated_at": manifest.generated_at,
        "sections": secs, "footnotes": footnotes, "freshness": freshness,
        "label": label, "llm_mode": "llm" if mode == "llm" else "data_only",
        "product": product, "version": version,
        "footer": f"Generated by {product} v{version} on {date} (UTC). {label}",
        "params": {k: params[k] for k in params}, "defaulted": list(defaulted),
    }


def _title_for(doc_type: str, params: Dict[str, Any], m: Manifest) -> str:
    if doc_type == "release_notes_brief":
        a, b = m.meta.get("first_version"), m.meta.get("last_version")
        return f"Release notes brief: v{a}" if a == b else f"Release notes brief: v{a} to v{b}"
    return get_type(doc_type).title


# -- shared section builders ------------------------------------------------

def _params_table(params: Dict[str, Any], defaulted: List[str], m: Manifest) -> Dict[str, Any]:
    rows = []
    for name, value in params.items():
        if name == "input_tokens":
            shown = _fv(m, "wl_in")
        elif name == "output_tokens":
            shown = _fv(m, "wl_out")
        elif isinstance(value, list):
            shown = ", ".join(str(x) for x in value)
        elif isinstance(value, int):
            shown = fmt_int(value)
        else:
            shown = str(value)
        rows.append([name, shown, "default" if name in defaulted else "requested"])
    return table(["Parameter", "Value", "Origin"], rows, "Parameters used (defaults are marked)")


def _dq_table(m: Manifest) -> Dict[str, Any]:
    rows = []
    for fid, label in (("dq_total_models", "Models in the catalogue"), ("dq_confirmed_pct", "Confirmed prices"),
                       ("dq_provider_confirmed", "Provider-confirmed"), ("dq_registry_sourced", "Registry-sourced"),
                       ("dq_withheld_for_drift", "Withheld for drift"), ("dq_never_priced", "Never priced"),
                       ("dq_stale_models", "Stale prices"), ("dq_retired_listed", "Retired, listed")):
        if m.has(fid):
            rows.append([label, _fv(m, fid)])
    return table(["Metric", "Value"], rows, "Data-quality snapshot taken during this generation")


def _sources_section(m: Manifest, notes: str) -> Dict[str, Any]:
    rows = [[c.get("tool", ""), c.get("scope", ""), c.get("at", "")] for c in m.calls]
    return section("sources", "Sources", "sources",
                   paragraphs=["Every figure in this document came from the product data calls below, made "
                               "during this generation. The full manifest is stored with the artefact."],
                   table=table(["Data call", "Scope", "Called at (UTC)"], rows), notes=notes)


def _freshness(m: Manifest, extra: str = "") -> str:
    reg = m.data_quality.get("registry_fetched")
    parts = [f"Data read from the product on {date_of(m.generated_at)} (UTC)."]
    if extra:
        parts.append(extra)
    if reg:
        parts.append(f"Reference price registry snapshot fetched {reg}.")
    return " ".join(parts)


# -- pricing ------------------------------------------------------------------

def _price_cell(row: Dict[str, Any], m: Manifest, key: str) -> str:
    return _fv(m, row[key]) if row["priced"] else NOT_AVAILABLE


def _pricing_sections(params, defaulted, m: Manifest, deck: bool):
    rows = priced_models(m)
    priced = [r for r in rows if r["priced"]]
    ranked = sorted(priced, key=lambda r: (m.get(r["cost"]).value, r["model"]))
    n_models, n_priced = _fv(m, "n_models"), _fv(m, "n_priced")
    wl_in, wl_out = _fv(m, "wl_in"), _fv(m, "wl_out")
    summary = [f"This document compares {n_models} models. {n_priced} of them have a usable price; "
               f"cost is calculated for a workload of {wl_in} input tokens and {wl_out} output tokens."]
    if ranked:
        lo, hi = ranked[0], ranked[-1]
        summary.append(f"The lowest cost is {lo['model']} at {_fv(m, lo['cost'])}; the highest is "
                       f"{hi['model']} at {_fv(m, hi['cost'])}.")
    if len(priced) < len(rows):
        summary.append("Models without a confirmed price are shown as 'price not available' and are left out "
                       "of the ranking and chart.")
    price_tbl = table(
        ["Model", "Provider", "Input per 1M tokens", "Output per 1M tokens", "Status", "Price as of"],
        [[r["model"], r["provider"] or "unknown", _price_cell(r, m, "fin"), _price_cell(r, m, "fout"),
          r["status"], (m.get(r["fin"]).price_as_of or "undated") if r["priced"] else "-"] for r in rows],
        "Prices in USD per 1 million tokens")
    rank_tbl = table(["Model", "Workload cost", "Status"],
                     [[r["model"], _fv(m, r["cost"]), r["status"]] for r in ranked],
                     "Cheapest first; models without a usable price are excluded")
    statuses = []
    for r in ranked:
        if r["status"] not in statuses:
            statuses.append(r["status"])
    chart = {"title": "Workload cost by model", "x_label": "Model", "y_label": "Workload cost (USD)",
             "unit": "USD", "legend": statuses,
             "bars": [{"label": r["model"], "fig": r["cost"], "value": m.get(r["cost"]).value,
                       "status": r["status"]} for r in ranked]}
    footnotes = [f"{r['model']}: price as of {m.get(r['fin']).price_as_of or 'undated'} ({r['status']})"
                 for r in priced] + [f"{r['model']}: {r['status']}, {NOT_AVAILABLE}" for r in rows if not r["priced"]]
    method = ["Prices are read from the product's pricing catalogue in USD per 1 million tokens. A model's "
              "status says how far to trust it: provider-confirmed, registry-sourced or stale prices carry a "
              "number; withheld, unpriced and retired models do not.",
              "Workload cost = input tokens x input price + output tokens x output price."]
    oldest = min((m.get(r["fin"]).price_as_of for r in priced if m.get(r["fin"]).price_as_of), default=None)
    fresh = _freshness(m, f"The oldest price shown was confirmed {oldest}." if oldest else "")
    secs: List[Dict[str, Any]] = []
    tn = {  # speaker notes for decks (data-only text; a model may replace them after verification)
        "title": "Open with the date and audience; state that every figure comes from the product's data.",
        "executive_summary": "Read the headline: the cheapest and the most expensive option at this workload.",
        "agenda": "Run through the order of the deck; there are no surprises after the chart.",
        "price_table": "Point out the status column; only priced rows feed the ranking.",
        "cost_ranking": "Explain that the ranking is cheapest first and excludes anything without a usable price.",
        "cost_chart": "Bars are the same values as the ranking table; colour shows the price status.",
        "methodology": "Explain how a price earns its status and quote the data-quality snapshot as read today.",
        "sources": "Every figure traces to a product data call made while generating this deck.",
        "next_steps": "Agree who owns the decision and when to regenerate with fresh data.",
    }
    if deck:
        secs.append(section("title", get_type("pricing_comparison_deck").title, "title",
                            paragraphs=[f"Prepared for {params.get('audience', DEFAULT_AUDIENCE)}",
                                        f"Generated {date_of(m.generated_at)} (UTC)",
                                        f"Workload: {wl_in} input and {wl_out} output tokens"],
                            notes=tn["title"]))
    secs.append(section("executive_summary", "Executive summary", "summary", paragraphs=summary,
                        notes=tn["executive_summary"]))
    if deck:
        secs.append(section("agenda", "Agenda", "agenda",
                            bullets=["Model prices", "Cost at your workload", "Cost comparison chart",
                                     "Methodology and data quality", "Sources", "Next steps"], notes=tn["agenda"]))
    secs.append(section("price_table", "Model prices", "table", table=price_tbl, notes=tn["price_table"]))
    secs.append(section("cost_ranking", "Cost at your workload", "table", table=rank_tbl, notes=tn["cost_ranking"]))
    secs.append(section("cost_chart", "Cost comparison chart", "chart", chart=chart, notes=tn["cost_chart"]))
    if deck:
        secs.append(section("methodology", "Methodology and data quality", "data_quality", paragraphs=method,
                            table=_dq_table(m), notes=tn["methodology"]))
        secs[-1]["extra_tables"] = [_params_table(params, defaulted, m)]
    else:
        secs.append(section("data_quality", "Data quality", "data_quality", paragraphs=method[:1],
                            table=_dq_table(m)))
        secs[-1]["extra_tables"] = [_params_table(params, defaulted, m)]
    secs.append(_sources_section(m, tn["sources"]))
    if deck:
        secs.append(section("next_steps", "Next steps", "next_steps",
                            bullets=["Regenerate this deck before it is shared so the figures are current.",
                                     "Review any model shown as 'price not available' before relying on it.",
                                     "Re-run with your own workload to see the cost at your volume."],
                            notes=tn["next_steps"]))
    else:
        secs.append(section("freshness", "Freshness", "freshness", paragraphs=[fresh]))
    return secs, (footnotes if not deck else []), fresh


def _deck_builder(params, defaulted, m):
    return _pricing_sections(params, defaulted, m, deck=True)


def _onepager_builder(params, defaulted, m):
    return _pricing_sections(params, defaulted, m, deck=False)


# -- release notes --------------------------------------------------------------

def _release_builder(params, defaulted, m: Manifest):
    entries = m.meta.get("entries", [])
    first, last = m.meta.get("first_version"), m.meta.get("last_version")
    n = _fv(m, "n_releases")
    titles = "; ".join(f"v{e['version']}: {e['title']}" for e in entries[:6])
    summary = [f"This brief covers {n} release(s), from v{first} to v{last}. {titles}."]
    secs = [section("executive_summary", "Executive summary", "summary", paragraphs=summary),
            section("releases_overview", "Releases covered", "table",
                    table=table(["Version", "Date", "Type", "Title"],
                                [["v" + e["version"], e["date"], e["tag"], e["title"]] for e in entries]))]
    for e in entries:
        bullets = []
        for label, items in e["sections"]:
            bullets += [(f"{label}: " if label else "") + it for it in items]
        secs.append(section("release_" + e["version"].replace(".", "_"), f"v{e['version']}: {e['title']}", "release",
                            bullets=bullets + [f"Stat: {s}" for s in e.get("stats", [])]))
    secs.append(_sources_section(m, ""))
    secs.append(section("freshness", "Freshness", "freshness", paragraphs=[_freshness(m)]))
    return secs, [], _freshness(m)


# -- internal status ---------------------------------------------------------------

def _status_builder(params, defaulted, m: Manifest):
    meta = m.meta
    audit = meta.get("audit")
    if audit:
        a_rows = [["Overall status", str(audit["status"])], ["Passed", _fv(m, "audit_pass")],
                  ["Warnings", _fv(m, "audit_warn")], ["Failed", _fv(m, "audit_fail")],
                  ["Not applicable", _fv(m, "audit_na")], ["Report", f"{audit['report_id']} ({audit['date']})"]]
        a_par = [f"Latest compliance audit report {audit['report_id']} from {audit['date']}: {audit['status']}."]
        if audit.get("failing"):
            a_par.append("Failing checks: " + ", ".join(audit["failing"]) + ".")
    else:
        a_rows, a_par = [["Overall status", "no report yet"]], ["No compliance audit report exists yet."]
    b_rows = [[w["workflow_id"], _fv(m, w["spent"]), _fv(m, w["cap"]) if w["cap"] else "no cap",
               _fv(m, w["run_cap"]) if w["run_cap"] else "no cap"] for w in meta.get("budget_rows", [])]
    r_rows = [[r["workflow_id"], r["status"], _fv(m, r["fig"])] for r in meta.get("run_rows", [])]
    gl = meta.get("budget_global")
    b_par = [f"Global spend this month is {_fv(m, 'bud_spent')} of a {_fv(m, 'bud_ceiling')} ceiling "
             f"({_fv(m, 'bud_pct')})."] if gl else []
    secs = [
        section("executive_summary", "Executive summary", "summary", paragraphs=[
            (a_par[0] + " " if audit else "No audit report exists yet. ") + (b_par[0] + " " if b_par else "")
            + f"{_fv(m, 'approvals_open')} approval(s) are waiting for the owner."]),
        section("audit", "Compliance audit", "table", paragraphs=a_par, table=table(["Item", "Value"], a_rows)),
        section("budget", "Orchestrator budget", "table", paragraphs=b_par,
                table=table(["Workflow", "Spent this month", "Monthly cap", "Per-run ceiling"], b_rows)),
        section("runs", "Orchestrator runs", "table",
                paragraphs=[f"Runs started in the last {_fv(m, 'window_days')} days, by workflow and status."],
                table=table(["Workflow", "Status", "Runs"], r_rows)),
        section("data_quality", "Data quality", "data_quality", table=_dq_table(m)),
        _sources_section(m, ""),
        section("freshness", "Freshness", "freshness", paragraphs=[_freshness(m)]),
    ]
    return secs, [], _freshness(m)


_BUILDERS = {
    "release_notes_brief": _release_builder,
    "pricing_comparison_deck": _deck_builder,
    "pricing_one_pager": _onepager_builder,
    "internal_status_report": _status_builder,
}
