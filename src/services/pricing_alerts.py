"""Pricing alert service: register webhooks that fire when prices change beyond a threshold."""
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.services import webhook_guard
from src.services.webhook_guard import PinnedWebhookTransport, WebhookURLRejected

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# v1.81.0: per-alert self-service credential (alert_token)
# ---------------------------------------------------------------------------
# Returned once at registration; only its SHA-256 hash is stored. The "lpa_" prefix
# is not secret — it lets redact_alert_tokens() and secret scanners recognise it.
ALERT_TOKEN_PREFIX = "lpa_"
_TOKEN_BODY_LEN = 43  # secrets.token_urlsafe(32) -> 43 url-safe chars, 256 bits
ALERT_TOKEN_RE = re.compile(r"^lpa_[A-Za-z0-9_-]{%d}$" % _TOKEN_BODY_LEN)
_REDACT_RE = re.compile(r"(?<![A-Za-z0-9_-])lpa_[A-Za-z0-9_-]{16,}")
ALERT_TOKEN_REDACTED = "[redacted alert token]"
ALERT_TOKEN_NOTICE = (
    "Save this alert_token now. It is shown only once and cannot be recovered later, "
    "not even by the operator. You need it to view or delete this alert: "
    "POST /pricing/alerts/self/view or /pricing/alerts/self/delete with "
    '{"alert_token": ...}, or the manage_price_alert MCP tool. Never put it in a URL.'
)
# One refusal for wrong, malformed, deleted, unknown and legacy-row tokens.
ALERT_TOKEN_REFUSAL_MESSAGE = "No alert matches this alert_token."

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pricing_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    threshold_pct REAL NOT NULL DEFAULT 5.0,
    provider TEXT,
    model_name TEXT,
    created_at REAL NOT NULL,
    token_hash TEXT
)
"""

# v1.81.0: migration-on-open for tables created before token_hash existed.
_ADD_TOKEN_HASH = "ALTER TABLE pricing_alerts ADD COLUMN token_hash TEXT"
_CREATE_TOKEN_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_pricing_alerts_token_hash "
    "ON pricing_alerts(token_hash)"
)

_INSERT = """
INSERT INTO pricing_alerts (url, threshold_pct, provider, model_name, created_at, token_hash)
VALUES (?, ?, ?, ?, ?, ?)
"""

# The six public columns, matched by hash only (never by id). The unique index on
# token_hash is what makes this an exact lookup; the hash itself is never returned.
_SELECT_BY_TOKEN_HASH = """
SELECT id, url, threshold_pct, provider, model_name, created_at
FROM pricing_alerts
WHERE token_hash = ?
"""

_DELETE_BY_TOKEN_HASH = "DELETE FROM pricing_alerts WHERE id = ? AND token_hash = ?"

_SELECT_ALL = """
SELECT id, url, threshold_pct, provider, model_name, created_at
FROM pricing_alerts
ORDER BY id
"""

_DELETE = "DELETE FROM pricing_alerts WHERE id = ?"

_EXISTS = "SELECT 1 FROM pricing_alerts WHERE id = ?"

_PUBLIC_FIELDS = ("id", "url", "threshold_pct", "provider", "model_name", "created_at")


def _new_token() -> str:
    """A fresh 256-bit alert_token."""
    return ALERT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    """SHA-256 hex digest stored in place of the token (no pepper: 256 random bits)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def redact_alert_tokens(text: Any) -> Any:
    """Replace every alert_token-shaped substring with ALERT_TOKEN_REDACTED.

    Non-string input is returned unchanged. Used for persisted chat turns and log lines.
    """
    if not isinstance(text, str):
        return text
    return _REDACT_RE.sub(ALERT_TOKEN_REDACTED, text)


