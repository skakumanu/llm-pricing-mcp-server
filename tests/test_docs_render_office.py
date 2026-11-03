"""Office editions (v1.90.0): decks -> .pptx, documents -> .docx, deck -> .docx handout. No network, no LLM."""
import re
import sys
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document as WordDocument
from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_SHAPE_TYPE

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import docs_catalogue as cat  # noqa: E402
from src.services import docs_render as r  # noqa: E402
from src.services import docs_render_office as office  # noqa: E402

from tests.test_docs_render import make  # noqa: E402


def _prs(doc, status="draft"):
    return Presentation(BytesIO(office.render_pptx(doc, status)))


def _wdoc(doc, status="draft"):
    return WordDocument(BytesIO(office.render_docx(doc, status)))


def _slide_texts(slide):
    return [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame]


async def test_deck_pptx_structure_notes_and_core_properties():
    doc, _ = await make("pricing_comparison_deck", {"audience": "Board"})
    prs = _prs(doc)
    assert len(prs.slides) == len(doc["sections"]) == 9
    for slide, s in zip(prs.slides, doc["sections"]):
        expected = doc["title"] if s["kind"] == "title" else s["title"]
        assert slide.shapes.title.text == expected
        assert slide.has_notes_slide and slide.notes_slide.notes_text_frame.text == s["notes"]
        assert doc["footer"] in _slide_texts(slide)
    sub = prs.slides[0].placeholders[1].text_frame.text
    assert "Prepared for Board" in sub and "2027-01-15" in sub and "1,000,000 input" in sub
    assert prs.core_properties.title == doc["title"]
    assert prs.core_properties.author == doc["product"]


async def test_pptx_tables_are_native_and_equal_the_model():
    doc, _ = await make("pricing_comparison_deck")
    prs = _prs(doc)
    for sid in ("price_table", "cost_ranking", "sources"):
        i = [s["id"] for s in doc["sections"]].index(sid)
        s = doc["sections"][i]
        tables = [sh.table for sh in prs.slides[i].shapes if sh.has_table]
        assert len(tables) == 1
        t = tables[0]
        assert [t.cell(0, c).text for c in range(len(s["table"]["columns"]))] == s["table"]["columns"]
        for ri, row in enumerate(s["table"]["rows"]):
            assert [t.cell(ri + 1, c).text for c in range(len(row))] == [str(x) for x in row]
    mi = [s["id"] for s in doc["sections"]].index("methodology")
    assert len([sh for sh in prs.slides[mi].shapes if sh.has_table]) == 2


async def test_pptx_price_not_available_cells_survive():
    doc, _ = await make("pricing_comparison_deck", withheld=("claude-haiku-5-5",))
    prs = _prs(doc)
    cells = [t.cell(r_, c).text for sl in prs.slides for sh in sl.shapes if sh.has_table
             for t in [sh.table] for r_ in range(len(t.rows)) for c in range(len(t.columns))]
    expected = any("price not available" in str(x) for s in doc["sections"] for t in
                   ([s.get("table")] + list(s.get("extra_tables", []))) if t for row in t["rows"] for x in row)
    assert ("price not available" in cells) == expected


async def test_pptx_chart_is_native_and_equals_the_bars_with_no_pictures():
    doc, m = await make("pricing_comparison_deck")
    prs = _prs(doc)
    i = [s["id"] for s in doc["sections"]].index("cost_chart")
    c = doc["sections"][i]["chart"]
    charts = [sh for sh in prs.slides[i].shapes if sh.has_chart]
    assert len(charts) == 1
    ch = charts[0].chart
    assert ch.chart_type == XL_CHART_TYPE.COLUMN_CLUSTERED
    assert list(ch.plots[0].categories) == [b["label"] for b in c["bars"]]
    assert list(ch.series[0].values) == [b["value"] for b in c["bars"]]
    assert ch.chart_title.text_frame.text == c["title"]
    assert ch.category_axis.axis_title.text_frame.text == c["x_label"]
    assert ch.value_axis.axis_title.text_frame.text == c["y_label"]
    for slide in prs.slides:
        assert all(sh.shape_type != MSO_SHAPE_TYPE.PICTURE for sh in slide.shapes)


async def test_pptx_with_no_priced_model_prints_a_placeholder_instead_of_a_chart():
    doc, _ = await make("pricing_comparison_deck")
    i = [s["id"] for s in doc["sections"]].index("cost_chart")
    doc["sections"][i]["chart"]["bars"] = []
    prs = _prs(doc)
    assert not [sh for sh in prs.slides[i].shapes if sh.has_chart]
    assert "No priced model to chart" in _slide_texts(prs.slides[i])


