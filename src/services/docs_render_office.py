"""Office renderers (v1.90.0): the stored Document model -> .pptx (decks) or .docx (documents, deck handouts).

Rendered on download from the same JSON the HTML/Markdown editions use; nothing binary is stored. Every
string printed here is already in the validated document (titles, paragraphs, bullets, table cells, chart
bars, notes, footnotes), so the files cannot carry a figure the provenance manifest did not back. No
pictures and no rasterised SVG: a deck's chart is a native pptx column chart, a Word chart is its data table.
"""
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional

from docx import Document as WordDocument
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt as DPt, RGBColor as DRGB
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches, Pt

from src.services.docs_render import (BRAND, PRINT, STATUS_COLORS, allowed_formats, banner_text,
                                      chart_table)

NA_TEXT = "price not available"
HTML_EDITION_LINE = "The chart for this table is in the HTML edition."
_DANGER_STATUSES = ("discarded", "failed")


class FormatNotAvailable(Exception):
    """A known format token that this kind of document does not offer (the route answers 415)."""

    def __init__(self, kind: str, fmt: str, allowed: List[str]):
        super().__init__(f"{fmt} is not available for kind '{kind}'")
        self.kind, self.fmt, self.allowed = kind, fmt, list(allowed)


def _hex(token: str) -> str:
    return token.lstrip("#")


def _rgb(token: str) -> RGBColor:
    return RGBColor.from_string(_hex(token))


def _drgb(token: str) -> DRGB:
    return DRGB.from_string(_hex(token))


def _generated(doc: Dict[str, Any]) -> datetime:
    raw = str(doc.get("generated_at") or "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime(1970, 1, 1)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _tables(s: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [t for t in [s.get("table")] + list(s.get("extra_tables") or []) if t]


def _check(doc: Dict[str, Any], fmt: str) -> None:
    allowed = allowed_formats(doc.get("kind"))
    if fmt not in allowed:
        raise FormatNotAvailable(str(doc.get("kind")), fmt, allowed)


# ------------------------------------------------------------------ pptx
def _textbox(slide, x, y, cx, cy, lines: List[str], size: int, color: str, bold: bool = False,
             italic: bool = False):
    tb = slide.shapes.add_textbox(x, y, cx, cy)
    tf = tb.text_frame
    tf.word_wrap = True
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        r = p.add_run()
        r.text = line
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.italic = italic
        r.font.color.rgb = _rgb(color)
    return tb


def _pptx_table(slide, t: Dict[str, Any], x, y, cx) -> int:
    """Add a native table and its caption; return the next y (EMU)."""
    cols, rows = list(t["columns"]), t["rows"]
    row_h = Inches(0.32)
    gf = slide.shapes.add_table(len(rows) + 1, len(cols), x, y, cx, row_h * (len(rows) + 1))
    table = gf.table
    for c, name in enumerate(cols):
        cell = table.cell(0, c)
        cell.text = str(name)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(BRAND["--surface2"])
        for r in cell.text_frame.paragraphs[0].runs:
            r.font.size, r.font.bold = Pt(11), True
            r.font.color.rgb = _rgb(BRAND["--text"])
    for ri, row in enumerate(rows):
        for c, val in enumerate(row):
            cell = table.cell(ri + 1, c)
            cell.text = str(val)
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(BRAND["--surface"])
            na = str(val) == NA_TEXT
            for r in cell.text_frame.paragraphs[0].runs:
                r.font.size = Pt(11)
                r.font.italic = na
                r.font.color.rgb = _rgb(BRAND["--muted"] if na else BRAND["--text"])
    y = y + row_h * (len(rows) + 1)
    if t.get("caption"):
        _textbox(slide, x, y, cx, Inches(0.3), [str(t["caption"])], 10, BRAND["--muted"])
        y += Inches(0.32)
    return y + Inches(0.1)


def _pptx_chart(slide, chart: Dict[str, Any], x, y, cx, cy) -> None:
    bars = chart.get("bars") or []
    if not bars:
        _textbox(slide, x, y, cx, Inches(0.4), ["No priced model to chart"], 14, BRAND["--muted"], italic=True)
        return
    cd = CategoryChartData()
    cd.categories = [b["label"] for b in bars]
    cd.add_series(chart["y_label"], [float(b["value"]) for b in bars], number_format="$#,##0.00")
    gf = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, x, y, cx, cy, cd)
    ch = gf.chart
    ch.font.color.rgb = _rgb(BRAND["--text"])
    ch.font.size = Pt(11)
    ch.has_title = True
    ch.chart_title.text_frame.text = chart["title"]
    ch.category_axis.has_title = True
    ch.category_axis.axis_title.text_frame.text = chart["x_label"]
    ch.value_axis.has_title = True
    ch.value_axis.axis_title.text_frame.text = chart["y_label"]
    ch.value_axis.tick_labels.number_format = "$#,##0.00"
    ch.value_axis.tick_labels.number_format_is_linked = False
    ch.has_legend = False
    plot = ch.plots[0]
    plot.has_data_labels = True
    plot.data_labels.number_format = "$#,##0.00"
    plot.data_labels.number_format_is_linked = False
    for i, b in enumerate(bars):
        pt = plot.series[0].points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = _rgb(STATUS_COLORS.get(b["status"], BRAND["--accent"]))
    legend = chart.get("legend") or []
    if legend:
        tb = slide.shapes.add_textbox(x, y + cy, cx, Inches(0.3))
        p = tb.text_frame.paragraphs[0]
        for status in legend:
            r = p.add_run()
            r.text = f"■ {status}   "
            r.font.size = Pt(10)
            r.font.color.rgb = _rgb(STATUS_COLORS.get(status, BRAND["--muted"]))


