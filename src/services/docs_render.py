"""Document writer renderers (v1.89.0): one structured Document -> self-contained printable HTML, or Markdown.

Deterministic: the same document and status produce the same bytes. The HTML is a single file with no
script, no web font and no external URL; a deck is one ``<section class="slide">`` per slide with its
speaker notes, with page breaks so "print to PDF" from a browser yields a deck. Charts are inline SVG
generated here from the same values as the tables next to them (never the sole artefact).

Brand kit: ``BRAND`` copies the ``:root`` design tokens of static/index.html (a test pins the two together,
so a design-token change fails loudly) and adds a print palette.
"""
from html import escape
from typing import Any, Dict, List

from src.services.docs_catalogue import fmt_usd

BRAND: Dict[str, str] = {
    "--bg": "#0f1117", "--surface": "#1a1d27", "--surface2": "#22263a", "--border": "#2e3347",
    "--accent": "#7c6af7", "--text": "#e2e8f0", "--muted": "#8892a4", "--success": "#48bb78",
    "--warn": "#ed8936", "--danger": "#f56565", "--radius": "10px",
}
PRINT = {"bg": "#ffffff", "text": "#1a1d27", "muted": "#4a5568", "border": "#cbd5e0"}
STATUS_COLORS = {"provider-confirmed": BRAND["--accent"], "registry-sourced": BRAND["--success"],
                 "stale": BRAND["--warn"]}

_CSS = """
:root{%(root)s}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.6 'Segoe UI',system-ui,sans-serif}
main{max-width:960px;margin:0 auto;padding:24px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:20px;margin:0 0 12px;color:var(--accent)}h3{font-size:14px;margin:16px 0 6px}
.subtitle,.footer,.caption,.notes,.footnotes{color:var(--muted);font-size:12px}
.banner{border:1px solid var(--warn);color:var(--warn);border-radius:var(--radius);padding:6px 12px;margin-bottom:12px;font-weight:600}
.banner.discarded{border-color:var(--danger);color:var(--danger)}
.label{display:inline-block;border:1px solid var(--border);border-radius:var(--radius);padding:2px 10px;margin:8px 0;font-size:12px}
.slide,.block{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:24px;margin:16px 0}
.slide{min-height:420px}
table{border-collapse:collapse;width:100%%;margin:8px 0;font-size:13px}
th,td{border:1px solid var(--border);padding:6px 10px;text-align:left}th{background:var(--surface2)}
.na{color:var(--muted);font-style:italic}
.notes{border-top:1px dashed var(--border);margin-top:16px;padding-top:8px}
svg text{fill:var(--text);font-family:inherit}
@media print{
  body{background:%(pbg)s;color:%(ptext)s}main{max-width:none;padding:0}
  .slide,.block{background:%(pbg)s;border-color:%(pborder)s;break-inside:avoid}
  .slide{page-break-after:always;break-after:page}
  h2{color:%(ptext)s}th{background:#edf2f7}th,td{border-color:%(pborder)s}
  .subtitle,.footer,.caption,.notes,.footnotes,.na{color:%(pmuted)s}
  svg text{fill:%(ptext)s}
}
""" % {"root": ";".join(f"{k}:{v}" for k, v in BRAND.items()), "pbg": PRINT["bg"], "ptext": PRINT["text"],
       "pmuted": PRINT["muted"], "pborder": PRINT["border"]}

_BANNERS = {"draft": ("banner", "DRAFT - not approved"),
            "gathering": ("banner", "DRAFT - not approved"),
            "discarded": ("banner discarded", "DISCARDED - rejected by the owner"),
            "failed": ("banner discarded", "FAILED - not a usable document")}


def _e(x: Any) -> str:
    return escape(str(x), quote=True)


