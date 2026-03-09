"""Pricing alert service: register webhooks that fire when prices change beyond a threshold."""
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS pricing_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    threshold_pct REAL NOT NULL DEFAULT 5.0,
    provider TEXT,
    model_name TEXT,
    created_at REAL NOT NULL
)
"""

_INSERT = """
INSERT INTO pricing_alerts (url, threshold_pct, provider, model_name, created_at)
VALUES (?, ?, ?, ?, ?)
"""

_SELECT_ALL = """
SELECT id, url, threshold_pct, provider, model_name, created_at
FROM pricing_alerts
ORDER BY id
"""

_DELETE = "DELETE FROM pricing_alerts WHERE id = ?"

_EXISTS = "SELECT 1 FROM pricing_alerts WHERE id = ?"


class PricingAlertService:
    """Store alert registrations in SQLite and fire webhooks on price changes."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def initialize(self) -> None:
        """Create the alerts table if it does not exist."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.commit()

    async def register(
        self,
        url: str,
        threshold_pct: float,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a new alert and return the stored record."""
        import aiosqlite
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(_INSERT, (url, threshold_pct, provider, model_name, now))
            alert_id = cursor.lastrowid
            await db.commit()
        return {
            "id": alert_id,
            "url": url,
            "threshold_pct": threshold_pct,
            "provider": provider,
            "model_name": model_name,
            "created_at": now,
        }

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
        async with httpx.AsyncClient(timeout=10.0) as client:
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
            resp = await client.post(alert["url"], content=body, headers=headers)
            logger.info(
                "Alert %d fired to %s — %s (HTTP %d)",
                alert["id"], alert["url"], trend["model_name"], resp.status_code,
            )
            return 1
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
