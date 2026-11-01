"""Self-service account data rights (v1.87.0): export and deletion of one organisation's data.

``GET /billing/account/export`` and ``POST /billing/account/delete`` are served by
:class:`AccountDataService`. Both need a customer service key (the handler turns "no customer"
into 401), and a key only ever reaches its own org: every store call below is scoped by the
caller's ``org_id``.

Deletion order is chosen so a failure part-way is retryable and a customer can never keep being
charged after they were told the account is gone:

1. confirmation (``confirm == "DELETE"`` and the caller's own ``org_id``), else nothing happens;
2. end the Stripe subscription (immediately) before anything is erased; any failure aborts;
3. erase pricing_history.db rows, then conversations, then stored provider keys;
4. delete the customer row and write the suppression marker in one billing.db transaction,
   so the service key stops working last.
"""
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from src.services import billing_service as _billing
from src.services import tiers as _tiers
from src.services.support_budget import support_action

logger = logging.getLogger(__name__)

EXPORT_FORMAT_VERSION = 1
EXPORT_ROW_CAP = 100_000   # per table; the export reports truncated=true when a table hits it
CONFIRM_WORD = "DELETE"

NOT_CONFIRMED_MESSAGE = (
    "deletion_not_confirmed: account deletion is irreversible and was NOT performed. To delete this account "
    "and all of its data, send confirm set to DELETE and your own org_id. Nothing has been changed."
)


