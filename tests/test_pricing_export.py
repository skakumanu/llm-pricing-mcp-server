"""Tests for GET /pricing/history/export (CSV and JSON download)."""
import csv
import json
import sys
import time
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)

_SNAP = {
    "model_name": "gpt-4o",
    "provider": "openai",
    "cost_per_input_token": 0.005,
    "cost_per_output_token": 0.015,
    "captured_at": 1700000000.0,
}


def _patch_export(snapshots=None):
    actual = snapshots if snapshots is not None else [_SNAP]
    payload = {"snapshots": actual, "total": len(actual)}
    mock_svc = MagicMock()
    mock_svc.get_history = AsyncMock(return_value=payload)
    return patch("src.main.get_pricing_history_service", return_value=mock_svc)


class TestCsvExport:
    def test_csv_content_type(self):
        with _patch_export():
            resp = client.get("/pricing/history/export?format=csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    def test_csv_content_disposition_attachment(self):
        with _patch_export():
            resp = client.get("/pricing/history/export?format=csv")
        cd = resp.headers["content-disposition"]
        assert "attachment" in cd
        assert ".csv" in cd

    def test_csv_filename_includes_days(self):
        with _patch_export():
            resp = client.get("/pricing/history/export?format=csv&days=7")
        assert "7d" in resp.headers["content-disposition"]

    def test_csv_filename_includes_model_name(self):
        with _patch_export():
            resp = client.get("/pricing/history/export?format=csv&model_name=gpt-4o")
        assert "gpt-4o" in resp.headers["content-disposition"]

    def test_csv_has_header_row(self):
        with _patch_export():
            resp = client.get("/pricing/history/export?format=csv")
        reader = csv.reader(StringIO(resp.text))
        headers = next(reader)
        assert "model_name" in headers
        assert "provider" in headers
        assert "cost_per_input_token" in headers
        assert "cost_per_output_token" in headers
        assert "captured_at_iso" in headers
        assert "cost_per_input_per_1m_usd" in headers
        assert "cost_per_output_per_1m_usd" in headers

    def test_csv_data_row_values(self):
        with _patch_export([_SNAP]):
            resp = client.get("/pricing/history/export?format=csv")
        reader = csv.DictReader(StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["model_name"] == "gpt-4o"
        assert rows[0]["provider"] == "openai"
        assert float(rows[0]["cost_per_input_token"]) == 0.005
        assert float(rows[0]["cost_per_input_per_1m_usd"]) == pytest.approx(5000.0, rel=1e-4)

    def test_csv_empty_when_no_snapshots(self):
        with _patch_export([]):
            resp = client.get("/pricing/history/export?format=csv")
        assert resp.status_code == 200
        reader = csv.reader(StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 1  # only header row

    def test_csv_multiple_rows(self):
        snaps = [
            {**_SNAP, "model_name": "gpt-4o"},
            {**_SNAP, "model_name": "claude-3", "provider": "anthropic"},
        ]
        with _patch_export(snaps):
            resp = client.get("/pricing/history/export?format=csv")
        reader = csv.DictReader(StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 2

    def test_csv_1m_usd_column_computed_correctly(self):
        snap = {**_SNAP, "cost_per_input_token": 0.000001}
        with _patch_export([snap]):
            resp = client.get("/pricing/history/export?format=csv")
        reader = csv.DictReader(StringIO(resp.text))
        row = next(reader)
        assert float(row["cost_per_input_per_1m_usd"]) == pytest.approx(1.0, rel=1e-4)


class TestJsonExport:
    def test_json_content_type(self):
        with _patch_export():
            resp = client.get("/pricing/history/export?format=json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_json_content_disposition_attachment(self):
        with _patch_export():
            resp = client.get("/pricing/history/export?format=json")
        cd = resp.headers["content-disposition"]
        assert "attachment" in cd
        assert ".json" in cd

    def test_json_has_exported_at(self):
        with _patch_export():
            resp = client.get("/pricing/history/export?format=json")
        data = json.loads(resp.text)
        assert "exported_at" in data

    def test_json_has_filters(self):
        with _patch_export():
            resp = client.get(
                "/pricing/history/export?format=json&model_name=gpt-4o&provider=openai&days=7"
            )
        data = json.loads(resp.text)
        assert data["filters"]["model_name"] == "gpt-4o"
        assert data["filters"]["provider"] == "openai"
        assert data["filters"]["days"] == 7

    def test_json_count_matches_snapshots(self):
        with _patch_export([_SNAP, _SNAP]):
            resp = client.get("/pricing/history/export?format=json")
        data = json.loads(resp.text)
        assert data["count"] == 2
        assert len(data["snapshots"]) == 2

    def test_json_snapshot_structure(self):
        with _patch_export([_SNAP]):
            resp = client.get("/pricing/history/export?format=json")
        snap = json.loads(resp.text)["snapshots"][0]
        assert snap["model_name"] == "gpt-4o"
        assert snap["provider"] == "openai"
        assert snap["cost_per_input_token"] == 0.005

    def test_json_filename_includes_provider(self):
        with _patch_export():
            resp = client.get("/pricing/history/export?format=json&provider=openai")
        assert "openai" in resp.headers["content-disposition"]


class TestExportValidation:
    def test_invalid_format_returns_422(self):
        resp = client.get("/pricing/history/export?format=xml")
        assert resp.status_code == 422

    def test_days_zero_returns_422(self):
        resp = client.get("/pricing/history/export?days=0")
        assert resp.status_code == 422

    def test_days_too_large_returns_422(self):
        resp = client.get("/pricing/history/export?days=366")
        assert resp.status_code == 422

    def test_limit_too_large_returns_422(self):
        resp = client.get("/pricing/history/export?limit=100001")
        assert resp.status_code == 422

    def test_default_format_is_csv(self):
        with _patch_export():
            resp = client.get("/pricing/history/export")
        assert "text/csv" in resp.headers["content-type"]

    def test_passes_filters_to_service(self):
        with _patch_export() as mock_patch:
            mock_svc = mock_patch.return_value
            client.get("/pricing/history/export?model_name=gpt-4o&provider=openai&days=14&limit=500")
        mock_svc.get_history.assert_called_once_with(
            model_name="gpt-4o", provider="openai", days=14, limit=500
        )


import pytest
