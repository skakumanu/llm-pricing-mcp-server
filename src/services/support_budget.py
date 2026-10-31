"""Support agent spend caps and action records (v1.84.0).

Two layers bound what the support agent may spend on the operator's LLM key:

1. **Per-caller daily counters** (this module): in-memory ``dict`` + ``asyncio.Lock`` with an
   hourly cleanup, the ``rate_limiter.py`` idiom. Anonymous callers are keyed ``ip:<client ip>``
   (``src.main._client_ip_for_support``: the Fly proxy's ``Fly-Client-IP`` first, so a client-
   prepended ``X-Forwarded-For`` hop cannot mint fresh buckets; the hosted ``/mcp`` handler passes
   the same address; never persisted), customers by ``cust:<customer id>``.
   Caps come from ``settings.support_daily_cap_*`` by tier (anonymous strictest) and reset at
   UTC midnight. **Limitation**: counters are per worker process (the Fly image runs
   ``--workers 2``), so a caller can spend up to ``workers x cap`` per day; the authoritative,
   cross-worker bound is layer 2.
2. **The orchestrator spend ledger**: every LLM call's cost is written to ``orch_spend``
   (``OrchestratorService.record_agent_spend``, workflow ``support_triage``) and
   ``OrchestratorService.budget_gate`` is asked before every call, so the global monthly ceiling
   and the per-workflow cap stop support chat for everyone. If the orchestrator store is not
   initialised the agent fails closed (no LLM call).

``support_action()`` is the one helper every support surface uses to append an
``orch_actions`` record (operator-only, append-only); it never carries message text, emails or
keys, and it swallows a missing store so an audit hiccup cannot turn into a customer-facing 500.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from src.services.tiers import support_cap_for

logger = logging.getLogger(__name__)

ANONYMOUS_TIER = "anonymous"


def utc_day(ts: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if ts is None else ts))


def next_utc_midnight_iso(ts: Optional[float] = None) -> str:
    ts = time.time() if ts is None else ts
    midnight = (int(ts) // 86400 + 1) * 86400
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(midnight))


def caller_key(caller: Any, client_ip: Optional[str]) -> str:
    """Bucket key: the customer id when identified, else the client address. ``mcp-anonymous`` is
    the shared fallback only for a call path that has no connection (direct ToolManager use)."""
    if caller is not None and getattr(caller, "is_customer", False) and getattr(caller, "customer_id", None):
        return f"cust:{caller.customer_id}"
    return f"ip:{client_ip or 'mcp-anonymous'}"


def tier_label(caller: Any) -> str:
    if caller is not None and getattr(caller, "is_customer", False):
        return getattr(caller, "tier", None) or "free"
    return ANONYMOUS_TIER


@dataclass
class _Counter:
    day: str
    spent_usd: float = 0.0
    escalations: int = 0
    last_seen: float = field(default_factory=time.time)


class SupportBudget:
    """Per-caller daily USD and escalation counters (one instance per process)."""

    def __init__(self, settings: Any, clock=time.time) -> None:
        self._settings = settings
        self._clock = clock
        self._counters: Dict[str, _Counter] = {}
        self._lock = asyncio.Lock()
        self._last_cleanup = clock()

    def cap_for(self, tier: str) -> float:
        return support_cap_for(None if tier == ANONYMOUS_TIER else tier, self._settings)

    def escalation_limit_for(self, tier: str) -> int:
        if tier == ANONYMOUS_TIER:
            return 1
        return max(0, int(self._settings.support_max_escalations_per_day))

    def _counter(self, key: str) -> _Counter:
        today = utc_day(self._clock())
        c = self._counters.get(key)
        if c is None or c.day != today:
            c = _Counter(day=today, last_seen=self._clock())
            self._counters[key] = c
        c.last_seen = self._clock()
        return c

    async def _cleanup(self) -> None:
        now = self._clock()
        if now - self._last_cleanup < 3600:
            return
        stale = now - 2 * 86400
        for key in [k for k, c in self._counters.items() if c.last_seen < stale]:
            del self._counters[key]
        self._last_cleanup = now

    async def check(self, key: str, tier: str) -> Tuple[bool, float, float]:
        """(allowed, spent_today_usd, cap_usd). Allowed while spent < cap."""
        cap = self.cap_for(tier)
        async with self._lock:
            await self._cleanup()
            spent = self._counter(key).spent_usd
        return spent < cap, spent, cap

    async def record(self, key: str, cost_usd: float) -> float:
        async with self._lock:
            c = self._counter(key)
            c.spent_usd += max(0.0, float(cost_usd))
            return c.spent_usd

    async def spent_today(self, key: str) -> float:
        async with self._lock:
            return self._counter(key).spent_usd

    async def escalation_allowed(self, key: str, tier: str) -> bool:
        async with self._lock:
            return self._counter(key).escalations < self.escalation_limit_for(tier)

    async def record_escalation(self, key: str) -> int:
        async with self._lock:
            c = self._counter(key)
            c.escalations += 1
            return c.escalations


def estimate_turn_cost(tokens_in: int, tokens_out: int, pricing: Any, settings: Any) -> float:
    """USD for one LLM call. ``pricing`` is the agent model's catalogue row
    (``PricingAggregator.find_model_pricing``) or None, in which case the fallback constants apply."""
    tokens_in = max(0, int(tokens_in or 0))
    tokens_out = max(0, int(tokens_out or 0))
    if pricing is not None:
        price_in = float(pricing.cost_per_input_token)
        price_out = float(pricing.cost_per_output_token)
    else:
        price_in = float(settings.support_fallback_price_per_1m_in_usd) / 1_000_000
        price_out = float(settings.support_fallback_price_per_1m_out_usd) / 1_000_000
    return tokens_in * price_in + tokens_out * price_out


async def support_action(action: str, outcome: str, actor: str = "support_agent", target: Optional[str] = None,
                         trigger: Optional[str] = None, provenance: Optional[Dict[str, Any]] = None,
                         run_id: Optional[str] = None) -> Optional[str]:
    """Append one operator-only action record. Never message text, emails or keys."""
    from src.services.orchestrator_store import get_orchestrator_store
    from src.services.orchestrator_workflows import redact_untrusted_text
    try:
        store = get_orchestrator_store()
    except RuntimeError:
        logger.warning("support action %s not recorded: orchestrator store not initialised", action)
        return None
    row = {
        "run_id": run_id, "actor": actor[:60], "at": time.time(), "action": action[:60],
        "target": redact_untrusted_text(str(target))[:200] if target is not None else None,
        "trigger": trigger[:100] if trigger else None, "outcome": redact_untrusted_text(str(outcome))[:100],
        "provenance_json": _bounded(provenance) if provenance else None,
    }
    try:
        return await store.append_action(row)
    except Exception as exc:  # audit failure must not become a customer-facing 500
        logger.warning("support action %s not recorded: %s", action, type(exc).__name__)
        return None


def _bounded(prov: Dict[str, Any]) -> Dict[str, Any]:
    from src.services.orchestrator_workflows import redact_untrusted_text
    out: Dict[str, Any] = {}
    for k, v in list(prov.items())[:20]:
        if isinstance(v, str):
            out[str(k)[:40]] = redact_untrusted_text(v)[:200]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[str(k)[:40]] = v
        elif isinstance(v, (list, tuple)):
            out[str(k)[:40]] = [redact_untrusted_text(str(x))[:60] for x in list(v)[:20]]
        else:
            out[str(k)[:40]] = redact_untrusted_text(str(v))[:200]
    return out


_budget: Optional[SupportBudget] = None


def get_support_budget(settings: Any) -> SupportBudget:
    """Process-wide counters (created on first use with the given settings)."""
    global _budget
    if _budget is None:
        _budget = SupportBudget(settings)
    return _budget


def reset_support_budget() -> None:
    """Tests only."""
    global _budget
    _budget = None
