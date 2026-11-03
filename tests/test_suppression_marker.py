"""v1.89.4: optional SUPPRESSION_HASH_KEY pepper for the deletion marker (schemes db-salt and pepper)."""
import asyncio
import sqlite3

import pytest
from pydantic import SecretStr

from src.services import billing_service
from src.services.billing_service import BillingService

EMAIL = "carol" + "@" + "example.org"
PEPPER = "".join(["pepper-", "p" * 24])


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(billing_service.settings, "suppression_hash_key", None, raising=False)
    s = BillingService(str(tmp_path / "billing.db"))
    run(s.initialize())
    s._path = str(tmp_path / "billing.db")
    return s


def markers(svc):
    with sqlite3.connect(svc._path) as c:
        return c.execute("SELECT email_hash, hash_scheme FROM deleted_accounts").fetchall()


def delete_one(svc, email=EMAIL):
    cust = run(svc.create_customer(email))
    assert run(svc.delete_customer(cust.id)) is not None


def test_unset_pepper_keeps_db_salt_scheme(svc):
    delete_one(svc)
    (h, scheme), = markers(svc)
    with sqlite3.connect(svc._path) as c:
        salt = bytes.fromhex(c.execute("SELECT value FROM billing_meta WHERE key='suppression_salt'").fetchone()[0])
    assert scheme == "db-salt" and h == svc._email_hash(salt, EMAIL)
    assert run(svc.suppression_marker_count(EMAIL)) == 1


def test_pepper_marker_differs_and_both_are_counted(svc, monkeypatch):
    delete_one(svc)
    monkeypatch.setattr(billing_service.settings, "suppression_hash_key", SecretStr(PEPPER), raising=False)
    delete_one(svc)
    rows = markers(svc)
    assert sorted(s for _, s in rows) == ["db-salt", "pepper"]
    assert len({h for h, _ in rows}) == 2
    assert run(svc.suppression_marker_count(EMAIL)) == 2
    raw = open(svc._path, "rb").read()
    assert EMAIL.encode() not in raw and PEPPER.encode() not in raw


def test_peppered_marker_uncheckable_without_pepper(svc, monkeypatch):
    monkeypatch.setattr(billing_service.settings, "suppression_hash_key", SecretStr(PEPPER), raising=False)
    delete_one(svc)
    assert run(svc.suppression_marker_count(EMAIL)) == 1
    monkeypatch.setattr(billing_service.settings, "suppression_hash_key", None, raising=False)
    assert run(svc.suppression_marker_count(EMAIL)) == 0


def test_legacy_table_without_scheme_column_migrates(tmp_path):
    path = str(tmp_path / "billing.db")
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE deleted_accounts (marker_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                  "email_hash TEXT NOT NULL, deleted_at REAL NOT NULL)")
        c.execute("INSERT INTO deleted_accounts (email_hash, deleted_at) VALUES ('abc', 1.0)")
    s = BillingService(path)
    run(s.initialize())
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT hash_scheme FROM deleted_accounts").fetchone()[0] == "db-salt"
