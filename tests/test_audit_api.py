"""/audit/* over HTTP (v1.88.0): operator-only, 503 when uninitialised, run -> list -> get -> latest (AC11)."""
import asyncio
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

from tests.test_audit_service import build  # noqa: E402

client = TestClient(app)

OPERATOR_KEY = "-".join(["op", "audit", "k", "61a"])
INTAKE_KEY = "-".join(["intake", "audit", "k", "72b"])
OP = {"x-api-key": OPERATOR_KEY}
IK = {"x-api-key": INTAKE_KEY}
ROUTES = (("POST", "/audit/run"), ("GET", "/audit/reports"), ("GET", "/audit/reports/latest"),
          ("GET", "/audit/reports/aud_missing"))


@pytest.fixture(autouse=True)
def _clean_rate_limiter():
    main_module._rate_limit_store.clear()
    yield
    main_module._rate_limit_store.clear()


@pytest.fixture
def audit_env(tmp_path):
    env = asyncio.run(build(tmp_path))
    patches = [
        patch("src.main.settings.mcp_api_key", OPERATOR_KEY),
        patch("src.main.settings.orchestrator_intake_key", SecretStr(INTAKE_KEY)),
        patch("src.main.get_audit_service", return_value=env.audit),
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
    def test_no_key_is_401(self, audit_env, method, path):
        assert client.request(method, path).status_code == 401

    @pytest.mark.parametrize("method,path", ROUTES)
    def test_wrong_key_is_401(self, audit_env, method, path):
        assert client.request(method, path, headers={"x-api-key": "-".join(["not", "a", "key", "00z"])}).status_code == 401

    @pytest.mark.parametrize("method,path", ROUTES)
    def test_intake_key_is_refused(self, audit_env, method, path):
        assert client.request(method, path, headers=IK).status_code == 401

    @pytest.mark.parametrize("method,path", ROUTES)
    def test_503_when_the_service_is_not_initialised(self, method, path):
        with patch("src.main.settings.mcp_api_key", OPERATOR_KEY), \
                patch("src.main.get_audit_service", side_effect=RuntimeError("not initialised")):
            assert client.request(method, path, headers=OP).status_code == 503

    def test_prefix_is_operator_only_and_in_no_public_set(self):
        assert "/audit/" in main_module._OPERATOR_ONLY_PREFIXES
        for path in main_module._unauthenticated_paths | main_module._anonymous_rate_limited_paths:
            assert not path.startswith("/audit"), path
        assert "/audit/" not in str(main_module._sensitive_paths)
        for method, path in ROUTES:
            assert main_module._is_operator_only_request(method, path)
        assert not main_module._is_orchestrator_intake_route("POST", "/audit/run")


class TestRoundTrip:
    def test_run_list_get_latest(self, audit_env):
        run = client.post("/audit/run", headers=OP)
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["workflow_id"] == "compliance_audit" and body["status"] == "done"
        assert body["report_id"].startswith("aud_") and body["report_status"] == "pass"

        listing = client.get("/audit/reports", headers=OP).json()
        assert listing["count"] == 1
        item = listing["reports"][0]
        assert item["report_id"] == body["report_id"] and item["status"] == "pass"
        assert item["counts"]["fail"] == 0 and item["numbers"]["confirmed_pct"] == 98.0
        assert "checks" not in item and item["summary_source"] == "deterministic"

        full = client.get(f"/audit/reports/{body['report_id']}", headers=OP).json()
        assert len(full["checks"]) >= 25 and full["summary_text"].startswith("Compliance audit")
        assert {"id", "status", "severity", "title", "evidence", "lc"} <= set(full["checks"][0])

        latest = client.get("/audit/reports/latest", headers=OP).json()
        assert latest["report_id"] == body["report_id"] and len(latest["checks"]) == len(full["checks"])

    def test_every_post_without_a_request_id_runs_a_fresh_audit(self, audit_env):
        a = client.post("/audit/run", headers=OP).json()
        b = client.post("/audit/run", headers=OP).json()
        assert a["run_id"] != b["run_id"]
        assert client.get("/audit/reports?limit=1", headers=OP).json()["count"] == 1
        assert client.get("/audit/reports", headers=OP).json()["count"] == 2

    def test_request_id_is_an_idempotency_key(self, audit_env):
        a = client.post("/audit/run", json={"request_id": "audit-manual-0001"}, headers=OP).json()
        b = client.post("/audit/run", json={"request_id": "audit-manual-0001"}, headers=OP).json()
        assert a["run_id"] == b["run_id"] and b["duplicate"] is True

    def test_bad_request_id_is_422_and_unknown_report_404(self, audit_env):
        assert client.post("/audit/run", json={"request_id": "x y"}, headers=OP).status_code == 422
        assert client.post("/audit/run", json={"request_id": "short"}, headers=OP).status_code == 422
        r = client.get("/audit/reports/aud_nope", headers=OP)
        assert r.status_code == 404 and r.json()["error"] == "report_not_found"
        assert client.get("/audit/reports/latest", headers=OP).status_code == 404  # none yet

    def test_failing_report_opens_one_approval_visible_in_the_orchestrator_queue(self, audit_env):
        import src
        audit_env.audit.settings.legal_pages_live = True
        (audit_env.audit._legal_docs_dir / "terms.md").write_text("[ENTITY NAME]", encoding="utf-8")
        body = client.post("/audit/run", headers=OP).json()
        assert body["status"] == "waiting_owner" and body["report_status"] == "fail"
        queue = client.get("/orchestrator/approvals", headers=OP).json()
        assert queue["count"] == 1
        art = queue["approvals"][0]["artifact_summary_json"]
        assert queue["approvals"][0]["kind"] == "audit_flag" and art["report_id"] == body["report_id"]
        assert "legal.placeholders" in art["failed_outcome"] and src.__version__
