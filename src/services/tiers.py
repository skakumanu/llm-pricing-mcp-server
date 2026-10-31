"""Tier catalogue: the single source of truth for customer-facing tier copy (v1.84.0).

Everything a customer can read about a tier (price, rate limit, support allowance,
escalation priority) is built here from ``settings`` and never from literals elsewhere.
``/billing`` and the landing page render these exact strings, ``docs/FAQ.md`` quotes the
same numbers, and the support agent's ``get_plan_catalog`` tool returns ``tier_catalog()``,
so the four surfaces cannot disagree (``tests/test_support_faq.py`` pins them to each other).

Only entitlements that are actually implemented appear here: the per-minute rate limit
(security middleware), the support assistant's daily allowance (support_budget.py) and the
escalation priority (orchestrator approval-queue ordering). Nothing else is advertised.

Pure module: no I/O, no settings import (the caller passes the settings object).
"""
from typing import Any, Dict, List

TIER_IDS = ("free", "pro", "enterprise")

# Owner decision (decisions_agents_program.md): prices stay $29 / $299; the HTML keeps the
# literal "$29" (tests/test_currency_ui.py pins it) and tests pin the literal to this map.
PRICE_USD_MONTH: Dict[str, int] = {"free": 0, "pro": 29, "enterprise": 299}

# Priority label used by the orchestrator approval queue (enterprise > pro > standard).
ESCALATION_PRIORITY: Dict[str, str] = {"free": "standard", "pro": "pro", "enterprise": "enterprise"}
PRIORITY_RANK: Dict[str, int] = {"enterprise": 0, "pro": 1, "standard": 2}

# Phrases that described entitlements with no implementation. They must not appear on
# any customer-facing surface (billing page, landing, FAQ, agent prompt).
FORBIDDEN_ENTITLEMENT_PHRASES = (
    "Priority routing", "Usage analytics", "SLA guarantee", "Dedicated support", "Email support",
    "Custom integrations", "SSO & audit logs", "Per-org tracking",
)


def rate_limit_for(tier: str, settings: Any) -> int:
    limits = {
        "free": settings.rate_limit_free,
        "pro": settings.rate_limit_pro,
        "enterprise": settings.rate_limit_enterprise,
    }
    return int(limits.get(tier, settings.rate_limit_per_minute))


def support_cap_for(tier: Any, settings: Any) -> float:
    """Daily USD allowance of LLM-backed support for a tier; ``None``/unknown = anonymous."""
    caps = {
        "free": settings.support_daily_cap_free_usd,
        "pro": settings.support_daily_cap_pro_usd,
        "enterprise": settings.support_daily_cap_enterprise_usd,
    }
    return float(caps.get(tier, settings.support_daily_cap_anonymous_usd))


def escalation_priority_for(tier: Any) -> str:
    return ESCALATION_PRIORITY.get(tier, "standard")


def _usd(amount: float) -> str:
    return f"${amount:.2f}"


def entitlements(tier: str, settings: Any) -> List[str]:
    """Exact customer-facing bullet strings for one tier (used verbatim by the UIs)."""
    if tier not in TIER_IDS:
        raise KeyError(tier)
    bullets = [f"{rate_limit_for(tier, settings)} requests / minute"]
    if tier == "free":
        bullets.append("Live pricing, router and every tool")
    else:
        bullets.append("Everything in Free")
    bullets.append(f"Support assistant: {_usd(support_cap_for(tier, settings))}/day allowance")
    priority = escalation_priority_for(tier)
    if priority == "standard":
        bullets.append("Escalations: standard priority")
    elif priority == "pro":
        bullets.append("Escalations: pro priority (ahead of free)")
    else:
        bullets.append("Escalations: enterprise priority (first in the queue)")
    return bullets


def stripe_configured(settings: Any) -> bool:
    """Stripe secret set: the one prerequisite for both Checkout and the customer portal."""
    return bool(getattr(settings, "stripe_secret_key", None))


# Two response fields, one fact: the portal cannot be enabled independently of checkout.
checkout_enabled = stripe_configured
portal_enabled = stripe_configured


def _checkout_available(tier: str, settings: Any) -> bool:
    if tier == "free" or not checkout_enabled(settings):
        return False
    price_id = {
        "pro": getattr(settings, "stripe_price_id_pro", None),
        "enterprise": getattr(settings, "stripe_price_id_enterprise", None),
    }.get(tier)
    return bool(price_id)


def catalog_payload(settings: Any) -> Dict[str, Any]:
    """The public catalogue payload: ``GET /billing/config`` serves it and the support agent's
    ``get_plan_catalog`` tool returns it, so the two cannot drift apart."""
    return {
        "checkout_enabled": checkout_enabled(settings),
        "portal_enabled": portal_enabled(settings),
        "past_due_grace_days": int(settings.billing_past_due_grace_days),
        "tiers": tier_catalog(settings),
    }


def tier_catalog(settings: Any) -> List[Dict[str, Any]]:
    """Per-tier entries of ``catalog_payload``."""
    out = []
    for tier in TIER_IDS:
        out.append({
            "id": tier,
            "name": tier.capitalize(),
            "price_usd_month": PRICE_USD_MONTH[tier],
            "rate_limit_per_minute": rate_limit_for(tier, settings),
            "support_allowance_usd_day": support_cap_for(tier, settings),
            "escalation_priority": escalation_priority_for(tier),
            "entitlements": entitlements(tier, settings),
            "checkout_available": _checkout_available(tier, settings),
        })
    return out
