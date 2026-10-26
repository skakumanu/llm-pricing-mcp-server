"""Dispatch gateway service (v1.77.0).

Turns a chat request + dispatch policy into one real provider call (with failover)
and a receipt that says what was chosen, why, what it cost and what it saved.

Flow (see docs/ARCHITECTURE.md "Dispatch gateway"):
  budget hard stop -> catalogue + enrichment -> eligibility stages (first failing
  reason per model, stage that empties the set = eliminating constraint) -> router
  ranking re-ordered by objective -> per-request cost cap -> failover loop (shadow
  concurrently) -> ledgers (usage event, routing decision + feedback, budget alerts,
  receipt row) -> OpenAI-shaped response + additive dispatch_receipt.

Privacy: this module logs ids, model/provider names, outcomes, status codes,
latencies, token counts and costs — never message or completion text, never a
provider key, never a provider error body. Keys are read only through the
ProviderKeyResolver passed per request and handed straight to the executor.
"""
import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.models.dispatch import DispatchPolicy
from src.services.benchmark_service import enrich_models
from src.services.cost_calculator import compute_token_cost
from src.services.currency import LocaleContext, conversion_metadata, convert
from src.services.dispatch_executors import (
    EXECUTABLE_PROVIDERS, OUTCOME_AUTH_ERROR, OUTCOME_CONTEXT_TOO_LONG, OUTCOME_OK,
    OUTCOME_PROVIDER_REJECTED, OUTCOME_TIMEOUT, ChatRequest, ChatResult, ExecutorError,
    ExecutorSpec, ProviderKeyResolver, executor_spec_for, get_executor, is_executable_provider,
)
from src.services.router import ModelRouter, RouterConstraints
from src.services.token_counter import count_tokens

logger = logging.getLogger(__name__)


class DispatchError(Exception):
    """A refusal or failure the endpoint turns into a JSON error response.

    ``payload`` carries only ids, model/provider names, policy values, outcomes and
    status codes — never request content or key material.
    """

    def __init__(self, status_code: int, payload: Dict[str, Any]):
        self.status_code = status_code
        self.payload = payload
        super().__init__(payload.get("error", "dispatch_error"))


# Exclusion reason codes -> human phrase (both appear in receipts and errors).
REASON_TEXT: Dict[str, str] = {
    "subscription_pricing": "subscription pricing, not per-token",
    "price_unconfirmed": "price not confirmed",
    "provider_not_executable": "provider is recommend-only (not executable)",
    "no_provider_key": "no key supplied for provider",
    "denied_by_policy": "denied by policy",
    "not_in_allow_list": "not in allow list",
    "below_quality_floor": "below quality floor",
    "above_cost_ceiling": "above cost ceiling",
    "context_window_too_small": "context window too small",
    "task_type_mismatch": "task type mismatch",
    "reasoning_model_excluded": "reasoning model excluded",
    "over_monthly_budget": "over monthly budget",
    "over_request_cost_cap": "exceeds per-request cost cap",
}

# Exclusion reason -> the policy field / constraint it names in an error.
REASON_TO_CONSTRAINT: Dict[str, str] = {
    "subscription_pricing": "per_token_pricing",
    "price_unconfirmed": "price_confirmed",
    "provider_not_executable": "executable_provider",
    "no_provider_key": "provider_key",
    "denied_by_policy": "deny",
    "not_in_allow_list": "allow",
    "below_quality_floor": "min_quality_score",
    "above_cost_ceiling": "max_cost_per_1m_tokens",
    "context_window_too_small": "min_context_window",
    "task_type_mismatch": "task_type",
    "reasoning_model_excluded": "exclude_reasoning_models",
    "over_monthly_budget": "monthly_budget_usd",
    "over_request_cost_cap": "max_request_cost_usd",
}

# Reasons that describe the catalogue rather than the caller's policy. Exclusions for these
# are summarised as counts unless the provider is executable (then they are listed).
_CATALOGUE_REASONS = frozenset({"subscription_pricing", "price_unconfirmed", "provider_not_executable"})


def _usd(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 10)


def _blended_per_1m(m) -> float:
    return (m.cost_per_input_token + m.cost_per_output_token) / 2 * 1_000_000


@dataclass
class Candidate:
    model: Any                      # PricingMetrics
    spec: ExecutorSpec
    blended_per_1m: float
    estimated_usd: float


@dataclass
class Attempt:
    n: int
    model: str
    provider: str
    outcome: str
    http_status: Optional[int]
    latency_ms: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "n": self.n, "model": self.model, "provider": self.provider, "outcome": self.outcome,
            "http_status": self.http_status, "latency_ms": round(self.latency_ms, 1),
        }


