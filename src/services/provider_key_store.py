"""Stored per-org provider keys for the dispatch gateway (v1.79.0).

A customer registers their own provider key (OpenAI, Anthropic, ...) once; dispatch uses it
whenever a request does not carry that provider's ``X-Provider-Key-<Provider>`` header (the
header always wins, and an ``auth_error`` on the winning key is never retried with the other).

Storage: one row per (org_id, provider) in the ``provider_keys`` table of billing.db, next to
``customers``. Key values are encrypted at rest with AES-256-GCM:

  * working key = HKDF-SHA256(length=32, salt=None, info=b'llm-pricing/provider-keys/v1') over
    the decoded PROVIDER_KEY_ENCRYPTION_KEY (base64url of >= 32 random bytes);
  * ciphertext BLOB = b'\\x01' (format version) + 12-byte random nonce + AESGCM ciphertext/tag;
  * AAD = b'provider-key:v1\\x00' + org_id + b'\\x00' + canonical provider, so a row copied to
    another org or provider fails authentication.

Decryption is all-or-nothing: a changed secret or a tampered row yields ``None`` (never partial
plaintext), the key counts as absent for dispatch and is listed as ``needs_reregistration``.
Without a usable secret the store is *unavailable*: management raises StoredKeysUnavailable
(the endpoints answer 503) and dispatch is header-only.

Privacy: no exception message, log line or repr in this module contains a key value, a
ciphertext or a fingerprint — only org ids, provider names and counts. Every SQL statement is
scoped by ``WHERE org_id = ?`` bound to the caller's own org, so another org's key is
indistinguishable from a key that does not exist.

Same aiosqlite/WAL idiom and init_X/get_X singleton pattern as dispatch_receipts.py.
"""
import base64
import binascii
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import aiosqlite
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.services.dispatch_executors import (
    EXECUTABLE_PROVIDERS, KEY_SOURCE_HEADER, KEY_SOURCE_STORED, HeaderKeyResolver, executor_spec_for,
    executor_spec_for_ref,
)

logger = logging.getLogger(__name__)

SECRET_ENV_VAR = "PROVIDER_KEY_ENCRYPTION_KEY"  # nosec B105 — the env var NAME, not a secret
MIN_SECRET_BYTES = 32
MAX_KEY_LENGTH = 512

STATUS_ACTIVE = "active"
STATUS_NEEDS_REREGISTRATION = "needs_reregistration"

_FORMAT_VERSION = b"\x01"
_NONCE_LEN = 12
_TAG_LEN = 16
_HKDF_INFO = b"llm-pricing/provider-keys/v1"
_AAD_PREFIX = b"provider-key:v1\x00"


@asynccontextmanager
async def _open_db(db_path: str):
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        yield db


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS provider_keys (
    org_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    ciphertext BLOB NOT NULL,
    fingerprint TEXT,
    key_version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    rotated_at REAL,
    last_used_at REAL,
    PRIMARY KEY (org_id, provider)
)
"""

# Columns added after v1.79.0 are appended here (same idiom as dispatch_receipts.py).
_MIGRATIONS: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Errors (messages carry provider names only — never a key, ciphertext or fingerprint)
# ---------------------------------------------------------------------------

class ProviderKeyStoreError(Exception):
    """Base class for stored-key refusals."""


class StoredKeysUnavailable(ProviderKeyStoreError):
    """No usable PROVIDER_KEY_ENCRYPTION_KEY is configured on this server."""


class UnsupportedProvider(ProviderKeyStoreError):
    """The provider is not one dispatch can execute."""


class InvalidProviderKey(ProviderKeyStoreError):
    """The key value is empty, too long or contains characters a header cannot carry."""


class ProviderKeyExists(ProviderKeyStoreError):
    """The org already stores a key for this provider (rotate it instead)."""


class ProviderKeyNotFound(ProviderKeyStoreError):
    """The org stores no key for this provider."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass(frozen=True)
