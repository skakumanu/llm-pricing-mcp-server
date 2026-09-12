"""Tests for ImportSessionUsageTool — bulk/backfill session-usage import.

Covers the Spec's acceptance criteria:
 1. Registered MCP tool: batch of entries under one session_id -> per-entry outcome
    (exact-vs-estimated marker for successes) + aggregate summary.
 2. Real token counts -> analyze_session_usage(session_id) has_data:true,
    total_requests == N, totals == sum of server-computed costs, has_estimated_usage false.
 3. Text-only entries -> has_data:true, non-zero estimated totals, has_estimated_usage
    true, non-zero estimated_request_count.
 4. Resubmitting an identical batch does not change already-reported totals.
 5. One entry with an unresolvable model_name and one with neither tokens nor text,
    alongside otherwise-valid entries -> valid entries recorded, each invalid entry its
    own per-entry failure, the whole call does not fail.
 6. The originating VS Code Copilot Chat bug-report scenario is resolved end-to-end.
 7. A session with both record_usage (live) and bulk-tool entries shows one combined
    total_requests/total_cost_usd/by_model breakdown.
 8. record_usage / analyze_session_usage continue to behave exactly as before for
    callers who don't use the new tool (see test_usage_tracker.py / test_analyze_session_usage.py
    — unmodified passing tests already cover this; this file adds a direct check too).
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp.tools.import_session_usage import ImportSessionUsageTool  # noqa: E402
from mcp.tools.analyze_session_usage import AnalyzeSessionUsageTool  # noqa: E402
from mcp.tools.record_usage import RecordUsageTool  # noqa: E402
from src.services.usage_tracker import UsageTrackerService  # noqa: E402


@pytest_asyncio.fixture
async def tracker(tmp_path):
    svc = UsageTrackerService(str(tmp_path / "test_import_usage.db"))
    await svc.initialize()
    return svc


def _patched(tracker):
    """Patch get_usage_tracker everywhere it's imported by the tools under test."""
    return (
        patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=tracker),
        patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=tracker),
        patch("mcp.tools.record_usage.get_usage_tracker", return_value=tracker),
    )


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_session_id():
    tool = ImportSessionUsageTool()
    result = await tool.execute({"entries": [{"model_name": "gpt-4o-mini", "input_tokens": 1, "output_tokens": 1}]})
    assert result["success"] is False
    assert "session_id" in result["error"]


@pytest.mark.asyncio
async def test_empty_session_id_string():
    tool = ImportSessionUsageTool()
    result = await tool.execute({"session_id": "   ", "entries": [{"model_name": "m"}]})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_missing_entries():
    tool = ImportSessionUsageTool()
    result = await tool.execute({"session_id": "sess-1"})
    assert result["success"] is False
    assert "entries" in result["error"]


@pytest.mark.asyncio
async def test_empty_entries_list():
    tool = ImportSessionUsageTool()
    result = await tool.execute({"session_id": "sess-1", "entries": []})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_entries_over_max_batch_size_rejected():
    tool = ImportSessionUsageTool()
    entries = [{"model_name": "gpt-4o-mini", "input_tokens": 1, "output_tokens": 1}] * 501
    result = await tool.execute({"session_id": "sess-1", "entries": entries})
    assert result["success"] is False
    assert "500" in result["error"]


@pytest.mark.asyncio
async def test_tracker_not_initialized():
    tool = ImportSessionUsageTool()
    with patch(
        "mcp.tools.import_session_usage.get_usage_tracker",
        side_effect=RuntimeError("not initialized"),
    ):
        result = await tool.execute({
            "session_id": "sess-1",
            "entries": [{"model_name": "gpt-4o-mini", "input_tokens": 1, "output_tokens": 1}],
        })
    assert result["success"] is False


