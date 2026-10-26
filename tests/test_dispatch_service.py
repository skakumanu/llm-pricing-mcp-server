"""DispatchService tests (v1.77.0): policy, eligibility, ranking, failover, shadow, receipts.

Fully offline: a scripted FakeExecutor replaces the provider layer and the catalogue is
four mocked models —
  A  OpenAI     quality 85  $10/1M   confirmed
  B  Anthropic  quality 72  $1/1M    confirmed
  C  Google     quality 60  $0.20/1M confirmed
  D  Groq       quality 80  $0.05/1M price_confirmed=False (never dispatched)
with keys supplied for all three executable providers unless a test says otherwise.
Ledgers (usage, savings, budget alerts, receipts) are real SQLite stores on tmp_path.
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.models.dispatch import DispatchPolicy  # noqa: E402
from src.models.pricing import PricingMetrics  # noqa: E402
from src.services.billing_service import CustomerRecord  # noqa: E402
from src.services.budget_alerts import BudgetAlertService  # noqa: E402
from src.services.currency import LocaleContext  # noqa: E402
from src.services.dispatch_executors import (  # noqa: E402
    OUTCOME_CONTEXT_TOO_LONG, ChatResult, ExecutorError, HeaderKeyResolver,
)
from src.services.dispatch_receipts import DispatchReceiptStore  # noqa: E402
from src.services.dispatch_service import DispatchError, DispatchService  # noqa: E402
from src.services.router import ModelRouter  # noqa: E402
from src.services.savings_tracker import SavingsTrackerService  # noqa: E402
from src.services.usage_tracker import UsageTrackerService  # noqa: E402

# Assembled at runtime so secret scanners do not flag the fixture; it is not a credential.
KEY = "-".join(["sk", "test", "SENTINEL", "123"])
PROMPT = "PROMPT-SENTINEL-XYZ please answer"
COMPLETION = "COMPLETION-SENTINEL-XYZ"
IN_TOKENS, OUT_TOKENS = 100, 50


def _pm(name, provider, quality, blended_per_1m, confirmed=True, ctx=128_000, latency=None):
    per_token = blended_per_1m / 1_000_000
    m = PricingMetrics(
        model_name=name, provider=provider, cost_per_input_token=per_token, cost_per_output_token=per_token,
        context_window=ctx, latency_ms=latency, price_confirmed=confirmed, price_as_of="2026-10-01",
    )
    m.quality_score = quality
    return m


def _catalogue():
    return [
        _pm("model-a", "OpenAI", 85, 10.0, ctx=128_000, latency=900),
        _pm("model-b", "Anthropic", 72, 1.0, ctx=32_000, latency=400),
        _pm("model-c", "Google", 60, 0.20, ctx=1_000_000, latency=250),
        _pm("model-d", "Groq", 80, 0.05, confirmed=False, latency=100),
    ]


def _cost(blended_per_1m, in_t=IN_TOKENS, out_t=OUT_TOKENS):
    return (in_t + out_t) * blended_per_1m / 1_000_000


ALL_KEYS = {"X-Provider-Key-OpenAI": KEY, "X-Provider-Key-Anthropic": KEY, "X-Provider-Key-Google": KEY}


class FakeExecutor:
    """Scripted executor: `script[model]` is an outcome string, 'ok', or a list consumed per call."""

    def __init__(self, script=None, delay=0.0):
        self.script = dict(script or {})
        self.calls = []            # (model, api_key)
        self.requests = []         # every ChatRequest as received
        self.delay = delay
        self.started = {}
        self.barrier = None        # asyncio.Barrier for the concurrency test

    def _next(self, model):
        entry = self.script.get(model, "ok")
        if isinstance(entry, list):
            return entry.pop(0) if entry else "ok"
        return entry

    async def execute(self, req, api_key):
        self.calls.append((req.model, api_key))
        self.requests.append(req)
        self.started.setdefault(req.model, time.perf_counter())
        if self.barrier is not None:
            await asyncio.wait_for(self.barrier.wait(), timeout=1.0)
        if self.delay:
            await asyncio.sleep(self.delay)
        outcome = self._next(req.model)
        if outcome == "ok":
            return ChatResult(COMPLETION, "stop", IN_TOKENS, OUT_TOKENS, 12.5, 200, req.model)
        if outcome == "hang":
            await asyncio.sleep(10)
        status = {"timeout": None, "server_error": 503, "rate_limited": 429, "auth_error": 401,
                  "auth_403": 403, "context_too_long": 400, "model_not_found": 404, "provider_rejected": 400}
        if outcome == "auth_403":
            raise ExecutorError("auth_error", 403)
        raise ExecutorError(outcome, status.get(outcome))

    def models_called(self):
        return [m for m, _ in self.calls]


def _customer(org="org-1", cid="cust-1", tier="free"):
    return CustomerRecord(id=cid, email=f"{cid}@example.com", stripe_customer_id=None, stripe_subscription_id=None,
                          api_key="svc-key", tier=tier, org_id=org, created_at=time.time(), updated_at=time.time())


@pytest.fixture(autouse=True)
def _fast_token_count():
    # tiktoken wants the network for its encoding file; a word count keeps the tests offline and fast.
    with patch("src.services.dispatch_service.count_tokens", lambda text: max(1, len(text.split()))):
        yield


@pytest_asyncio.fixture
async def stores(tmp_path):
    db = str(tmp_path / "ledgers.db")
    usage = UsageTrackerService(db)
    await usage.initialize()
    savings = SavingsTrackerService(db)
    await savings.initialize()
    budget = BudgetAlertService(db)
    await budget.initialize()
    receipts = DispatchReceiptStore(db)
    await receipts.initialize()
    return {"usage": usage, "savings": savings, "budget": budget, "receipts": receipts, "db": db}


def _service(stores, executor, models=None):
    aggregator = MagicMock()
    aggregator.get_all_pricing_async = AsyncMock(return_value=(models if models is not None else _catalogue(), []))
    return DispatchService(
        aggregator=aggregator, router=ModelRouter(aggregator),
        usage_tracker=stores["usage"], savings_tracker=stores["savings"], budget_alerts=stores["budget"],
        receipts=stores["receipts"], executor_factory=lambda spec, timeout_s: executor,
        default_max_tokens=100, attempt_timeout_ms=500, max_attempts=3,
    )


async def _dispatch(svc, policy, headers=ALL_KEYS, model="gpt-4", customer=None, **kw):
    return await svc.dispatch(
        customer=customer or _customer(), requested_model=model,
        messages=[{"role": "user", "content": PROMPT}],
        policy=DispatchPolicy(**policy), key_resolver=HeaderKeyResolver(headers),
        locale=LocaleContext(), inferred_task_type="chat", **kw,
    )


# ---------------------------------------------------------------------------
# AC2 — cheapest meeting quality floor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac2_cheapest_meeting_quality_floor(stores):
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    assert ex.models_called() == ["model-b"]
    assert body["object"] == "chat.completion"
    assert body["model"] == "model-b"
    assert body["choices"][0]["message"]["content"] == COMPLETION
    assert body["usage"] == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    assert body["routing_metadata"]["actual_forwarding"] is True
    r = body["dispatch_receipt"]
    assert r["live_call"] is True
    assert r["chosen"]["model"] == "model-b" and r["chosen"]["provider"] == "Anthropic"
    assert [c["model"] for c in r["candidates"]] == ["model-b", "model-a"]
    excluded = {e["model"]: e for e in r["excluded"]}
    assert excluded["model-c"]["reason"] == "below_quality_floor"
    assert excluded["model-c"]["detail"] == "below quality floor"
    assert r["cost"]["actual_usd"] == pytest.approx(_cost(1.0))
    me = r["savings"]["vs_most_expensive_eligible"]
    assert me["model"] == "model-a"
    assert me["savings_usd"] == pytest.approx(_cost(10.0) - _cost(1.0))
    assert r["latency"]["total_ms"] > 0 and r["latency"]["provider_ms"] > 0
    assert len(r["attempts"]) == 1 and r["attempts"][0]["outcome"] == "ok"
    assert r["reason"].startswith("cheapest")


# ---------------------------------------------------------------------------
# AC3 — pinned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac3_pinned_reports_forgone_savings(stores):
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {"objective": "pinned", "pinned_model": "model-a",
                                                  "min_quality_score": 70})
    assert ex.models_called() == ["model-a"]
    r = body["dispatch_receipt"]
    assert r["chosen"]["pinned"] is True and r["chosen"]["model"] == "model-a"
    forgone = r["savings"]["forgone_vs_cheapest_meeting_floor"]
    assert forgone["model"] == "model-b" and forgone["floor"] == 70
    assert forgone["forgone_usd"] == pytest.approx(_cost(10.0) - _cost(1.0))
    assert forgone["forgone_usd"] >= 0
    assert r["reason"].startswith("pinned")


@pytest.mark.asyncio
async def test_ac3_pinned_needs_no_quality_floor(stores):
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {"objective": "pinned", "pinned_model": "model-a"})
    assert ex.models_called() == ["model-a"]
    forgone = body["dispatch_receipt"]["savings"]["forgone_vs_cheapest_meeting_floor"]
    assert forgone["model"] == "model-c" and forgone["floor"] is None


@pytest.mark.asyncio
async def test_pinned_model_failing_a_constraint_is_refused_before_any_call(stores):
    ex = FakeExecutor()
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"objective": "pinned", "pinned_model": "model-c",
                                               "min_quality_score": 70})
    assert exc.value.status_code == 400
    assert exc.value.payload["error"] == "pinned_model_ineligible"
    assert exc.value.payload["reason"] == "below_quality_floor"
    assert ex.calls == []


@pytest.mark.asyncio
async def test_pinned_model_not_in_catalogue(stores):
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, FakeExecutor()), {"objective": "pinned", "pinned_model": "ghost"})
    assert exc.value.payload["reason"] == "not_in_catalogue"


def test_policy_pinned_requires_pinned_model():
    with pytest.raises(ValueError):
        DispatchPolicy(objective="pinned")
    with pytest.raises(ValueError):
        DispatchPolicy(pinned_model="model-a")


# ---------------------------------------------------------------------------
# AC4 — savings vs requested model
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac4_savings_vs_requested_model(stores):
    body = await _dispatch(_service(stores, FakeExecutor()), {"objective": "cheapest", "min_quality_score": 70},
                           model="model-a")
    vs = body["dispatch_receipt"]["savings"]["vs_requested_model"]
    assert vs["model"] == "model-a"
    assert vs["cost_usd"] == pytest.approx(_cost(10.0))
    assert vs["savings_usd"] == pytest.approx(_cost(10.0) - _cost(1.0))


@pytest.mark.asyncio
async def test_savings_vs_requested_unknown_model_is_null(stores):
    body = await _dispatch(_service(stores, FakeExecutor()), {"objective": "cheapest", "min_quality_score": 70},
                           model="gpt-unknown")
    vs = body["dispatch_receipt"]["savings"]["vs_requested_model"]
    assert vs["cost_usd"] is None and vs["savings_usd"] is None and "no comparison" in vs["note"]


@pytest.mark.asyncio
async def test_savings_vs_requested_can_be_negative(stores):
    body = await _dispatch(_service(stores, FakeExecutor()), {"objective": "best_quality"}, model="model-c")
    vs = body["dispatch_receipt"]["savings"]["vs_requested_model"]
    assert body["model"] == "model-a"
    assert vs["savings_usd"] == pytest.approx(_cost(0.2) - _cost(10.0))
    assert vs["savings_usd"] < 0


# ---------------------------------------------------------------------------
# AC5 — failover
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected_outcome", [
    ("timeout", "timeout"), ("server_error", "server_error"), ("rate_limited", "rate_limited"),
    ("model_not_found", "model_not_found"),
])
async def test_ac5_failover_to_next_best(stores, failure, expected_outcome):
    ex = FakeExecutor({"model-b": failure})
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    assert ex.models_called() == ["model-b", "model-a"]
    assert body["model"] == "model-a"
    assert body["choices"][0]["message"]["content"] == COMPLETION
    attempts = body["dispatch_receipt"]["attempts"]
    assert [(a["model"], a["outcome"]) for a in attempts] == [("model-b", expected_outcome), ("model-a", "ok")]
    assert attempts[0]["n"] == 1 and attempts[1]["n"] == 2


@pytest.mark.asyncio
async def test_failover_wraps_a_hanging_provider_in_the_attempt_timeout(stores):
    ex = FakeExecutor({"model-b": "hang"})
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                                  "failover": {"timeout_ms": 100}})
    assert body["model"] == "model-a"
    assert body["dispatch_receipt"]["attempts"][0]["outcome"] == "timeout"


@pytest.mark.asyncio
async def test_failover_respects_max_attempts(stores):
    ex = FakeExecutor({"model-c": "server_error", "model-b": "server_error", "model-a": "ok"})
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"objective": "cheapest", "failover": {"max_attempts": 2}})
    assert exc.value.status_code == 502
    assert exc.value.payload["error"] == "all_attempts_failed"
    assert ex.models_called() == ["model-c", "model-b"]
    assert exc.value.payload["eligible_remaining"] == 1
    assert exc.value.payload["eliminating_constraint"] is None


# ---------------------------------------------------------------------------
# AC6 — no failover below the floor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac6_no_failover_below_quality_floor(stores):
    ex = FakeExecutor({"model-a": "server_error"})
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"min_quality_score": 80})
    assert ex.models_called() == ["model-a"]
    p = exc.value.payload
    assert p["error"] == "all_attempts_failed"
    assert p["eliminating_constraint"] == "min_quality_score"
    assert p["constraint_value"] == 80
    assert "quality floor" in p["message"] and "min_quality_score" in p["message"]
    assert len(p["attempts"]) == 1


# ---------------------------------------------------------------------------
# AC7 — caller key errors are terminal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["auth_error", "auth_403"])
async def test_ac7_auth_error_is_terminal(stores, failure):
    ex = FakeExecutor({"model-b": failure})
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    assert ex.models_called() == ["model-b"]
    p = exc.value.payload
    assert p["error"] == "provider_auth_failed"
    assert p["provider"] == "Anthropic" and "X-Provider-Key-Anthropic" in p["message"]
    assert KEY not in str(p)


# ---------------------------------------------------------------------------
# AC8 — failover off
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac8_failover_off_single_attempt(stores):
    ex = FakeExecutor({"model-b": "timeout"})
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                               "failover": {"enabled": False}})
    assert ex.models_called() == ["model-b"]
    assert len(exc.value.payload["attempts"]) == 1
    assert exc.value.payload["failover_enabled"] is False
    assert "Failover is off" in exc.value.payload["message"]


# ---------------------------------------------------------------------------
# AC9 — pinned never fails over
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac9_pinned_never_fails_over(stores):
    ex = FakeExecutor({"model-b": "server_error"})
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"objective": "pinned", "pinned_model": "model-b"})
    assert ex.models_called() == ["model-b"]
    assert exc.value.payload["error"] == "all_attempts_failed"
    assert exc.value.payload["max_attempts"] == 1


# ---------------------------------------------------------------------------
# AC10 — unconfirmed prices never dispatched
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac10_unconfirmed_price_never_dispatched(stores):
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70},
                           headers={**ALL_KEYS, "X-Provider-Key-Groq": KEY})
    assert body["model"] == "model-b"
    assert "model-d" not in ex.models_called()
    excluded = {e["model"]: e for e in body["dispatch_receipt"]["excluded"]}
    assert excluded["model-d"]["reason"] == "price_unconfirmed"
    assert excluded["model-d"]["detail"] == "price not confirmed"


# ---------------------------------------------------------------------------
# AC11 — missing keys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac11_only_one_key_filters_candidates(stores):
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest"}, headers={"X-Provider-Key-OpenAI": KEY})
    assert ex.models_called() == ["model-a"]
    excluded = {e["model"]: e for e in body["dispatch_receipt"]["excluded"]}
    assert excluded["model-b"]["reason"] == "no_provider_key"
    assert excluded["model-b"]["detail"] == "no key supplied for provider"


@pytest.mark.asyncio
async def test_ac11_no_keys_at_all(stores):
    ex = FakeExecutor()
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {}, headers={})
    assert ex.calls == []
    p = exc.value.payload
    assert p["error"] == "no_eligible_candidate"
    assert "no provider key supplied" in p["message"]
    assert p["eliminating_constraint"] == "provider_key"


# ---------------------------------------------------------------------------
# AC12 — no candidate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac12_quality_floor_eliminates_everything(stores):
    ex = FakeExecutor()
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"min_quality_score": 99})
    assert ex.calls == []
    p = exc.value.payload
    assert p["eliminating_constraint"] == "min_quality_score" and p["constraint_value"] == 99
    assert "quality floor" in p["message"]
    below = {e["model"] for e in p["excluded"] if e["reason"] == "below_quality_floor"}
    assert below == {"model-a", "model-b", "model-c"}


# ---------------------------------------------------------------------------
# AC13 — per-request cost cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac13_cap_eliminates_everything(stores):
    ex = FakeExecutor()
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"max_request_cost_usd": 0.000001})
    assert ex.calls == []
    p = exc.value.payload
    assert p["eliminating_constraint"] == "max_request_cost_usd"
    assert "cost cap" in p["message"]


@pytest.mark.asyncio
async def test_ac13_cap_fitting_only_b_and_c_excludes_a(stores):
    ex = FakeExecutor()
    # 4 prompt words + 100 max_tokens (service default) = 104 tokens; A costs 104e-5 > cap > B's 104e-6.
    body = await _dispatch(_service(stores, ex), {"objective": "best_quality", "max_request_cost_usd": 0.0005})
    assert body["model"] == "model-b"
    excluded = {e["model"]: e for e in body["dispatch_receipt"]["excluded"]}
    assert excluded["model-a"]["reason"] == "over_request_cost_cap"
    assert excluded["model-a"]["detail"] == "exceeds per-request cost cap"
    assert [c["model"] for c in body["dispatch_receipt"]["candidates"]] == ["model-b", "model-c"]


@pytest.mark.asyncio
async def test_ac13_cap_bounds_the_provider_to_the_estimated_max_tokens(stores):
    """The cap is estimated with effective_max_tokens, so the provider must be bounded by that same
    number — otherwise an unbounded completion could blow straight through a 'hard' cap."""
    ex = FakeExecutor()
    await _dispatch(_service(stores, ex), {"objective": "cheapest", "max_request_cost_usd": 0.5})
    assert ex.requests[-1].max_tokens == 100            # service default_max_tokens, no caller max_tokens
    ex = FakeExecutor()
    await _dispatch(_service(stores, ex), {"objective": "cheapest", "max_request_cost_usd": 0.5}, max_tokens=33)
    assert ex.requests[-1].max_tokens == 33             # caller's own bound wins
    ex = FakeExecutor()
    await _dispatch(_service(stores, ex), {"objective": "cheapest"})
    assert ex.requests[-1].max_tokens is None           # no cap: the caller's (absent) max_tokens is forwarded as-is


@pytest.mark.asyncio
async def test_reasoning_model_flag_reaches_the_executor(stores):
    models = _catalogue()
    models[0].is_reasoning_model = True                 # model-a
    ex = FakeExecutor()
    await _dispatch(_service(stores, ex, models), {"objective": "best_quality"})
    assert ex.requests[-1].model == "model-a" and ex.requests[-1].reasoning_model is True


# ---------------------------------------------------------------------------
# AC25 — budget hard stop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac25_budget_hard_stop_refuses_dispatch(stores):
    await stores["budget"].register("https://example.com/hook", threshold_usd=0.001, org_id="org-1", hard_stop=True)
    await stores["usage"].record_event("OpenAI", "model-a", 100, 100, 0.002, org_id="org-1")
    ex = FakeExecutor()
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {})
    assert exc.value.status_code == 402
    p = exc.value.payload
    assert p["error"] == "budget_hard_stop" and p["threshold_usd"] == 0.001 and p["org_id"] == "org-1"
    assert "budget" in p["message"].lower()
    assert ex.calls == []
    assert await stores["receipts"].list_receipts("org-1") == []


@pytest.mark.asyncio
async def test_ac25_orgless_hard_stop_never_stops_anyone(stores):
    await stores["budget"].register("https://example.com/hook", threshold_usd=0.001, org_id=None, hard_stop=True)
    await stores["usage"].record_event("OpenAI", "model-a", 100, 100, 0.002, org_id="org-1")
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {})
    assert body["object"] == "chat.completion" and len(ex.calls) == 1


@pytest.mark.asyncio
async def test_ac25_other_orgs_stop_does_not_apply(stores):
    await stores["budget"].register("https://example.com/hook", threshold_usd=0.001, org_id="org-2", hard_stop=True)
    await stores["usage"].record_event("OpenAI", "model-a", 100, 100, 0.002, org_id="org-2")
    body = await _dispatch(_service(stores, FakeExecutor()), {})
    assert body["object"] == "chat.completion"


@pytest.mark.asyncio
async def test_ac25_notify_only_alert_does_not_stop(stores):
    await stores["budget"].register("https://example.com/hook", threshold_usd=0.001, org_id="org-1", hard_stop=False)
    await stores["usage"].record_event("OpenAI", "model-a", 100, 100, 0.002, org_id="org-1")
    body = await _dispatch(_service(stores, FakeExecutor()), {})
    assert body["object"] == "chat.completion"


# ---------------------------------------------------------------------------
# AC26 — shadow A/B
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac26_shadow_runs_concurrently_and_is_reported(stores):
    ex = FakeExecutor()
    ex.barrier = asyncio.Barrier(2)   # both calls must be in flight at once or this times out
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                                  "shadow": {"model": "model-a"}})
    assert set(ex.models_called()) == {"model-b", "model-a"}
    assert body["model"] == "model-b"
    assert body["choices"][0]["message"]["content"] == COMPLETION
    s = body["dispatch_receipt"]["shadow"]
    assert s["model"] == "model-a" and s["provider"] == "OpenAI" and s["outcome"] == "success"
    assert s["cost_usd"] == pytest.approx(_cost(10.0))
    assert s["cost_delta_usd"] == pytest.approx(_cost(10.0) - _cost(1.0))
    assert s["provider_latency_ms"] is not None and "latency_delta_ms" in s
    # Shadow spend is real money: it lands in the ledger too.
    summary = await stores["usage"].get_summary(org_id="org-1")
    assert summary["total_requests"] == 2


@pytest.mark.asyncio
async def test_ac26_shadow_failure_never_fails_the_request(stores):
    ex = FakeExecutor({"model-a": "server_error"})
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                                  "shadow": {"model": "model-a"}})
    assert body["model"] == "model-b"
    s = body["dispatch_receipt"]["shadow"]
    assert s["outcome"] == "server_error" and s["cost_usd"] is None


@pytest.mark.asyncio
async def test_ac26_shadow_by_objective_excludes_primary(stores):
    body = await _dispatch(_service(stores, FakeExecutor()),
                           {"objective": "cheapest", "shadow": {"objective": "best_quality"}})
    assert body["model"] == "model-c"
    assert body["dispatch_receipt"]["shadow"]["model"] == "model-a"


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [
    0.0011,   # A alone fits (104e-5 = 0.00104) but primary B + shadow A (0.001144) does not
    0.0005,   # A does not even fit alone; still reported as the cap, not a generic ineligibility
])
async def test_ac26_combined_cap_skips_shadow(stores, cap):
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                                  "max_request_cost_usd": cap,
                                                  "shadow": {"model": "model-a"}})
    assert ex.models_called() == ["model-b"]
    s = body["dispatch_receipt"]["shadow"]
    assert s["outcome"] == "skipped" and s["skip_reason"] == "would_exceed_request_cost_cap"
    assert s["cost_usd"] is None


@pytest.mark.asyncio
async def test_ac26_combined_cap_allows_shadow_when_both_fit(stores):
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                                  "max_request_cost_usd": 0.002,
                                                  "shadow": {"model": "model-a"}})
    assert set(ex.models_called()) == {"model-b", "model-a"}
    assert body["dispatch_receipt"]["shadow"]["outcome"] == "success"


@pytest.mark.asyncio
async def test_ac26_failover_never_lands_on_the_shadow_model(stores):
    """Order under cheapest is [C, B, A] with shadow A (best_quality). C and B fail: the primary loop
    must not fall over onto A, which is already in flight as the shadow — one call to A, one ledger
    event for it, and the A/B block still compares two different models."""
    ex = FakeExecutor({"model-c": "server_error", "model-b": "server_error"})
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"objective": "cheapest", "shadow": {"objective": "best_quality"}})
    assert exc.value.payload["error"] == "all_attempts_failed"
    assert ex.models_called().count("model-a") == 1
    assert [a["model"] for a in exc.value.payload["attempts"]] == ["model-c", "model-b"]
    summary = await stores["usage"].get_summary(org_id="org-1")
    assert summary["total_requests"] == 1 and summary["by_model"][0]["model_name"] == "model-a"


@pytest.mark.asyncio
async def test_shadow_ineligible_model_is_skipped_not_an_error(stores):
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "shadow": {"model": "model-d"}})
    assert ex.models_called() == ["model-c"]
    assert body["dispatch_receipt"]["shadow"]["skip_reason"] == "shadow_model_ineligible"


def test_shadow_policy_validation():
    with pytest.raises(ValueError):
        DispatchPolicy(shadow={})
    with pytest.raises(ValueError):
        DispatchPolicy(shadow={"model": "x", "objective": "cheapest"})
    with pytest.raises(ValueError):
        DispatchPolicy(objective="pinned", pinned_model="model-a", shadow={"model": "MODEL-A"})


# ---------------------------------------------------------------------------
# AC28 — context-too-long failover
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac28_context_too_long_moves_to_larger_window(stores):
    ex = FakeExecutor({"model-b": "context_too_long"})
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    # B (32k) -> A (128k) is larger: tried.
    assert ex.models_called() == ["model-b", "model-a"]
    assert body["model"] == "model-a"
    assert body["dispatch_receipt"]["attempts"][0]["outcome"] == OUTCOME_CONTEXT_TOO_LONG


@pytest.mark.asyncio
async def test_ac28_context_too_long_skips_smaller_windows(stores):
    # Order under cheapest: C (1M) first; context_too_long on C leaves nobody larger.
    ex = FakeExecutor({"model-c": "context_too_long"})
    with pytest.raises(DispatchError) as exc:
        await _dispatch(_service(stores, ex), {"objective": "cheapest"})
    assert ex.models_called() == ["model-c"]
    assert exc.value.payload["attempts"][0]["outcome"] == OUTCOME_CONTEXT_TOO_LONG


# ---------------------------------------------------------------------------
# Objective orderings, allow/deny, request shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("objective,expected", [
    ("cheapest", ["model-c", "model-b", "model-a"]),
    ("best_quality", ["model-a", "model-b", "model-c"]),
    ("lowest_latency", ["model-c", "model-b", "model-a"]),
])
async def test_objective_orderings(stores, objective, expected):
    body = await _dispatch(_service(stores, FakeExecutor()), {"objective": objective})
    assert [c["model"] for c in body["dispatch_receipt"]["candidates"]] == expected
    assert body["model"] == expected[0]


@pytest.mark.asyncio
async def test_balanced_uses_router_order(stores):
    body = await _dispatch(_service(stores, FakeExecutor()), {})
    # quality_value_score: C 300, B 72, A 8.5
    assert [c["model"] for c in body["dispatch_receipt"]["candidates"]] == ["model-c", "model-b", "model-a"]
    assert body["dispatch_receipt"]["objective"] == "balanced"


@pytest.mark.asyncio
async def test_lowest_latency_puts_unknown_latency_last(stores):
    models = _catalogue()
    models[2].latency_ms = None   # C unknown
    body = await _dispatch(_service(stores, FakeExecutor(), models), {"objective": "lowest_latency"})
    assert [c["model"] for c in body["dispatch_receipt"]["candidates"]] == ["model-b", "model-a", "model-c"]


@pytest.mark.asyncio
async def test_deny_and_allow_lists(stores):
    ex = FakeExecutor()
    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "deny": ["model-c", "provider:openai"]})
    assert body["model"] == "model-b"
    excluded = {e["model"]: e["reason"] for e in body["dispatch_receipt"]["excluded"]}
    assert excluded["model-c"] == "denied_by_policy" and excluded["model-a"] == "denied_by_policy"

    body = await _dispatch(_service(stores, ex), {"objective": "cheapest", "allow": ["provider:OpenAI"]})
    assert body["model"] == "model-a"
    excluded = {e["model"]: e["reason"] for e in body["dispatch_receipt"]["excluded"]}
    assert excluded["model-b"] == "not_in_allow_list"


@pytest.mark.asyncio
async def test_task_type_from_policy_vs_inferred(stores):
    body = await _dispatch(_service(stores, FakeExecutor()), {"task_type": "analysis"})
    assert body["dispatch_receipt"]["task_type"] == "analysis"
    assert body["dispatch_receipt"]["task_type_source"] == "policy"
    body = await _dispatch(_service(stores, FakeExecutor()), {})
    assert body["dispatch_receipt"]["task_type"] == "chat"
    assert body["dispatch_receipt"]["task_type_source"] == "inferred"


@pytest.mark.asyncio
async def test_inferred_task_type_is_named_when_it_eliminates_everything(stores):
    models = _catalogue()
    for m in models:
        m.use_cases = ["chat"]                          # nothing matches the 'code' keywords
    with pytest.raises(DispatchError) as exc:
        await _service(stores, FakeExecutor(), models).dispatch(
            customer=_customer(), requested_model="gpt-4", messages=[{"role": "user", "content": "def f(): pass"}],
            policy=DispatchPolicy(), key_resolver=HeaderKeyResolver(ALL_KEYS), locale=LocaleContext(),
            inferred_task_type="code",
        )
    p = exc.value.payload
    assert p["error"] == "no_eligible_candidate" and p["eliminating_constraint"] == "task_type"
    assert p["constraint_value"] == "code" and "task_type=code" in p["message"]


@pytest.mark.asyncio
async def test_subscription_and_non_executable_models_are_summarised(stores):
    models = _catalogue()
    sub = _pm("copilot", "IDETools", 70, 0.0)
    sub.pricing_model = "subscription"
    models.append(sub)
    models.append(_pm("command-r", "Cohere", 70, 1.0))
    body = await _dispatch(_service(stores, FakeExecutor(), models), {})
    summary = body["dispatch_receipt"]["excluded_summary"]
    assert summary == {"subscription_pricing": 1, "provider_not_executable": 1}
    assert {e["model"] for e in body["dispatch_receipt"]["excluded"]} == {"model-d"}


@pytest.mark.asyncio
async def test_executor_receives_the_right_key_and_only_that_key(stores):
    keys = {"X-Provider-Key-OpenAI": "key-openai", "X-Provider-Key-Anthropic": "key-anthropic",
            "X-Provider-Key-Google": "key-google"}
    ex = FakeExecutor({"model-c": "server_error", "model-b": "server_error"})
    await _dispatch(_service(stores, ex), {"objective": "cheapest"}, headers=keys)
    assert ex.calls == [("model-c", "key-google"), ("model-b", "key-anthropic"), ("model-a", "key-openai")]


@pytest.mark.asyncio
async def test_receipt_and_ledgers_after_success(stores):
    body = await _dispatch(_service(stores, FakeExecutor()), {"objective": "cheapest", "min_quality_score": 70,
                                                              "request_id": "req-1", "session_id": "sess-1"})
    r = body["dispatch_receipt"]
    rows = await stores["receipts"].list_receipts("org-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["dispatch_id"] == r["dispatch_id"] and row["request_id"] == "req-1"
    assert row["chosen_model"] == "model-b" and row["outcome"] == "success"
    assert row["cost_usd"] == pytest.approx(r["cost"]["actual_usd"])
    assert row["live_call"] is True
    savings = await stores["savings"].get_savings(org_id="org-1")
    assert savings["total"] == 1 and savings["acceptance_rate"] == 1.0
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["total_requests"] == 1 and usage["estimated_request_count"] == 0


@pytest.mark.asyncio
async def test_failed_dispatch_still_writes_a_receipt_row(stores):
    ex = FakeExecutor({"model-b": "timeout"})
    with pytest.raises(DispatchError):
        await _dispatch(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                               "failover": {"enabled": False}})
    rows = await stores["receipts"].list_receipts("org-1")
    assert len(rows) == 1 and rows[0]["outcome"] == "failed" and rows[0]["attempts"] == 1
    assert rows[0]["chosen_model"] is None
    assert await stores["receipts"].list_receipts("org-1", outcome="success") == []


@pytest.mark.asyncio
async def test_local_currency_block_when_locale_active(stores):
    from src.services.currency import resolve_locale, ExchangeRateTable
    from datetime import date
    table = ExchangeRateTable(base="USD", as_of=date.today(), rates={"EUR": 2.0, "USD": 1.0}, source="t",
                              placeholder=False)
    locale = resolve_locale(currency="EUR", table=table)
    svc = _service(stores, FakeExecutor())
    body = await svc.dispatch(
        customer=_customer(), requested_model="model-a", messages=[{"role": "user", "content": PROMPT}],
        policy=DispatchPolicy(objective="cheapest", min_quality_score=70),
        key_resolver=HeaderKeyResolver(ALL_KEYS), locale=locale, inferred_task_type="chat",
    )
    r = body["dispatch_receipt"]
    assert r["local"]["currency"] == "EUR"
    assert r["local"]["actual_cost"] == pytest.approx(r["cost"]["actual_usd"] * 2.0)
    assert r["currency_conversion"]["currency"] == "EUR"


@pytest.mark.asyncio
async def test_no_local_block_without_locale(stores):
    body = await _dispatch(_service(stores, FakeExecutor()), {})
    assert "local" not in body["dispatch_receipt"] and "currency_conversion" not in body["dispatch_receipt"]


@pytest.mark.asyncio
async def test_describe_executable(stores):
    svc = _service(stores, FakeExecutor())
    out = await svc.describe_executable()
    by_name = {m["model_name"]: m for m in out["models"]}
    assert all(by_name[n]["dispatchable"] for n in ("model-a", "model-b", "model-c"))
    assert by_name["model-d"] == {"model_name": "model-d", "provider": "Groq", "dispatchable": False,
                                  "reason": "price_unconfirmed"}
    assert out["total_dispatchable"] == 3 and out["total_models"] == 4
    providers = {p["provider"]: p for p in out["executable_providers"]}
    assert providers["OpenAI"]["dispatchable_models"] == 1 and providers["Groq"]["dispatchable_models"] == 0
    assert providers["Anthropic"]["key_header"] == "X-Provider-Key-Anthropic"
    only = await svc.describe_executable(dispatchable_only=True)
    assert {m["model_name"] for m in only["models"]} == {"model-a", "model-b", "model-c"}
    one = await svc.describe_executable(provider="google")
    assert [m["model_name"] for m in one["models"]] == ["model-c"]
    assert [p["provider"] for p in one["executable_providers"]] == ["Google"]
