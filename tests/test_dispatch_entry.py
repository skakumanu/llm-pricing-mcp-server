"""v1.80.0 — unit tests for src/services/dispatch_entry.py (the one pre-dispatch pipeline shared
by HTTP and MCP dispatch) and src/services/rate_limiter.py (the one limiter store).

The HTTP behaviour itself is pinned by tests/test_dispatch_api.py and
tests/test_provider_keys_api.py, which pass unmodified; these tests pin the step order, the
single-source payloads, the import boundaries and the helper fallbacks directly.
"""
import ast
import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import src.main as main_module  # noqa: E402
from src.models.dispatch import ChatCompletionsRequest  # noqa: E402
from src.services import dispatch_entry, rate_limiter  # noqa: E402
from src.services.dispatch_entry import (  # noqa: E402
    DispatchCustomer, DispatchDeps, build_key_resolver, default_deps, normalize_stop, prepare_and_dispatch,
)
from src.services.dispatch_executors import HeaderKeyResolver  # noqa: E402
from src.services.dispatch_service import DispatchError  # noqa: E402
from mcp.caller import McpCaller  # noqa: E402

PROMPT = "PROMPT-SENTINEL-ENTRY"
CUSTOMER = SimpleNamespace(id="cust-e", org_id="org-e", tier="free")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _req(**extra):
    body = {"model": "auto", "messages": [{"role": "user", "content": PROMPT}], "dispatch": {}}
    body.update(extra)
    return ChatCompletionsRequest.model_validate(body)


class _Recorder:
    """Fake deps that record the order in which each step is reached."""

    def __init__(self, enabled=True, rate_ok=True, per_minute=60, service_error=False):
        self.steps = []
        self.cfg = SimpleNamespace(dispatch_enabled=enabled, rate_limit_per_minute=per_minute,
                                   exchange_rates_path=main_module.settings.exchange_rates_path,
                                   webhook_secret=None)
        self.rate_ok = rate_ok
        self.service_error = service_error
        self.svc = MagicMock()
        self.svc.dispatch = AsyncMock(side_effect=self._dispatch)
        self.telemetry = MagicMock()
        self.kwargs = None

    async def _dispatch(self, **kwargs):
        self.steps.append("dispatch")
        self.kwargs = kwargs
        return {"choices": [{"message": {"content": "ok"}}]}

    async def resolve_customer(self):
        self.steps.append("customer")
        return CUSTOMER

    async def resolve_none(self):
        self.steps.append("customer")
        return None

    def deps(self):
        async def _allow(key, tier):
            self.steps.append(("rate", key, tier))
            return self.rate_ok

        async def _cleanup():
            self.steps.append("cleanup")

        def _svc():
            self.steps.append("service")
            if self.service_error:
                raise RuntimeError("not initialised")
            return self.svc

        def _store():
            raise RuntimeError("no store")

        return DispatchDeps(settings=self.cfg, get_service=_svc, get_key_store=_store, rate_limit_allow=_allow,
                            cleanup_rate_limits=_cleanup, get_telemetry=lambda: self.telemetry)


def _go(rec, req=None, resolve=None, **kw):
    return _run(prepare_and_dispatch(
        req or _req(), resolve_customer=resolve or rec.resolve_customer, header_resolver=HeaderKeyResolver({}),
        header_currency=kw.pop("header_currency", None), header_country=kw.pop("header_country", None),
        deps=rec.deps(), **kw,
    ))


# ---------------------------------------------------------------------------
# Step order (identical to the pre-v1.80.0 HTTP handler)
# ---------------------------------------------------------------------------

def test_happy_path_runs_every_step_in_order_and_tracks_telemetry():
    rec = _Recorder()
    out = _go(rec)
    assert out == {"choices": [{"message": {"content": "ok"}}]}
    assert rec.steps == ["customer", "cleanup", ("rate", "cust-e", "free"), "service", "dispatch"]
    rec.telemetry.track_feature_usage.assert_called_once_with("dispatch")
    assert rec.kwargs["customer"] is CUSTOMER and rec.kwargs["requested_model"] == "auto"
    assert rec.kwargs["inferred_task_type"] == "chat" and rec.kwargs["webhook_secret"] is None


