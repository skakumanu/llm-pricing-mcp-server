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
    EVENT_DELTA, EVENT_FINISH, EVENT_USAGE, OUTCOME_CLIENT_DISCONNECTED, OUTCOME_CONTEXT_TOO_LONG, ChatResult,
    ExecutorError, HeaderKeyResolver, StreamEvent,
)
from src.services.dispatch_receipts import COLUMNS as RECEIPT_COLUMNS, DispatchReceiptStore  # noqa: E402
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


_STATUS = {"timeout": None, "server_error": 503, "rate_limited": 429, "auth_error": 401,
           "auth_403": 403, "context_too_long": 400, "model_not_found": 404, "provider_rejected": 400}

# The streamed completion, in pieces; "".join(PIECES) == COMPLETION.
PIECES = ["COMPLETION-", "SENTINEL-XYZ"]


def _error_for(outcome):
    if outcome == "auth_403":
        return ExecutorError("auth_error", 403)
    return ExecutorError(outcome, _STATUS.get(outcome))


class FakeExecutor:
    """Scripted executor: `script[model]` is an outcome string, 'ok', or a list consumed per call.

    ``stream()`` (v1.78.0) honours the same failure strings (raised before the first yield) plus
    tuple scripts for post-content behaviour: ``("fail_after", n, outcome)`` yields n pieces then
    raises; ``("stall_after", n)`` yields n pieces then hangs; ``("eof_after", n)`` yields n pieces
    then ends without a finish reason; ``"length"`` finishes with ``length``. ``usage=None``
    streams no usage record. ``yielded[model]`` holds the timestamp of every delta yielded and
    ``closed[model]`` is set when the generator's cleanup ran (client-disconnect test).
    """

    def __init__(self, script=None, delay=0.0, usage=(IN_TOKENS, OUT_TOKENS), pieces=None, execute_delay=0.0):
        self.script = dict(script or {})
        self.calls = []            # (model, api_key)
        self.requests = []         # every ChatRequest as received
        self.delay = delay         # before the first event/result (both execute and stream)
        self.execute_delay = execute_delay   # extra delay for execute() only (the shadow)
        self.chunk_delay = 0.0     # between streamed pieces
        self.usage = usage
        self.pieces = list(PIECES if pieces is None else pieces)
        self.started = {}
        self.finished = {}         # model -> perf_counter when execute() returned
        self.yielded = {}          # model -> [perf_counter per delta]
        self.closed = {}           # model -> True once stream()'s finally ran
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
        if self.delay or self.execute_delay:
            await asyncio.sleep(self.delay + self.execute_delay)
        outcome = self._next(req.model)
        if outcome in ("ok", "length") or isinstance(outcome, tuple):
            self.finished[req.model] = time.perf_counter()
            return ChatResult(COMPLETION, "stop", IN_TOKENS, OUT_TOKENS, 12.5, 200, req.model)
        if outcome == "hang":
            await asyncio.sleep(10)
        raise _error_for(outcome)

    async def stream(self, req, api_key):
        self.calls.append((req.model, api_key))
        self.requests.append(req)
        self.started.setdefault(req.model, time.perf_counter())
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            outcome = self._next(req.model)
            if outcome == "hang":
                await asyncio.sleep(10)
            if isinstance(outcome, str) and outcome not in ("ok", "length"):
                raise _error_for(outcome)
            n, then, then_arg = len(self.pieces), None, None
            if isinstance(outcome, tuple):
                then, n = outcome[0], outcome[1]
                then_arg = outcome[2] if len(outcome) > 2 else None
            for piece in self.pieces[:n]:
                if self.chunk_delay:
                    await asyncio.sleep(self.chunk_delay)
                self.yielded.setdefault(req.model, []).append(time.perf_counter())
                yield StreamEvent(EVENT_DELTA, text=piece)
            if then == "fail_after":
                raise _error_for(then_arg)
            if then == "stall_after":
                await asyncio.sleep(10)
            if then == "eof_after":
                return
            yield StreamEvent(EVENT_FINISH, finish_reason="length" if outcome == "length" else "stop")
            if self.usage is not None:
                yield StreamEvent(EVENT_USAGE, input_tokens=self.usage[0], output_tokens=self.usage[1])
        finally:
            self.closed[req.model] = True

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
async def test_inferred_task_type_never_filters_candidates(stores):
    # The proxy infers "chat" for almost any short prompt, and the router's task filter
    # matches that against curated use-case strings. Found live: with an OpenAI key and
    # objective "cheapest", gpt-4o / gpt-4.1 / o1-mini were all excluded as
    # task_type_mismatch because their use-cases do not say "chat". Only a task type the
    # caller states is a hard constraint; the inferred one is recorded, not enforced.
    models = _catalogue()
    for m in models:
        m.use_cases = ["chat"]                          # nothing matches the 'code' keywords
    fake = FakeExecutor()
    body = await _service(stores, fake, models).dispatch(
        customer=_customer(), requested_model="gpt-4", messages=[{"role": "user", "content": "def f(): pass"}],
        policy=DispatchPolicy(), key_resolver=HeaderKeyResolver(ALL_KEYS), locale=LocaleContext(),
        inferred_task_type="code",
    )
    rc = body["dispatch_receipt"]
    assert rc["task_type"] == "code" and rc["task_type_source"] == "inferred"
    assert len(fake.calls) == 1
    assert not any(e["reason"] == "task_type_mismatch" for e in rc["excluded"])


