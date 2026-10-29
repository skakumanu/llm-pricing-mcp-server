"""Unit tests for src/services/provider_key_store.py (v1.79.0): encryption at rest, org/provider
binding, all-or-nothing decryption, metadata-only listing, the composite header-then-stored
resolver and last_used bookkeeping.

No real secret anywhere: every server secret is generated per test from os.urandom, and fake
provider keys are assembled at runtime so secret scanners never see a key-shaped literal.
"""
import asyncio
import base64
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.dispatch import DispatchPolicy  # noqa: E402
from src.services.dispatch_executors import (  # noqa: E402
    EXECUTABLE_PROVIDERS, HeaderKeyResolver, executor_spec_for_ref,
)
from src.services.dispatch_service import Attempt, DispatchService  # noqa: E402
from src.services import provider_key_store as pks  # noqa: E402
from src.services.provider_key_store import (  # noqa: E402
    STATUS_ACTIVE, STATUS_NEEDS_REREGISTRATION, CompositeKeyResolver, InvalidProviderKey, ProviderKeyExists,
    ProviderKeyNotFound, ProviderKeyStore, StoredKeysUnavailable, StoredSecret, UnsupportedProvider,
    canonical_provider, derive_working_key, fingerprint_of, validate_api_key,
)


def _key(tag: str) -> str:
    return "-".join(["sk", "unit", tag, "Zq9X"])


KEY_A = _key("ALPHAKEY")
KEY_B = _key("BRAVOKEY")


def _secret() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "billing.db")


@pytest.fixture
def store(db):
    s = ProviderKeyStore(db, _secret())
    run(s.initialize())
    return s


# ---------------------------------------------------------------------------
# Slugs / provider references
# ---------------------------------------------------------------------------

def test_slugs_for_every_executable_provider():
    slugs = {spec.provider: spec.slug for spec in EXECUTABLE_PROVIDERS.values()}
    assert slugs == {
        "OpenAI": "openai", "Groq": "groq", "Together AI": "together", "Fireworks AI": "fireworks",
        "DeepSeek": "deepseek", "Mistral AI": "mistral", "xAI": "xai", "Anthropic": "anthropic",
        "Google": "google",
    }


@pytest.mark.parametrize("ref,expected", [
    ("openai", "OpenAI"), ("OPENAI", "OpenAI"), ("OpenAI", "OpenAI"), ("together", "Together AI"),
    ("Together AI", "Together AI"), (" fireworks ", "Fireworks AI"), ("mistral", "Mistral AI"),
    ("xai", "xAI"), ("XAI", "xAI"),
])
def test_executor_spec_for_ref_accepts_slug_or_display_name(ref, expected):
    assert executor_spec_for_ref(ref).provider == expected
    assert canonical_provider(ref) == expected


@pytest.mark.parametrize("ref", ["Cohere", "cohere", "", None, "unknown-provider", 42])
def test_canonical_provider_rejects_non_executable(ref):
    with pytest.raises(UnsupportedProvider):
        canonical_provider(ref)


# ---------------------------------------------------------------------------
# Validation / fingerprint / secret
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "\t\n", None, 123, "a" * 513, "has space-inside", "tab\there",
                                 "café-key-value"])
def test_validate_api_key_rejects(bad):
    with pytest.raises(InvalidProviderKey) as exc:
        validate_api_key(bad)
    if isinstance(bad, str) and bad.strip():
        assert bad.strip() not in str(exc.value)


def test_validate_api_key_strips_and_accepts_max_length():
    assert validate_api_key("  " + KEY_A + "\n") == KEY_A
    assert validate_api_key("a" * 512) == "a" * 512


def test_fingerprint_is_last_four_only_for_long_keys():
    assert fingerprint_of(KEY_A) == "Zq9X"
    assert fingerprint_of("short7x") is None
    assert fingerprint_of("exactly8") == "tly8"