def test_disabled_is_checked_before_features_and_customer():
    rec = _Recorder(enabled=False)
    with pytest.raises(DispatchError) as exc:
        _go(rec, req=_req(tools=[{"type": "function"}]))
    assert exc.value.status_code == 503 and exc.value.payload["error"] == "dispatch_disabled"
    assert rec.steps == []


def test_unsupported_feature_is_checked_before_customer():
    rec = _Recorder()
    with pytest.raises(DispatchError) as exc:
        _go(rec, req=_req(n=3))
    assert exc.value.status_code == 400 and exc.value.payload["feature"] == "n"
    assert rec.steps == []


def test_no_customer_is_401_before_rate_limit():
    rec = _Recorder()
    with pytest.raises(DispatchError) as exc:
        _go(rec, resolve=rec.resolve_none)
    assert exc.value.status_code == 401 and exc.value.payload == dispatch_entry.dispatch_requires_api_key_payload()
    assert rec.steps == ["customer"]


def test_rate_limited_is_429_with_the_http_body_and_no_service_lookup():
    rec = _Recorder(rate_ok=False)
    with pytest.raises(DispatchError) as exc:
        _go(rec)
    assert exc.value.status_code == 429 and exc.value.payload == {"detail": "Rate limit exceeded"}
    assert "service" not in rec.steps
    exc.value.payload["detail"] = "mutated"
    assert dispatch_entry.RATE_LIMITED_PAYLOAD == {"detail": "Rate limit exceeded"}


def test_rate_limit_skipped_when_disabled_globally():
    rec = _Recorder(per_minute=0)
    _go(rec)
    assert rec.steps == ["customer", "service", "dispatch"]


def test_unsupported_currency_before_service_lookup():
    rec = _Recorder()
    with pytest.raises(DispatchError) as exc:
        _go(rec, req=_req(dispatch={"currency": "XYZ"}))
    assert exc.value.status_code == 400 and exc.value.payload["error"] == "unsupported_currency"
    assert "service" not in rec.steps


def test_header_currency_is_used_when_policy_has_none():
    rec = _Recorder()
    _go(rec, header_currency="EUR")
    assert rec.kwargs["locale"].currency == "EUR"


def test_service_uninitialised_is_503_dispatch_unavailable():
    rec = _Recorder(service_error=True)
    with pytest.raises(DispatchError) as exc:
        _go(rec)
    assert exc.value.status_code == 503 and exc.value.payload == dispatch_entry.dispatch_unavailable_payload()


def test_service_dispatch_errors_propagate_unchanged():
    rec = _Recorder()
    rec.svc.dispatch = AsyncMock(side_effect=DispatchError(402, {"error": "budget_hard_stop", "x": 1}))
    with pytest.raises(DispatchError) as exc:
        _go(rec)
    assert exc.value.status_code == 402 and exc.value.payload == {"error": "budget_hard_stop", "x": 1}
    rec.telemetry.track_feature_usage.assert_not_called()


def test_stream_returns_first_event_and_generator():
    rec = _Recorder()

    async def _gen(**kwargs):
        yield "first"
        yield "second"

    rec.svc.dispatch_stream = MagicMock(side_effect=_gen)
    first, agen = _go(rec, stream=True)
    assert first == "first"
    rec.svc.dispatch.assert_not_called()
    rec.telemetry.track_feature_usage.assert_called_once_with("dispatch")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stop,expected", [("END", ["END"]), (["a", "b"], ["a", "b"]), (None, None), (5, None),
                                           ({"x": 1}, None)])
def test_normalize_stop(stop, expected):
    assert normalize_stop(stop) == expected


def test_dispatch_customer_from_caller_never_holds_a_credential():
    record = SimpleNamespace(id="cust-1", org_id="org-1", tier="pro", api_key="svc-secret", email="a@b.c")
    c = DispatchCustomer.from_caller(McpCaller.from_customer(record))
    assert (c.id, c.org_id, c.tier) == ("cust-1", "org-1", "pro")
    assert "svc-secret" not in repr(c) and "a@b.c" not in repr(c)
    assert not hasattr(c, "api_key") and not hasattr(c, "email")