@pytest.mark.asyncio
async def test_stated_task_type_is_named_when_it_eliminates_everything(stores):
    models = _catalogue()
    for m in models:
        m.use_cases = ["chat"]                          # nothing matches the 'code' keywords
    with pytest.raises(DispatchError) as exc:
        await _service(stores, FakeExecutor(), models).dispatch(
            customer=_customer(), requested_model="gpt-4", messages=[{"role": "user", "content": "def f(): pass"}],
            policy=DispatchPolicy(task_type="code"), key_resolver=HeaderKeyResolver(ALL_KEYS),
            locale=LocaleContext(), inferred_task_type="chat",
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


# ===========================================================================
# Streaming (v1.78.0) — dispatch_stream() at the service level
# ===========================================================================

def _stream_gen(svc, policy, headers=ALL_KEYS, model="gpt-4", customer=None, **kw):
    return svc.dispatch_stream(
        customer=customer or _customer(), requested_model=model,
        messages=[{"role": "user", "content": PROMPT}],
        policy=DispatchPolicy(**policy), key_resolver=HeaderKeyResolver(headers),
        locale=LocaleContext(), inferred_task_type="chat", **kw,
    )


async def _stream(svc, policy, **kw):
    """Collect every DispatchStreamEvent. Returns (deltas, terminal event)."""
    events = []
    async for ev in _stream_gen(svc, policy, **kw):
        events.append(ev)
    terminal = events[-1]
    assert terminal.kind in ("end", "error")
    assert all(e.kind == "delta" for e in events[:-1]), [e.kind for e in events]
    return [e.text for e in events[:-1]], terminal


# PROMPT is 3 words and COMPLETION one: the patched count_tokens is a word count.
EST_IN, EST_OUT = 3, 1


@pytest.mark.asyncio
async def test_stream_success_yields_deltas_then_end_envelope(stores):
    ex = FakeExecutor()
    deltas, end = await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    assert deltas == PIECES and "".join(deltas) == COMPLETION
    p = end.payload
    assert p["model"] == "model-b" and p["finish_reason"] == "stop"
    assert p["id"].startswith("chatcmpl-") and isinstance(p["created"], int)
    assert p["usage"] == {"prompt_tokens": IN_TOKENS, "completion_tokens": OUT_TOKENS, "total_tokens": 150}
    assert p["routing_metadata"]["actual_forwarding"] is True
    assert p["routing_metadata"]["dispatch_id"] == p["dispatch_receipt"]["dispatch_id"]
    r = p["dispatch_receipt"]
    assert r["streamed"] is True and r["live_call"] is True
    assert r["chosen"]["model"] == "model-b" and r["attempts"] == [dict(r["attempts"][0], outcome="ok")]
    assert r["latency"]["provider_ms"] > 0
    assert ex.models_called() == ["model-b"]


@pytest.mark.asyncio
async def test_stream_delta_payload_carries_the_ids_the_handler_needs(stores):
    events = [e async for e in _stream_gen(_service(stores, FakeExecutor()), {"objective": "cheapest"})]
    first, end = events[0], events[-1]
    assert first.kind == "delta"
    assert first.payload["dispatch_id"] == end.payload["dispatch_id"]
    assert first.payload["model"] == end.payload["model"] == "model-c"
    assert first.payload["created"] == end.payload["created"]


@pytest.mark.asyncio
async def test_stream_empty_completion_commits_on_finish(stores):
    ex = FakeExecutor(pieces=[])
    deltas, end = await _stream(_service(stores, ex), {"objective": "cheapest"})
    assert deltas == [] and end.kind == "end" and end.payload["finish_reason"] == "stop"
    assert end.payload["dispatch_receipt"]["attempts"][0]["outcome"] == "ok"


@pytest.mark.asyncio
async def test_stream_length_finish_reason(stores):
    deltas, end = await _stream(_service(stores, FakeExecutor({"model-c": "length"})), {"objective": "cheapest"})
    assert end.payload["finish_reason"] == "length" and "".join(deltas) == COMPLETION


# ---------------------------------------------------------------------------
# AC4a / AC4b — measured vs estimated usage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac4a_provider_usage_is_measured(stores):
    ex = FakeExecutor(usage=(10, 5))
    _, end = await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    p = end.payload
    assert p["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    r = p["dispatch_receipt"]
    assert r["usage"] == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "estimated": False}
    assert r["cost"]["actual_usd"] == pytest.approx(_cost(1.0, 10, 5))
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["total_requests"] == 1 and usage["estimated_request_count"] == 0
    assert usage["total_input_tokens"] == 10 and usage["total_output_tokens"] == 5
    row = (await stores["receipts"].list_receipts("org-1"))[0]
    assert row["streamed"] is True and row["usage_estimated"] is False
    assert (row["input_tokens"], row["output_tokens"]) == (10, 5)


@pytest.mark.asyncio
async def test_ac4b_missing_provider_usage_is_estimated_and_flagged(stores):
    ex = FakeExecutor(usage=None)
    _, end = await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    p = end.payload
    assert p["usage"] == {"prompt_tokens": EST_IN, "completion_tokens": EST_OUT, "total_tokens": EST_IN + EST_OUT}
    r = p["dispatch_receipt"]
    assert r["usage"]["estimated"] is True
    assert r["cost"]["actual_usd"] == pytest.approx(_cost(1.0, EST_IN, EST_OUT))
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["total_requests"] == 1 and usage["estimated_request_count"] == 1
    row = (await stores["receipts"].list_receipts("org-1"))[0]
    assert row["streamed"] is True and row["usage_estimated"] is True


@pytest.mark.asyncio
async def test_ac4_partial_usage_counts_as_estimated(stores):
    """Anthropic-style: input tokens arrive before any text, output tokens never do."""
    class HalfUsage(FakeExecutor):
        async def stream(self, req, api_key):
            yield StreamEvent(EVENT_USAGE, input_tokens=77)
            async for ev in FakeExecutor.stream(self, req, api_key):
                if ev.kind != EVENT_USAGE:
                    yield ev
    _, end = await _stream(_service(stores, HalfUsage()), {"objective": "cheapest"})
    assert end.payload["usage"] == {"prompt_tokens": 77, "completion_tokens": EST_OUT, "total_tokens": 77 + EST_OUT}
    assert end.payload["dispatch_receipt"]["usage"]["estimated"] is True


# ---------------------------------------------------------------------------
# AC16 — the non-streaming path is unchanged except for the two receipt fields
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac16_non_streaming_receipt_says_not_streamed_and_measured(stores):
    body = await _dispatch(_service(stores, FakeExecutor()), {"objective": "cheapest", "min_quality_score": 70})
    r = body["dispatch_receipt"]
    assert r["streamed"] is False and r["usage"]["estimated"] is False
    row = (await stores["receipts"].list_receipts("org-1"))[0]
    assert row["streamed"] is False and row["usage_estimated"] is False
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["estimated_request_count"] == 0


@pytest.mark.asyncio
async def test_ac5_streamed_and_plain_receipts_have_the_same_keys(stores):
    body = await _dispatch(_service(stores, FakeExecutor()), {"objective": "cheapest", "min_quality_score": 70})
    _, end = await _stream(_service(stores, FakeExecutor()), {"objective": "cheapest", "min_quality_score": 70})
    plain, streamed = body["dispatch_receipt"], end.payload["dispatch_receipt"]
    assert set(plain) == set(streamed)
    assert plain["streamed"] is False and streamed["streamed"] is True
    assert set(plain["usage"]) == set(streamed["usage"])


# ---------------------------------------------------------------------------
# FR7 — pre-provider refusals raise before the first yield, no provider call
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("policy,status,error", [
    ({"min_quality_score": 99}, 400, "no_eligible_candidate"),
    ({"objective": "pinned", "pinned_model": "model-c", "min_quality_score": 70}, 400, "pinned_model_ineligible"),
    ({"max_request_cost_usd": 0.000001}, 400, "no_eligible_candidate"),
])
async def test_fr7_pre_provider_refusals_raise_before_any_event(stores, policy, status, error):
    ex = FakeExecutor()
    agen = _stream_gen(_service(stores, ex), policy)
    with pytest.raises(DispatchError) as exc:
        await agen.__anext__()
    assert exc.value.status_code == status and exc.value.payload["error"] == error
    assert ex.calls == []
    assert await stores["receipts"].list_receipts("org-1") == []


@pytest.mark.asyncio
async def test_fr7_budget_hard_stop_refuses_a_stream(stores):
    await stores["budget"].register("https://example.com/hook", threshold_usd=0.001, org_id="org-1", hard_stop=True)
    await stores["usage"].record_event("OpenAI", "model-a", 100, 100, 0.002, org_id="org-1")
    ex = FakeExecutor()
    with pytest.raises(DispatchError) as exc:
        await _stream(_service(stores, ex), {})
    assert exc.value.status_code == 402 and exc.value.payload["error"] == "budget_hard_stop"
    assert ex.calls == []


# ---------------------------------------------------------------------------
# AC14 — the per-request cost cap keeps its teeth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac14_cap_bounds_max_tokens_on_the_streaming_request(stores):
    ex = FakeExecutor()
    await _stream(_service(stores, ex), {"objective": "cheapest", "max_request_cost_usd": 0.5})
    assert ex.requests[-1].max_tokens == 100
    ex = FakeExecutor()
    await _stream(_service(stores, ex), {"objective": "cheapest", "max_request_cost_usd": 0.5}, max_tokens=33)
    assert ex.requests[-1].max_tokens == 33
    ex = FakeExecutor()
    await _stream(_service(stores, ex), {"objective": "cheapest"})
    assert ex.requests[-1].max_tokens is None


# ---------------------------------------------------------------------------
# AC8 — failover before first content
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rate_limited", "timeout", "server_error", "model_not_found"])
async def test_ac8_pre_content_failure_fails_over(stores, failure):
    ex = FakeExecutor({"model-b": failure})
    deltas, end = await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    assert ex.models_called() == ["model-b", "model-a"]
    assert "".join(deltas) == COMPLETION and end.payload["model"] == "model-a"
    r = end.payload["dispatch_receipt"]
    assert r["chosen"]["model"] == "model-a"
    assert [(a["model"], a["outcome"]) for a in r["attempts"]] == [("model-b", failure), ("model-a", "ok")]
    assert [a["n"] for a in r["attempts"]] == [1, 2]
    assert ex.closed["model-b"] is True   # the failed attempt's stream was closed before moving on


@pytest.mark.asyncio
async def test_ac8_context_too_long_only_tries_a_larger_window(stores):
    ex = FakeExecutor({"model-b": "context_too_long"})
    _, end = await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    assert ex.models_called() == ["model-b", "model-a"]      # B 32k -> A 128k
    assert end.payload["dispatch_receipt"]["attempts"][0]["outcome"] == OUTCOME_CONTEXT_TOO_LONG
    ex = FakeExecutor({"model-c": "context_too_long"})       # C (1M) first under cheapest: nobody larger
    with pytest.raises(DispatchError) as exc:
        await _stream(_service(stores, ex), {"objective": "cheapest"})
    assert ex.models_called() == ["model-c"]
    assert exc.value.payload["error"] == "all_attempts_failed"


@pytest.mark.asyncio
async def test_ac8_failover_respects_max_attempts(stores):
    ex = FakeExecutor({"model-c": "server_error", "model-b": "server_error"})
    with pytest.raises(DispatchError) as exc:
        await _stream(_service(stores, ex), {"objective": "cheapest", "failover": {"max_attempts": 2}})
    assert ex.models_called() == ["model-c", "model-b"]
    assert exc.value.payload["eligible_remaining"] == 1


# ---------------------------------------------------------------------------
# AC9 — terminal and exhausted failures are plain DispatchErrors (no stream)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["auth_error", "auth_403"])
async def test_ac9_auth_error_is_terminal_json(stores, failure):
    ex = FakeExecutor({"model-b": failure})
    with pytest.raises(DispatchError) as exc:
        await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    assert ex.models_called() == ["model-b"]
    p = exc.value.payload
    assert exc.value.status_code == 400 and p["error"] == "provider_auth_failed"
    assert set(p) >= {"provider", "model", "http_status", "attempts", "dispatch_id", "message"}
    assert p["provider"] == "Anthropic" and p["model"] == "model-b"
    assert KEY not in str(p)
    rows = await stores["receipts"].list_receipts("org-1")
    assert len(rows) == 1 and rows[0]["outcome"] == "failed" and rows[0]["streamed"] is True


@pytest.mark.asyncio
async def test_ac9_provider_rejected_is_terminal_502(stores):
    ex = FakeExecutor({"model-b": "provider_rejected"})
    with pytest.raises(DispatchError) as exc:
        await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    assert exc.value.status_code == 502 and exc.value.payload["error"] == "provider_rejected"
    assert ex.models_called() == ["model-b"]


@pytest.mark.asyncio
async def test_ac9_all_attempts_failed_502_with_the_usual_fields(stores):
    ex = FakeExecutor({"model-b": "timeout", "model-a": "server_error"})
    with pytest.raises(DispatchError) as exc:
        await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70})
    p = exc.value.payload
    assert exc.value.status_code == 502 and p["error"] == "all_attempts_failed"
    assert set(p) >= {"attempts", "eligible_remaining", "eliminating_constraint", "failover_enabled",
                      "max_attempts", "dispatch_id"}
    assert [a["outcome"] for a in p["attempts"]] == ["timeout", "server_error"]
    assert p["eliminating_constraint"] == "min_quality_score" and p["failover_enabled"] is True