async def test_pptx_banner_only_when_not_published():
    doc, _ = await make("pricing_comparison_deck")
    assert "DRAFT - not approved" in _slide_texts(_prs(doc, "draft").slides[0])
    assert "DISCARDED - rejected by the owner" in _slide_texts(_prs(doc, "discarded").slides[0])
    for slide in _prs(doc, "published").slides:
        assert not [t for t in _slide_texts(slide) if "DRAFT" in t or "DISCARDED" in t or "FAILED" in t]


async def test_one_pager_docx_structure():
    doc, _ = await make("pricing_one_pager")
    d = _wdoc(doc, "published")
    heads = [p.text for p in d.paragraphs if p.style.name.startswith("Heading")]
    assert heads == [s["title"] for s in doc["sections"]] + ["Notes"]
    texts = [p.text for p in d.paragraphs]
    notes = texts[texts.index("Notes") + 1:][:len(doc["footnotes"])]
    assert notes == [f"{i}. {f}" for i, f in enumerate(doc["footnotes"], 1)]
    assert doc["freshness"] in texts and doc["footer"] in texts
    assert d.paragraphs[0].style.name == "Title" and texts[0] == doc["title"]
    chart_sec = next(s for s in doc["sections"] if s.get("chart"))
    assert office.HTML_EDITION_LINE in texts and "in the HTML edition" in office.HTML_EDITION_LINE
    expected = r.chart_table(chart_sec["chart"])["rows"]
    grids = [[[c.text for c in row.cells] for row in t.rows] for t in d.tables]
    assert expected in [g[1:] for g in grids]
    n_tables = sum(len([t for t in [s.get("table")] + list(s.get("extra_tables", [])) if t]) + (1 if s.get("chart") else 0)
                   for s in doc["sections"])
    assert len(d.tables) == n_tables


async def test_docx_table_cells_equal_model_rows_and_header_is_shaded():
    doc, _ = await make("pricing_one_pager")
    d = _wdoc(doc)
    sec = next(s for s in doc["sections"] if s.get("table"))
    grids = [[[c.text for c in row.cells] for row in t.rows] for t in d.tables]
    assert [list(map(str, row)) for row in sec["table"]["rows"]] in [g[1:] for g in grids]
    assert [str(c) for c in sec["table"]["columns"]] in [g[0] for g in grids]
    assert 'w:fill="edf2f7"' in d.tables[0]._tbl.xml


async def test_docx_banner_first_for_draft_and_absent_for_published():
    doc, _ = await make("pricing_one_pager")
    assert _wdoc(doc, "draft").paragraphs[0].text == "DRAFT - not approved"
    pub = _wdoc(doc, "published")
    assert pub.paragraphs[0].style.name == "Title"
    assert not [p for p in pub.paragraphs if "DRAFT" in p.text]
    assert _wdoc(doc, "failed").paragraphs[0].text.startswith("FAILED")


async def test_deck_docx_handout_has_headings_and_speaker_notes():
    doc, _ = await make("pricing_comparison_deck")
    d = _wdoc(doc)
    heads = [p.text for p in d.paragraphs if p.style.name == "Heading 1"]
    assert heads == [s["title"] for s in doc["sections"] if s["kind"] != "title"]
    notes = [p.text for p in d.paragraphs if p.text.startswith("Speaker notes: ")]
    assert notes == [f"Speaker notes: {s['notes']}" for s in doc["sections"]]
    assert d.paragraphs[0].text == "DRAFT - not approved"


async def test_document_to_pptx_is_refused_with_the_allowed_formats():
    doc, _ = await make("pricing_one_pager")
    with pytest.raises(office.FormatNotAvailable) as exc:
        office.render_pptx(doc, "draft")
    assert exc.value.allowed == ["html", "md", "docx"] and exc.value.kind == "document"
    assert r.allowed_formats("deck") == ["html", "md", "pptx", "docx"]
    assert r.allowed_formats("document") == ["html", "md", "docx"]


@pytest.mark.parametrize("doc_type", list(cat.DOC_TYPES))
async def test_every_catalogue_type_renders_to_every_allowed_format(doc_type):
    doc, _ = await make(doc_type)
    renderers = {"html": r.render_html, "md": r.render_markdown,
                 "pptx": office.render_pptx, "docx": office.render_docx}
    for fmt in r.allowed_formats(doc["kind"]):
        body = renderers[fmt](doc, "draft")
        assert body
        if fmt in ("pptx", "docx"):
            assert body[:2] == b"PK"


def test_requirements_pin_the_office_libraries_and_floors():
    text = (project_root / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^python-pptx==1\.0\.2\s*$", text, re.M)
    assert re.search(r"^python-docx==1\.2\.0\s*$", text, re.M)
    for pkg in ("lxml", "Pillow", "XlsxWriter"):
        assert re.search(rf"^{pkg}>=\d", text, re.M), pkg