def render_pptx(doc: Dict[str, Any], status: str = "draft") -> bytes:
    _check(doc, "pptx")
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    margin = Inches(0.6)
    width = prs.slide_width - 2 * margin
    banner = banner_text(status)
    for s in doc["sections"]:
        is_title = s["kind"] == "title"
        slide = prs.slides.add_slide(prs.slide_layouts[0 if is_title else 5])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = _rgb(BRAND["--bg"])
        title = slide.shapes.title
        title.text = doc["title"] if is_title else s["title"]
        for r in title.text_frame.paragraphs[0].runs:
            r.font.color.rgb = _rgb(BRAND["--accent"])
        if is_title:
            sub = slide.placeholders[1]
            tf = sub.text_frame
            lines = list(s["paragraphs"]) + [doc["label"]]
            for i, line in enumerate(lines):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                r = p.add_run()
                r.text = line
                r.font.color.rgb = _rgb(BRAND["--muted"] if i == len(lines) - 1 else BRAND["--text"])
            if banner:
                color = BRAND["--danger"] if status in _DANGER_STATUSES else BRAND["--warn"]
                _textbox(slide, prs.slide_width - margin - Inches(5), Inches(0.2), Inches(5), Inches(0.4),
                         [banner], 14, color, bold=True)
        else:
            title.left, title.top, title.width, title.height = margin, Inches(0.3), width, Inches(0.9)
            y = Inches(1.3)
            if s["paragraphs"]:
                _textbox(slide, margin, y, width, Inches(0.4) * len(s["paragraphs"]), list(s["paragraphs"]),
                         16, BRAND["--text"])
                y += Inches(0.45) * len(s["paragraphs"])
            if s["bullets"]:
                _textbox(slide, margin, y, width, Inches(0.35) * len(s["bullets"]),
                         ["• " + b for b in s["bullets"]], 16, BRAND["--text"])
                y += Inches(0.4) * len(s["bullets"])
            for t in _tables(s):
                y = _pptx_table(slide, t, margin, y, width)
            if s.get("chart"):
                _pptx_chart(slide, s["chart"], margin, y, width, max(Inches(2.5), Inches(6.7) - y - Inches(0.4)))
        _textbox(slide, margin, Inches(7.1), width, Inches(0.3), [doc["footer"]], 9, BRAND["--muted"])
        slide.notes_slide.notes_text_frame.text = s["notes"]
    cp = prs.core_properties
    cp.title, cp.subject, cp.author = doc["title"], doc["subtitle"], doc["product"]
    cp.comments, cp.category = doc["label"], doc["doc_type"]
    cp.created = cp.modified = _generated(doc)
    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ docx