def _table_html(t: Dict[str, Any]) -> str:
    head = "".join(f"<th>{_e(c)}</th>" for c in t["columns"])
    body = []
    for row in t["rows"]:
        cells = "".join(f'<td class="na">{_e(c)}</td>' if str(c) == "price not available" else f"<td>{_e(c)}</td>"
                        for c in row)
        body.append(f"<tr>{cells}</tr>")
    cap = f'<div class="caption">{_e(t["caption"])}</div>' if t.get("caption") else ""
    return f"{cap}<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def chart_svg(chart: Dict[str, Any]) -> str:
    """Inline SVG bar chart: title, axis labels with units, legend, brand palette. Values are the table's."""
    bars: List[Dict[str, Any]] = chart["bars"]
    w, h, left, bottom, top = 720, 360, 90, 110, 50
    plot_w, plot_h = w - left - 20, h - top - bottom
    vmax = max([b["value"] for b in bars] + [0.0]) or 1.0
    n = max(len(bars), 1)
    slot = plot_w / n
    bw = min(slot * 0.6, 80)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="100%" role="img" '
             f'aria-labelledby="chart-title">',
             f'<title id="chart-title">{_e(chart["title"])}</title>',
             f'<text x="{w / 2}" y="24" text-anchor="middle" font-size="16" font-weight="600">{_e(chart["title"])}</text>',
             f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="{BRAND["--muted"]}"/>',
             f'<line x1="{left}" y1="{top + plot_h}" x2="{w - 20}" y2="{top + plot_h}" stroke="{BRAND["--muted"]}"/>',
             f'<text x="{left - 8}" y="{top + plot_h + 4}" text-anchor="end" font-size="11">{_e(fmt_usd(0.0))}</text>',
             f'<text x="{left - 8}" y="{top + 4}" text-anchor="end" font-size="11">{_e(fmt_usd(vmax))}</text>',
             f'<text x="16" y="{top + plot_h / 2}" text-anchor="middle" font-size="12" '
             f'transform="rotate(-90 16 {top + plot_h / 2})">{_e(chart["y_label"])}</text>',
             f'<text x="{left + plot_w / 2}" y="{h - 8}" text-anchor="middle" font-size="12">{_e(chart["x_label"])}</text>']
    for i, b in enumerate(bars):
        bh = plot_h * (b["value"] / vmax)
        x = left + slot * i + (slot - bw) / 2
        y = top + plot_h - bh
        color = STATUS_COLORS.get(b.get("status"), BRAND["--accent"])
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="{color}" '
                     f'data-value="{b["value"]!r}"><title>{_e(b["label"])}: {_e(fmt_usd(b["value"]))}</title></rect>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" font-size="11">'
                     f'{_e(fmt_usd(b["value"]))}</text>')
        parts.append(f'<text x="{x + bw / 2:.1f}" y="{top + plot_h + 14}" text-anchor="end" font-size="11" '
                     f'transform="rotate(-30 {x + bw / 2:.1f} {top + plot_h + 14})">{_e(b["label"])}</text>')
    lx = left
    for status in chart.get("legend", []):
        parts.append(f'<rect x="{lx}" y="{h - 30}" width="10" height="10" fill="{STATUS_COLORS.get(status, BRAND["--accent"])}"/>'
                     f'<text x="{lx + 14}" y="{h - 21}" font-size="11">{_e(status)}</text>')
        lx += 30 + 7 * len(status)
    parts.append("</svg>")
    return "".join(parts)


def _section_body(s: Dict[str, Any]) -> str:
    out = []
    for p in s.get("paragraphs", []):
        out.append(f"<p>{_e(p)}</p>")
    if s.get("bullets"):
        out.append("<ul>" + "".join(f"<li>{_e(b)}</li>" for b in s["bullets"]) + "</ul>")
    for t in [s.get("table")] + list(s.get("extra_tables", [])):
        if t:
            out.append(_table_html(t))
    if s.get("chart"):
        out.append(chart_svg(s["chart"]))
    return "".join(out)


