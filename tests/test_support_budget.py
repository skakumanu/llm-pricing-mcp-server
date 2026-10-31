"""Support spend caps (v1.84.0): per-caller daily counters, the orchestrator ledger, the fallback.

Spec AC15: anonymous exhausts first, then free, then paid; the global ceiling stops every
LLM-backed support turn while FAQ links and escalation remain usable.
"""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from mcp.caller import ANONYMOUS  # noqa: E402
from src.services.orchestrator_service import SUPPORT_SPEND_AGENT, SUPPORT_SPEND_WORKFLOW  # noqa: E402
from src.services.support_budget import (  # noqa: E402
    ANONYMOUS_TIER, SupportBudget, caller_key, estimate_turn_cost, next_utc_midnight_iso, tier_label, utc_day,
)
from src.services.tiers import support_cap_for  # noqa: E402
from tests.support_helpers import (  # noqa: E402
    FakeClock, build_env, caller_for, dump_actions, run, say, settings,
)

DAY = 86400.0


@pytest.fixture
def env_factory(tmp_path):
    envs = []

    def _make(script, **kw):
        env = build_env(tmp_path, script, **kw)
        envs.append(env)
        return env
    yield _make
    for env in envs:
        env.stop()


class TestCounters:
    def test_caps_are_tiered_anonymous_strictest(self):
        b = SupportBudget(settings)
        caps = [b.cap_for(t) for t in (ANONYMOUS_TIER, "free", "pro", "enterprise")]
        assert caps == [settings.support_daily_cap_anonymous_usd, settings.support_daily_cap_free_usd,
                        settings.support_daily_cap_pro_usd, settings.support_daily_cap_enterprise_usd]
        assert caps == sorted(caps) and caps[0] < caps[1] < caps[2] < caps[3]
        assert support_cap_for(None, settings) == caps[0] and support_cap_for("nonsense", settings) == caps[0]

    def test_check_and_record(self):
        clock = FakeClock()
        b = SupportBudget(settings, clock=clock.now)
        ok, spent, cap = run(b.check("ip:1.2.3.4", ANONYMOUS_TIER))
        assert ok and spent == 0.0 and cap == settings.support_daily_cap_anonymous_usd
        run(b.record("ip:1.2.3.4", cap - 0.001))
        assert run(b.check("ip:1.2.3.4", ANONYMOUS_TIER))[0] is True
        run(b.record("ip:1.2.3.4", 0.002))
        ok, spent, _ = run(b.check("ip:1.2.3.4", ANONYMOUS_TIER))
        assert ok is False and spent >= cap
        # a different caller is unaffected
        assert run(b.check("ip:5.6.7.8", ANONYMOUS_TIER))[0] is True
        # same spend would still be allowed for a free customer
        assert run(b.check("cust:abc", "free"))[0] is True

    def test_resets_at_utc_midnight(self):
        clock = FakeClock(start=1_800_000_000.0)
        b = SupportBudget(settings, clock=clock.now)
        run(b.record("ip:x", 1.0))
        assert run(b.check("ip:x", "free"))[0] is False
        day_before = utc_day(clock.now())
        clock.advance(DAY)
        assert utc_day(clock.now()) != day_before
        ok, spent, _ = run(b.check("ip:x", "free"))
        assert ok is True and spent == 0.0

    def test_escalation_limits(self):
        b = SupportBudget(settings)
        assert b.escalation_limit_for(ANONYMOUS_TIER) == 1
        assert b.escalation_limit_for("free") == settings.support_max_escalations_per_day
        assert run(b.escalation_allowed("ip:a", ANONYMOUS_TIER)) is True
        run(b.record_escalation("ip:a"))
        assert run(b.escalation_allowed("ip:a", ANONYMOUS_TIER)) is False
        for _ in range(settings.support_max_escalations_per_day):
            assert run(b.escalation_allowed("cust:c", "pro")) is True
            run(b.record_escalation("cust:c"))
        assert run(b.escalation_allowed("cust:c", "pro")) is False

    def test_cleanup_drops_stale_counters(self):
        clock = FakeClock()
        b = SupportBudget(settings, clock=clock.now)
        run(b.record("ip:old", 0.01))
        clock.advance(3 * DAY)
        run(b.check("ip:new", "free"))
        assert "ip:old" not in b._counters and "ip:new" in b._counters

    def test_caller_key_and_tier_label(self, env_factory):
        env = env_factory([])
        me = env.customers["free"]
        assert caller_key(ANONYMOUS, "1.2.3.4") == "ip:1.2.3.4"
        assert caller_key(ANONYMOUS, None) == "ip:mcp-anonymous"
        assert caller_key(caller_for(me), "1.2.3.4") == f"cust:{me.id}"
        assert tier_label(ANONYMOUS) == ANONYMOUS_TIER and tier_label(caller_for(me)) == "free"
        assert tier_label(caller_for(me, tier="pro")) == "pro"

    def test_next_utc_midnight(self):
        assert next_utc_midnight_iso(1_800_000_000.0).endswith("T00:00:00Z")