class DispatchService:
    """Policy, eligibility, ranking, failover, shadow, receipt and ledger writes."""

    def __init__(
        self,
        aggregator,
        router: ModelRouter,
        usage_tracker,
        savings_tracker,
        budget_alerts,
        receipts,
        executor_factory: Optional[Callable[[ExecutorSpec, float], Any]] = None,
        default_max_tokens: int = 1024,
        attempt_timeout_ms: int = 30_000,
        max_attempts: int = 3,
    ) -> None:
        self._aggregator = aggregator
        self._router = router
        self._usage = usage_tracker
        self._savings = savings_tracker
        self._budget = budget_alerts
        self._receipts = receipts
        self._executor_factory = executor_factory or (
            lambda spec, timeout_s: get_executor(spec, timeout_s, default_max_tokens=default_max_tokens)
        )
        self._default_max_tokens = default_max_tokens
        self._attempt_timeout_ms = attempt_timeout_ms
        self._max_attempts = max_attempts

    # ------------------------------------------------------------------
    # Discoverability (GET /dispatch/providers)
    # ------------------------------------------------------------------

    async def describe_executable(
        self, provider: Optional[str] = None, dispatchable_only: bool = False
    ) -> Dict[str, Any]:
        models, _ = await self._aggregator.get_all_pricing_async(include_unconfirmed=True)
        rows = []
        per_provider: Counter = Counter()
        recommend_only = set()
        for m in models:
            reason = self._catalogue_reason(m)
            dispatchable = reason is None
            if dispatchable:
                per_provider[executor_spec_for(m.provider).provider] += 1
            if not is_executable_provider(m.provider):
                recommend_only.add(m.provider)
            if provider and m.provider.lower() != provider.strip().lower():
                continue
            if dispatchable_only and not dispatchable:
                continue
            rows.append({
                "model_name": m.model_name,
                "provider": m.provider,
                "dispatchable": dispatchable,
                "reason": reason,
            })
        executable = [
            {
                "provider": spec.provider,
                "family": spec.family,
                "base_url": spec.base_url,
                "key_header": spec.key_header,
                "dispatchable_models": per_provider.get(spec.provider, 0),
            }
            for spec in EXECUTABLE_PROVIDERS.values()
            if not provider or spec.provider.lower() == provider.strip().lower()
        ]
        return {
            "executable_providers": executable,
            "recommend_only_providers": sorted(recommend_only),
            "models": rows,
            "total_models": len(models),
            "total_dispatchable": sum(per_provider.values()),
            "note": (
                "Dispatch is an opt-in mode of POST /v1/chat/completions: send a `dispatch` policy, a free-tier "
                "service API key (x-api-key or Authorization: Bearer) and your own provider key in the listed "
                "key_header. Only models with a confirmed price under an executable provider are ever dispatched; "
                "everything else is recommend-only."
            ),
        }

    # ------------------------------------------------------------------
    # Eligibility
    # ------------------------------------------------------------------

    @staticmethod
    def _catalogue_reason(m) -> Optional[str]:
        """Catalogue-level dispatchability (independent of the caller)."""
        if getattr(m, "pricing_model", "per_token") != "per_token":
            return "subscription_pricing"
        if not getattr(m, "price_confirmed", True):
            return "price_unconfirmed"  # covers retired models too (the oracle unconfirms them)
        if not is_executable_provider(m.provider):
            return "provider_not_executable"
        return None

    @staticmethod
    def _matches_list(m, entries: List[str]) -> bool:
        name = m.model_name.strip().lower()
        prov = m.provider.strip().lower()
        for raw in entries:
            e = (raw or "").strip().lower()
            if not e:
                continue
            if e.startswith("provider:"):
                if e[len("provider:"):].strip() == prov:
                    return True
            elif e == name:
                return True
        return False

    def _estimate_usd(self, m, input_tokens: int, max_tokens: int) -> float:
        return compute_token_cost(m.cost_per_input_token, m.cost_per_output_token, input_tokens, max_tokens).total_cost

    def _build_constraints(self, policy: DispatchPolicy, task_type: Optional[str]) -> RouterConstraints:
        return RouterConstraints(
            max_cost_per_1m_tokens=policy.max_cost_per_1m_tokens,
            min_quality_score=policy.min_quality_score,
            min_context_window=policy.min_context_window,
            preferred_provider=policy.preferred_provider,
            task_type=task_type,
            prefer_low_latency=policy.objective == "lowest_latency",
            exclude_reasoning_models=policy.exclude_reasoning_models,
        )

    def _filter(
        self,
        models: List[Any],
        policy: DispatchPolicy,
        constraints: RouterConstraints,
        key_resolver: ProviderKeyResolver,
        est_input_tokens: int,
        effective_max_tokens: int,
    ) -> Tuple[List[Candidate], List[Dict[str, Any]], Dict[str, int], Optional[str], Dict[str, str]]:
        """Run the eligibility stages in fixed order.

        Returns (eligible candidates, explicit exclusions, summary counts, eliminating
        reason code or None, {model_name_lower: reason} for pinned lookups).
        """
        excluded: List[Dict[str, Any]] = []
        summary: Counter = Counter()
        reason_by_model: Dict[str, str] = {}
        eliminating: Optional[str] = None

        def exclude(m, reason: str) -> None:
            reason_by_model[f"{m.provider.lower()}::{m.model_name.lower()}"] = reason
            if reason in _CATALOGUE_REASONS and not is_executable_provider(m.provider):
                summary[reason] += 1
            else:
                excluded.append({
                    "model": m.model_name, "provider": m.provider,
                    "reason": reason, "detail": REASON_TEXT.get(reason, reason),
                })

        stages: List[Tuple[str, Callable[[Any], Optional[str]]]] = [
            # Same predicate GET /dispatch/providers uses to say "dispatchable", so the
            # discoverability endpoint and the real gate cannot drift apart.
            ("catalogue", self._catalogue_reason),
            ("no_provider_key", lambda m: None if key_resolver.resolve(m.provider) else "no_provider_key"),
            ("denied_by_policy", lambda m: "denied_by_policy"
             if policy.deny and self._matches_list(m, policy.deny) else None),
            ("not_in_allow_list", lambda m: "not_in_allow_list"
             if policy.allow and not self._matches_list(m, policy.allow) else None),
            ("router", lambda m: self._router.hard_filter_reason(m, constraints)),
        ]
        if policy.max_request_cost_usd is not None:
            cap = policy.max_request_cost_usd
            stages.append((
                "over_request_cost_cap",
                lambda m: "over_request_cost_cap"
                if self._estimate_usd(m, est_input_tokens, effective_max_tokens) > cap else None,
            ))

        remaining = list(models)
        for stage_name, fn in stages:
            survivors = []
            stage_reasons: Counter = Counter()
            for m in remaining:
                reason = fn(m)
                if reason:
                    exclude(m, reason)
                    stage_reasons[reason] += 1
                else:
                    survivors.append(m)
            if not survivors and eliminating is None and remaining:
                eliminating = stage_reasons.most_common(1)[0][0] if stage_reasons else stage_name
            remaining = survivors

        eligible = [
            Candidate(
                model=m,
                spec=executor_spec_for(m.provider),
                blended_per_1m=_blended_per_1m(m),
                estimated_usd=self._estimate_usd(m, est_input_tokens, effective_max_tokens),
            )
            for m in remaining
        ]
        return eligible, excluded, dict(summary), eliminating, reason_by_model

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------

    def _order(self, cands: List[Candidate], objective: str, constraints: RouterConstraints) -> List[Candidate]:
        by_id = {id(c.model): c for c in cands}
        ranked = self._router.rank_models([c.model for c in cands], constraints)
        ordered = [by_id[id(m)] for m in ranked if id(m) in by_id]
        # Stable sorts: ties keep the router's order.
        if objective == "cheapest":
            ordered.sort(key=lambda c: (c.blended_per_1m, -(c.model.quality_score or 0.0)))
        elif objective == "best_quality":
            ordered.sort(key=lambda c: (-(c.model.quality_score or 0.0), c.blended_per_1m))
        elif objective == "lowest_latency":
            ordered.sort(key=lambda c: (c.model.latency_ms is None, c.model.latency_ms or 0.0, c.blended_per_1m))
        return ordered

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _run_attempt(self, cand: Candidate, chat_req: ChatRequest, api_key: str, timeout_s: float) -> ChatResult:
        executor = self._executor_factory(cand.spec, timeout_s)
        req = ChatRequest(
            model=cand.model.model_name, messages=chat_req.messages, max_tokens=chat_req.max_tokens,
            temperature=chat_req.temperature, top_p=chat_req.top_p, stop=chat_req.stop,
            reasoning_model=bool(getattr(cand.model, "is_reasoning_model", False)),
        )
        try:
            return await asyncio.wait_for(executor.execute(req, api_key), timeout=timeout_s)
        except asyncio.TimeoutError:
            raise ExecutorError(OUTCOME_TIMEOUT)

    async def _primary_loop(
        self,
        ordered: List[Candidate],
        chat_req: ChatRequest,
        key_resolver: ProviderKeyResolver,
        timeout_s: float,
        max_attempts: int,
        skip: Optional[Candidate] = None,
    ) -> Tuple[Optional[Candidate], Optional[ChatResult], List[Attempt], Optional[ExecutorError], int]:
        """Return (chosen, result, attempts, terminal error, candidates never tried).

        ``skip`` is the shadow candidate when one is in flight: failover never lands on
        it, so the same model is never billed twice for one request and the receipt's
        A/B block always compares two different models.
        """
        attempts: List[Attempt] = []
        min_context: Optional[int] = None
        idx = 0
        while idx < len(ordered) and len(attempts) < max_attempts:
            cand = ordered[idx]
            idx += 1
            if skip is not None and cand is skip:
                continue
            if min_context is not None:
                cw = cand.model.context_window
                if cw is None or cw <= min_context:
                    continue  # context_too_long: only a larger window can help
            started = time.perf_counter()
            api_key = key_resolver.resolve(cand.model.provider) or ""
            try:
                result = await self._run_attempt(cand, chat_req, api_key, timeout_s)
            except ExecutorError as err:
                elapsed = (time.perf_counter() - started) * 1000.0
                attempts.append(Attempt(
                    len(attempts) + 1, cand.model.model_name, cand.model.provider,
                    err.outcome, err.http_status, elapsed,
                ))
                if err.outcome in (OUTCOME_AUTH_ERROR, OUTCOME_PROVIDER_REJECTED):
                    return None, None, attempts, err, len(ordered) - idx
                if err.outcome == OUTCOME_CONTEXT_TOO_LONG:
                    min_context = cand.model.context_window or 0
                continue
            attempts.append(Attempt(
                len(attempts) + 1, cand.model.model_name, cand.model.provider, OUTCOME_OK,
                result.http_status, result.provider_latency_ms,
            ))
            return cand, result, attempts, None, len(ordered) - idx
        return None, None, attempts, None, max(0, len(ordered) - idx)

    async def _shadow_run(
        self, cand: Candidate, chat_req: ChatRequest, key_resolver: ProviderKeyResolver, timeout_s: float
    ) -> Dict[str, Any]:
        started = time.perf_counter()
        api_key = key_resolver.resolve(cand.model.provider) or ""
        try:
            res = await self._run_attempt(cand, chat_req, api_key, timeout_s)
        except ExecutorError as err:
            return self._shadow_failure(cand, err.outcome, err.http_status, started)
        except Exception:  # a shadow failure never fails the request
            logger.debug("dispatch shadow %s/%s raised unexpectedly", cand.model.provider, cand.model.model_name)
            return self._shadow_failure(cand, "error", None, started)
        cost = compute_token_cost(
            cand.model.cost_per_input_token, cand.model.cost_per_output_token, res.input_tokens, res.output_tokens,
        ).total_cost
        return {
            "model": cand.model.model_name, "provider": cand.model.provider,
            "outcome": "success", "http_status": res.http_status,
            "input_tokens": res.input_tokens, "output_tokens": res.output_tokens, "cost_usd": _usd(cost),
            "provider_latency_ms": round(res.provider_latency_ms, 1),
        }

    @staticmethod
    def _shadow_failure(cand: Candidate, outcome: str, http_status: Optional[int],
                        started: Optional[float]) -> Dict[str, Any]:
        """The one shape every non-success shadow report has (ExecutorError, unexpected exception)."""
        return {
            "model": cand.model.model_name, "provider": cand.model.provider,
            "outcome": outcome, "http_status": http_status,
            "input_tokens": None, "output_tokens": None, "cost_usd": None,
            "provider_latency_ms": round((time.perf_counter() - started) * 1000.0, 1) if started else None,
        }

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        *,
        customer,
        requested_model: str,
        messages: List[Dict[str, Any]],
        policy: DispatchPolicy,
        key_resolver: ProviderKeyResolver,
        locale: LocaleContext,
        inferred_task_type: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        stop: Optional[List[str]] = None,
        webhook_secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        t_start = time.perf_counter()
        dispatch_id = str(uuid.uuid4())
        org_id = getattr(customer, "org_id", None)

        # b) Budget hard stop — before any eligibility work, zero provider calls.
        stop_hit = await self._budget.find_hard_stop(org_id, self._usage)
        if stop_hit:
            raise DispatchError(402, {
                "error": "budget_hard_stop",
                "message": (
                    f"Dispatch refused: recorded spend ${stop_hit['spent_usd']:.6f} over the last "
                    f"{stop_hit['period_days']} day(s) has reached budget alert {stop_hit['alert_id']}'s "
                    f"hard-stop threshold of ${stop_hit['threshold_usd']:.2f}."
                ),
                **stop_hit,
            })

        # c) Catalogue (unconfirmed included so they can be *reported* excluded, never dispatched).
        models, _ = await self._aggregator.get_all_pricing_async(include_unconfirmed=True)
        models = await enrich_models(list(models or []))
        task_type = policy.task_type or inferred_task_type
        task_type_source = "policy" if policy.task_type else ("inferred" if inferred_task_type else None)
        constraints = self._build_constraints(policy, task_type)

        plain_messages = [
            {"role": str(m.get("role") or "user"), "content": str(m.get("content") or "")} for m in messages
        ]
        est_input_tokens = count_tokens(" ".join(m["content"] for m in plain_messages)) if plain_messages else 0
        effective_max_tokens = max_tokens if max_tokens is not None else self._default_max_tokens
        # A per-request cost cap is estimated against effective_max_tokens, so the provider must be
        # bounded by that same number or the cap is only advisory. Without a cap the caller's own
        # max_tokens (possibly none) is forwarded as-is.
        bound_max_tokens = effective_max_tokens if policy.max_request_cost_usd is not None else max_tokens
        chat_req = ChatRequest(
            model="", messages=plain_messages, max_tokens=bound_max_tokens, temperature=temperature, top_p=top_p,
            stop=stop,
        )

        # d) Eligibility.
        eligible, excluded, excluded_summary, eliminating, reason_by_model = self._filter(
            models, policy, constraints, key_resolver, est_input_tokens, effective_max_tokens,
        )
        policy_dump = self._policy_dump(policy)

        pinned: Optional[Candidate] = None
        if policy.objective == "pinned":
            target = policy.pinned_model.strip().lower()
            pinned_matches = [c for c in eligible if c.model.model_name.lower() == target]
            if pinned_matches:
                pinned = min(pinned_matches, key=lambda c: c.blended_per_1m)
            else:
                reason = next((r for k, r in reason_by_model.items() if k.endswith(f"::{target}")), None)
                in_catalogue = any(m.model_name.lower() == target for m in models)
                code = reason or ("not_in_catalogue" if not in_catalogue else "ineligible")
                raise DispatchError(400, {
                    "error": "pinned_model_ineligible",
                    "pinned_model": policy.pinned_model,
                    "reason": code,
                    "detail": REASON_TEXT.get(
                        code, "model is not in the catalogue" if code == "not_in_catalogue" else code,
                    ),
                    "constraint": REASON_TO_CONSTRAINT.get(code),
                })

        if not eligible:
            constraint = REASON_TO_CONSTRAINT.get(eliminating or "", eliminating)
            raise DispatchError(400, {
                "error": "no_eligible_candidate",
                "message": self._no_candidate_message(eliminating, policy, task_type, key_resolver),
                "eliminating_constraint": constraint,
                "constraint_value": self._constraint_value(eliminating, policy, task_type),
                "excluded": excluded,
                "excluded_summary": excluded_summary,
            })

        # e) Ranking + objective ordering.
        ordered = [pinned] if pinned else self._order(eligible, policy.objective, constraints)
        primary = ordered[0]

        # h) Shadow selection (never an error; skipped with a reason instead).
        shadow_cand, shadow_skip = self._select_shadow(policy, eligible, primary, constraints, reason_by_model)

        # g) Failover parameters.
        failover_on = policy.failover.enabled and not pinned
        max_attempts = (policy.failover.max_attempts or self._max_attempts) if failover_on else 1
        timeout_s = (policy.failover.timeout_ms or self._attempt_timeout_ms) / 1000.0

        shadow_report: Optional[Dict[str, Any]] = None
        if policy.shadow is not None:
            if shadow_cand is None:
                shadow_report = {"model": policy.shadow.model, "provider": None, "outcome": "skipped",
                                 "skip_reason": shadow_skip, "input_tokens": None, "output_tokens": None,
                                 "cost_usd": None, "provider_latency_ms": None}
            else:
                primary_task = self._primary_loop(
                    ordered, chat_req, key_resolver, timeout_s, max_attempts, skip=shadow_cand,
                )
                shadow_task = self._shadow_run(shadow_cand, chat_req, key_resolver, timeout_s)
                # return_exceptions so a primary failure still lets the shadow finish and be
                # recorded (its spend is real money); _shadow_run itself only lets
                # CancelledError/KeyboardInterrupt escape.
                primary_out, shadow_out = await asyncio.gather(primary_task, shadow_task, return_exceptions=True)
                shadow_report = (
                    self._shadow_failure(shadow_cand, "error", None, None)
                    if isinstance(shadow_out, BaseException) else shadow_out
                )
                if isinstance(primary_out, BaseException):
                    raise primary_out
                chosen, result, attempts, terminal, remaining_untried = primary_out
                await self._record_shadow_spend(shadow_report, org_id, policy)
        if policy.shadow is None or shadow_cand is None:
            chosen, result, attempts, terminal, remaining_untried = await self._primary_loop(
                ordered, chat_req, key_resolver, timeout_s, max_attempts,
            )

        attempts_dicts = [a.as_dict() for a in attempts]
        total_ms = (time.perf_counter() - t_start) * 1000.0

        if result is None or chosen is None:
            if attempts:  # never reached a provider -> not a receipt row
                await self._record_receipt(
                    dispatch_id, org_id, requested_model, policy, policy_dump, task_type, attempts_dicts,
                    total_ms, shadow_report,
                )
                logger.info("dispatch %s: outcome=failed attempts=%d org=%s", dispatch_id, len(attempts), org_id)
            if terminal is not None and terminal.outcome == OUTCOME_AUTH_ERROR:
                failed = attempts[-1]
                raise DispatchError(400, {
                    "error": "provider_auth_failed",
                    "message": (
                        f"{failed.provider} rejected the provider key supplied in "
                        f"{executor_spec_for(failed.provider).key_header} (HTTP {failed.http_status}). "
                        "The key is the caller's; dispatch does not route around it."
                    ),
                    "provider": failed.provider,
                    "model": failed.model,
                    "http_status": failed.http_status,
                    "attempts": attempts_dicts,
                    "dispatch_id": dispatch_id,
                })
            if terminal is not None and terminal.outcome == OUTCOME_PROVIDER_REJECTED:
                failed = attempts[-1]
                raise DispatchError(502, {
                    "error": "provider_rejected",
                    "message": (
                        f"{failed.provider} rejected the request for {failed.model} (HTTP {failed.http_status})."
                    ),
                    "provider": failed.provider,
                    "model": failed.model,
                    "http_status": failed.http_status,
                    "attempts": attempts_dicts,
                    "dispatch_id": dispatch_id,
                })
            elim = self._exhausted_constraint(excluded)
            constraint = REASON_TO_CONSTRAINT.get(elim or "", elim)
            raise DispatchError(502, {
                "error": "all_attempts_failed",
                "message": self._exhausted_message(
                    attempts, remaining_untried, elim, policy, task_type, failover_on,
                ),
                "attempts": attempts_dicts,
                "eligible_remaining": remaining_untried,
                "eliminating_constraint": constraint if remaining_untried == 0 else None,
                "constraint_value": (
                    self._constraint_value(elim, policy, task_type) if remaining_untried == 0 else None
                ),
                "failover_enabled": failover_on,
                "max_attempts": max_attempts,
                "dispatch_id": dispatch_id,
            })

        # i) Costs, savings, ledgers.
        m = chosen.model
        cost = compute_token_cost(
            m.cost_per_input_token, m.cost_per_output_token, result.input_tokens, result.output_tokens,
        )
        actual = _usd(cost.total_cost)

        most_expensive = max(eligible, key=lambda c: c.blended_per_1m)
        me_cost = _usd(compute_token_cost(
            most_expensive.model.cost_per_input_token, most_expensive.model.cost_per_output_token,
            result.input_tokens, result.output_tokens,
        ).total_cost)
        savings_me = _usd(max(0.0, me_cost - actual))
        savings_me_pct = round(savings_me / me_cost * 100.0, 2) if me_cost and me_cost > 0 else 0.0

        vs_requested = self._savings_vs_requested(requested_model, models, chosen, result, actual)
        forgone = self._forgone_vs_cheapest(pinned, eligible, policy, result, actual) if pinned else None

        if shadow_report and shadow_report.get("outcome") == "success":
            shadow_report["cost_delta_usd"] = _usd(shadow_report["cost_usd"] - actual)
            shadow_report["latency_delta_ms"] = round(
                shadow_report["provider_latency_ms"] - result.provider_latency_ms, 1
            )
        elif shadow_report is not None:
            shadow_report.setdefault("cost_delta_usd", None)
            shadow_report.setdefault("latency_delta_ms", None)

        await self._write_ledgers(
            dispatch_id, customer, chosen, result, actual, most_expensive, task_type, policy, webhook_secret,
        )

        total_ms = (time.perf_counter() - t_start) * 1000.0
        receipt = {
            "dispatch_id": dispatch_id,
            "request_id": policy.request_id,
            "live_call": True,
            "objective": policy.objective,
            "task_type": task_type,
            "task_type_source": task_type_source,
            "policy": policy_dump,
            "reason": self._reason_text(policy, chosen, eligible, pinned),
            "chosen": {
                "model": m.model_name,
                "provider": m.provider,
                "quality_score": m.quality_score,
                "cost_per_1m_blended_usd": round(chosen.blended_per_1m, 6),
                "cost_per_input_token": m.cost_per_input_token,
                "cost_per_output_token": m.cost_per_output_token,
                "context_window": m.context_window,
                "latency_ms": m.latency_ms,
                "price_as_of": m.price_as_of,
                "price_source_kind": getattr(m, "price_source_kind", "provider"),
                "pinned": bool(pinned),
            },
            "candidates": [
                {
                    "rank": i + 1,
                    "model": c.model.model_name,
                    "provider": c.model.provider,
                    "quality_score": c.model.quality_score,
                    "cost_per_1m_blended_usd": round(c.blended_per_1m, 6),
                    "latency_ms": c.model.latency_ms,
                    "context_window": c.model.context_window,
                    "estimated_cost_usd": _usd(c.estimated_usd),
                }
                for i, c in enumerate(ordered if not pinned else self._order(eligible, "balanced", constraints))
            ],
            "excluded": excluded,
            "excluded_summary": excluded_summary,
            "usage": {
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.input_tokens + result.output_tokens,
            },
            "cost": {
                "actual_usd": actual,
                "input_usd": _usd(cost.input_cost),
                "output_usd": _usd(cost.output_cost),
                "estimated_usd_before_call": _usd(chosen.estimated_usd),
            },
            "savings": {
                "vs_most_expensive_eligible": {
                    "model": most_expensive.model.model_name,
                    "provider": most_expensive.model.provider,
                    "cost_usd": me_cost,
                    "savings_usd": savings_me,
                    "savings_pct": savings_me_pct,
                },
                "vs_requested_model": vs_requested,
                "forgone_vs_cheapest_meeting_floor": forgone,
            },
            "latency": {
                "total_ms": round(total_ms, 1),
                "provider_ms": round(result.provider_latency_ms, 1),
                "overhead_ms": round(max(0.0, total_ms - result.provider_latency_ms), 1),
            },
            "attempts": attempts_dicts,
            "shadow": shadow_report,
        }
        if locale.active:
            receipt["local"] = {
                "currency": locale.currency,
                "actual_cost": convert(actual, locale),
                "savings_vs_most_expensive": convert(savings_me, locale),
                "savings_vs_requested": convert(vs_requested["savings_usd"], locale)
                if vs_requested.get("savings_usd") is not None else None,
                "shadow_cost": convert(shadow_report["cost_usd"], locale)
                if shadow_report and shadow_report.get("cost_usd") is not None else None,
            }
            receipt["currency_conversion"] = conversion_metadata(locale)

        await self._record_receipt(
            dispatch_id, org_id, requested_model, policy, policy_dump, task_type, attempts_dicts, total_ms,
            shadow_report, chosen=chosen, result=result, actual=actual, savings_me=savings_me,
            vs_requested=vs_requested,
        )

        logger.info(
            "dispatch %s: %s/%s outcome=ok attempts=%d tokens=%d/%d cost_usd=%.8f org=%s",
            dispatch_id, m.provider, m.model_name, len(attempts), result.input_tokens, result.output_tokens,
            actual, org_id,
        )

        return {
            "id": f"chatcmpl-{uuid.UUID(dispatch_id).hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": m.model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.content},
                    "finish_reason": result.finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": result.input_tokens,
                "completion_tokens": result.output_tokens,
                "total_tokens": result.input_tokens + result.output_tokens,
            },
            "routing_metadata": {
                "requested_model": requested_model,
                "recommended_model": m.model_name,
                "actual_forwarding": True,
                "task_type_inferred": task_type,
                "dispatch_id": dispatch_id,
            },
            "dispatch_receipt": receipt,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_shadow(
        self,
        policy: DispatchPolicy,
        eligible: List[Candidate],
        primary: Candidate,
        constraints: RouterConstraints,
        reason_by_model: Dict[str, str],
    ) -> Tuple[Optional[Candidate], Optional[str]]:
        if policy.shadow is None:
            return None, None
        if policy.shadow.model:
            target = policy.shadow.model.strip().lower()
            matches = [c for c in eligible if c.model.model_name.lower() == target]
            if not matches:
                reason = next((r for k, r in reason_by_model.items() if k.endswith(f"::{target}")), None)
                # Over the cap on its own is still "the cap" to the caller, not a generic ineligibility.
                if reason == "over_request_cost_cap":
                    return None, "would_exceed_request_cost_cap"
                return None, "shadow_model_ineligible"
            cand = min(matches, key=lambda c: c.blended_per_1m)
        else:
            others = [c for c in eligible if c is not primary]
            ordered = self._order(others, policy.shadow.objective, constraints)
            if not ordered:
                return None, "no_other_eligible_candidate"
            cand = ordered[0]
        if cand is primary or (
            cand.model.model_name.lower() == primary.model.model_name.lower()
            and cand.model.provider.lower() == primary.model.provider.lower()
        ):
            return None, "same_as_primary"
        if policy.max_request_cost_usd is not None:
            if primary.estimated_usd + cand.estimated_usd > policy.max_request_cost_usd:
                return None, "would_exceed_request_cost_cap"
        return cand, None

    def _savings_vs_requested(self, requested_model, models, chosen, result, actual) -> Dict[str, Any]:
        target = (requested_model or "").strip().lower()
        if target == chosen.model.model_name.lower():
            return {"model": requested_model, "provider": chosen.model.provider, "cost_usd": actual,
                    "savings_usd": 0.0, "note": "requested model was dispatched"}
        matches = [m for m in models if m.model_name.lower() == target and getattr(m, "price_confirmed", True)]
        if not matches:
            return {"model": requested_model, "provider": None, "cost_usd": None, "savings_usd": None,
                    "note": "requested model has no confirmed catalogue price; no comparison possible"}
        executable = [m for m in matches if is_executable_provider(m.provider)]
        ref = min(executable or matches, key=_blended_per_1m)
        ref_cost = _usd(compute_token_cost(
            ref.cost_per_input_token, ref.cost_per_output_token, result.input_tokens, result.output_tokens,
        ).total_cost)
        diff = _usd(ref_cost - actual)
        note = "savings vs the requested model at the same token counts" if diff >= 0 else \
            "the dispatched model cost more than the requested one at the same token counts"
        return {
            "model": ref.model_name, "provider": ref.provider, "cost_usd": ref_cost, "savings_usd": diff, "note": note,
        }

    def _forgone_vs_cheapest(self, pinned, eligible, policy, result, actual) -> Optional[Dict[str, Any]]:
        floor = policy.min_quality_score
        pool = [
            c for c in eligible
            if c is not pinned
            and (floor is None or (c.model.quality_score is not None and c.model.quality_score >= floor))
        ]
        if not pool:
            return {"model": None, "provider": None, "cost_usd": None, "forgone_usd": 0.0, "floor": floor,
                    "note": "no other eligible candidate meets the floor"}
        cheapest = min(pool, key=lambda c: c.blended_per_1m)
        c_cost = _usd(compute_token_cost(
            cheapest.model.cost_per_input_token, cheapest.model.cost_per_output_token,
            result.input_tokens, result.output_tokens,
        ).total_cost)
        return {
            "model": cheapest.model.model_name,
            "provider": cheapest.model.provider,
            "quality_score": cheapest.model.quality_score,
            "cost_usd": c_cost,
            "forgone_usd": _usd(max(0.0, actual - c_cost)),
            "floor": floor,
            "note": "what the cheapest eligible candidate meeting the floor would have cost at the same token counts",
        }

    @staticmethod
    def _reason_text(policy: DispatchPolicy, chosen: Candidate, eligible: List[Candidate], pinned) -> str:
        m = chosen.model
        q = f"quality={m.quality_score:.0f}/100" if m.quality_score is not None else "quality=N/A"
        base = f"{m.model_name} ({m.provider}); {q}; cost=${chosen.blended_per_1m:.2f}/1M tokens"
        if pinned:
            return f"pinned by policy: {base}; {len(eligible) - 1} other eligible candidate(s) not considered"
        objective = {
            "balanced": "best quality-per-dollar among eligible candidates",
            "cheapest": "cheapest eligible candidate meeting every hard constraint",
            "best_quality": "highest quality eligible candidate",
            "lowest_latency": "lowest latency eligible candidate",
        }.get(policy.objective, policy.objective)
        extras = []
        if policy.min_quality_score is not None:
            extras.append(f"quality >= {policy.min_quality_score:g}")
        if policy.max_cost_per_1m_tokens is not None:
            extras.append(f"cost <= ${policy.max_cost_per_1m_tokens:g}/1M")
        if policy.max_request_cost_usd is not None:
            extras.append(f"request cap ${policy.max_request_cost_usd:g}")
        cons = f" ({', '.join(extras)})" if extras else ""
        return f"{objective}{cons}: {base}; {len(eligible)} eligible candidate(s)"

    @staticmethod
    def _policy_dump(policy: DispatchPolicy) -> Dict[str, Any]:
        return {
            "objective": policy.objective,
            "min_quality_score": policy.min_quality_score,
            "max_cost_per_1m_tokens": policy.max_cost_per_1m_tokens,
            "min_context_window": policy.min_context_window,
            "task_type": policy.task_type,
            "preferred_provider": policy.preferred_provider,
            "exclude_reasoning_models": policy.exclude_reasoning_models,
            "allow": policy.allow,
            "deny": policy.deny,
            "max_request_cost_usd": policy.max_request_cost_usd,
            "failover": policy.failover.model_dump(),
            "pinned_model": policy.pinned_model,
            "shadow": policy.shadow.model_dump() if policy.shadow else None,
        }

    @staticmethod
    def _constraint_value(reason: Optional[str], policy: DispatchPolicy, task_type: Optional[str] = None):
        """The policy value behind an exclusion reason (REASON_TO_CONSTRAINT names the field).

        ``task_type`` is the *effective* one — inferred from the messages when the policy
        did not state it — so a task_type_mismatch elimination can show what eliminated.
        """
        if reason == "task_type_mismatch":
            return task_type if task_type is not None else policy.task_type
        return getattr(policy, REASON_TO_CONSTRAINT.get(reason or "", ""), None)

    @staticmethod
    def _no_candidate_message(reason: Optional[str], policy: DispatchPolicy, task_type: Optional[str],
                              key_resolver: ProviderKeyResolver) -> str:
        phrase = REASON_TEXT.get(reason or "", reason or "unknown")
        constraint = REASON_TO_CONSTRAINT.get(reason or "", reason)
        if reason == "no_provider_key":
            have = key_resolver.providers_with_keys()
            return (
                "No eligible candidate: no provider key supplied for any executable provider"
                + (f" (keys present for: {', '.join(have)})" if have else "")
                + ". Send X-Provider-Key-<Provider> headers; see GET /dispatch/providers."
            )
        value = DispatchService._constraint_value(reason, policy, task_type)
        if reason == "below_quality_floor":
            return f"No eligible candidate: every model is below quality floor min_quality_score >= {value:g}."
        if reason == "over_request_cost_cap":
            return f"No eligible candidate: every model exceeds per-request cost cap max_request_cost_usd={value:g}."
        if reason == "price_unconfirmed":
            return "No eligible candidate: no dispatchable model has a provider-confirmed price."
        if reason == "provider_not_executable":
            return "No eligible candidate: no priced model belongs to an executable provider."
        detail = f" ({constraint}={value})" if value is not None else ""
        return f"No eligible candidate: every model was eliminated as '{phrase}'{detail}."

    @staticmethod
    def _exhausted_constraint(excluded: List[Dict[str, Any]]) -> Optional[str]:
        counts = Counter(e["reason"] for e in excluded if e["reason"] not in _CATALOGUE_REASONS)
        if not counts:
            return None
        return counts.most_common(1)[0][0]

    @staticmethod
    def _exhausted_message(attempts, remaining, elim, policy, task_type, failover_on) -> str:
        parts = [f"All {len(attempts)} attempt(s) failed ({', '.join(a.outcome for a in attempts)})."]
        if not failover_on:
            parts.append("Failover is off, so no other candidate was tried.")
        elif remaining == 0:
            if elim:
                value = DispatchService._constraint_value(elim, policy, task_type)
                phrase = REASON_TEXT.get(elim, elim)
                constraint = REASON_TO_CONSTRAINT.get(elim, elim)
                numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
                bound = f" >= {value:g}" if numeric else ""
                parts.append(
                    f"No further candidate satisfies {constraint}{bound} ({phrase}); "
                    "dispatch fails rather than downgrading."
                )
            else:
                parts.append("No further eligible candidate remained.")
        else:
            parts.append(f"max_attempts reached with {remaining} eligible candidate(s) untried.")
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Ledger writes
    # ------------------------------------------------------------------

    async def _write_ledgers(self, dispatch_id, customer, chosen, result, actual, most_expensive, task_type, policy,
                             webhook_secret) -> None:
        m = chosen.model
        org_id = getattr(customer, "org_id", None)
        try:
            await self._usage.record_event(
                provider=m.provider, model_name=m.model_name,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                cost_usd=actual, org_id=org_id, request_id=policy.request_id,
                session_id=policy.session_id, is_estimated=False,
            )
        except Exception as exc:
            logger.warning("dispatch %s: usage ledger write failed: %s", dispatch_id, exc)
        try:
            await self._savings.record_routing(
                recommended_model=m.model_name, recommended_provider=m.provider,
                recommended_cost_per_1m=chosen.blended_per_1m, org_id=org_id,
                api_key_tier=getattr(customer, "tier", None),
                baseline_model=most_expensive.model.model_name if most_expensive is not chosen else None,
                baseline_cost_per_1m=most_expensive.blended_per_1m if most_expensive is not chosen else None,
                task_type=task_type, routing_id=dispatch_id,
            )
            await self._savings.record_feedback(routing_id=dispatch_id, was_used=True, actual_model_used=m.model_name)
        except Exception as exc:
            logger.warning("dispatch %s: savings ledger write failed: %s", dispatch_id, exc)
        try:
            await self._budget.check_and_fire(self._usage, secret=webhook_secret)
        except Exception as exc:
            logger.warning("dispatch %s: budget alert check failed: %s", dispatch_id, exc)

    async def _record_shadow_spend(self, shadow_report, org_id, policy) -> None:
        if not shadow_report or shadow_report.get("outcome") != "success":
            return
        try:
            await self._usage.record_event(
                provider=shadow_report["provider"], model_name=shadow_report["model"],
                input_tokens=shadow_report["input_tokens"], output_tokens=shadow_report["output_tokens"],
                cost_usd=shadow_report["cost_usd"], org_id=org_id,
                request_id=f"{policy.request_id}:shadow" if policy.request_id else None,
                session_id=policy.session_id, is_estimated=False,
            )
        except Exception as exc:
            logger.warning("dispatch shadow usage ledger write failed: %s", exc)

    async def _record_receipt(
        self, dispatch_id, org_id, requested_model, policy, policy_dump, task_type, attempts, total_ms,
        shadow_report, *, chosen: Optional[Candidate] = None, result: Optional[ChatResult] = None,
        actual: Optional[float] = None, savings_me: Optional[float] = None,
        vs_requested: Optional[Dict[str, Any]] = None,
    ) -> None:
        """One receipt row for a dispatch that reached a provider; ``chosen``/``result`` set = success."""
        shadow = shadow_report or {}
        row: Dict[str, Any] = {
            "dispatch_id": dispatch_id, "org_id": org_id, "created_at": time.time(),
            "request_id": policy.request_id, "session_id": policy.session_id,
            "requested_model": requested_model, "objective": policy.objective,
            "policy_json": json.dumps(policy_dump, separators=(",", ":")), "task_type": task_type,
            "chosen_model": None, "chosen_provider": None,
            "outcome": "success" if chosen is not None and result is not None else "failed",
            "attempts": len(attempts), "attempts_json": json.dumps(attempts, separators=(",", ":")),
            "latency_total_ms": round(total_ms, 1),
            "shadow_model": shadow.get("model"), "shadow_provider": shadow.get("provider"),
            "shadow_outcome": shadow.get("outcome"), "shadow_cost_usd": shadow.get("cost_usd"),
            "shadow_latency_ms": shadow.get("provider_latency_ms"),
            "live_call": 1,
        }
        if chosen is not None and result is not None:
            m = chosen.model
            row.update({
                "chosen_model": m.model_name, "chosen_provider": m.provider,
                "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "cost_usd": actual, "estimated_cost_usd": _usd(chosen.estimated_usd),
                "savings_vs_most_expensive_usd": savings_me,
                "savings_vs_requested_usd": (vs_requested or {}).get("savings_usd"),
                "latency_provider_ms": round(result.provider_latency_ms, 1),
                "quality_score": m.quality_score, "price_as_of": m.price_as_of,
                "price_source_kind": getattr(m, "price_source_kind", "provider"),
            })
        try:
            await self._receipts.record(row)
        except Exception as exc:
            logger.warning("dispatch %s: receipt write failed: %s", dispatch_id, exc)


# ---- Singleton wiring -------------------------------------------------------

_dispatch_service: Optional[DispatchService] = None


def init_dispatch_service(service: DispatchService) -> DispatchService:
    global _dispatch_service
    _dispatch_service = service
    return _dispatch_service


def get_dispatch_service() -> DispatchService:
    if _dispatch_service is None:
        raise RuntimeError("DispatchService has not been initialized")
    return _dispatch_service