class StoredKeyInfo:
    """Metadata of one stored key. Never holds the key value."""

    provider: str
    slug: str
    fingerprint: Optional[str]
    created_at: float
    rotated_at: Optional[float]
    last_used_at: Optional[float]
    status: str = STATUS_ACTIVE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "slug": self.slug,
            "fingerprint": self.fingerprint,
            "created_at": _iso(self.created_at),
            "rotated_at": _iso(self.rotated_at),
            "last_used_at": _iso(self.last_used_at),
            "status": self.status,
        }


class StoredSecret:
    """A decrypted stored key, held only for one dispatch request. Redacted repr/str."""

    __slots__ = ("value", "key_version")

    def __init__(self, value: str, key_version: int) -> None:
        self.value = value
        self.key_version = key_version

    def __repr__(self) -> str:
        return "StoredSecret(****)"

    __str__ = __repr__


# ---------------------------------------------------------------------------
# Validation + crypto helpers
# ---------------------------------------------------------------------------

def canonical_provider(ref: Optional[str]) -> str:
    """Slug or display name (any case) -> canonical display name; UnsupportedProvider otherwise."""
    spec = executor_spec_for_ref(ref) if isinstance(ref, str) else None
    if spec is None:
        raise UnsupportedProvider("provider is not executable by dispatch")
    return spec.provider


def accepted_providers() -> List[Dict[str, str]]:
    return [{"provider": s.provider, "slug": s.slug} for s in EXECUTABLE_PROVIDERS.values()]


def validate_api_key(value: Any) -> str:
    """Return the key to store (surrounding whitespace stripped) or raise InvalidProviderKey.

    Printable ASCII without spaces only: anything else could not be sent as an HTTP header, and
    httpx's illegal-header error would carry the value into an exception. Never echoes the value.
    """
    if not isinstance(value, str):
        raise InvalidProviderKey("api_key must be a string")
    key = value.strip()
    if not key:
        raise InvalidProviderKey("api_key must not be empty")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidProviderKey(f"api_key must be at most {MAX_KEY_LENGTH} characters")
    if any(not (0x21 <= ord(ch) <= 0x7E) for ch in key):
        raise InvalidProviderKey("api_key may contain printable ASCII characters only (no spaces)")
    return key


def fingerprint_of(key: str) -> Optional[str]:
    """Last 4 characters, only when the key is long enough (>= 8) for that to reveal little."""
    return key[-4:] if len(key) >= 8 else None


def derive_working_key(secret: Optional[str]) -> Optional[bytes]:
    """HKDF-SHA256 working key from the configured secret, or None when it is unset/unusable."""
    if not secret or not secret.strip():
        return None
    raw = secret.strip()
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        return None
    if len(decoded) < MIN_SECRET_BYTES:
        return None
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(decoded)