def _shade(cell, fill_hex: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    tc_pr.append(shd)


def _para(d, text: str, style: Optional[str] = None, color: Optional[str] = None, bold: bool = False,
          italic: bool = False, size: Optional[int] = None):
    p = d.add_paragraph(style=style)
    r = p.add_run(text)
    r.bold, r.italic = bold or None, italic or None
    if color:
        r.font.color.rgb = _drgb(color)
    if size:
        r.font.size = DPt(size)
    return p


def _heading(d, text: str, level: int = 1):
    h = d.add_paragraph(style=f"Heading {level}")
    r = h.add_run(text)
    r.font.color.rgb = _drgb(BRAND["--accent"])
    return h


def _docx_table(d, t: Dict[str, Any]) -> None:
    cols, rows = list(t["columns"]), t["rows"]
    table = d.add_table(rows=len(rows) + 1, cols=len(cols), style="Table Grid")
    for c, name in enumerate(cols):
        cell = table.cell(0, c)
        cell.text = ""
        cell.paragraphs[0].add_run(str(name)).bold = True
        _shade(cell, "edf2f7")
    for ri, row in enumerate(rows):
        for c, val in enumerate(row):
            cell = table.cell(ri + 1, c)
            cell.text = ""
            run = cell.paragraphs[0].add_run(str(val))
            if str(val) == NA_TEXT:
                run.italic = True
    if t.get("caption"):
        _para(d, str(t["caption"]), italic=True, color=PRINT["muted"], size=9)


def _docx_body(d, s: Dict[str, Any]) -> None:
    for p in s["paragraphs"]:
        _para(d, p, style="Normal")
    for b in s["bullets"]:
        _para(d, b, style="List Bullet")
    for t in _tables(s):
        _docx_table(d, t)
    if s.get("chart"):
        c = s["chart"]
        _para(d, c["title"], bold=True)
        _docx_table(d, chart_table(c))
        _para(d, HTML_EDITION_LINE, italic=True, color=PRINT["muted"], size=9)


def render_docx(doc: Dict[str, Any], status: str = "draft") -> bytes:
    _check(doc, "docx")
    d = WordDocument()
    d.styles["Normal"].font.size = DPt(11)
    deck = doc["kind"] == "deck"
    banner = banner_text(status)
    if banner:
        _para(d, banner, bold=True, color=BRAND["--danger"] if status in _DANGER_STATUSES else BRAND["--warn"])
    d.add_paragraph(doc["title"], style="Title")
    _para(d, doc["subtitle"], color=PRINT["muted"])
    _para(d, doc["label"], italic=True, color=PRINT["muted"], size=9)
    for s in doc["sections"]:
        if s["kind"] == "title":
            for p in s["paragraphs"]:
                _para(d, p, style="Normal")
        else:
            _heading(d, s["title"])
            _docx_body(d, s)
        if deck:
            _para(d, f"Speaker notes: {s['notes']}", italic=True, color=PRINT["muted"], size=10)
    if doc.get("footnotes"):
        _heading(d, "Notes")
        for i, f in enumerate(doc["footnotes"], 1):
            _para(d, f"{i}. {f}", size=9)
    if not deck:
        _para(d, doc["freshness"])
    _para(d, doc["footer"], color=PRINT["muted"], size=9)
    cp = d.core_properties
    cp.title, cp.subject, cp.author = doc["title"], doc["subtitle"], doc["product"]
    cp.comments, cp.category = doc["label"], doc["doc_type"]
    cp.created = cp.modified = _generated(doc)
    buf = BytesIO()
    d.save(buf)
    return buf.getvalue()