def render_html(doc: Dict[str, Any], status: str = "draft") -> str:
    deck = doc["kind"] == "deck"
    banner = ""
    if status in _BANNERS:
        cls, text = _BANNERS[status]
        banner = f'<div class="{cls}">{_e(text)}</div>'
    blocks = []
    for s in doc["sections"]:
        if deck:
            notes = f'<aside class="notes"><h3>Speaker notes</h3><p>{_e(s["notes"])}</p></aside>'
            heading = "" if s["kind"] == "title" else f"<h2>{_e(s['title'])}</h2>"
            if s["kind"] == "title":
                heading = f"<h1>{_e(doc['title'])}</h1><div class=\"subtitle\">{_e(doc['subtitle'])}</div>"
            blocks.append(f'<section class="slide" id="slide-{_e(s["id"])}">{heading}{_section_body(s)}{notes}</section>')
        else:
            blocks.append(f'<section class="block" id="{_e(s["id"])}"><h2>{_e(s["title"])}</h2>{_section_body(s)}</section>')
    foot = ""
    if doc.get("footnotes"):
        foot = '<div class="footnotes"><h3>Footnotes</h3><ol>' + "".join(
            f"<li>{_e(f)}</li>" for f in doc["footnotes"]) + "</ol></div>"
    head = "" if deck else f"<h1>{_e(doc['title'])}</h1><div class=\"subtitle\">{_e(doc['subtitle'])}</div>"
    fresh = "" if deck else f'<p class="subtitle">{_e(doc["freshness"])}</p>'
    return ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"UTF-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{_e(doc['title'])}</title><style>{_CSS}</style></head><body><main>"
            f"{banner}{head}<div class=\"label\">{_e(doc['label'])}</div>{''.join(blocks)}{foot}{fresh}"
            f"<div class=\"footer\">{_e(doc['footer'])}</div></main></body></html>\n")


def _md_cell(x: Any) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ")


def _md_table(t: Dict[str, Any]) -> List[str]:
    lines = []
    if t.get("caption"):
        lines += [f"_{t['caption']}_", ""]
    lines.append("| " + " | ".join(_md_cell(c) for c in t["columns"]) + " |")
    lines.append("|" + "|".join(" --- " for _ in t["columns"]) + "|")
    lines += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in t["rows"]]
    return lines + [""]


def render_markdown(doc: Dict[str, Any], status: str = "draft") -> str:
    lines: List[str] = []
    if status in _BANNERS:
        lines += [f"> **{_BANNERS[status][1]}**", ""]
    lines += [f"# {doc['title']}", "", doc["subtitle"], "", f"_{doc['label']}_", ""]
    for s in doc["sections"]:
        if s["kind"] == "title":
            lines += [f"{p}  " for p in s["paragraphs"]] + [""]
        else:
            lines += [f"## {s['title']}", ""]
            for p in s["paragraphs"]:
                lines += [p, ""]
            lines += [f"- {b}" for b in s["bullets"]] + ([""] if s["bullets"] else [])
            for t in [s.get("table")] + list(s.get("extra_tables", [])):
                if t:
                    lines += _md_table(t)
            if s.get("chart"):
                c = s["chart"]
                lines += [f"**{c['title']}** ({c['y_label']} by {c['x_label'].lower()}; legend: "
                          f"{', '.join(c.get('legend', [])) or 'none'})", ""]
                lines += _md_table({"columns": [c["x_label"], c["y_label"], "Status"],
                                    "rows": [[b["label"], fmt_usd(b["value"]), b["status"]] for b in c["bars"]],
                                    "caption": "Chart data (the same values as the table above)"})
        if doc["kind"] == "deck":
            lines += [f"> Speaker notes: {s['notes']}", ""]
    if doc.get("footnotes"):
        lines += ["### Footnotes", ""] + [f"{i}. {f}" for i, f in enumerate(doc["footnotes"], 1)] + [""]
    if doc["kind"] != "deck":
        lines += [doc["freshness"], ""]
    lines += ["---", doc["footer"], ""]
    return "\n".join(lines)


def content_type(fmt: str) -> str:
    return "text/html; charset=utf-8" if fmt == "html" else "text/markdown; charset=utf-8"