class TestPricing:
    def test_catalogue_price_wins_over_fallback(self):
        class M:
            model_name = "fake-model"
            cost_per_input_token = 1e-6
            cost_per_output_token = 2e-6
        assert estimate_turn_cost(1000, 1000, M(), settings) == pytest.approx(0.003)
        fallback = (1000 * settings.support_fallback_price_per_1m_in_usd
                    + 1000 * settings.support_fallback_price_per_1m_out_usd) / 1_000_000
        assert estimate_turn_cost(1000, 1000, None, settings) == pytest.approx(fallback)
        assert estimate_turn_cost(0, 0, None, settings) == 0.0


class TestLedger:
    def test_every_turn_is_written_to_orch_spend_under_support_triage(self, env_factory):
        env = env_factory([say("a", tokens_in=1000, tokens_out=500), say("b", tokens_in=1000, tokens_out=500)])
        run(env.agent.chat("hi", None, caller=ANONYMOUS, client_ip="1.1.1.1"))
        run(env.agent.chat("hi", None, caller=caller_for(env.customers["pro"]), client_ip="2.2.2.2"))
        import time
        period = time.strftime("%Y-%m", time.gmtime(env.clock.now()))
        expected_each = (1000 * settings.support_fallback_price_per_1m_in_usd
                         + 500 * settings.support_fallback_price_per_1m_out_usd) / 1_000_000
        total = run(env.store.spend_total(period=period, workflow_id=SUPPORT_SPEND_WORKFLOW))
        assert total == pytest.approx(2 * expected_each)
        assert run(env.store.spend_total(period=period)) == pytest.approx(2 * expected_each)
        by_wf = run(env.store.spend_by_workflow(period))
        assert set(by_wf) == {SUPPORT_SPEND_WORKFLOW}
        answers = [a for a in dump_actions(env.store) if a["action"] == "support.answer"]
        assert answers[0]["provenance_json"]["cost_usd"] == pytest.approx(expected_each, rel=1e-3)
        assert answers[0]["provenance_json"]["model"] == settings.agent_model

    def test_record_agent_spend_row_shape(self, env_factory):
        env = env_factory([])
        run(env.svc.record_agent_spend(SUPPORT_SPEND_WORKFLOW, SUPPORT_SPEND_AGENT, "conv:sup-x", "m", 10, 5, 0.25))
        budget = run(env.svc.budget_view())
        wf = next(w for w in budget["workflows"] if w["workflow_id"] == SUPPORT_SPEND_WORKFLOW)
        assert wf["spent_usd"] == pytest.approx(0.25) and wf["monthly_cap_usd"] == 40.0
        assert budget["global"]["spent_usd"] == pytest.approx(0.25)

    def test_budget_gate_refuses_at_workflow_and_global_cap(self, env_factory):
        env = env_factory([])
        ok, reason, _ = run(env.svc.budget_gate(SUPPORT_SPEND_WORKFLOW))
        assert ok and reason is None
        run(env.svc.record_agent_spend(SUPPORT_SPEND_WORKFLOW, SUPPORT_SPEND_AGENT, "conv:a", "m", 1, 1, 40.0))
        ok, reason, _ = run(env.svc.budget_gate(SUPPORT_SPEND_WORKFLOW))
        assert ok is False and reason == "workflow_monthly_cap"
        # other workflows are still fine until the global ceiling
        assert run(env.svc.budget_gate("github_accepted_bug"))[0] is True
        run(env.svc.record_agent_spend("github_accepted_bug", "sdlc", "conv:b", "m", 1, 1, 110.0))
        ok, reason, info = run(env.svc.budget_gate("github_accepted_bug"))
        assert ok is False and reason == "global_monthly_cap" and info["global_spent"] >= 150.0
        soft = [a for a in dump_actions(env.store) if a["action"].startswith("budget.soft_alert:")]
        assert len(soft) == 1  # once per period