# ---------------------------------------------------------------------------
# AC10 — failover off / pinned: one attempt
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [
    {"objective": "cheapest", "min_quality_score": 70, "failover": {"enabled": False}},
    {"objective": "pinned", "pinned_model": "model-b"},
])
async def test_ac10_single_attempt_when_failover_off_or_pinned(stores, policy):
    ex = FakeExecutor({"model-b": "server_error"})
    with pytest.raises(DispatchError) as exc:
        await _stream(_service(stores, ex), policy)
    p = exc.value.payload
    assert exc.value.status_code == 502 and p["error"] == "all_attempts_failed"
    assert len(p["attempts"]) == 1 and p["max_attempts"] == 1 and p["failover_enabled"] is False
    assert ex.models_called() == ["model-b"]


# ---------------------------------------------------------------------------
# AC11 / FR21 — failure after first content: error event, no failover, partial spend
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("script,expected_outcome", [
    (("fail_after", 2, "server_error"), "server_error"),   # provider error event / connection drop
    (("fail_after", 2, "rate_limited"), "rate_limited"),   # retryable outcome, but never retried mid-stream
    (("eof_after", 2), "server_error"),                    # stream ends without a finish reason
])
async def test_ac11_mid_stream_failure_ends_with_error_event(stores, script, expected_outcome):
    ex = FakeExecutor({"model-b": script}, pieces=["COMPLETION-", "SENTINEL-", "XYZ"])
    svc = _service(stores, ex)
    deltas, err = await _stream(svc, {"objective": "cheapest", "min_quality_score": 70, "request_id": "r-11"})
    assert deltas == ["COMPLETION-", "SENTINEL-"]           # exactly two chunks, nothing after the error
    assert err.kind == "error"
    p = err.payload
    assert p["outcome"] == expected_outcome and "error" not in p   # the relay wraps it in {"error": ...}
    assert p["model"] == "model-b" and p["provider"] == "Anthropic"
    assert len(p["attempts"]) == 1 and p["attempts"][0]["outcome"] == expected_outcome
    assert "dispatch_id" in p and "message" in p
    assert ex.models_called() == ["model-b"]                # no failover
    rows = await stores["receipts"].list_receipts("org-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["dispatch_id"] == p["dispatch_id"] and row["outcome"] == "failed" and row["streamed"] is True
    assert row["attempts"] == 1 and row["chosen_model"] == "model-b" and row["usage_estimated"] is True
    attempts = __import__("json").loads(row["attempts_json"])
    assert attempts[0]["outcome"] == expected_outcome
    # FR21: the two delivered pieces ("COMPLETION-SENTINEL-" = one word) are estimated spend.
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["total_requests"] == 1 and usage["estimated_request_count"] == 1
    assert usage["total_input_tokens"] == EST_IN and usage["total_output_tokens"] == 1
    assert row["input_tokens"] == EST_IN and row["output_tokens"] == 1
    assert row["cost_usd"] == pytest.approx(_cost(1.0, EST_IN, 1))
    assert usage["total_cost_usd"] == pytest.approx(row["cost_usd"], abs=1e-9)


@pytest.mark.asyncio
async def test_ac11_partial_spend_uses_provider_input_tokens_when_known(stores):
    class InputFirst(FakeExecutor):
        async def stream(self, req, api_key):
            yield StreamEvent(EVENT_USAGE, input_tokens=55)
            async for ev in FakeExecutor.stream(self, req, api_key):
                yield ev
    ex = InputFirst({"model-c": ("fail_after", 1, "server_error")})
    _, err = await _stream(_service(stores, ex), {"objective": "cheapest"})
    assert err.kind == "error"
    row = (await stores["receipts"].list_receipts("org-1"))[0]
    assert row["input_tokens"] == 55 and row["output_tokens"] == 1


# ---------------------------------------------------------------------------
# AC21 — timeouts: before first content -> failover; after -> error event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac21_no_content_within_timeout_is_a_pre_content_timeout(stores):
    ex = FakeExecutor({"model-b": "hang"})
    deltas, end = await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                                       "failover": {"timeout_ms": 100}})
    assert "".join(deltas) == COMPLETION and end.payload["model"] == "model-a"
    attempts = end.payload["dispatch_receipt"]["attempts"]
    assert [(a["model"], a["outcome"]) for a in attempts] == [("model-b", "timeout"), ("model-a", "ok")]
    assert attempts[0]["latency_ms"] < 1000
    assert ex.closed["model-b"] is True