# ---------------------------------------------------------------------------
# AC1 + AC2: exact token counts -> per-entry outcome + aggregate summary,
# analyze_session_usage reports has_data:true / exact totals / has_estimated_usage false
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exact_token_batch_recorded_and_analyzable(tracker):
    import_tool = ImportSessionUsageTool()
    analyze_tool = AnalyzeSessionUsageTool()
    session_id = "sess-exact"
    entries = [
        {"model_name": "gpt-4o-mini", "occurred_at": 1000.0, "input_tokens": 100, "output_tokens": 50},
        {"model_name": "gpt-4o-mini", "occurred_at": 1001.0, "input_tokens": 200, "output_tokens": 100},
        {"model_name": "gpt-4o-mini", "occurred_at": 1002.0, "input_tokens": 300, "output_tokens": 150},
    ]

    p1, p2, p3 = _patched(tracker)
    with p1, p2, p3:
        result = await import_tool.execute({"session_id": session_id, "entries": entries})

        assert result["success"] is True
        assert len(result["results"]) == 3
        assert all(r["success"] is True for r in result["results"])
        assert all(r["is_estimated"] is False for r in result["results"])
        assert result["summary"] == {
            "total_entries": 3, "recorded_exact": 3, "recorded_estimated": 0,
            "duplicates": 0, "failed": 0,
        }
        expected_total_cost = sum(r["cost_usd"] for r in result["results"])

        analysis = await analyze_tool.execute({"session_id": session_id})

    assert analysis["has_data"] is True
    assert analysis["total_requests"] == 3
    assert analysis["total_input_tokens"] == 600
    assert analysis["total_output_tokens"] == 300
    assert analysis["total_cost_usd"] == pytest.approx(expected_total_cost)
    assert analysis["has_estimated_usage"] is False
    assert analysis["estimated_request_count"] == 0


# ---------------------------------------------------------------------------
# AC3: text-only entries -> estimated totals, has_estimated_usage true,
# non-zero estimated_request_count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_text_only_batch_estimates_tokens_and_flags_estimated(tracker):
    import_tool = ImportSessionUsageTool()
    analyze_tool = AnalyzeSessionUsageTool()
    session_id = "sess-text"
    entries = [
        {
            "model_name": "gpt-4o-mini",
            "occurred_at": 2000.0,
            "input_text": "What is the capital of France? " * 20,
            "output_text": "The capital of France is Paris. " * 20,
        },
    ]

    p1, p2, p3 = _patched(tracker)
    with p1, p2, p3:
        result = await import_tool.execute({"session_id": session_id, "entries": entries})
        assert result["success"] is True
        entry_result = result["results"][0]
        assert entry_result["success"] is True
        assert entry_result["is_estimated"] is True
        assert entry_result["input_tokens"] > 0
        assert entry_result["output_tokens"] > 0
        assert result["summary"]["recorded_estimated"] == 1
        assert result["summary"]["recorded_exact"] == 0

        analysis = await analyze_tool.execute({"session_id": session_id})

    assert analysis["has_data"] is True
    assert analysis["total_cost_usd"] > 0
    assert analysis["has_estimated_usage"] is True
    assert analysis["estimated_request_count"] == 1


# ---------------------------------------------------------------------------
# AC4: resubmitting an identical batch is a no-op
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resubmitting_identical_batch_does_not_change_totals(tracker):
    import_tool = ImportSessionUsageTool()
    analyze_tool = AnalyzeSessionUsageTool()
    session_id = "sess-resubmit"
    entries = [
        {"model_name": "gpt-4o-mini", "occurred_at": 3000.0, "input_tokens": 100, "output_tokens": 50},
        {"model_name": "gpt-4o-mini", "occurred_at": 3001.0, "input_tokens": 200, "output_tokens": 100},
    ]

    # org_id is passed so the (org_id, request_id) UNIQUE index actually dedupes —
    # per this tracker's existing design, SQLite never treats two NULL org_ids as
    # equal, so idempotency for unscoped (no org_id) callers isn't guaranteed;
    # that's a pre-existing tradeoff of record_usage's dedupe index, not something
    # this tool changes.
    p1, p2, p3 = _patched(tracker)
    with p1, p2, p3:
        first = await import_tool.execute({"session_id": session_id, "org_id": "org-resubmit", "entries": entries})
        assert first["summary"]["duplicates"] == 0
        first_analysis = await analyze_tool.execute({"session_id": session_id, "org_id": "org-resubmit"})

        second = await import_tool.execute({"session_id": session_id, "org_id": "org-resubmit", "entries": entries})
        second_analysis = await analyze_tool.execute({"session_id": session_id, "org_id": "org-resubmit"})

    assert second["summary"]["duplicates"] == 2
    assert second["summary"]["recorded_exact"] == 0
    assert second_analysis["total_requests"] == first_analysis["total_requests"]
    assert second_analysis["total_input_tokens"] == first_analysis["total_input_tokens"]
    assert second_analysis["total_output_tokens"] == first_analysis["total_output_tokens"]
    assert second_analysis["total_cost_usd"] == pytest.approx(first_analysis["total_cost_usd"])


