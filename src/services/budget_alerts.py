"""Budget alert service: register webhooks that fire when actual spend crosses a threshold.

Same webhook/signing mechanism as pricing_alerts.py (register a URL, HMAC-SHA256 sign
with WEBHOOK_SECRET), but triggered by recorded usage_events spend instead of a price
drift. Checked inline after usage is recorded (src/main.py's /usage endpoints), not on
a periodic schedule — a budget alert is about crossing a threshold as soon as it happens.

v1.77.0: an alert may additionally be flagged ``hard_stop``. The dispatch gateway asks
``find_hard_stop(org_id, usage_tracker)`` before every dispatch and refuses with 402 when
the org's recorded spend has reached such an alert's threshold. Only org-scoped alerts can
stop anyone — an org-less alert stays notify-only regardless of the flag.
"""
import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import httpx

from src.services import webhook_guard
from src.services.webhook_guard import PinnedWebhookTransport, WebhookURLRejected

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
    last_fired_at REAL,
    hard_stop INTEGER NOT NULL DEFAULT 0
)
"""

_INSERT = """
INSERT INTO budget_alerts (url, org_id, threshold_usd, period_days, created_at, last_fired_at, hard_stop)
VALUES (?, ?, ?, ?, ?, NULL, ?)
"""

_SELECT_ALL = """
SELECT id, url, org_id, threshold_usd, period_days, created_at, last_fired_at, hard_stop
FROM budget_alerts
ORDER BY id
"""

_SELECT_BY_ORG = """
SELECT id, url, org_id, threshold_usd, period_days, created_at, last_fired_at, hard_stop
FROM budget_alerts
WHERE org_id = ?
ORDER BY id
"""

_UPDATE_LAST_FIRED = "UPDATE budget_alerts SET last_fired_at = ? WHERE id = ?"

_DELETE = "DELETE FROM budget_alerts WHERE id = ?"

_EXISTS = "SELECT 1 FROM budget_alerts WHERE id = ?"

_EXISTS_IN_ORG = "SELECT 1 FROM budget_alerts WHERE id = ? AND org_id = ?"


class BudgetAlertService:
    """Store budget alert registrations in SQLite and fire webhooks when spend crosses threshold_usd."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    async def initialize(self) -> None:
        """Create the budget_alerts table if it does not exist and apply pending migrations."""
        import aiosqlite
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            # Migrate: add hard_stop to budget_alerts tables created before v1.77.0. Existing
            # alerts default to 0 (notify-only) — they were registered before a stop existed.
            try:
                await db.execute(
                    "ALTER TABLE budget_alerts ADD COLUMN hard_stop INTEGER NOT NULL DEFAULT 0"
                )
            except aiosqlite.OperationalError:
                pass  # nosec B110 — column already exists, this is intentional
            await db.commit()

    async def register(
        self,
        url: str,
        threshold_usd: float,
        org_id: Optional[str] = None,
        period_days: int = 30,
        hard_stop: bool = False,
    ) -> Dict[str, Any]:
        """Persist a new budget alert and return the stored record.

        Raises WebhookURLRejected (nothing stored) when ``url`` breaks the webhook rules.
        """
        await webhook_guard.validate_webhook_url(url)
        import aiosqlite
        now = time.time()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                _INSERT, (url, org_id, threshold_usd, period_days, now, 1 if hard_stop else 0)
            )
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
            "hard_stop": bool(hard_stop),
        }

    async def list_alerts(self, org_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return registered budget alerts; with ``org_id``, only that org's (v1.77.0 customer scoping)."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if org_id is None:
                cur = await db.execute(_SELECT_ALL)
            else:
                cur = await db.execute(_SELECT_BY_ORG, (org_id,))
            rows = await cur.fetchall()
            await cur.close()
        alerts = []
        for r in rows:
            row = dict(r)
            row["hard_stop"] = bool(row.get("hard_stop", 0))
            alerts.append(row)
        return alerts

    async def delete_org(self, org_id: str) -> int:
        """v1.87.0 account deletion: erase every budget alert of one org."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute("DELETE FROM budget_alerts WHERE org_id = ?", (org_id,))
            await db.commit()
            return cur.rowcount or 0

    async def delete(self, alert_id: int, org_id: Optional[str] = None) -> bool:
        """Delete a budget alert by ID. Returns True if a row was deleted.

        With ``org_id`` the alert must belong to that org (a customer key cannot delete
        another org's alert — it looks like "not found").
        """
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            if org_id is None:
                cur = await db.execute(_EXISTS, (alert_id,))
            else:
                cur = await db.execute(_EXISTS_IN_ORG, (alert_id, org_id))
            exists = await cur.fetchone()
            await cur.close()
            if not exists:
                return False
            await db.execute(_DELETE, (alert_id,))
            await db.commit()
        return True

    async def find_hard_stop(
        self,
        org_id: Optional[str],
        usage_tracker: "UsageTrackerService",
    ) -> Optional[Dict[str, Any]]:
        """Return the first hard_stop alert for `org_id` whose threshold recorded spend has reached.

        Independent of the webhook cooldown: a stop holds for as long as the spend over
        the alert's own period stays at or above its threshold. Org-less alerts never
        match (they are notify-only), and a caller without an org can never be stopped.
        """
        if not org_id:
            return None
        alerts = [
            a for a in await self.list_alerts()
            if a.get("hard_stop") and a.get("org_id") == org_id
        ]
        for alert in alerts:
            summary = await usage_tracker.get_summary(org_id=org_id, days=alert["period_days"])
            spent = summary["total_cost_usd"]
            if spent >= alert["threshold_usd"]:
                return {
                    "alert_id": alert["id"],
                    "org_id": org_id,
                    "threshold_usd": alert["threshold_usd"],
                    "period_days": alert["period_days"],
                    "spent_usd": round(spent, 6),
                }
        return None

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
        # v1.80.1: never follow redirects, ignore env proxies, connect only to a checked IP.
        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=False,
            trust_env=False,
            transport=PinnedWebhookTransport(),
        ) as client:
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
            # v1.80.1: skip stored URLs that break the rules (legacy rows) before any I/O.
            webhook_guard.check_url_static(alert["url"])
            resp = await client.post(alert["url"], content=body, headers=headers)
            logger.info(
                "Budget alert %d fired to %s — $%.2f spent (HTTP %d)",
                alert["id"], alert["url"], summary["total_cost_usd"], resp.status_code,
            )
            return 1
        except WebhookURLRejected as exc:
            logger.warning("Budget alert %d delivery blocked (%s) for %s", alert["id"], exc.reason, alert["url"])
            return 0
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
