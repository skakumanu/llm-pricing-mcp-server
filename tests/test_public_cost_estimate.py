"""v1.82.0: POST /cost-estimate and /cost-estimate/batch are public but throttled.

They compute from public prices (the same math as the public estimate_cost /
compare_costs MCP tools) and needed a key only because they were never listed.
They are now anonymous yet rate-limited and body-capped, a valid customer key
still gets that customer's tier bucket, and comparisons are capped at
MAX_COMPARE_MODELS models on both the REST and MCP surfaces.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402

import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402
from src.services.cost_calculator import MAX_COMPARE_MODELS  # noqa: E402

client = TestClient(app)
# Built at runtime so no secret-shaped literal lands in a commit (gitleaks scans them all).
_OPERATOR_KEY = "-".join(["operator", "test", "value"])
ESTIMATE = {"model_name": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}


@pytest.fixture
def operator_key_configured():
    original = main_module.settings.mcp_api_key
    main_module.settings.mcp_api_key = _OPERATOR_KEY
    try:
        yield
    finally:
        main_module.settings.mcp_api_key = original


@pytest.fixture
def fresh_limiter():
    main_module._rate_limit_store.clear()
    yield
    main_module._rate_limit_store.clear()


def test_paths_are_anonymous_but_throttled_not_full_bypass():
    for path in ("/cost-estimate", "/cost-estimate/batch"):
        assert path in main_module._anonymous_rate_limited_paths
        assert path not in main_module._unauthenticated_paths
        assert path in main_module._customer_tiered_anonymous_paths


@pytest.mark.usefixtures("operator_key_configured")
class TestAnonymousAccess:
    def test_estimate_without_key(self):
        resp = client.post("/cost-estimate", json=ESTIMATE)
        assert resp.status_code == 200
        assert resp.json()["total_cost"] > 0

    def test_batch_without_key(self):
        resp = client.post("/cost-estimate/batch", json={
            "model_names": ["gpt-4o", "gpt-4o-mini"], "input_tokens": 1000, "output_tokens": 500})
        assert resp.status_code == 200
        assert resp.json()["cheapest_model"]

    def test_wrong_key_falls_back_to_anonymous(self):
        resp = client.post("/cost-estimate", json=ESTIMATE, headers={"x-api-key": "not-a-real-key"})
        assert resp.status_code == 200

    def test_unknown_model_still_404(self):
        resp = client.post("/cost-estimate", json={**ESTIMATE, "model_name": "no-such-model-xyz"})
        assert resp.status_code == 404


@pytest.mark.usefixtures("operator_key_configured", "fresh_limiter")
class TestThrottling:
    def test_anonymous_callers_are_rate_limited(self):
        with patch.object(main_module.settings, "rate_limit_per_minute", 2), \
                patch.object(main_module.settings, "rate_limit_free", 2):
            codes = [client.post("/cost-estimate", json=ESTIMATE).status_code for _ in range(4)]
        assert codes[:2] == [200, 200]
        assert 429 in codes[2:]

    def test_customer_key_uses_the_customer_bucket(self, tmp_path):
        from src.services.billing_service import BillingService
        svc = BillingService(str(tmp_path / "billing.db"))
        asyncio.run(svc.initialize())
        customer = asyncio.run(svc.create_customer("estimator@example.com"))
        with patch("src.main.get_billing_service", return_value=svc):
            resp = client.post("/cost-estimate", json=ESTIMATE, headers={"x-api-key": customer.api_key})
        assert resp.status_code == 200
        assert customer.id in main_module._rate_limit_store

    def test_body_cap_applies(self):
        with patch.object(main_module.settings, "max_body_bytes", 64):
            resp = client.post("/cost-estimate/batch", json={
                "model_names": ["m" * 200], "input_tokens": 1, "output_tokens": 1})
        assert resp.status_code == 413


@pytest.mark.usefixtures("operator_key_configured")
class TestModelCap:
    def test_rest_batch_rejects_more_than_the_cap(self):
        resp = client.post("/cost-estimate/batch", json={
            "model_names": [f"m{i}" for i in range(MAX_COMPARE_MODELS + 1)],
            "input_tokens": 1, "output_tokens": 1})
        assert resp.status_code == 422

    def test_rest_batch_accepts_exactly_the_cap(self):
        resp = client.post("/cost-estimate/batch", json={
            "model_names": ["gpt-4o"] * MAX_COMPARE_MODELS, "input_tokens": 1, "output_tokens": 1})
        assert resp.status_code == 200

    def test_rest_batch_rejects_empty_list(self):
        resp = client.post("/cost-estimate/batch", json={
            "model_names": [], "input_tokens": 1, "output_tokens": 1})
        assert resp.status_code == 422


def test_mcp_compare_costs_rejects_more_than_the_cap():
    from mcp.tools.compare_costs import CompareCostsTool
    result = asyncio.run(CompareCostsTool().execute({
        "model_names": [f"m{i}" for i in range(MAX_COMPARE_MODELS + 1)],
        "input_tokens": 1, "output_tokens": 1}))
    assert result["success"] is False
    assert str(MAX_COMPARE_MODELS) in result["error"]


def test_mcp_compare_costs_schema_declares_the_cap():
    from mcp.tools.tool_manager import ToolManager
    schema = ToolManager().tools["compare_costs"]["input_schema"]
    assert schema["properties"]["model_names"]["maxItems"] == MAX_COMPARE_MODELS