@pytest.mark.asyncio
async def test_ac21_stall_after_content_is_an_error_event_not_a_failover(stores):
    ex = FakeExecutor({"model-b": ("stall_after", 1)})
    t0 = time.perf_counter()
    deltas, err = await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                                       "failover": {"timeout_ms": 100}})
    assert time.perf_counter() - t0 < 2.0
    assert deltas == [PIECES[0]] and err.kind == "error" and err.payload["outcome"] == "timeout"
    assert ex.models_called() == ["model-b"]
    assert ex.closed["model-b"] is True
    row = (await stores["receipts"].list_receipts("org-1"))[0]
    assert row["outcome"] == "failed" and __import__("json").loads(row["attempts_json"])[0]["outcome"] == "timeout"


@pytest.mark.asyncio
async def test_ac21_slow_but_steady_chunks_do_not_time_out(stores):
    """The timeout is per event, not for the whole stream: 4 chunks at 60 ms each under a 100 ms budget."""
    ex = FakeExecutor(pieces=["a", "b", "c", "d"])
    ex.chunk_delay = 0.06
    deltas, end = await _stream(_service(stores, ex), {"objective": "cheapest", "failover": {"timeout_ms": 100}})
    assert deltas == ["a", "b", "c", "d"] and end.kind == "end"