@pytest.mark.parametrize("secret", [None, "", "   ", "not base64 !!", base64.urlsafe_b64encode(b"x" * 31).decode()])
def test_unusable_secret_makes_store_unavailable(db, secret):
    assert derive_working_key(secret) is None
    s = ProviderKeyStore(db, secret)
    assert s.available is False
    run(s.initialize())  # the table is created even when unavailable
    con = sqlite3.connect(db)
    assert con.execute("SELECT name FROM sqlite_master WHERE name='provider_keys'").fetchone()
    con.close()
    for coro in (s.register("org-1", "OpenAI", KEY_A), s.rotate("org-1", "OpenAI", KEY_A),
                 s.delete("org-1", "OpenAI"), s.list("org-1")):
        with pytest.raises(StoredKeysUnavailable):
            run(coro)
    assert run(s.load_for_dispatch("org-1")) == {}


def test_secret_padding_is_optional():
    raw = os.urandom(32)
    padded = base64.urlsafe_b64encode(raw).decode()
    assert padded.endswith("=")
    assert derive_working_key(padded) == derive_working_key(padded.rstrip("="))
    assert len(derive_working_key(padded)) == 32


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------

def test_ciphertext_format_and_no_plaintext_on_disk(store, db):
    run(store.register("org-1", "OpenAI", KEY_A))
    con = sqlite3.connect(db)
    blob, fp = con.execute("SELECT ciphertext, fingerprint FROM provider_keys").fetchone()
    con.close()
    assert blob[:1] == b"\x01" and len(blob) == 1 + 12 + len(KEY_A) + 16
    assert KEY_A.encode() not in blob and fp == "Zq9X"
    for path in (Path(db), Path(db + "-wal")):
        if path.exists():
            assert KEY_A.encode() not in path.read_bytes()


def test_same_key_twice_gives_different_ciphertext(store):
    a = store.encrypt("org-1", "OpenAI", KEY_A)
    b = store.encrypt("org-1", "OpenAI", KEY_A)
    assert a != b
    assert store.decrypt("org-1", "OpenAI", a) == KEY_A == store.decrypt("org-1", "OpenAI", b)


def test_ciphertext_is_bound_to_org_and_provider(store):
    blob = store.encrypt("org-1", "OpenAI", KEY_A)
    assert store.decrypt("org-2", "OpenAI", blob) is None
    assert store.decrypt("org-1", "Anthropic", blob) is None


@pytest.mark.parametrize("mutate", [
    lambda b: b"\x02" + b[1:],                         # unknown format version
    lambda b: b[:20] + bytes([b[20] ^ 0x01]) + b[21:],  # flipped ciphertext bit
    lambda b: b[:-1] + bytes([b[-1] ^ 0x80]),          # flipped tag bit
    lambda b: b[:-5],                                  # truncated
    lambda b: b[:10],                                  # shorter than header + tag
    lambda b: b"",
])
def test_tampered_ciphertext_never_decrypts(store, mutate):
    blob = store.encrypt("org-1", "OpenAI", KEY_A)
    assert store.decrypt("org-1", "OpenAI", mutate(blob)) is None


def test_decrypt_rejects_non_bytes(store):
    assert store.decrypt("org-1", "OpenAI", None) is None
    assert store.decrypt("org-1", "OpenAI", "text") is None


def test_wrong_secret_never_decrypts(db, store):
    run(store.register("org-1", "OpenAI", KEY_A))
    other = ProviderKeyStore(db, _secret())
    con = sqlite3.connect(db)
    blob = con.execute("SELECT ciphertext FROM provider_keys").fetchone()[0]
    con.close()
    assert other.decrypt("org-1", "OpenAI", blob) is None


# ---------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------

