"""Budget alert service: register webhooks that fire when actual spend crosses a threshold.

Same webhook/signing mechanism as pricing_alerts.py (register a URL, HMAC-SHA256 sign
with WEBHOOK_SECRET), but triggered by recorded usage_events spend instead of a price
drift. Checked inline after usage is recorded (src/main.py's /usage endpoints), not on
a periodic schedule — a budget alert is about crossing a threshold as soon as it happens.
"""
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from src.services.usage_tracker import UsageTrackerService

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS budget_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    org_id TEXT,
    threshold_usd REAL NOT NULL,
    period_days INTEGER NOT NULL DEFAULT 30,
    created_at REAL NOT NULL,
    last_fired_at REAL
)
"""

_INSERT = """
INSERT INTO budget_alerts (url, org_id, threshold_usd, period_days, created_at, last_fired_at)
VALUES (?, ?, ?, ?, ?, NULL)
"""

_SELECT_ALL = """
SELECT id, url, org_id, threshold_usd, period_days, created_at, last_fired_at
FROM budget_alerts
ORDER BY id
"""

_UPDATE_LAST_FIRED = "UPDATE budget_alerts SET last_fired_at = ? WHERE id = ?"

_DELETE = "DELETE FROM budget_alerts WHERE id = ?"

_EXISTS = "SELECT 1 FROM budget_alerts WHERE id = ?"


class BudgetAlertService:
    """Store budget alert registrations in SQLite and fire webhooks when spend crosses threshold_usd."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def initialize(self) -> None:
        """Create the budget_alerts table if it does not exist."""
        import aiosqlite
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.commit()

    async def register(
        self,
        url: str,
        threshold_usd: float,
        org_id: Optional[str] = None,
        period_days: int = 30,
    ) -> Dict[str, Any]:
        """Persist a new budget alert and return the stored record."""
        import aiosqlite
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(_INSERT, (url, org_id, threshold_usd, period_days, now))
            alert_id = cursor.lastrowid
            await db.commit()
        return {
            "id": alert_id,
            "url": url,
            "org_id": org_id,
            "threshold_usd": threshold_usd,
            "period_days": period_days,
            "created_at": now,
            "last_fired_at": None,
        }

    async def list_alerts(self) -> List[Dict[str, Any]]:
        """Return all registered budget alerts."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(_SELECT_ALL) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete(self, alert_id: int) -> bool:
        """Delete a budget alert by ID. Returns True if a row was deleted."""
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
        self,
        usage_tracker: "UsageTrackerService",
        secret: Optional[str] = None,
    ) -> int:
        """
        Re-evaluate every registered alert against current spend and fire matching webhooks.

        For each alert, looks up actual spend for its own `org_id`/`period_days` via
        `usage_tracker.get_summary()`. Fires when spend has reached `threshold_usd` and
        the alert hasn't already fired within the last `period_days` (a simple cooldown
        so a sustained over-budget state doesn't re-fire on every usage event).

        Returns the number of webhooks fired.
        """
        alerts = await self.list_alerts()
        if not alerts:
            return 0

        now = time.time()
        fired = 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            for alert in alerts:
                if alert["last_fired_at"] is not None:
                    cooldown_until = alert["last_fired_at"] + alert["period_days"] * 86400
                    if now < cooldown_until:
                        continue

                summary = await usage_tracker.get_summary(
                    org_id=alert["org_id"], days=alert["period_days"]
                )
                if summary["total_cost_usd"] < alert["threshold_usd"]:
                    continue

                fired += await self._fire(client, alert, summary, secret=secret)
                await self._mark_fired(alert["id"], now)
        return fired

    async def _mark_fired(self, alert_id: int, when: float) -> None:
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_UPDATE_LAST_FIRED, (when, alert_id))
            await db.commit()

    @staticmethod
    async def _fire(
        client: httpx.AsyncClient,
        alert: Dict[str, Any],
        summary: Dict[str, Any],
        *,
        secret: Optional[str] = None,
    ) -> int:
        payload = {
            "alert_id": alert["id"],
            "org_id": alert["org_id"],
            "threshold_usd": alert["threshold_usd"],
            "period_days": alert["period_days"],
            "total_cost_usd": summary["total_cost_usd"],
            "total_requests": summary["total_requests"],
        }
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"}
        if secret:
            sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-LLM-Pricing-Signature"] = f"sha256={sig}"
        try:
            resp = await client.post(alert["url"], content=body, headers=headers)
            logger.info(
                "Budget alert %d fired to %s — $%.2f spent (HTTP %d)",
                alert["id"], alert["url"], summary["total_cost_usd"], resp.status_code,
            )
            return 1
        except Exception as exc:
            logger.warning("Budget alert %d delivery failed to %s: %s", alert["id"], alert["url"], exc)
            return 0


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_budget_alert_service: Optional[BudgetAlertService] = None


def get_budget_alert_service() -> BudgetAlertService:
    """Return the singleton BudgetAlertService (must call initialize first)."""
    if _budget_alert_service is None:
        raise RuntimeError("BudgetAlertService has not been initialized")
    return _budget_alert_service


async def init_budget_alert_service(db_path: str) -> BudgetAlertService:
    """Create, initialize, and register the singleton service."""
    global _budget_alert_service
    _budget_alert_service = BudgetAlertService(db_path)
    await _budget_alert_service.initialize()
    return _budget_alert_service