# ---------------------------------------------------------------------------
# AC12a / AC12b — shadow with streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac12a_shadow_never_delays_deltas_but_the_receipt_waits_for_it(stores):
    ex = FakeExecutor(execute_delay=0.3)    # the shadow (execute) completes well after the primary's chunks
    svc = _service(stores, ex)
    deltas, end = await _stream(svc, {"objective": "cheapest", "min_quality_score": 70,
                                      "shadow": {"model": "model-a"}, "request_id": "r-12"})
    assert "".join(deltas) == COMPLETION
    assert set(ex.models_called()) == {"model-b", "model-a"}
    assert max(ex.yielded["model-b"]) < ex.finished["model-a"]   # every delta was out before the shadow ended
    s = end.payload["dispatch_receipt"]["shadow"]
    assert s["model"] == "model-a" and s["provider"] == "OpenAI" and s["outcome"] == "success"
    assert s["input_tokens"] == IN_TOKENS and s["output_tokens"] == OUT_TOKENS
    assert s["cost_usd"] == pytest.approx(_cost(10.0)) and s["provider_latency_ms"] is not None
    assert ex.finished["model-a"] - ex.started["model-a"] >= 0.3      # the shadow really ran that long
    assert s["cost_delta_usd"] == pytest.approx(_cost(10.0) - _cost(1.0)) and "latency_delta_ms" in s
    import sqlite3
    con = sqlite3.connect(stores["db"])
    rows = con.execute("SELECT request_id, model_name, is_estimated FROM usage_events ORDER BY request_id").fetchall()
    con.close()
    assert rows == [("r-12", "model-b", 0), ("r-12:shadow", "model-a", 0)]