def test_register_list_rotate_delete_cycle(store):
    before = time.time()
    info = run(store.register("org-1", "openai", KEY_A))
    assert (info.provider, info.slug, info.fingerprint, info.status) == ("OpenAI", "openai", "Zq9X", STATUS_ACTIVE)
    assert info.created_at >= before and info.rotated_at is None and info.last_used_at is None

    with pytest.raises(ProviderKeyExists):
        run(store.register("org-1", "OpenAI", KEY_B))

    listed = run(store.list("org-1"))
    assert [k.provider for k in listed] == ["OpenAI"]

    v1 = run(store.load_for_dispatch("org-1"))["OpenAI"].key_version
    rotated = run(store.rotate("org-1", "OpenAI", "-".join(["sk", "unit", "NEWVALUE", "Wk3P"])))
    assert rotated.fingerprint == "Wk3P" and rotated.rotated_at >= info.created_at
    assert rotated.created_at == pytest.approx(info.created_at)
    loaded = run(store.load_for_dispatch("org-1"))
    assert loaded["OpenAI"].value.endswith("Wk3P") and loaded["OpenAI"].key_version == v1 + 1

    assert run(store.delete("org-1", "OpenAI")) is True
    assert run(store.delete("org-1", "OpenAI")) is False
    assert run(store.list("org-1")) == []
    with pytest.raises(ProviderKeyNotFound):
        run(store.rotate("org-1", "OpenAI", KEY_B))


def test_list_is_ordered_and_org_scoped(store):
    run(store.register("org-1", "Groq", KEY_A))
    run(store.register("org-1", "Anthropic", KEY_B))
    run(store.register("org-2", "OpenAI", KEY_B))
    assert [k.provider for k in run(store.list("org-1"))] == ["Anthropic", "Groq"]
    assert [k.provider for k in run(store.list("org-2"))] == ["OpenAI"]
    assert run(store.list("org-3")) == []
    # Another org's row is the same as a missing row.
    assert run(store.delete("org-2", "Groq")) is False
    with pytest.raises(ProviderKeyNotFound):
        run(store.rotate("org-2", "Anthropic", KEY_A))
    assert run(store.load_for_dispatch("org-1"))["Anthropic"].value == KEY_B


def test_register_rejects_unsupported_provider_and_bad_key_without_writing(store, db):
    with pytest.raises(UnsupportedProvider):
        run(store.register("org-1", "Cohere", KEY_A))
    with pytest.raises(InvalidProviderKey):
        run(store.register("org-1", "OpenAI", "   "))
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM provider_keys").fetchone()[0] == 0
    con.close()


def test_short_key_has_no_fingerprint(store):
    info = run(store.register("org-1", "OpenAI", "abc1234"))
    assert info.fingerprint is None
    assert info.as_dict()["fingerprint"] is None


def test_info_as_dict_is_iso_utc_and_metadata_only(store):
    info = run(store.register("org-1", "Together AI", KEY_A))
    d = info.as_dict()
    assert set(d) == {"provider", "slug", "fingerprint", "created_at", "rotated_at", "last_used_at", "status"}
    assert d["slug"] == "together" and d["created_at"].endswith("Z") and d["rotated_at"] is None
    assert KEY_A not in repr(info) and KEY_A not in str(d)


def test_undecryptable_rows_listed_as_needing_reregistration_and_skipped_for_dispatch(db, store, caplog):
    run(store.register("org-1", "OpenAI", KEY_A))
    other = ProviderKeyStore(db, _secret())
    caplog.set_level(logging.DEBUG)
    listed = run(other.list("org-1"))
    assert [(k.provider, k.status) for k in listed] == [("OpenAI", STATUS_NEEDS_REREGISTRATION)]
    assert run(other.load_for_dispatch("org-1")) == {}
    assert any("cannot be decrypted" in r.getMessage() for r in caplog.records)
    assert KEY_A not in caplog.text and "Zq9X" not in caplog.text
    # Rotating is the re-registration path.
    run(other.rotate("org-1", "OpenAI", KEY_B))
    assert run(other.list("org-1"))[0].status == STATUS_ACTIVE
    assert run(other.load_for_dispatch("org-1"))["OpenAI"].value == KEY_B


