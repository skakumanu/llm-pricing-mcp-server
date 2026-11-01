"""/documents/* over HTTP (v1.89.0): operator-only, 503 when uninitialised, generate -> list -> get -> download."""
import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import src.main as main_module  # noqa: E402
from src.main import app  # noqa: E402

from tests.test_docs_service import build, llm_answer  # noqa: E402
from tests.test_audit_service import FakeBackend  # noqa: E402

client = TestClient(app)

OPERATOR_KEY = "-".join(["op", "docs", "k", "81a"])
INTAKE_KEY = "-".join(["intake", "docs", "k", "92b"])
CUSTOMER_KEY = "-".join(["cust", "docs", "k", "03c"])
OP = {"x-api-key": OPERATOR_KEY}
ROUTES = (("GET", "/documents/types"), ("POST", "/documents/generate"), ("GET", "/documents/artifacts"),
          ("GET", "/documents/artifacts/doc_missing"), ("GET", "/documents/artifacts/doc_missing/download"))


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    main_module._rate_limit_store.clear()
    yield
    main_module._rate_limit_store.clear()


@pytest.fixture
def docs_env(tmp_path):
    env = asyncio.run(build(tmp_path, FakeBackend(text=llm_answer())))
    patches = [
        patch("src.main.settings.mcp_api_key", OPERATOR_KEY),
        patch("src.main.settings.orchestrator_intake_key", SecretStr(INTAKE_KEY)),
        patch("src.main.get_docs_service", return_value=env.svc),
        patch("src.main.get_orchestrator_service", return_value=env.orch),
    ]
    for p in patches:
        p.start()
    try:
        yield env
    finally:
        for p in reversed(patches):
            p.stop()


class TestAccess:
    @pytest.mark.parametrize("method,path", ROUTES)
    def test_anonymous_is_refused(self, docs_env, method, path):
        assert client.request(method, path).status_code == 401

    @pytest.mark.parametrize("method,path", ROUTES)
    def test_customer_and_intake_keys_are_refused(self, docs_env, method, path):
        for key in (CUSTOMER_KEY, INTAKE_KEY):
            assert client.request(method, path, headers={"x-api-key": key}).status_code == 401

    @pytest.mark.parametrize("method,path", ROUTES)
    def test_503_when_uninitialised(self, method, path):
        with patch("src.main.settings.mcp_api_key", OPERATOR_KEY), \
                patch("src.main.get_docs_service", side_effect=RuntimeError("no")):
            body = {"doc_type": "pricing_one_pager"} if method == "POST" else None
            assert client.request(method, path, headers=OP, json=body).status_code == 503

    def test_prefix_is_operator_only_and_swagger_is_unaffected(self):
        assert "/documents/" in main_module._OPERATOR_ONLY_PREFIXES
        for path in main_module._unauthenticated_paths | main_module._anonymous_rate_limited_paths:
            assert not path.startswith("/documents"), path
        for method, path in ROUTES:
            assert main_module._is_operator_only_request(method, path)
        assert not main_module._is_operator_only_request("GET", "/docs")
        assert not main_module._is_operator_only_request("GET", "/docs/oauth2-redirect")
        assert "/docs" in main_module._unauthenticated_paths
        assert client.get("/docs").status_code == 200