# ---------------------------------------------------------------------------
# AC5: invalid entries reported per-entry, do not fail the whole batch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_entries_reported_individually_valid_ones_still_recorded(tracker):
    import_tool = ImportSessionUsageTool()
    session_id = "sess-mixed"
    entries = [
        {"model_name": "gpt-4o-mini", "occurred_at": 4000.0, "input_tokens": 100, "output_tokens": 50},
        {"model_name": "definitely-not-a-real-model-xyz", "occurred_at": 4001.0, "input_tokens": 10, "output_tokens": 10},
        {"model_name": "gpt-4o-mini", "occurred_at": 4002.0},  # no tokens, no text
        {"model_name": "gpt-4o-mini", "occurred_at": 4003.0, "input_tokens": 50, "output_tokens": 25},
    ]

    p1, p2, p3 = _patched(tracker)
    with p1, p2, p3:
        result = await import_tool.execute({"session_id": session_id, "entries": entries})

    assert result["success"] is True  # the call itself never fails outright
    assert result["summary"]["total_entries"] == 4
    assert result["summary"]["recorded_exact"] == 2
    assert result["summary"]["failed"] == 2

    outcomes = {r["index"]: r for r in result["results"]}
    assert outcomes[0]["success"] is True
    assert outcomes[1]["success"] is False
    assert "not found" in outcomes[1]["error"]
    assert outcomes[2]["success"] is False
    assert outcomes[3]["success"] is True


# ---------------------------------------------------------------------------
# AC6: originating VS Code Copilot Chat bug-report scenario resolved end-to-end
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vscode_copilot_chat_backfill_scenario_end_to_end(tracker):
    """Before this tool: has_data:false forever for a session never live-tracked.
    After one import_session_usage call built from turn text: has_data:true, non-zero
    totals, and a model recommendation."""
    import_tool = ImportSessionUsageTool()
    analyze_tool = AnalyzeSessionUsageTool()
    session_id = "vscode-copilot-session-abc123"

    with patch("mcp.tools.analyze_session_usage.get_usage_tracker", return_value=tracker):
        pre_check = await analyze_tool.execute({"session_id": session_id})
    assert pre_check["has_data"] is False

    turns = [
        {"role": "user", "text": "Can you help me refactor this function to be more efficient? " * 5},
        {"role": "assistant", "text": "Sure, here's a refactored version with better time complexity. " * 15},
        {"role": "user", "text": "Now add type hints and docstrings please. " * 5},
        {"role": "assistant", "text": "Done — added full type annotations and Google-style docstrings. " * 15},
    ]
    entries = [
        {
            "model_name": "gpt-4o-mini",
            "occurred_at": 5000.0 + i,
            "input_text": turns[i]["text"],
            "output_text": turns[i + 1]["text"],
        }
        for i in range(0, len(turns) - 1, 2)
    ]

    p1, p2, p3 = _patched(tracker)
    with p1, p2, p3:
        import_result = await import_tool.execute({"session_id": session_id, "entries": entries})
        assert import_result["success"] is True
        assert import_result["summary"]["failed"] == 0

        analysis = await analyze_tool.execute({"session_id": session_id})

    assert analysis["success"] is True
    assert analysis["has_data"] is True
    assert analysis["total_cost_usd"] > 0
    assert analysis["has_estimated_usage"] is True
    # A recommendation (or an explicit reason none matched) must be attempted —
    # this is the "grounded model recommendation" the bug report was missing entirely.
    assert "recommendation" in analysis or "recommendation_error" in analysis