@pytest.mark.asyncio
async def test_ac12b_shadow_failure_never_fails_the_stream(stores):
    ex = FakeExecutor({"model-a": "server_error"})
    deltas, end = await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                                       "shadow": {"model": "model-a"}})
    assert end.kind == "end" and "".join(deltas) == COMPLETION
    s = end.payload["dispatch_receipt"]["shadow"]
    assert s["outcome"] == "server_error" and s["cost_usd"] is None
    # The shadow's text never appears: the fake's execute() returns COMPLETION too, so count the deltas.
    assert deltas == PIECES


@pytest.mark.asyncio
async def test_ac12_shadow_is_recorded_when_the_primary_stream_fails_mid_way(stores):
    ex = FakeExecutor({"model-b": ("fail_after", 1, "server_error")})
    _, err = await _stream(_service(stores, ex), {"objective": "cheapest", "min_quality_score": 70,
                                                  "shadow": {"model": "model-a"}})
    assert err.kind == "error"
    row = (await stores["receipts"].list_receipts("org-1"))[0]
    assert row["shadow_model"] == "model-a" and row["shadow_outcome"] == "success"
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["total_requests"] == 2 and usage["estimated_request_count"] == 1


@pytest.mark.asyncio
async def test_ac12_failover_never_lands_on_the_shadow_model_when_streaming(stores):
    ex = FakeExecutor({"model-c": "server_error", "model-b": "server_error"})
    with pytest.raises(DispatchError) as exc:
        await _stream(_service(stores, ex), {"objective": "cheapest", "shadow": {"objective": "best_quality"}})
    assert exc.value.payload["error"] == "all_attempts_failed"
    assert ex.models_called().count("model-a") == 1
    assert [a["model"] for a in exc.value.payload["attempts"]] == ["model-c", "model-b"]
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["total_requests"] == 1 and usage["by_model"][0]["model_name"] == "model-a"


