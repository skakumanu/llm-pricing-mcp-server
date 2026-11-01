"""Document writer renderers (v1.89.0): deck and document structure, charts, brand kit, banners."""
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import docs_catalogue as cat  # noqa: E402
from src.services import docs_render as r  # noqa: E402
from src.services import docs_sources as dsrc  # noqa: E402

from tests.test_docs_service import AS_OF, fake_sources  # noqa: E402

LLM = {"mode": "data_only", "reason": "requested", "model": None}


async def make(doc_type, params=None, prose=None, llm=None, **src_kw):
    resolved, defaulted = cat.validate_params(doc_type, params or {})
    m = await dsrc.gather(doc_type, resolved, fake_sources(**src_kw), now=1_800_000_000.0)
    doc = cat.build_document(doc_type, resolved, defaulted, m, prose, llm or LLM, "LLM Pricing API", "1.89.0")
    return doc, m


async def test_deck_has_the_seven_slide_kinds_in_order_with_notes():
    doc, _m = await make("pricing_comparison_deck")
    ids = [s["id"] for s in doc["sections"]]
    assert ids == ["title", "executive_summary", "agenda", "price_table", "cost_ranking", "cost_chart",
                   "methodology", "sources", "next_steps"]
    assert all(s["notes"].strip() for s in doc["sections"])
    html = r.render_html(doc, "draft")
    assert html.count('<section class="slide"') == 9 and html.count('<aside class="notes">') == 9
    assert re.findall(r'<section class="slide" id="slide-(\w+)"', html) == ids
    md = r.render_markdown(doc, "draft")
    for title in ("Executive summary", "Agenda", "Model prices", "Methodology and data quality", "Sources", "Next steps"):
        assert f"## {title}" in md
    assert md.count("Speaker notes:") == 9


async def test_title_slide_carries_utc_date_audience_and_defaults():
    doc, _ = await make("pricing_comparison_deck", {"audience": "Board"})
    title = doc["sections"][0]["paragraphs"]
    assert "Prepared for Board" in title[0] and "2027-01-15" in title[1]
    assert "1,000,000 input" in title[2] and "150,000 output" in title[2]
    method = next(s for s in doc["sections"] if s["id"] == "methodology")
    origin = {row_[0]: row_[2] for row_ in method["extra_tables"][0]["rows"]}
    assert origin["audience"] == "requested" and origin["input_tokens"] == "default"


async def test_one_pager_document_structure():
    doc, _ = await make("pricing_one_pager")
    assert doc["kind"] == "document" and "title" not in [s["id"] for s in doc["sections"]]
    assert doc["subtitle"].startswith("Prepared for Owner on 2027-01-15")
    assert doc["footnotes"] and any(AS_OF in f for f in doc["footnotes"])
    assert "price not available" in " ".join(doc["footnotes"])
    html = r.render_html(doc, "published")
    assert "<h1>Pricing one-pager</h1>" in html and "Footnotes" in html and doc["freshness"] in html
    assert "Speaker notes" not in html and 'class="slide"' not in html.split("</style>")[1]


async def test_chart_has_title_axes_legend_and_values_equal_the_table():
    doc, m = await make("pricing_comparison_deck")
    chart = next(s for s in doc["sections"] if s["id"] == "cost_chart")["chart"]
    ranking = next(s for s in doc["sections"] if s["id"] == "cost_ranking")["table"]["rows"]
    svg = r.chart_svg(chart)
    assert svg.startswith("<svg") and "Workload cost by model" in svg
    assert "Workload cost (USD)" in svg and ">Model<" in svg
    for status in chart["legend"]:
        assert status in svg
    values = [float(x) for x in re.findall(r'data-value="([0-9.e-]+)"', svg)]
    assert [cat.fmt_usd(v) for v in values] == [row_[1] for row_ in ranking]
    assert values == [m.get(b["fig"]).value for b in chart["bars"]]
    assert r.BRAND["--accent"] in svg


async def test_unavailable_models_carry_status_and_na_marker_in_html():
    doc, _ = await make("pricing_comparison_deck")
    html = r.render_html(doc, "draft")
    assert '<td class="na">price not available</td>' in html and "unpriced" in html


def test_brand_equals_the_ui_tokens():
    text = (project_root / "static" / "index.html").read_text(encoding="utf-8")
    root = re.search(r":root\s*\{(.*?)\}", text, flags=re.S).group(1)
    tokens = dict(re.findall(r"(--[a-z0-9]+):\s*([^;]+);", root))
    assert {k: tokens[k] for k in r.BRAND} == r.BRAND


async def test_html_is_self_contained_and_print_ready():
    doc, _ = await make("pricing_comparison_deck")
    html = r.render_html(doc, "draft")
    assert "<script" not in html.lower() and not re.search(r'(src|href)="https?://', html)
    assert "@media print" in html and "page-break-after:always" in html
    assert r.render_html(doc, "draft") == html  # deterministic


async def test_draft_banner_only_while_not_approved():
    doc, _ = await make("pricing_one_pager")
    assert "DRAFT - not approved" in r.render_html(doc, "draft") and "DRAFT" in r.render_markdown(doc, "draft")
    assert "DRAFT" not in r.render_html(doc, "published") and "DRAFT" not in r.render_markdown(doc, "published")
    assert "DISCARDED" in r.render_html(doc, "discarded")


async def test_data_only_label_and_footer_name_product_and_version():
    doc, _ = await make("pricing_one_pager")
    assert doc["label"] == "Data-only edition (no narrative model was used): requested"
    assert doc["footer"].startswith("Generated by LLM Pricing API v1.89.0 on 2027-01-15 (UTC)")
    llm_doc, _ = await make("pricing_one_pager", llm={"mode": "llm", "model": "claude-haiku-5-5"})
    assert "drafted by claude-haiku-5-5" in llm_doc["label"]


async def test_html_escapes_content():
    doc, _ = await make("pricing_one_pager", prose={"summary": "<script>alert(1)</script> is not run."})
    assert "<script>alert" not in r.render_html(doc) and "&lt;script&gt;" in r.render_html(doc)


async def test_markdown_tables_escape_pipes():
    doc, _ = await make("pricing_one_pager")
    doc["sections"][1]["table"]["rows"][0][0] = "a|b"
    assert "a\\|b" in r.render_markdown(doc)


async def test_data_quality_section_equals_the_manifest_snapshot():
    doc, m = await make("pricing_comparison_deck")
    rows = dict(next(s for s in doc["sections"] if s["id"] == "methodology")["table"]["rows"])
    assert rows["Confirmed prices"] == cat.fmt_pct(m.data_quality["confirmed_pct"])
    assert rows["Withheld for drift"] == cat.fmt_int(m.data_quality["withheld_for_drift"])


async def test_release_brief_and_status_documents_render_in_both_formats():
    for dt in ("release_notes_brief", "internal_status_report"):
        doc, _ = await make(dt)
        assert r.render_html(doc, "draft").startswith("<!DOCTYPE html>") and r.render_markdown(doc).startswith("> **DRAFT")
    assert "Document writer" in r.render_markdown((await make("release_notes_brief"))[0])


@pytest.mark.parametrize("fmt,ctype", [("html", "text/html; charset=utf-8"), ("md", "text/markdown; charset=utf-8")])
def test_content_types(fmt, ctype):
    assert r.content_type(fmt) == ctype