def _aad(org_id: str, provider: str) -> bytes:
    return _AAD_PREFIX + org_id.encode("utf-8") + b"\x00" + provider.encode("utf-8")


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class ProviderKeyStore:
    """Encrypted per-org provider keys in billing.db."""

    def __init__(self, db_path: str, secret: Optional[str]) -> None:
        self._db_path = db_path
        working = derive_working_key(secret)
        self._aead: Optional[AESGCM] = AESGCM(working) if working else None

    def __repr__(self) -> str:
        return f"ProviderKeyStore(available={self.available})"

    __str__ = __repr__

    @property
    def available(self) -> bool:
        return self._aead is not None

    async def initialize(self) -> None:
        """Create the table (also when unavailable, so a later configured restart finds it)."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with _open_db(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            for statement in _MIGRATIONS:
                try:
                    await db.execute(statement)
                except aiosqlite.OperationalError:
                    pass  # nosec B110 — column already exists, this is intentional
            await db.commit()

    # ---- crypto ------------------------------------------------------------

    def _require(self) -> AESGCM:
        if self._aead is None:
            raise StoredKeysUnavailable("stored provider keys are not configured on this server")
        return self._aead

    def encrypt(self, org_id: str, provider: str, key: str) -> bytes:
        aead = self._require()
        nonce = os.urandom(_NONCE_LEN)
        return _FORMAT_VERSION + nonce + aead.encrypt(nonce, key.encode("utf-8"), _aad(org_id, provider))

    def decrypt(self, org_id: str, provider: str, blob: Any) -> Optional[str]:
        """The plaintext key, or None when it cannot be decrypted (changed secret, tampered or
        moved row, unknown format). GCM authenticates before releasing anything: no partial output."""
        if self._aead is None or not isinstance(blob, (bytes, bytearray, memoryview)):
            return None
        data = bytes(blob)
        if len(data) < 1 + _NONCE_LEN + _TAG_LEN or data[:1] != _FORMAT_VERSION:
            return None
        nonce, body = data[1:1 + _NONCE_LEN], data[1 + _NONCE_LEN:]
        try:
            return self._aead.decrypt(nonce, body, _aad(org_id, provider)).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError):
            return None

    # ---- management --------------------------------------------------------

    async def register(self, org_id: str, provider: str, api_key: str) -> StoredKeyInfo:
        self._require()
        name = canonical_provider(provider)
        key = validate_api_key(api_key)
        now = time.time()
        blob = self.encrypt(org_id, name, key)
        fp = fingerprint_of(key)
        # key_version starts from a never-repeating value (not 1) so a key deleted and then
        # re-registered cannot be confused with the old one by touch_last_used's version guard.
        try:
            async with _open_db(self._db_path) as db:
                await db.execute(
                    "INSERT INTO provider_keys (org_id, provider, ciphertext, fingerprint, key_version, "
                    "created_at, rotated_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                    (org_id, name, blob, fp, time.time_ns(), now),
                )
                await db.commit()
        except aiosqlite.IntegrityError:
            raise ProviderKeyExists(f"a key for {name} is already stored for this org")
        logger.info("stored provider key registered: org=%s provider=%s", org_id, name)
        return StoredKeyInfo(name, executor_spec_for(name).slug, fp, now, None, None, STATUS_ACTIVE)

    async def rotate(self, org_id: str, provider: str, api_key: str) -> StoredKeyInfo:
        """Replace the stored value. Also the re-registration path for an undecryptable row."""
        self._require()
        name = canonical_provider(provider)
        key = validate_api_key(api_key)
        now = time.time()
        blob = self.encrypt(org_id, name, key)
        fp = fingerprint_of(key)
        async with _open_db(self._db_path) as db:
            cur = await db.execute(
                "UPDATE provider_keys SET ciphertext = ?, fingerprint = ?, key_version = key_version + 1, "
                "rotated_at = ?, last_used_at = NULL WHERE org_id = ? AND provider = ?",
                (blob, fp, now, org_id, name),
            )
            changed = cur.rowcount or 0
            await db.commit()
            if not changed:
                raise ProviderKeyNotFound(f"no key for {name} is stored for this org")
            async with db.execute(
                "SELECT created_at FROM provider_keys WHERE org_id = ? AND provider = ?", (org_id, name),
            ) as c2:
                row = await c2.fetchone()
        logger.info("stored provider key rotated: org=%s provider=%s", org_id, name)
        created = row[0] if row else now
        return StoredKeyInfo(name, executor_spec_for(name).slug, fp, created, now, None, STATUS_ACTIVE)

    async def delete(self, org_id: str, provider: str) -> bool:
        self._require()
        name = canonical_provider(provider)
        async with _open_db(self._db_path) as db:
            cur = await db.execute(
                "DELETE FROM provider_keys WHERE org_id = ? AND provider = ?", (org_id, name),
            )
            deleted = (cur.rowcount or 0) > 0
            await db.commit()
        if deleted:
            logger.info("stored provider key deleted: org=%s provider=%s", org_id, name)
        return deleted

    async def list(self, org_id: str) -> List[StoredKeyInfo]:
        """The org's stored keys (metadata only), ordered by provider. Each row is test-decrypted
        to set its status; the plaintext is discarded immediately."""
        self._require()
        async with _open_db(self._db_path) as db:
            async with db.execute(
                "SELECT provider, ciphertext, fingerprint, created_at, rotated_at, last_used_at "
                "FROM provider_keys WHERE org_id = ? ORDER BY provider", (org_id,),
            ) as cur:
                rows = await cur.fetchall()
        out: List[StoredKeyInfo] = []
        for provider, blob, fp, created, rotated, last_used in rows:
            spec = executor_spec_for(provider)
            if spec is None:
                continue  # not executable any more: never listed, never used
            ok = self.decrypt(org_id, provider, blob) is not None
            out.append(StoredKeyInfo(
                provider, spec.slug, fp, created, rotated, last_used,
                STATUS_ACTIVE if ok else STATUS_NEEDS_REREGISTRATION,
            ))
        return out

    async def export_metadata(self, org_id: str) -> List[Dict[str, Any]]:
        """v1.87.0 account export: the org's stored-key METADATA, never key material.

        Selects no ciphertext and no fingerprint into the result. Works when the store is
        unavailable (status then reports ``unavailable``) so an export is never blocked by it.
        """
        async with _open_db(self._db_path) as db:
            async with db.execute(
                "SELECT provider, ciphertext, key_version, created_at, rotated_at "
                "FROM provider_keys WHERE org_id = ? ORDER BY provider", (org_id,),
            ) as cur:
                rows = await cur.fetchall()
        out: List[Dict[str, Any]] = []
        for provider, blob, version, created, rotated in rows:
            if not self.available:
                status = "unavailable"
            else:
                status = (STATUS_ACTIVE if self.decrypt(org_id, provider, blob) is not None
                          else STATUS_NEEDS_REREGISTRATION)
            out.append({"provider": provider, "created_at": _iso(created), "rotated_at": _iso(rotated),
                        "key_version": version, "status": status})
        return out

    async def delete_all(self, org_id: str) -> int:
        """v1.87.0 account deletion: erase every stored key of the org. Needs no secret."""
        async with _open_db(self._db_path) as db:
            cur = await db.execute("DELETE FROM provider_keys WHERE org_id = ?", (org_id,))
            await db.commit()
            return cur.rowcount or 0

    # ---- dispatch ----------------------------------------------------------

    async def load_for_dispatch(self, org_id: str, exclude: Iterable[str] = ()) -> Dict[str, "StoredSecret"]:
        """Decrypted stored keys for one org, keyed by canonical provider name.

        Providers in ``exclude`` (already keyed by a request header) are never decrypted.
        Undecryptable rows are left out (they count as no_provider_key) after a warning that
        names only the org and provider.
        """
        if not self.available:
            return {}
        skip = set(exclude)  # canonical names (HeaderKeyResolver.providers_with_keys())
        async with _open_db(self._db_path) as db:
            async with db.execute(
                "SELECT provider, ciphertext, key_version FROM provider_keys WHERE org_id = ?", (org_id,),
            ) as cur:
                rows = await cur.fetchall()
        out: Dict[str, StoredSecret] = {}
        for provider, blob, key_version in rows:
            spec = executor_spec_for(provider)
            if spec is None or spec.provider in skip:
                continue
            value = self.decrypt(org_id, spec.provider, blob)
            if value is None:
                logger.warning(
                    "stored provider key cannot be decrypted (needs re-registration): org=%s provider=%s",
                    org_id, spec.provider,
                )
                continue
            out[spec.provider] = StoredSecret(value, int(key_version))
        return out

    async def touch_last_used(self, org_id: str, uses: List[Tuple[str, int, float]]) -> None:
        """Stamp last_used for (provider, key_version, ts) uses. Monotonic and idempotent; the
        key_version guard means a value used mid-rotation never stamps the replacement key."""
        if not uses:
            return
        async with _open_db(self._db_path) as db:
            for provider, key_version, ts in uses:
                await db.execute(
                    "UPDATE provider_keys SET last_used_at = MAX(COALESCE(last_used_at, 0), ?) "
                    "WHERE org_id = ? AND provider = ? AND key_version = ?",
                    (ts, org_id, provider, key_version),
                )
            await db.commit()


# ---------------------------------------------------------------------------
# Per-request resolver: header first, stored second
# ---------------------------------------------------------------------------

class CompositeKeyResolver:
    """ProviderKeyResolver over the request's header keys and the org's stored keys.

    A header key always wins; there is no fallback to the other key when the winning one fails.
    A snapshot taken at request start. ``repr()``/``str()`` show only provider names.
    """

    def __init__(
        self, header: HeaderKeyResolver, stored: Dict[str, StoredSecret], store: Optional[ProviderKeyStore],
        org_id: Optional[str],
    ) -> None:
        self._header = header
        self._stored = dict(stored)
        self._store = store
        self._org_id = org_id
        self._uses: List[Tuple[str, int, float]] = []

    def _stored_for(self, provider: str) -> Optional[StoredSecret]:
        spec = executor_spec_for(provider)
        return self._stored.get(spec.provider) if spec else None

    def resolve(self, provider: str) -> Optional[str]:
        key = self._header.resolve(provider)
        if key:
            return key
        secret = self._stored_for(provider)
        return secret.value if secret else None

    def providers_with_keys(self) -> List[str]:
        return sorted(set(self._header.providers_with_keys()) | set(self._stored.keys()))

    def source(self, provider: str) -> Optional[str]:
        if self._header.resolve(provider):
            return KEY_SOURCE_HEADER
        return KEY_SOURCE_STORED if self._stored_for(provider) else None

    def note_used(self, provider: str) -> None:
        if self.source(provider) != KEY_SOURCE_STORED:
            return
        spec = executor_spec_for(provider)
        secret = self._stored_for(provider)
        self._uses.append((spec.provider, secret.key_version, time.time()))

    async def flush_usage(self) -> None:
        if not self._uses:
            return
        latest: Dict[Tuple[str, int], float] = {}
        for provider, version, ts in self._uses:
            latest[(provider, version)] = max(ts, latest.get((provider, version), 0.0))
        self._uses = []
        if self._store is None or self._org_id is None:
            return
        try:
            await self._store.touch_last_used(self._org_id, [(p, v, ts) for (p, v), ts in latest.items()])
        except Exception as exc:
            logger.warning(
                "stored provider key last_used update failed: org=%s (%s)", self._org_id, type(exc).__name__,
            )

    def __repr__(self) -> str:  # redacted on purpose
        return (
            f"CompositeKeyResolver(header={self._header.providers_with_keys()}, "
            f"stored={sorted(self._stored.keys())})"
        )

    __str__ = __repr__


# ---------------------------------------------------------------------------
# Singleton wiring
# ---------------------------------------------------------------------------

_store: Optional[ProviderKeyStore] = None


def get_provider_key_store() -> ProviderKeyStore:
    if _store is None:
        raise RuntimeError("ProviderKeyStore has not been initialized")
    return _store


async def init_provider_key_store(db_path: str, secret: Optional[str]) -> ProviderKeyStore:
    global _store
    store = ProviderKeyStore(db_path, secret)
    await store.initialize()
    if not store.available:
        # Names the setting, never its value.
        logger.error(
            "%s is not set or does not decode to at least %d bytes: stored provider keys are disabled "
            "(management returns 503; dispatch is header-only)", SECRET_ENV_VAR, MIN_SECRET_BYTES,
        )
    _store = store
    return _store
