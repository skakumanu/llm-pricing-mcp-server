"""v1.87.0 hashed service keys (Spec AC11): SHA-256 + 8-char prefix, in-place idempotent migration."""
import asyncio
import hashlib
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import src.main as main_module  # noqa: E402
from src.services.billing_service import BillingService, hash_api_key  # noqa: E402

OPERATOR_KEY = "-".join(["op", "hash", "k", "5e2"])
LEGACY_KEY_1 = "-".join(["legacy", "key", "one", "A1b2C3d4E5f6"])
LEGACY_KEY_2 = "-".join(["legacy", "key", "two", "Z9y8X7w6V5u4"])


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def svc(tmp_path):
    s = BillingService(str(tmp_path / "billing.db"))
    run(s.initialize())
    return s


def rows(path):
    with sqlite3.connect(path) as c:
        return c.execute("SELECT id, api_key, api_key_hash, api_key_prefix FROM customers ORDER BY email").fetchall()


def dump(path):
    with sqlite3.connect(path) as c:
        return "\n".join(c.iterdump())


def test_fresh_customer_row_holds_no_plaintext(svc):
    cust = run(svc.create_customer("a" + "@" + "example.org"))
    assert cust.api_key and cust.api_key_prefix == cust.api_key[:8]
    [(cid, legacy, digest, prefix)] = rows(svc._db_path)
    # 256-bit random key: a fast hash is the right choice (see hash_api_key); CodeQL assumes a password.
    assert digest == hashlib.sha256(cust.api_key.encode()).hexdigest() == hash_api_key(cust.api_key)
    assert len(digest) == 64 and prefix == cust.api_key[:8]
    assert legacy != cust.api_key and cust.api_key not in dump(svc._db_path)


def test_authentication_uses_the_hash(svc):
    cust = run(svc.create_customer("a" + "@" + "example.org"))
    assert run(svc.get_customer_by_api_key(cust.api_key)).id == cust.id
    assert run(svc.get_customer_by_api_key(cust.api_key + "x")) is None
    assert run(svc.get_customer_by_api_key("")) is None
    assert run(svc.get_customer_by_api_key(hash_api_key(cust.api_key))) is None  # the hash is not a credential
    assert run(svc.get_customer_by_api_key("hashed:" + cust.id)) is None  # nor is the placeholder
    stored = run(svc.get_customer_by_id(cust.id))
    assert stored.api_key is None and stored.api_key_prefix == cust.api_key[:8]


def test_rotation_invalidates_old_key_and_stores_only_the_hash(svc):
    cust = run(svc.create_customer("a" + "@" + "example.org"))
    new_key = run(svc.rotate_api_key(cust.id))
    assert new_key and new_key != cust.api_key
    assert run(svc.get_customer_by_api_key(cust.api_key)) is None
    assert run(svc.get_customer_by_api_key(new_key)).id == cust.id
    assert run(svc.get_customer_by_id(cust.id)).api_key_prefix == new_key[:8]
    text = dump(svc._db_path)
    assert new_key not in text and cust.api_key not in text
    assert run(svc.rotate_api_key("missing")) is None


def _legacy_db(path):
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE customers (id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, stripe_customer_id TEXT, "
            "stripe_subscription_id TEXT, api_key TEXT NOT NULL UNIQUE, tier TEXT NOT NULL DEFAULT 'free', "
            "org_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "billing_status TEXT NOT NULL DEFAULT 'active', past_due_since REAL)"
        )
        now = time.time()
        for i, key in enumerate((LEGACY_KEY_1, LEGACY_KEY_2), 1):
            c.execute("INSERT INTO customers VALUES (?, ?, NULL, NULL, ?, 'pro', ?, ?, ?, 'active', NULL)",
                      (f"id-{i}", f"u{i}@example.org", key, f"org-{i}", now, now))


def test_legacy_plaintext_rows_are_migrated_in_place(tmp_path):
    path = str(tmp_path / "legacy.db")
    _legacy_db(path)
    assert LEGACY_KEY_1 in dump(path)
    s = BillingService(path)
    run(s.initialize())
    assert LEGACY_KEY_1 not in dump(path) and LEGACY_KEY_2 not in dump(path)
    for (cid, legacy, digest, prefix), key in zip(rows(path), (LEGACY_KEY_1, LEGACY_KEY_2)):
        assert digest == hash_api_key(key) and prefix == key[:8] and legacy != key
    assert run(s.get_customer_by_api_key(LEGACY_KEY_1)).id == "id-1"
    assert run(s.get_customer_by_api_key(LEGACY_KEY_2)).org_id == "org-2"
    assert run(s.get_customer_by_api_key(LEGACY_KEY_1[:-1])) is None
    assert run(s.get_customer_by_id("id-1")).tier == "pro"  # the rest of the row survived


def test_second_init_is_a_noop(tmp_path):
    path = str(tmp_path / "legacy.db")
    _legacy_db(path)
    s = BillingService(path)
    run(s.initialize())
    first = rows(path)
    run(s.initialize())
    assert rows(path) == first
    assert run(s.migrate_plaintext_keys()) == 0
    assert run(s.get_customer_by_api_key(LEGACY_KEY_2)).id == "id-2"


def test_migration_counts_are_logged(tmp_path, caplog):
    path = str(tmp_path / "legacy.db")
    _legacy_db(path)
    with caplog.at_level("INFO"):
        run(BillingService(path).initialize())
    assert any("2 migrated" in r.getMessage() for r in caplog.records)
    assert LEGACY_KEY_1 not in caplog.text


def test_admin_customers_shows_prefix(tmp_path):
    s = BillingService(str(tmp_path / "billing.db"))
    run(s.initialize())
    cust = run(s.create_customer("a" + "@" + "example.org"))
    with patch("src.main.settings.mcp_api_key", OPERATOR_KEY), patch("src.main.get_billing_service", return_value=s):
        main_module._rate_limit_store.clear()
        r = TestClient(main_module.app).get("/admin/customers", headers={"x-api-key": OPERATOR_KEY})
    assert r.status_code == 200
    assert r.json()["customers"][0]["api_key_preview"] == cust.api_key[:8] + "..."
    assert cust.api_key not in r.text


def test_customer_key_still_authenticates_over_http(tmp_path):
    s = BillingService(str(tmp_path / "billing.db"))
    run(s.initialize())
    cust = run(s.create_customer("a" + "@" + "example.org"))
    with patch("src.main.settings.mcp_api_key", OPERATOR_KEY), patch("src.main.get_billing_service", return_value=s):
        main_module._rate_limit_store.clear()
        c = TestClient(main_module.app)
        ok = c.get("/billing/me", headers={"x-api-key": cust.api_key})
        bad = c.get("/billing/me", headers={"x-api-key": cust.api_key + "x"})
    assert ok.status_code == 200 and ok.json()["api_key_preview"] == cust.api_key[:8] + "..."
    assert bad.status_code == 401
