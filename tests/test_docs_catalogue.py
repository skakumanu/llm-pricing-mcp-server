"""Document writer catalogue (v1.89.0): closed set, params and defaults, deterministic outlines."""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services import docs_catalogue as cat  # noqa: E402


def test_catalogue_is_exactly_the_four_types():
    assert tuple(cat.DOC_TYPES) == cat.DOC_TYPE_IDS == ("release_notes_brief", "pricing_comparison_deck",
                                                        "pricing_one_pager", "internal_status_report")
    with pytest.raises(cat.ParamError):
        cat.get_type("sales_one_pager")


def test_describe_types_lists_params_and_defaults():
    types = {t["doc_type"]: t for t in cat.describe_types()}
    assert set(types) == set(cat.DOC_TYPE_IDS)
    deck = types["pricing_comparison_deck"]
    assert deck["defaults"]["input_tokens"] == 1_000_000 and deck["defaults"]["output_tokens"] == 150_000
    assert deck["defaults"]["model_names"] == list(cat.DEFAULT_MODEL_NAMES)
    assert {p["name"] for p in deck["params"]} == {"model_names", "input_tokens", "output_tokens", "audience"}
    assert deck["kind"] == "deck" and [s["id"] for s in deck["sections"]][0] == "title"


def test_defaults_are_applied_and_reported():
    resolved, defaulted = cat.validate_params("pricing_one_pager", {"input_tokens": 5})
    assert resolved["input_tokens"] == 5 and resolved["output_tokens"] == cat.DEFAULT_OUTPUT_TOKENS
    assert "input_tokens" not in defaulted and {"model_names", "output_tokens", "audience"} <= set(defaulted)


@pytest.mark.parametrize("params,field", [
    ({"bogus": 1}, "bogus"),
    ({"input_tokens": "5"}, "input_tokens"),
    ({"input_tokens": True}, "input_tokens"),
    ({"input_tokens": 0}, "input_tokens"),
    ({"model_names": ["only-one"]}, "model_names"),
    ({"model_names": ["a"] * 3}, "model_names"),
    ({"model_names": [f"m{i}" for i in range(11)]}, "model_names"),
    ({"model_names": ["a", "x" * 81]}, "model_names"),
    ({"audience": "x" * 61}, "audience"),
    ({"audience": ""}, "audience"),
])
def test_invalid_params_are_rejected_with_the_field(params, field):
    with pytest.raises(cat.ParamError) as exc:
        cat.validate_params("pricing_comparison_deck", params)
    assert exc.value.field == field


def test_params_must_be_known_per_type():
    with pytest.raises(cat.ParamError):
        cat.validate_params("internal_status_report", {"model_names": ["a", "b"]})
    assert cat.validate_params("internal_status_report", {"window_days": 7})[0]["window_days"] == 7
    with pytest.raises(cat.ParamError):
        cat.validate_params("internal_status_report", {"window_days": 91})


def test_required_sections_per_type():
    assert cat.required_section_ids("pricing_comparison_deck")[:3] == ["title", "executive_summary", "agenda"]
    assert "freshness" in cat.required_section_ids("pricing_one_pager")


def test_formatters_round_trip_the_displayed_precision():
    assert cat.fmt_usd(1234.5) == "$1,234.50" and cat.fmt_usd(0.0123456) == "$0.01"
    assert cat.fmt_usd(0.0004) == "$0.0004" and cat.fmt_pct(98.14) == "98.1%" and cat.fmt_int(1500000) == "1,500,000"