def test_main_aliases_point_at_the_shared_helpers():
    assert main_module._infer_task_from_messages is dispatch_entry.infer_task_from_messages


def test_build_key_resolver_falls_back_to_header_only():
    header = HeaderKeyResolver({})

    def _missing():
        raise RuntimeError("no store")

    assert _run(build_key_resolver(header, CUSTOMER, _missing)) is header
    unavailable = SimpleNamespace(available=False)
    assert _run(build_key_resolver(header, CUSTOMER, lambda: unavailable)) is header
    empty = SimpleNamespace(available=True, load_for_dispatch=AsyncMock(return_value={}))
    assert _run(build_key_resolver(header, CUSTOMER, lambda: empty)) is header
    empty.load_for_dispatch.assert_awaited_once_with("org-e", exclude=[])


def test_build_key_resolver_db_error_logs_type_name_only(caplog):
    caplog.set_level(logging.DEBUG)
    header = HeaderKeyResolver({})
    broken = SimpleNamespace(available=True,
                             load_for_dispatch=AsyncMock(side_effect=ValueError(f"leak {PROMPT}")))
    assert _run(build_key_resolver(header, CUSTOMER, lambda: broken)) is header
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "ValueError" in text and PROMPT not in text


def test_default_deps_reads_this_modules_globals_at_call_time(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(dispatch_entry, "get_dispatch_service", lambda: sentinel)
    deps = default_deps()
    assert deps.get_service() is sentinel
    assert deps.settings is dispatch_entry.settings


def test_default_deps_rate_limit_uses_the_shared_store():
    rate_limiter._rate_limit_store.pop("entry-test-bucket", None)
    try:
        assert _run(default_deps().rate_limit_allow("entry-test-bucket", "free")) is True
        assert len(main_module._rate_limit_store["entry-test-bucket"]) == 1
    finally:
        rate_limiter._rate_limit_store.pop("entry-test-bucket", None)


# ---------------------------------------------------------------------------
# Rate limiter is one store and main re-exports the same objects
# ---------------------------------------------------------------------------

def test_main_shares_the_limiter_objects():
    assert main_module._rate_limit_store is rate_limiter._rate_limit_store
    assert main_module._rate_limit_lock is rate_limiter._rate_limit_lock
    assert main_module.cleanup_stale_rate_limit_entries is rate_limiter.cleanup_stale_rate_limit_entries


def test_rate_limit_allow_honours_the_tier_limit_from_the_given_settings():
    cfg = SimpleNamespace(rate_limit_free=2, rate_limit_pro=5, rate_limit_enterprise=9, rate_limit_per_minute=1)
    key = "entry-tier-bucket"
    rate_limiter._rate_limit_store.pop(key, None)
    try:
        results = [_run(rate_limiter.rate_limit_allow(key, "free", cfg)) for _ in range(3)]
        assert results == [True, True, False]
        rate_limiter._rate_limit_store.pop(key, None)
        unknown = [_run(rate_limiter.rate_limit_allow(key, "mystery", cfg)) for _ in range(2)]
        assert unknown == [True, False]  # falls back to rate_limit_per_minute
    finally:
        rate_limiter._rate_limit_store.pop(key, None)


# ---------------------------------------------------------------------------
# Import boundaries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel", ["mcp/tools/dispatch.py", "src/services/dispatch_entry.py",
                                 "src/services/rate_limiter.py"])
def test_shared_modules_never_import_src_main(rel):
    tree = ast.parse((project_root / rel).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name not in ("src.main", "main") for a in node.names), rel
        elif isinstance(node, ast.ImportFrom):
            assert node.module not in ("src.main", "main"), rel
            if node.module == "src":
                assert all(a.name != "main" for a in node.names), rel


def test_dispatch_tool_and_entry_add_no_log_lines_about_content():
    """The tool logs nothing at all; the entry's only log line is the type-name-only warning."""
    tool_src = (project_root / "mcp/tools/dispatch.py").read_text(encoding="utf-8")
    assert "logger" not in tool_src and "logging" not in tool_src and "print(" not in tool_src
    entry_src = (project_root / "src/services/dispatch_entry.py").read_text(encoding="utf-8")
    assert entry_src.count("logger.") == 1