def test_load_for_dispatch_skips_excluded_providers_without_decrypting(store, monkeypatch):
    run(store.register("org-1", "OpenAI", KEY_A))
    run(store.register("org-1", "Groq", KEY_B))
    decrypted = []
    real = store.decrypt
    monkeypatch.setattr(store, "decrypt", lambda o, p, b: decrypted.append(p) or real(o, p, b))
    loaded = run(store.load_for_dispatch("org-1", exclude=["OpenAI"]))
    assert set(loaded) == {"Groq"} and decrypted == ["Groq"]


def test_touch_last_used_is_monotonic_and_version_guarded(store):
    run(store.register("org-1", "OpenAI", KEY_A))
    v1 = run(store.load_for_dispatch("org-1"))["OpenAI"].key_version
    run(store.touch_last_used("org-1", [("OpenAI", v1, 2000.0)]))
    run(store.touch_last_used("org-1", [("OpenAI", v1, 1000.0)]))  # older: ignored
    assert run(store.list("org-1"))[0].last_used_at == 2000.0
    run(store.rotate("org-1", "OpenAI", KEY_B))                   # new version, last_used reset
    v2 = run(store.load_for_dispatch("org-1"))["OpenAI"].key_version
    assert v2 != v1
    assert run(store.list("org-1"))[0].last_used_at is None
    run(store.touch_last_used("org-1", [("OpenAI", v1, 3000.0)]))  # old value used mid-rotation
    assert run(store.list("org-1"))[0].last_used_at is None
    run(store.touch_last_used("org-1", [("OpenAI", v2, 3000.0)]))
    assert run(store.list("org-1"))[0].last_used_at == 3000.0
    run(store.touch_last_used("org-2", [("OpenAI", v2, 4000.0)]))  # other org: no effect
    assert run(store.list("org-1"))[0].last_used_at == 3000.0


def test_delete_then_reregister_does_not_reuse_key_version(store):
    """An in-flight dispatch holding the deleted key must not stamp last_used on its replacement."""
    run(store.register("org-1", "OpenAI", KEY_A))
    old = run(store.load_for_dispatch("org-1"))["OpenAI"].key_version
    run(store.delete("org-1", "OpenAI"))
    run(store.register("org-1", "OpenAI", KEY_B))
    new = run(store.load_for_dispatch("org-1"))["OpenAI"].key_version
    assert new != old
    run(store.touch_last_used("org-1", [("OpenAI", old, 5000.0)]))
    assert run(store.list("org-1"))[0].last_used_at is None


def test_initialize_is_idempotent(store):
    run(store.register("org-1", "OpenAI", KEY_A))
    run(store.initialize())
    assert len(run(store.list("org-1"))) == 1


def test_singleton_wiring(db, caplog):
    pks._store = None
    with pytest.raises(RuntimeError):
        pks.get_provider_key_store()
    caplog.set_level(logging.DEBUG)
    s = run(pks.init_provider_key_store(db, None))
    assert pks.get_provider_key_store() is s and s.available is False
    assert "PROVIDER_KEY_ENCRYPTION_KEY" in caplog.text
    secret = _secret()
    s2 = run(pks.init_provider_key_store(db, secret))
    assert s2.available and secret not in caplog.text
    pks._store = None


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def test_reprs_never_show_key_material(store):
    secret = StoredSecret(KEY_A, 1)
    assert repr(secret) == str(secret) == "StoredSecret(****)"
    with pytest.raises(AttributeError):
        secret.extra = 1  # __slots__: no accidental attribute carrying the value
    resolver = CompositeKeyResolver(
        HeaderKeyResolver({"X-Provider-Key-Anthropic": KEY_B}), {"OpenAI": secret}, store, "org-1",
    )
    for text in (repr(resolver), str(resolver), repr(store), f"{resolver}"):
        assert KEY_A not in text and KEY_B not in text and "Zq9X" not in text
    assert "Anthropic" in repr(resolver) and "OpenAI" in repr(resolver)