@pytest.mark.asyncio
async def test_ac12_skipped_shadow_is_reported_in_the_stream_receipt(stores):
    ex = FakeExecutor()
    _, end = await _stream(_service(stores, ex), {"objective": "cheapest", "shadow": {"model": "model-d"}})
    assert ex.models_called() == ["model-c"]
    assert end.payload["dispatch_receipt"]["shadow"]["skip_reason"] == "shadow_model_ineligible"


# ---------------------------------------------------------------------------
# AC19 / FR21 — the client stops reading: provider stream closed, partial spend recorded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac19_client_disconnect_closes_the_provider_stream_and_settles(stores):
    ex = FakeExecutor(pieces=["COMPLETION-", "SENTINEL-", "XYZ"])
    ex.chunk_delay = 0.01
    svc = _service(stores, ex)
    agen = _stream_gen(svc, {"objective": "cheapest", "min_quality_score": 70, "request_id": "r-19"})
    got = [await agen.__anext__(), await agen.__anext__()]
    assert [e.text for e in got] == ["COMPLETION-", "SENTINEL-"]
    await agen.aclose()                      # the client went away
    await svc.wait_settled()
    assert ex.closed["model-b"] is True and len(ex.yielded["model-b"]) == 2   # "XYZ" was never produced
    rows = await stores["receipts"].list_receipts("org-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["outcome"] == "failed" and row["streamed"] is True and row["chosen_model"] == "model-b"
    attempts = __import__("json").loads(row["attempts_json"])
    assert len(attempts) == 1 and attempts[0]["outcome"] == OUTCOME_CLIENT_DISCONNECTED
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["total_requests"] == 1 and usage["estimated_request_count"] == 1
    assert usage["total_output_tokens"] == 1 and row["output_tokens"] == 1


@pytest.mark.asyncio
async def test_ac19_disconnect_still_waits_for_and_records_the_shadow(stores):
    ex = FakeExecutor(pieces=["a", "b", "c"], execute_delay=0.2)
    ex.chunk_delay = 0.01
    svc = _service(stores, ex)
    agen = _stream_gen(svc, {"objective": "cheapest", "min_quality_score": 70, "shadow": {"model": "model-a"},
                             "request_id": "r-19s"})
    await agen.__anext__()
    await agen.aclose()
    await svc.wait_settled()
    row = (await stores["receipts"].list_receipts("org-1"))[0]
    assert row["shadow_outcome"] == "success" and row["shadow_model"] == "model-a"
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["total_requests"] == 2


@pytest.mark.asyncio
async def test_ac19_cancellation_mid_await_is_settled_too(stores):
    """Starlette cancels the body iterator (older ASGI spec) rather than closing it: same outcome."""
    ex = FakeExecutor(pieces=["a", "b", "c"])
    ex.chunk_delay = 0.05
    svc = _service(stores, ex)

    async def consume():
        async for _ in _stream_gen(svc, {"objective": "cheapest", "min_quality_score": 70}):
            pass
    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.08)               # one delta out, the second in flight
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await svc.wait_settled()
    assert ex.closed["model-b"] is True
    rows = await stores["receipts"].list_receipts("org-1")
    assert len(rows) == 1 and rows[0]["outcome"] == "failed"
    assert __import__("json").loads(rows[0]["attempts_json"])[0]["outcome"] == OUTCOME_CLIENT_DISCONNECTED