class TestFallback:
    def test_anonymous_cap_hit_returns_deterministic_fallback_and_keeps_escalation(self, env_factory):
        env = env_factory([say("should not run")])
        run(env.budget.record("ip:1.1.1.1", settings.support_daily_cap_anonymous_usd))
        a = run(env.agent.chat("hello", None, caller=ANONYMOUS, client_ip="1.1.1.1"))
        b = run(env.agent.chat("hello again", None, caller=ANONYMOUS, client_ip="1.1.1.1"))
        assert env.backend.calls == []
        assert a.fallback == b.fallback == "spend_cap"
        assert a.reply == b.reply
        for link in ("/faq", "/billing", "/mcp-setup", "/api-docs"):
            assert link in a.reply
        assert "talk to a human" in a.reply
        actions = dump_actions(env.store)
        assert sum(1 for x in actions if x["action"] == "support.cap_hit") == 2
        assert all(x["outcome"] == "fallback_cap" for x in actions if x["action"] == "support.answer")
        # the same address as a free customer is NOT capped (customer bucket, higher cap)
        c = run(env.agent.chat("hi", None, caller=caller_for(env.customers["free"]), client_ip="1.1.1.1"))
        assert c.fallback is None

    def test_cap_tiers_exhaust_in_order(self, env_factory):
        env = env_factory([say("x")] * 10)
        free, pro = env.customers["free"], env.customers["pro"]
        spend = settings.support_daily_cap_free_usd  # > anonymous cap, < pro cap
        for key in ("ip:7.7.7.7", f"cust:{free.id}", f"cust:{pro.id}"):
            run(env.budget.record(key, spend))
        assert run(env.agent.chat("q", None, caller=ANONYMOUS, client_ip="7.7.7.7")).fallback == "spend_cap"
        assert run(env.agent.chat("q", None, caller=caller_for(free), client_ip="7.7.7.7")).fallback == "spend_cap"
        assert run(env.agent.chat("q", None, caller=caller_for(pro), client_ip="7.7.7.7")).fallback is None

    def test_fallback_still_escalates_an_explicit_human_request(self, env_factory):
        env = env_factory([say("should not run")])
        run(env.budget.record("ip:8.8.8.8", 1.0))
        resp = run(env.agent.chat("I need to talk to a human about a security vulnerability", None,
                                  caller=ANONYMOUS, client_ip="8.8.8.8"))
        assert env.backend.calls == []
        assert resp.fallback == "spend_cap" and resp.escalation is not None
        ref = resp.escalation["reference"]
        assert ref in resp.reply and "best effort" in resp.reply
        queue = run(env.svc.approval_queue())
        assert queue[0]["run_id"] == ref and queue[0]["artifact_summary_json"]["label"].startswith(
            "customer escalation: bug")

    def test_global_ceiling_stops_everyone(self, env_factory):
        env = env_factory([say("should not run")])
        run(env.svc.record_agent_spend("github_accepted_bug", "sdlc", "conv:z", "m", 1, 1, 150.0))
        for caller, ip in ((ANONYMOUS, "1.1.1.1"), (caller_for(env.customers["free"]), None),
                           (caller_for(env.customers["pro"]), None)):
            resp = run(env.agent.chat("hello", None, caller=caller, client_ip=ip))
            assert resp.fallback == "spend_cap" and "/faq" in resp.reply
        assert env.backend.calls == []
        caps = [a for a in dump_actions(env.store) if a["action"] == "support.cap_hit"]
        assert len(caps) == 3 and all(c["outcome"] == "global_monthly_cap" for c in caps)