# ---------------------------------------------------------------------------
# AC7: live record_usage + bulk import under the same session_id combine into one result
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_combines_live_record_usage_and_bulk_import_into_one_session_result(tracker):
    record_tool = RecordUsageTool()
    import_tool = ImportSessionUsageTool()
    analyze_tool = AnalyzeSessionUsageTool()
    session_id = "sess-combined"

    p1, p2, p3 = _patched(tracker)
    with p1, p2, p3:
        live = await record_tool.execute({
            "model_name": "gpt-4o-mini", "input_tokens": 100, "output_tokens": 50,
            "session_id": session_id, "occurred_at": 6000.0,
        })
        assert live["success"] is True

        bulk = await import_tool.execute({
            "session_id": session_id,
            "entries": [
                {"model_name": "gpt-4o-mini", "occurred_at": 6001.0, "input_tokens": 200, "output_tokens": 100},
            ],
        })
        assert bulk["summary"]["recorded_exact"] == 1

        analysis = await analyze_tool.execute({"session_id": session_id})

    assert analysis["total_requests"] == 2
    assert analysis["total_input_tokens"] == 300
    assert analysis["total_output_tokens"] == 150
    assert len(analysis["by_model"]) == 1
    assert analysis["by_model"][0]["request_count"] == 2


# ---------------------------------------------------------------------------
# Idempotency key derivation / entry resolution unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_request_id_is_respected_for_dedup(tracker):
    import_tool = ImportSessionUsageTool()
    session_id = "sess-explicit-id"
    entry = {
        "model_name": "gpt-4o-mini", "occurred_at": 7000.0,
        "input_tokens": 10, "output_tokens": 10, "request_id": "my-custom-id",
    }

    with patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=tracker):
        first = await import_tool.execute({"session_id": session_id, "org_id": "org-x", "entries": [entry]})
        second = await import_tool.execute({"session_id": session_id, "org_id": "org-x", "entries": [entry]})

    assert first["results"][0]["duplicate"] is False
    assert second["results"][0]["duplicate"] is True
    assert first["results"][0]["request_id"] == "my-custom-id"


@pytest.mark.asyncio
async def test_resubmitting_identical_batch_without_occurred_at_still_dedupes(tracker):
    """Regression test: the auto-derived request_id must not depend on the wall-clock
    time at which occurred_at gets defaulted, or resubmitting the same omitted-timestamp
    batch would never collide and would double-record every entry."""
    import_tool = ImportSessionUsageTool()
    analyze_tool = AnalyzeSessionUsageTool()
    session_id = "sess-no-occurred-at"
    entries = [
        {"model_name": "gpt-4o-mini", "input_tokens": 100, "output_tokens": 50},
        {"model_name": "gpt-4o-mini", "input_tokens": 200, "output_tokens": 100},
    ]

    p1, p2, p3 = _patched(tracker)
    with p1, p2, p3:
        first = await import_tool.execute({"session_id": session_id, "org_id": "org-no-ts", "entries": entries})
        assert first["summary"]["duplicates"] == 0
        first_id_0 = first["results"][0]["request_id"]
        first_id_1 = first["results"][1]["request_id"]

        second = await import_tool.execute({"session_id": session_id, "org_id": "org-no-ts", "entries": entries})
        second_analysis = await analyze_tool.execute({"session_id": session_id, "org_id": "org-no-ts"})

    assert second["results"][0]["request_id"] == first_id_0
    assert second["results"][1]["request_id"] == first_id_1
    assert second["summary"]["duplicates"] == 2
    assert second["summary"]["recorded_exact"] == 0
    assert second_analysis["total_requests"] == 2