@pytest.mark.asyncio
async def test_cancellation_after_a_terminal_failure_does_not_settle_the_failed_attempt(stores):
    """The primary's only attempt is a 401 (terminal, already recorded); while dispatch_stream waits
    for the shadow the task is cancelled (server shutdown, not a client disconnect). The failed
    candidate is no longer 'in flight', so no client_disconnected attempt, usage event or receipt
    row with a chosen model may be fabricated for a request the provider rejected."""
    ex = FakeExecutor({"model-b": "auth_error", "model-a": "ok"}, execute_delay=0.3)   # slow shadow only
    svc = _service(stores, ex)

    async def consume():
        async for _ in _stream_gen(svc, {"objective": "cheapest", "min_quality_score": 70,
                                         "shadow": {"model": "model-a"}}):
            pass
    task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.1)                 # 401 recorded immediately; now awaiting the shadow
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await svc.wait_settled()
    await asyncio.sleep(0.35)                # let the shadow task finish before the loop closes
    assert ex.models_called() == ["model-b", "model-a"]
    usage = await stores["usage"].get_summary(org_id="org-1")
    assert usage["total_requests"] == 0      # nothing billed for the rejected primary
    for row in await stores["receipts"].list_receipts("org-1"):
        attempts = __import__("json").loads(row["attempts_json"])
        assert row["chosen_model"] is None and [a["outcome"] for a in attempts] == ["auth_error"]


@pytest.mark.asyncio
async def test_closing_a_finished_stream_settles_nothing_twice(stores):
    svc = _service(stores, FakeExecutor())
    agen = _stream_gen(svc, {"objective": "cheapest"})
    async for _ in agen:
        pass
    await agen.aclose()
    await svc.wait_settled()
    rows = await stores["receipts"].list_receipts("org-1")
    assert len(rows) == 1 and rows[0]["outcome"] == "success"
    assert (await stores["usage"].get_summary(org_id="org-1"))["total_requests"] == 1


# ---------------------------------------------------------------------------
# Receipt store: new columns, migration of a v1.77.0 database
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_receipt_columns_end_with_the_two_new_flags():
    assert RECEIPT_COLUMNS[-3:] == ["live_call", "streamed", "usage_estimated"]


@pytest.mark.asyncio
async def test_receipt_store_migrates_a_pre_streaming_database(tmp_path):
    import sqlite3
    db = str(tmp_path / "old.db")
    old_cols = [c for c in RECEIPT_COLUMNS if c not in ("streamed", "usage_estimated")]
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE dispatch_receipts (" + ", ".join(
        f"{c} {'TEXT PRIMARY KEY' if c == 'dispatch_id' else 'REAL NOT NULL' if c == 'created_at' else 'TEXT'}"
        for c in old_cols) + ")")
    con.execute("INSERT INTO dispatch_receipts (dispatch_id, org_id, created_at, outcome, attempts) "
                "VALUES ('legacy', 'o', ?, 'success', 1)", (time.time(),))
    con.commit()
    con.close()
    store = DispatchReceiptStore(db)
    await store.initialize()
    await store.initialize()    # idempotent: the second ALTER TABLE is swallowed
    await store.record({"dispatch_id": "new", "org_id": "o", "outcome": "success", "streamed": True,
                        "usage_estimated": None})
    rows = {r["dispatch_id"]: r for r in await store.list_receipts("o")}
    assert rows["legacy"]["streamed"] is False and rows["legacy"]["usage_estimated"] is False
    assert rows["new"]["streamed"] is True and rows["new"]["usage_estimated"] is False
    csv_text = store.to_csv(list(rows.values()))
    assert csv_text.splitlines()[0].endswith(",live_call,streamed,usage_estimated")
