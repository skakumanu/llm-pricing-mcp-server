"""Document writer verification (v1.89.0): never fabricate, count corrections, structural validation."""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import docs_catalogue as cat  # noqa: E402
from src.services import docs_verify as v  # noqa: E402


def manifest():
    m = cat.Manifest(generated_at="2026-11-01T10:00:00Z", names=["claude-sonnet-5-5", "Anthropic"],
                     dates=["2026-11-01", "2026-10-31"], meta={"app_version": "1.89.0"})
    m.add(cat.Figure("a", 1234.5, "USD", "a", "t"))
    m.add(cat.Figure("b", 12.3, "percent", "b", "t"))
    m.add(cat.Figure("c", 1_000_000, "tokens", "c", "t"))
    m.add(cat.Figure("d", 0.1, "USD", "d", "t"))
    m.source_texts.append("Shipped in v1.88.0 on Nov 1, 2026 with 4 tests.")
    return m


@pytest.mark.parametrize("text", ["It costs $1,234.50.", "It costs 1234.5 dollars.", "Up 12.3% overall.",
                                  "A workload of 1M tokens.", "A workload of 1,000,000 tokens.",
                                  "The price is $0.10 per million.", "Model claude-sonnet-5-5 from Anthropic.",
                                  "Dated 2026-10-31 at 10:00:00.", "Released in v1.88.0.", "Version 1.89.0 ships.",
                                  "Four tests on Nov 1, 2026."])
def test_traceable_text_is_kept_with_zero_corrections(text):
    clean, n = v.verify_prose(text, manifest())
    assert n == 0 and clean == text


@pytest.mark.parametrize("text,bad", [("It costs $99.99.", "$99.99"), ("Up 12.4% overall.", "12.4%"),
                                      ("Saves 73% versus rivals.", "73%"), ("Dated 2026-01-01.", "2026-01-01"),
                                      ("Released in v9.9.9.", "v9.9.9"), ("A workload of 2M tokens.", "2M")])
def test_untraceable_number_removes_its_sentence_and_counts_one(text, bad):
    clean, n = v.verify_prose("Fine start. " + text + " Fine end.", manifest())
    assert n == 1 and clean == "Fine start. Fine end." and bad not in clean


def test_each_untraceable_number_counts():
    _clean, n = v.verify_prose("Costs $7.77 and 55%.", manifest())
    assert n == 2


def test_prose_block_verifies_summary_and_notes():
    clean, n = v.verify_prose_block({"summary": "Good. It is 61% cheaper.", "notes": {"price_table": "See $0.10.",
                                                                                  "x": "Costs 8.8%."}}, manifest())
    assert n == 2 and clean["summary"] == "Good." and clean["notes"] == {"price_table": "See $0.10.", "x": ""}


def test_scrub_removes_pii_shapes():
    out = v.scrub("mail a@b.io key sk-" + "a" * 20 + " cus_" + "b" * 10)
    assert "@" not in out and "sk-aaa" not in out and "cus_bbb" not in out


def doc(kind="deck"):
    secs = [cat.section(i, i, notes="n", paragraphs=["Four models."] if i == "executive_summary" else [])
            for i in ("title", "executive_summary", "agenda", "price_table", "cost_ranking", "cost_chart",
                      "methodology", "sources", "next_steps")]
    return {"doc_type": "pricing_comparison_deck", "kind": kind, "title": "T", "subtitle": "On 2026-11-01",
            "sections": secs, "footnotes": [], "freshness": "Read on 2026-11-01.", "footer": "Generated v1.89.0"}


def test_valid_document_passes():
    m = manifest()
    m.add(cat.Figure("n_models", 4, "models", "n", "t"))
    v.validate_document(doc(), m)


@pytest.mark.parametrize("mutate,match", [
    (lambda d: d["sections"].pop(1), "missing required section: executive_summary"),
    (lambda d: d["sections"][1].update(paragraphs=[" "]), "executive summary is empty"),
    (lambda d: d["sections"][3].update(notes=" "), "no speaker notes"),
    (lambda d: d["sections"][3].update(paragraphs=["Reach me at a@b.io"]), "email"),
    (lambda d: d["sections"][3].update(paragraphs=["key sk-" + "a" * 20]), "key"),
    (lambda d: d["sections"][3].update(table=cat.table(["x"], [["$77.77"]])), "not in the manifest"),
])
def test_structural_failures_raise(mutate, match):
    d = doc()
    mutate(d)
    with pytest.raises(v.ValidationError, match=match):
        v.validate_document(d, manifest())