class TestRoundTrip:
    def test_types_lists_exactly_four(self, docs_env):
        body = client.get("/documents/types", headers=OP).json()
        assert body["count"] == 4
        assert [t["doc_type"] for t in body["types"]] == ["release_notes_brief", "pricing_comparison_deck",
                                                          "pricing_one_pager", "internal_status_report"]
        assert all("params" in t and "defaults" in t for t in body["types"])

    def test_generate_list_get_download(self, docs_env):
        res = client.post("/documents/generate", headers=OP, json={"doc_type": "pricing_comparison_deck"})
        assert res.status_code == 200, res.text
        body = res.json()
        aid = body["artifact_id"]
        assert body["status"] == "waiting_owner" and body["approval"]["kind"] == "publish"
        assert body["corrections"] == 0 and body["llm_mode"] == "llm" and body["manifest"]["figures"] > 0
        assert body["resolved_params"]["input_tokens"] == 1_000_000 and len(body["outline"]) == 9

        listing = client.get("/documents/artifacts", headers=OP).json()
        assert listing["count"] == 1 and listing["artifacts"][0]["artifact_id"] == aid
        assert "document" not in listing["artifacts"][0] and listing["artifacts"][0]["status"] == "draft"

        one = client.get(f"/documents/artifacts/{aid}", headers=OP).json()
        assert one["manifest"]["figures"] and one["outline"][0]["id"] == "title" and one["params"]["resolved"]

        html = client.get(f"/documents/artifacts/{aid}/download", headers=OP)
        assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
        assert re.fullmatch(r'attachment; filename="pricing-comparison-deck-\d{8}-doc_\w+\.html"',
                            html.headers["content-disposition"])
        assert "DRAFT - not approved" in html.text
        md = client.get(f"/documents/artifacts/{aid}/download?format=md", headers=OP)
        assert md.headers["content-type"].startswith("text/markdown") and md.headers["content-disposition"].endswith('.md"')

    def test_download_works_after_discard(self, docs_env):
        aid = client.post("/documents/generate", headers=OP, json={"doc_type": "pricing_one_pager"}).json()["artifact_id"]
        approval = asyncio.run(docs_env.ostore.list_approvals(status="waiting"))[0]
        asyncio.run(docs_env.orch.decide_approval(approval["approval_id"], "reject"))
        assert client.get(f"/documents/artifacts/{aid}", headers=OP).json()["status"] == "discarded"
        res = client.get(f"/documents/artifacts/{aid}/download", headers=OP)
        assert res.status_code == 200 and "DISCARDED" in res.text

    def test_unknown_artifact_is_404(self, docs_env):
        assert client.get("/documents/artifacts/doc_nope", headers=OP).status_code == 404
        assert client.get("/documents/artifacts/doc_nope/download", headers=OP).status_code == 404

    def test_expired_artifact_is_gone(self, docs_env):
        aid = client.post("/documents/generate", headers=OP, json={"doc_type": "pricing_one_pager"}).json()["artifact_id"]
        asyncio.run(docs_env.store.purge_expired(now=docs_env.clock() + 40 * 86400))
        assert client.get(f"/documents/artifacts/{aid}/download", headers=OP).status_code == 404

    def test_out_of_catalogue_type_is_422_and_makes_no_run(self, docs_env):
        res = client.post("/documents/generate", headers=OP, json={"doc_type": "sales_one_pager"})
        assert res.status_code == 422
        assert asyncio.run(docs_env.ostore.list_runs()) == []

    def test_bad_param_is_422_with_field(self, docs_env):
        res = client.post("/documents/generate", headers=OP,
                          json={"doc_type": "pricing_one_pager", "params": {"input_tokens": -4}})
        assert res.status_code == 422 and res.json()["field"] == "input_tokens"

    def test_data_only_flag_makes_no_llm_call(self, docs_env):
        res = client.post("/documents/generate", headers=OP, json={"doc_type": "pricing_one_pager", "data_only": True})
        assert res.json()["llm_mode"] == "data_only:requested"

    def test_failed_generation_returns_error_not_a_link(self, docs_env):
        res = client.post("/documents/generate", headers=OP, json={"doc_type": "release_notes_brief",
                                                                  "params": {"version_range": "9.0.0"}})
        assert res.status_code == 422 and res.json()["error"] == "document_failed" and res.json()["run_id"]

    def test_same_request_id_is_idempotent(self, docs_env):
        body = {"doc_type": "pricing_one_pager", "request_id": "doc-test-0001", "data_only": True}
        a = client.post("/documents/generate", headers=OP, json=body).json()
        b = client.post("/documents/generate", headers=OP, json=body).json()
        assert a["run_id"] == b["run_id"] and a["artifact_id"] == b["artifact_id"]