class PricingAlertService:
    """Store alert registrations in SQLite and fire webhooks on price changes."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def initialize(self) -> None:
        """Create the alerts table if it does not exist."""
        import aiosqlite
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            try:
                await db.execute(_ADD_TOKEN_HASH)
            except aiosqlite.OperationalError:
                pass  # nosec B110 — column already exists
            await db.execute(_CREATE_TOKEN_INDEX)
            await db.commit()

    async def register(
        self,
        url: str,
        threshold_pct: float,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a new alert and return the stored record.

        The record carries ``alert_token`` (v1.81.0) — the only time the raw token
        exists; just its SHA-256 hash is stored — plus ``alert_token_notice``.

        Raises WebhookURLRejected (nothing stored) when ``url`` breaks the webhook rules.
        """
        await webhook_guard.validate_webhook_url(url)
        import aiosqlite
        now = time.time()
        token = _new_token()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                _INSERT, (url, threshold_pct, provider, model_name, now, _hash_token(token))
            )
            alert_id = cursor.lastrowid
            await db.commit()
        return {
            "id": alert_id,
            "url": url,
            "threshold_pct": threshold_pct,
            "provider": provider,
            "model_name": model_name,
            "created_at": now,
            "alert_token": token,
            "alert_token_notice": ALERT_TOKEN_NOTICE,
        }

    @staticmethod
    async def _lookup(db: Any, token: Any) -> Optional[tuple]:
        """``(record, token_hash)`` for the alert ``token`` manages, else None.

        ``record`` holds only the six public fields. Anything that is not a well-formed
        alert_token (None, empty, short, non-ASCII) returns None without querying, so a
        NULL legacy hash is never bound or matched.
        """
        if not isinstance(token, str) or not ALERT_TOKEN_RE.fullmatch(token):
            return None
        computed = _hash_token(token)
        async with db.execute(_SELECT_BY_TOKEN_HASH, (computed,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return dict(zip(_PUBLIC_FIELDS, row)), computed

    async def get_by_token(self, token: Any) -> Optional[Dict[str, Any]]:
        """The one alert this alert_token manages (six public fields), or None."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            found = await self._lookup(db, token)
        return None if found is None else found[0]

    async def delete_by_token(self, token: Any) -> Optional[int]:
        """Delete the one alert this alert_token manages; its id, or None if none matched."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            found = await self._lookup(db, token)
            if found is None:
                return None
            record, token_hash = found
            cursor = await db.execute(_DELETE_BY_TOKEN_HASH, (record["id"], token_hash))
            await db.commit()
            if cursor.rowcount < 1:
                return None
        logger.info("Alert %d deleted by its alert_token", record["id"])
        return record["id"]

    async def list_alerts(self) -> List[Dict[str, Any]]:
        """Return all registered alerts."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(_SELECT_ALL) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete(self, alert_id: int) -> bool:
        """Delete an alert by ID. Returns True if a row was deleted."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            async with db.execute(_EXISTS, (alert_id,)) as cur:
                exists = await cur.fetchone()
            if not exists:
                return False
            await db.execute(_DELETE, (alert_id,))
            await db.commit()
        return True

    async def check_and_fire(
        self, trends: List[Dict[str, Any]], secret: Optional[str] = None
    ) -> int:
        """
        Compare each trend against registered alerts and fire matching webhooks.

        An alert matches a trend when:
        - The alert's provider/model_name filters match (None = wildcard)
        - The absolute change in input OR output price exceeds threshold_pct

        When ``secret`` is provided the POST body is signed with HMAC-SHA256 and
        the digest is included as ``X-LLM-Pricing-Signature: sha256=<hex>``.

        Returns the number of webhooks fired.
        """
        alerts = await self.list_alerts()
        if not alerts or not trends:
            return 0

        fired = 0
        # v1.80.1: never follow redirects, ignore env proxies, connect only to a checked IP.
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
            transport=PinnedWebhookTransport(),
        ) as client:
            for trend in trends:
                max_change = max(
                    abs(trend.get("input_change_pct", 0.0)),
                    abs(trend.get("output_change_pct", 0.0)),
                )
                for alert in alerts:
                    if not self._matches(alert, trend):
                        continue
                    if max_change < alert["threshold_pct"]:
                        continue
                    fired += await self._fire(client, alert, trend, secret=secret)
        return fired

    @staticmethod
    def _matches(alert: Dict[str, Any], trend: Dict[str, Any]) -> bool:
        if alert["provider"] and alert["provider"] != trend.get("provider"):
            return False
        if alert["model_name"] and alert["model_name"] != trend.get("model_name"):
            return False
        return True

    @staticmethod
    async def _fire(
        client: httpx.AsyncClient,
        alert: Dict[str, Any],
        trend: Dict[str, Any],
        *,
        secret: Optional[str] = None,
    ) -> int:
        payload = {
            "alert_id": alert["id"],
            "threshold_pct": alert["threshold_pct"],
            "model_name": trend["model_name"],
            "provider": trend["provider"],
            "input_change_pct": trend["input_change_pct"],
            "output_change_pct": trend["output_change_pct"],
            "direction": trend["direction"],
            "first_seen": trend["first_seen"],
            "last_seen": trend["last_seen"],
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"}
        if secret:
            sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-LLM-Pricing-Signature"] = f"sha256={sig}"
        try:
            # v1.80.1: skip stored URLs that break the rules (legacy rows) before any I/O.
            webhook_guard.check_url_static(alert["url"])
            resp = await client.post(alert["url"], content=body, headers=headers)
            logger.info(
                "Alert %d fired to %s — %s (HTTP %d)",
                alert["id"], alert["url"], trend["model_name"], resp.status_code,
            )
            return 1
        except WebhookURLRejected as exc:
            logger.warning("Alert %d delivery blocked (%s) for %s", alert["id"], exc.reason, alert["url"])
            return 0
        except Exception as exc:
            logger.warning("Alert %d delivery failed to %s: %s", alert["id"], alert["url"], exc)
            return 0


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_alert_service: Optional[PricingAlertService] = None


def get_pricing_alert_service() -> PricingAlertService:
    """Return the singleton PricingAlertService (must call initialize first)."""
    if _alert_service is None:
        raise RuntimeError("PricingAlertService has not been initialized")
    return _alert_service


async def init_pricing_alert_service(db_path: str) -> PricingAlertService:
    """Create, initialize, and register the singleton service."""
    global _alert_service
    _alert_service = PricingAlertService(db_path)
    await _alert_service.initialize()
    return _alert_service


# ---------------------------------------------------------------------------
# Receiver-side helper
# ---------------------------------------------------------------------------

def verify_webhook_signature(
    secret: str,
    body: bytes,
    signature_header: str,
) -> bool:
    """Verify an ``X-LLM-Pricing-Signature`` header value.

    Use this on the **receiving** end of a webhook to confirm the POST
    originated from this server and has not been tampered with.

    Args:
        secret:           The shared ``WEBHOOK_SECRET`` value.
        body:             The raw request body bytes (read before any JSON parsing).
        signature_header: The full value of the ``X-LLM-Pricing-Signature`` header
                          (e.g. ``"sha256=abc123..."``).

    Returns:
        ``True`` if the signature is valid, ``False`` otherwise.

    Example::

        from src.services.pricing_alerts import verify_webhook_signature

        @app.post("/webhook")
        async def receive_alert(request: Request):
            body = await request.body()
            sig  = request.headers.get("X-LLM-Pricing-Signature", "")
            if not verify_webhook_signature(SECRET, body, sig):
                raise HTTPException(status_code=401, detail="Invalid signature")
            payload = json.loads(body)
            ...
    """
    if not signature_header.startswith("sha256="):
        return False
    expected_hex = signature_header[len("sha256="):]
    computed = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, expected_hex)