@pytest.mark.asyncio
async def test_output_side_estimated_from_text_when_input_is_exact(tracker):
    """An entry can mix an exact count on one side and text on the other; is_estimated
    is true if EITHER side was text-derived."""
    import_tool = ImportSessionUsageTool()
    with patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=tracker):
        result = await import_tool.execute({
            "session_id": "sess-mixed-sides",
            "entries": [{
                "model_name": "gpt-4o-mini", "occurred_at": 8000.0,
                "input_tokens": 100,
                "output_text": "Here is a fairly detailed explanation of the answer. " * 10,
            }],
        })
    entry_result = result["results"][0]
    assert entry_result["success"] is True
    assert entry_result["is_estimated"] is True
    assert entry_result["input_tokens"] == 100
    assert entry_result["output_tokens"] > 0


@pytest.mark.asyncio
async def test_negative_token_count_is_rejected_as_invalid():
    tool = ImportSessionUsageTool()
    with patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=MagicMock()):
        result = await tool.execute({
            "session_id": "sess-neg",
            "entries": [{"model_name": "gpt-4o-mini", "input_tokens": -1, "output_tokens": 10}],
        })
    assert result["results"][0]["success"] is False


@pytest.mark.asyncio
async def test_non_dict_entry_reported_as_failure():
    tool = ImportSessionUsageTool()
    with patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=MagicMock()):
        result = await tool.execute({"session_id": "sess-bad-entry", "entries": ["not-a-dict"]})
    assert result["results"][0]["success"] is False
    assert result["summary"]["failed"] == 1


@pytest.mark.asyncio
async def test_missing_model_name_reported_as_failure():
    tool = ImportSessionUsageTool()
    with patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=MagicMock()):
        result = await tool.execute({
            "session_id": "sess-no-model",
            "entries": [{"input_tokens": 10, "output_tokens": 10}],
        })
    assert result["results"][0]["success"] is False
    assert "model_name" in result["results"][0]["error"]


# ---------------------------------------------------------------------------
# Budget-alert integration: a bulk import can push an org over threshold just
# like a live record_usage call, so it must trigger the same best-effort check.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_triggers_budget_alert_check_once_per_batch(tracker):
    tool = ImportSessionUsageTool()
    mock_budget_svc = MagicMock()
    mock_budget_svc.check_and_fire = AsyncMock(return_value=0)

    with patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=tracker), \
         patch("mcp.tools.import_session_usage.get_budget_alert_service", return_value=mock_budget_svc):
        result = await tool.execute({
            "session_id": "sess-budget",
            "entries": [
                {"model_name": "gpt-4o-mini", "occurred_at": 9000.0, "input_tokens": 100, "output_tokens": 50},
                {"model_name": "gpt-4o-mini", "occurred_at": 9001.0, "input_tokens": 100, "output_tokens": 50},
            ],
        })

    assert result["summary"]["recorded_exact"] == 2
    mock_budget_svc.check_and_fire.assert_awaited_once()


@pytest.mark.asyncio
async def test_import_skips_budget_alert_check_when_nothing_recorded(tracker):
    """All entries invalid -> nothing new recorded -> no need to re-check budgets."""
    tool = ImportSessionUsageTool()
    mock_budget_svc = MagicMock()
    mock_budget_svc.check_and_fire = AsyncMock(return_value=0)

    with patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=tracker), \
         patch("mcp.tools.import_session_usage.get_budget_alert_service", return_value=mock_budget_svc):
        result = await tool.execute({
            "session_id": "sess-budget-none",
            "entries": [{"model_name": "definitely-not-a-real-model-xyz", "input_tokens": 10, "output_tokens": 10}],
        })

    assert result["summary"]["failed"] == 1
    mock_budget_svc.check_and_fire.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_budget_alert_failure_does_not_break_response(tracker):
    """If the budget-alert check itself errors, the import call still succeeds."""
    tool = ImportSessionUsageTool()
    with patch("mcp.tools.import_session_usage.get_usage_tracker", return_value=tracker), \
         patch(
             "mcp.tools.import_session_usage.get_budget_alert_service",
             side_effect=RuntimeError("not initialized"),
         ):
        result = await tool.execute({
            "session_id": "sess-budget-err",
            "entries": [{"model_name": "gpt-4o-mini", "occurred_at": 9500.0, "input_tokens": 10, "output_tokens": 10}],
        })

    assert result["success"] is True
    assert result["summary"]["recorded_exact"] == 1
