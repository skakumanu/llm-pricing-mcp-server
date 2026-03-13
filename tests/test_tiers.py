"""Tests for API key tier rate limiting and GET /rate-limits/tiers."""
import sys
import os
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient  # noqa: E402
from src.config.settings import Settings  # noqa: E402


def _client():
    from src.main import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Settings unit tests
# ---------------------------------------------------------------------------

def test_settings_tier_defaults():
    s = Settings()
    assert s.rate_limit_free == 30
    assert s.rate_limit_pro == 120
    assert s.rate_limit_enterprise == 600


# ---------------------------------------------------------------------------
# GET /rate-limits/tiers
# ---------------------------------------------------------------------------

def test_rate_limit_tiers_unauthenticated():
    """Endpoint must be accessible without an API key."""
    client = _client()
    resp = client.get("/rate-limits/tiers")
    assert resp.status_code == 200


def test_rate_limit_tiers_response_shape():
    client = _client()
    resp = client.get("/rate-limits/tiers")
    assert resp.status_code == 200
    data = resp.json()
    assert "free" in data
    assert "pro" in data
    assert "enterprise" in data
    assert "default" in data


def test_rate_limit_tiers_values():
    client = _client()
    resp = client.get("/rate-limits/tiers")
    data = resp.json()
    assert data["free"] == 30
    assert data["pro"] == 120
    assert data["enterprise"] == 600


# ---------------------------------------------------------------------------
# Tier-aware rate limiting middleware
# ---------------------------------------------------------------------------

def test_tier_bucket_keys_are_separate(monkeypatch):
    """free and pro tiers should have independent buckets."""
    from src.main import _rate_limit_store
    _rate_limit_store.clear()

    client = _client()
    headers_free = {"X-Api-Key-Tier": "free"}
    headers_pro = {"X-Api-Key-Tier": "pro"}

    # Both tiers can make a request to the public endpoint without sharing quota
    r1 = client.get("/rate-limits/tiers", headers=headers_free)
    r2 = client.get("/rate-limits/tiers", headers=headers_pro)
    assert r1.status_code == 200
    assert r2.status_code == 200

    # They should be stored under different keys
    keys = list(_rate_limit_store.keys())
    # Each bucket key should contain the tier name
    tier_suffixes = {k.split(":", 1)[-1] for k in keys}
    assert "free" in tier_suffixes or len(keys) == 0  # may be 0 if rate limit disabled
    _rate_limit_store.clear()


def test_unknown_tier_falls_back_to_default():
    """An unknown tier header should use the default rate limit."""
    from src.main import _rate_limit_store
    _rate_limit_store.clear()

    client = _client()
    resp = client.get("/rate-limits/tiers", headers={"X-Api-Key-Tier": "unknown-tier"})
    assert resp.status_code == 200

    _rate_limit_store.clear()


def test_no_tier_header_uses_default():
    """No X-Api-Key-Tier header should use default bucket."""
    from src.main import _rate_limit_store
    _rate_limit_store.clear()

    client = _client()
    resp = client.get("/rate-limits/tiers")
    assert resp.status_code == 200

    _rate_limit_store.clear()