# ---------------------------------------------------------------------------
# Composite resolver
# ---------------------------------------------------------------------------

def _resolver(store, header=None, stored=None, org="org-1"):
    return CompositeKeyResolver(HeaderKeyResolver(header or {}), stored or {}, store, org)


def test_header_wins_over_stored(store):
    r = _resolver(store, {"X-Provider-Key-OpenAI": KEY_B}, {"OpenAI": StoredSecret(KEY_A, 1)})
    assert r.resolve("OpenAI") == KEY_B and r.source("OpenAI") == "header"
    assert r.resolve("openai") == KEY_B


def test_stored_used_when_no_header(store):
    r = _resolver(store, {"X-Provider-Key-Anthropic": KEY_B}, {"OpenAI": StoredSecret(KEY_A, 1)})
    assert r.resolve("OpenAI") == KEY_A and r.source("OpenAI") == "stored"
    assert r.resolve("Anthropic") == KEY_B and r.source("Anthropic") == "header"
    assert r.resolve("Google") is None and r.source("Google") is None
    assert r.resolve("Cohere") is None and r.source("Cohere") is None
    assert r.providers_with_keys() == ["Anthropic", "OpenAI"]


def test_header_resolver_source_and_noops():
    h = HeaderKeyResolver({"X-Provider-Key-OpenAI": KEY_A})
    assert h.source("OpenAI") == "header" and h.source("Google") is None
    h.note_used("OpenAI")
    assert run(h.flush_usage()) is None


def test_note_used_only_records_stored_and_flush_collapses(store):
    run(store.register("org-1", "OpenAI", KEY_A))
    run(store.register("org-1", "Groq", KEY_B))
    stored = run(store.load_for_dispatch("org-1"))
    r = _resolver(store, {"X-Provider-Key-Groq": KEY_A}, stored)
    t0 = time.time()
    r.note_used("OpenAI")
    r.note_used("OpenAI")
    r.note_used("Groq")      # header-sourced: no stored usage
    r.note_used("Google")    # no key at all
    run(r.flush_usage())
    rows = {k.provider: k for k in run(store.list("org-1"))}
    assert rows["OpenAI"].last_used_at >= t0
    assert rows["Groq"].last_used_at is None
    run(r.flush_usage())     # nothing pending: no-op


def test_flush_failure_is_swallowed_and_logged_without_values(store, caplog, monkeypatch):
    stored = {"OpenAI": StoredSecret(KEY_A, 1)}
    r = _resolver(store, None, stored)

    async def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "touch_last_used", boom)
    caplog.set_level(logging.DEBUG)
    r.note_used("OpenAI")
    run(r.flush_usage())
    assert "last_used update failed" in caplog.text and KEY_A not in caplog.text


# ---------------------------------------------------------------------------
# Dispatch-service surfaces that depend on the resolver
# ---------------------------------------------------------------------------

def test_attempt_as_dict_carries_key_source_last():
    d = Attempt(1, "m", "OpenAI", "ok", 200, 1.23, "stored").as_dict()
    assert list(d)[-1] == "key_source" and d["key_source"] == "stored"
    assert Attempt(1, "m", "OpenAI", "ok", 200, 1.0).as_dict()["key_source"] is None


def test_no_candidate_message_lists_both_sources_and_both_ways(store):
    r = _resolver(store, {"X-Provider-Key-Anthropic": KEY_B}, {"OpenAI": StoredSecret(KEY_A, 1)})
    msg = DispatchService._no_candidate_message("no_provider_key", DispatchPolicy(), None, r)
    assert "keys present for: Anthropic, OpenAI" in msg
    assert "X-Provider-Key-<Provider>" in msg and "POST /dispatch/keys" in msg
    assert KEY_A not in msg and KEY_B not in msg
    bare = DispatchService._no_candidate_message("no_provider_key", DispatchPolicy(), None, HeaderKeyResolver({}))
    assert "keys present" not in bare and "POST /dispatch/keys" in bare