class AccountDataError(Exception):
    """A refused or failed export/deletion. ``status`` is the HTTP status the route returns."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def account_dict(customer: Any) -> Dict[str, Any]:
    """The account section, built field by field so the service key can never leak into it."""
    return {
        "email": customer.email,
        "org_id": customer.org_id,
        "tier": customer.tier,
        "billing_status": customer.billing_status,
        "past_due_since": _iso(customer.past_due_since),
        "first_paid_at": _iso(customer.first_paid_at),
        "created_at": _iso(customer.created_at),
        "updated_at": _iso(customer.updated_at),
        "stripe_customer_id": customer.stripe_customer_id,
        "stripe_subscription_id": customer.stripe_subscription_id,
        "service_key_prefix": customer.api_key_prefix,
    }


def _capped(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], bool]:
    cap = EXPORT_ROW_CAP
    return (rows[:cap], len(rows) > cap)


class AccountDataService:
    def __init__(
        self,
        billing: Any,
        usage: Any,
        receipts: Any,
        budget_alerts: Any,
        savings: Any,
        provider_keys: Any,
        conversations: Any,
        settings: Any,
        cancel_subscription: Optional[Callable[[str], Awaitable[Any]]] = None,
        orchestrator_getter: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.billing = billing
        self.usage = usage
        self.receipts = receipts
        self.budget_alerts = budget_alerts
        self.savings = savings
        self.provider_keys = provider_keys
        self.conversations = conversations
        self.settings = settings
        self._cancel = cancel_subscription or self._default_cancel
        self._orchestrator_getter = orchestrator_getter

    async def _default_cancel(self, subscription_id: str) -> Any:
        return await _billing.cancel_stripe_subscription(subscription_id, self.settings.stripe_secret_key)

    # ------------------------------------------------------------------ export

    async def export(self, customer: Any) -> Dict[str, Any]:
        org = customer.org_id
        cap = EXPORT_ROW_CAP
        usage, t1 = _capped(await self.usage.list_events(org, limit=cap + 1))
        receipts, t2 = _capped(await self.receipts.list_receipts(org, limit=cap + 1))
        alerts, t3 = _capped(await self.budget_alerts.list_alerts(org))
        routing, t4 = _capped(await self.savings.list_routing(org, limit=cap + 1))
        feedback, t5 = _capped(await self.savings.list_feedback(org, limit=cap + 1))
        conversations, t6 = _capped(await self.conversations.list_for_org(org))
        provider_keys = await self.provider_keys.export_metadata(org)
        data = {
            "format_version": EXPORT_FORMAT_VERSION,
            "exported_at": _iso(time.time()),
            "account": account_dict(customer),
            "provider_keys": provider_keys,
            "usage_events": usage,
            "dispatch_receipts": receipts,
            "budget_alerts": alerts,
            "routing_savings": routing,
            "routing_feedback": feedback,
            "support_conversations": conversations,
        }
        data["counts"] = {k: len(v) for k, v in data.items() if isinstance(v, list)}
        data["truncated"] = any((t1, t2, t3, t4, t5, t6))
        await support_action(
            "billing.account_export", "exported", actor="customer", target=f"org:{org[:8]}",
            trigger="http:/billing/account/export", provenance=dict(data["counts"]),
        )
        return data

    # ------------------------------------------------------------------ delete

    async def delete(self, customer: Any, confirm: Optional[str], org_id: Optional[str]) -> Dict[str, Any]:
        org = customer.org_id
        target = f"org:{org[:8]}"
        trigger = "http:/billing/account/delete"
        if confirm != CONFIRM_WORD or org_id != org:
            await support_action("billing.account_delete", "refused:unconfirmed", actor="customer",
                                 target=target, trigger=trigger)
            raise AccountDataError(400, "deletion_not_confirmed", NOT_CONFIRMED_MESSAGE)

        sub_id = customer.stripe_subscription_id
        in_window = _billing.within_refund_window(customer, self.settings.billing_refund_window_days)
        subscription_ended = False
        if sub_id:
            if not self.settings.stripe_secret_key:
                await support_action("billing.account_delete", "refused:stripe_unavailable", actor="customer",
                                     target=target, trigger=trigger)
                raise AccountDataError(
                    503, "stripe_unavailable",
                    "Your account has a subscription but payments are not reachable from this server, so it "
                    "cannot be ended safely. Nothing was deleted. Try again later or ask the support assistant.",
                )
            try:
                await self._cancel(sub_id)
            except Exception as exc:  # any Stripe failure other than 'already cancelled' aborts
                logger.error("account deletion: Stripe cancel failed for org %s (%s)", org[:8], type(exc).__name__)
                await support_action("billing.account_delete", "refused:stripe_error", actor="customer",
                                     target=target, trigger=trigger)
                raise AccountDataError(
                    502, "stripe_error",
                    "We could not end your subscription with the payment provider, so nothing was deleted "
                    "and you have not been charged anything new. Please try again.",
                ) from exc
            subscription_ended = True

        rows: Dict[str, int] = {}
        steps: List[Tuple[str, Callable[[], Awaitable[Any]]]] = [
            ("usage_events", lambda: self.usage.delete_org(org)),
            ("dispatch_receipts", lambda: self.receipts.delete_org(org)),
            ("budget_alerts", lambda: self.budget_alerts.delete_org(org)),
            ("routing_savings", lambda: self.savings.delete_org(org)),
            ("support_conversations", lambda: self.conversations.delete_for_org(org)),
            ("provider_keys", lambda: self.provider_keys.delete_all(org)),
        ]
        for name, step in steps:
            try:
                rows[name] = int(await step() or 0)
            except Exception as exc:
                logger.error("account deletion: step %s failed for org %s (%s)", name, org[:8], type(exc).__name__)
                await support_action("billing.account_delete", f"error:{name}", actor="customer", target=target,
                                     trigger=trigger, provenance={"subscription_ended": subscription_ended})
                raise AccountDataError(
                    500, "deletion_incomplete",
                    "Deletion did not finish. Your subscription may already have ended. Send the request again "
                    "to complete it, or ask the support assistant.",
                ) from exc
        try:
            deleted_at = await self.billing.delete_customer(customer.id)
        except Exception as exc:
            logger.error("account deletion: customer row failed for org %s (%s)", org[:8], type(exc).__name__)
            await support_action("billing.account_delete", "error:customer", actor="customer", target=target,
                                 trigger=trigger, provenance={"subscription_ended": subscription_ended})
            raise AccountDataError(500, "deletion_incomplete",
                                   "Deletion did not finish. Send the request again to complete it.") from exc
        if deleted_at is None:
            raise AccountDataError(404, "not_found", "Account not found")

        reference = None
        if in_window:
            reference = await self._open_refund_review(customer, deleted_at)
        await support_action(
            "billing.account_delete", "deleted", actor="customer", target=target, trigger=trigger,
            provenance={"subscription_ended": subscription_ended, "refund_escalation": bool(reference),
                        "rows": rows},
        )
        message = ("Your account and all of its data have been permanently deleted. This is irreversible. "
                   "Your service key no longer works.")
        if subscription_ended:
            message += " Your subscription has ended and no further charges will occur."
        if reference:
            message += (f" Because you deleted within {self.settings.billing_refund_window_days} days of your "
                        f"first payment, a refund review was opened for the owner. Reference: {reference}.")
        return {
            "deleted": True, "org_id": org, "deleted_at": _iso(deleted_at),
            "subscription_ended": subscription_ended, "refund_escalation_reference": reference,
            "message": message,
        }

    async def _open_refund_review(self, customer: Any, deleted_at: float) -> Optional[str]:
        """Open a support_escalation run (category refund) for the owner. No money moves.
        The account is already gone, so a failure here is logged, never raised."""
        from src.services.orchestrator_service import get_orchestrator_service
        org8 = customer.org_id[:8]
        window = self.settings.billing_refund_window_days
        if customer.first_paid_at is None:
            when = "an unknown time after first payment (counted as inside the window)"
        else:
            when = f"{int((deleted_at - customer.first_paid_at) // 86400)} days after first payment"
        payload = {
            "trigger_class": "customer_message",
            "event_type": "account.deletion_refund_review",
            "dedup_key": f"msg:account_delete:{org8}:{int(deleted_at)}",
            "actor": "account_deletion",
            "actor_is_owner": False,
            "source": {"kind": "account_deletion", "ref": "http:/billing/account/delete"},
            "data": {
                "title": "refund review: account deleted inside the refund window",
                "body": f"{org8} (plan {customer.tier}) deleted {when}; the full-refund window is {window} days.",
                "category": "refund",
                "priority": _tiers.escalation_priority_for(customer.tier),
                "tier": customer.tier,
                "identified": True,
                "customer_ref": org8,
            },
        }
        try:
            svc = (self._orchestrator_getter or get_orchestrator_service)()
            result = await svc.intake_event(payload, actor_kind="account_service")
        except Exception as exc:  # the deletion already happened; never turn it into a 500
            logger.error("account deletion: refund review for org %s failed (%s)", org8, type(exc).__name__)
            return None
        return result.get("run_id")


def build_account_data_service(settings: Any, cancel_subscription=None) -> AccountDataService:
    """Wire the service from the module singletons. A missing store is a 503, never a silent skip,
    because a partial export or deletion would be a false statement."""
    from agent.conversation import get_conversation_store
    from src.services.billing_service import get_billing_service
    from src.services.budget_alerts import get_budget_alert_service
    from src.services.dispatch_receipts import get_dispatch_receipt_store
    from src.services.provider_key_store import get_provider_key_store
    from src.services.savings_tracker import get_savings_tracker
    from src.services.usage_tracker import get_usage_tracker
    try:
        return AccountDataService(
            billing=get_billing_service(), usage=get_usage_tracker(), receipts=get_dispatch_receipt_store(),
            budget_alerts=get_budget_alert_service(), savings=get_savings_tracker(),
            provider_keys=get_provider_key_store(), conversations=get_conversation_store(),
            settings=settings, cancel_subscription=cancel_subscription,
        )
    except RuntimeError as exc:
        raise AccountDataError(503, "service_unavailable", "Account data services are not ready") from exc
