"""Tests for HMAC-SHA256 webhook payload signing and verify_webhook_signature."""
import hashlib
import hmac
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.services.pricing_alerts import (  # noqa: E402
    PricingAlertService,
    verify_webhook_signature,
)
from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402

client = TestClient(app)
HEADERS = {"x-api-key": "test-key"}

SECRET = "super-secret-key-for-tests"

_TREND = {
    "model_name": "gpt-4o",
    "provider": "openai",
    "input_change_pct": 10.0,
    "output_change_pct": 5.0,
    "direction": "increased",
    "first_seen": 1700000000.0,
    "last_seen": 1700086400.0,
}

_ALERT = {
    "id": 1,
    "url": "https://example.com/hook",
    "threshold_pct": 5.0,
    "provider": None,
    "model_name": None,
    "created_at": 1700000000.0,
}


# ---------------------------------------------------------------------------
# verify_webhook_signature helper
# ---------------------------------------------------------------------------

class TestVerifyWebhookSignature:
    def _make_sig(self, body: bytes, secret: str = SECRET) -> str:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def test_valid_signature_returns_true(self):
        body = b'{"foo":"bar"}'
        sig = self._make_sig(body)
        assert verify_webhook_signature(SECRET, body, sig) is True

    def test_wrong_secret_returns_false(self):
        body = b'{"foo":"bar"}'
        sig = self._make_sig(body, secret="wrong-secret")
        assert verify_webhook_signature(SECRET, body, sig) is False

    def test_tampered_body_returns_false(self):
        body = b'{"foo":"bar"}'
        sig = self._make_sig(body)
        assert verify_webhook_signature(SECRET, b'{"foo":"baz"}', sig) is False

    def test_missing_prefix_returns_false(self):
        body = b'{"foo":"bar"}'
        raw_hex = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert verify_webhook_signature(SECRET, body, raw_hex) is False

    def test_empty_signature_returns_false(self):
        assert verify_webhook_signature(SECRET, b"body", "") is False

    def test_empty_body_valid_signature(self):
        body = b""
        sig = self._make_sig(body)
        assert verify_webhook_signature(SECRET, body, sig) is True

    def test_json_payload_roundtrip(self):
        payload = {"alert_id": 1, "model_name": "gpt-4o", "direction": "increased"}
        body = json.dumps(payload, separators=(",", ":")).encode()
        sig = self._make_sig(body)
        assert verify_webhook_signature(SECRET, body, sig) is True

    def test_case_sensitive_secret(self):
        body = b"data"
        sig = self._make_sig(body, secret=SECRET)
        assert verify_webhook_signature(SECRET.upper(), body, sig) is False


# ---------------------------------------------------------------------------
# PricingAlertService._fire with signing
# ---------------------------------------------------------------------------

class TestFireWithSigning:
    @pytest.mark.asyncio
    async def test_signed_request_includes_signature_header(self):
        captured = {}

        async def fake_post(url, *, content, headers):
            captured["headers"] = headers
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        await PricingAlertService._fire(mock_client, _ALERT, _TREND, secret=SECRET)

        assert "X-LLM-Pricing-Signature" in captured["headers"]
        sig = captured["headers"]["X-LLM-Pricing-Signature"]
        assert sig.startswith("sha256=")

    @pytest.mark.asyncio
    async def test_unsigned_request_has_no_signature_header(self):
        captured = {}

        async def fake_post(url, *, content, headers):
            captured["headers"] = headers
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        await PricingAlertService._fire(mock_client, _ALERT, _TREND, secret=None)

        assert "X-LLM-Pricing-Signature" not in captured["headers"]

    @pytest.mark.asyncio
    async def test_signature_verifies_against_body(self):
        captured = {}

        async def fake_post(url, *, content, headers):
            captured["body"] = content
            captured["headers"] = headers
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        await PricingAlertService._fire(mock_client, _ALERT, _TREND, secret=SECRET)

        sig = captured["headers"]["X-LLM-Pricing-Signature"]
        assert verify_webhook_signature(SECRET, captured["body"], sig) is True

    @pytest.mark.asyncio
    async def test_body_is_valid_json(self):
        captured = {}

        async def fake_post(url, *, content, headers):
            captured["body"] = content
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        await PricingAlertService._fire(mock_client, _ALERT, _TREND, secret=SECRET)

        payload = json.loads(captured["body"])
        assert payload["model_name"] == "gpt-4o"
        assert payload["alert_id"] == 1

    @pytest.mark.asyncio
    async def test_content_type_header_is_json(self):
        captured = {}

        async def fake_post(url, *, content, headers):
            captured["headers"] = headers
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_client = AsyncMock()
        mock_client.post = fake_post

        await PricingAlertService._fire(mock_client, _ALERT, _TREND, secret=SECRET)

        assert captured["headers"].get("Content-Type") == "application/json"


# ---------------------------------------------------------------------------
# check_and_fire passes secret through
# ---------------------------------------------------------------------------

class TestCheckAndFireWithSecret:
    @pytest.mark.asyncio
    async def test_secret_passed_to_fire(self):
        fired_headers = []

        async def fake_post(url, *, content, headers):
            fired_headers.append(headers)
            resp = MagicMock()
            resp.status_code = 200
            return resp

        svc = PricingAlertService.__new__(PricingAlertService)
        svc.list_alerts = AsyncMock(return_value=[_ALERT])

        import httpx
        real_client = AsyncMock(spec=httpx.AsyncClient)
        real_client.__aenter__ = AsyncMock(return_value=real_client)
        real_client.__aexit__ = AsyncMock(return_value=False)
        real_client.post = fake_post

        with patch("src.services.pricing_alerts.httpx.AsyncClient", return_value=real_client):
            count = await svc.check_and_fire([_TREND], secret=SECRET)

        assert count == 1
        assert any("X-LLM-Pricing-Signature" in h for h in fired_headers)


# ---------------------------------------------------------------------------
# GET /pricing/alerts/signing-info endpoint
# ---------------------------------------------------------------------------

class TestSigningInfoEndpoint:
    def test_returns_200(self):
        resp = client.get("/pricing/alerts/signing-info", headers=HEADERS)
        assert resp.status_code == 200

    def test_signing_disabled_when_no_secret(self):
        import src.main as main_module
        original = main_module.settings.webhook_secret
        try:
            main_module.settings.webhook_secret = None
            resp = client.get("/pricing/alerts/signing-info", headers=HEADERS)
            data = resp.json()
            assert data["signing_enabled"] is False
            assert data["algorithm"] is None
            assert data["header"] is None
        finally:
            main_module.settings.webhook_secret = original

    def test_signing_enabled_when_secret_set(self):
        import src.main as main_module
        original = main_module.settings.webhook_secret
        try:
            main_module.settings.webhook_secret = "my-secret"
            resp = client.get("/pricing/alerts/signing-info", headers=HEADERS)
            data = resp.json()
            assert data["signing_enabled"] is True
            assert data["algorithm"] == "hmac-sha256"
            assert data["header"] == "X-LLM-Pricing-Signature"
            assert data["format"] == "sha256=<hex_digest>"
        finally:
            main_module.settings.webhook_secret = original

    def test_secret_value_never_returned(self):
        import src.main as main_module
        original = main_module.settings.webhook_secret
        try:
            main_module.settings.webhook_secret = "top-secret-value"
            resp = client.get("/pricing/alerts/signing-info", headers=HEADERS)
            assert "top-secret-value" not in resp.text
        finally:
            main_module.settings.webhook_secret = original

    def test_note_field_present(self):
        resp = client.get("/pricing/alerts/signing-info", headers=HEADERS)
        assert "note" in resp.json()
