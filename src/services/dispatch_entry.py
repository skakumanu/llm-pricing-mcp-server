"""The one pre-dispatch pipeline shared by every dispatch transport (v1.80.0).

Both the HTTP dispatch branch of POST /v1/chat/completions (src/main.py) and the MCP
``dispatch`` tool (mcp/tools/dispatch.py) call :func:`prepare_and_dispatch`, which runs
the steps between a validated request body and ``DispatchService.dispatch()`` in one
fixed order: dispatch enabled -> supported features -> customer -> per-customer rate
limit -> locale -> service -> key resolver -> dispatch. Every refusal is raised as the
existing ``DispatchError(status_code, payload)`` with the exact payload HTTP has always
returned; DispatchService's own DispatchErrors propagate unchanged. There is no second
implementation of eligibility, objectives, cost cap, budget hard stop, failover, shadow
or ledgers anywhere — those all stay inside DispatchService.

Collaborators arrive through :class:`DispatchDeps`, so each transport decides where they
come from: src/main.py builds them per call from its own module globals (so existing
``patch("src.main.get_dispatch_service")``-style tests still steer HTTP), and
:func:`default_deps` builds them per call from this module's globals.

This module deliberately imports nothing from ``src.main`` and logs nothing about
request content: no prompt, completion or key ever reaches a log line from here.
"""
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from src.config.settings import settings
from src.services.currency import UnsupportedCurrencyError, load_rate_table, resolve_locale
from src.services.dispatch_executors import EXECUTABLE_PROVIDERS, HeaderKeyResolver
from src.services.dispatch_service import DispatchError, get_dispatch_service
from src.services.provider_key_store import CompositeKeyResolver, get_provider_key_store
from src.services.rate_limiter import cleanup_stale_rate_limit_entries, rate_limit_allow
from src.services.telemetry import get_telemetry_service

logger = logging.getLogger(__name__)

RATE_LIMITED_PAYLOAD: Dict[str, Any] = {"detail": "Rate limit exceeded"}


# ---------------------------------------------------------------------------
# Identity and collaborators
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DispatchCustomer:
    """The minimum a dispatch needs to know about who is paying.

    DispatchService reads only ``org_id`` and ``tier``; the rate limiter reads ``id`` and
    ``tier``. Never holds an API key or email. HTTP passes the billing CustomerRecord
    itself (same attributes); MCP builds one from the already-resolved caller.
    """

    id: str
    org_id: str
    tier: Optional[str] = None

    @classmethod
    def from_caller(cls, caller: Any) -> "DispatchCustomer":
        """Build from an mcp.caller.McpCaller (duck-typed: customer_id, org_id, tier)."""
        return cls(
            id=getattr(caller, "customer_id", None),
            org_id=getattr(caller, "org_id", None),
            tier=getattr(caller, "tier", None),
        )


@dataclass(frozen=True)
class DispatchDeps:
    """Collaborators for one dispatch, resolved by the transport at call time."""

    settings: Any
    get_service: Callable[[], Any]
    get_key_store: Callable[[], Any]
    rate_limit_allow: Callable[[str, Optional[str]], Awaitable[bool]]
    cleanup_rate_limits: Callable[[], Awaitable[None]]
    get_telemetry: Callable[[], Any]


def default_deps() -> DispatchDeps:
    """Deps built from this module's globals at call time (tests patch them here)."""
    cfg = settings

    async def _allow(bucket_key: str, tier: Optional[str]) -> bool:
        return await rate_limit_allow(bucket_key, tier, cfg)

    return DispatchDeps(
        settings=cfg,
        get_service=get_dispatch_service,
        get_key_store=get_provider_key_store,
        rate_limit_allow=_allow,
        cleanup_rate_limits=cleanup_stale_rate_limit_entries,
        get_telemetry=get_telemetry_service,
    )


# ---------------------------------------------------------------------------
# Refusal payloads — the single source for both transports
# ---------------------------------------------------------------------------

def dispatch_disabled_payload() -> Dict[str, Any]:
    return {
        "error": "dispatch_disabled",
        "message": "Dispatch mode is disabled on this server (DISPATCH_ENABLED=false).",
    }


def unsupported_feature_payload(feature: str) -> Dict[str, Any]:
    return {
        "error": "unsupported_feature",
        "feature": feature,
        "message": (
            f"Dispatch does not support '{feature}' in this release: text-only, single-choice chat "
            "completions without tools/functions (streaming is supported). Omit the field or drop the "
            "`dispatch` object to get a routing recommendation instead."
        ),
    }


def dispatch_requires_api_key_payload() -> Dict[str, Any]:
    return {
        "error": "dispatch_requires_api_key",
        "message": (
            "Dispatch requires a free-tier API key. Sign up at POST /billing/signup and send the key "
            "as x-api-key or Authorization: Bearer. The global operator key is not accepted for dispatch."
        ),
        "signup": "/billing/signup",
    }


def unsupported_currency_payload(exc: UnsupportedCurrencyError) -> Dict[str, Any]:
    return {
        "error": "unsupported_currency",
        "currency": exc.code,
        "supported_currencies": exc.supported,
        "message": str(exc),
    }


def dispatch_unavailable_payload() -> Dict[str, Any]:
    return {"error": "dispatch_unavailable", "message": "Dispatch service is not initialized."}


# ---------------------------------------------------------------------------
# Helpers (duck-typed over the request model)
# ---------------------------------------------------------------------------

def infer_task_from_messages(messages: List[dict]) -> Optional[str]:
    """Heuristically infer a task_type from the message content."""
    text = " ".join(
        (m.get("content", "") if isinstance(m.get("content", ""), str) else "") or "" for m in messages
    ).lower()
    if any(kw in text for kw in ("function", "code", "class", "def ", "import", "debug", "script")):
        return "code"
    if any(kw in text for kw in ("summarize", "summary", "tldr", "shorten", "abstract")):
        return "summarization"
    if any(kw in text for kw in ("analyze", "analyse", "research", "reason", "explain", "compare")):
        return "analysis"
    return "chat"


def unsupported_dispatch_feature(req: Any) -> Optional[str]:
    """FR16: name the first unsupported feature in a dispatch-mode request, or None.

    `stream` is supported on HTTP since v1.78.0 and is deliberately not checked here.
    """
    if req.tools is not None:
        return "tools"
    if req.functions is not None:
        return "functions"
    if req.tool_choice is not None:
        return "tool_choice"
    if req.n is not None and req.n != 1:
        return "n"
    if req.response_format is not None and not (
        isinstance(req.response_format, dict) and req.response_format.get("type") == "text"
    ):
        return "response_format"  # json_object / json_schema would be silently ignored otherwise
    for m in req.messages:
        content = m.get("content")
        if content is not None and not isinstance(content, str):
            return "multimodal_content"
    return None


def normalize_stop(stop: Any) -> Optional[List[str]]:
    """A single stop string becomes a one-item list; anything that is not a list is dropped."""
    if isinstance(stop, str):
        return [stop]
    if stop is not None and not isinstance(stop, list):
        return None
    return stop


async def build_key_resolver(header: HeaderKeyResolver, customer: Any, get_key_store: Callable[[], Any]):
    """Header keys first, then the org's stored keys (v1.79.0). Never fails the dispatch: an
    uninitialized/unavailable store or a DB error falls back to header-only.

    MCP passes an empty HeaderKeyResolver, so only the org's stored keys are ever used there.
    """
    try:
        store = get_key_store()
    except RuntimeError:
        return header
    if not store.available:
        return header
    covered = header.providers_with_keys()
    if len(covered) >= len(EXECUTABLE_PROVIDERS):
        return header  # every provider already keyed by a header: no stored key could win
    try:
        stored = await store.load_for_dispatch(customer.org_id, exclude=covered)
    except Exception as exc:
        logger.warning("stored provider keys unavailable for this dispatch (%s); header-only", type(exc).__name__)
        return header
    if not stored:
        return header
    return CompositeKeyResolver(header, stored, store, customer.org_id)


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

async def prepare_and_dispatch(
    req: Any,
    *,
    resolve_customer: Callable[[], Awaitable[Optional[Any]]],
    header_resolver: HeaderKeyResolver,
    header_currency: Optional[str],
    header_country: Optional[str],
    deps: DispatchDeps,
    stream: bool = False,
) -> Union[Dict[str, Any], Tuple[Any, Any]]:
    """Run every pre-dispatch step, then DispatchService; raise DispatchError on any refusal.

    Returns the chat.completion body, or for ``stream=True`` ``(first_event, agen)`` after
    pulling the first event (so every pre-content refusal is still a plain DispatchError).
    The customer is resolved only after the enabled and feature checks, as HTTP always has.
    """
    cfg = deps.settings
    if not cfg.dispatch_enabled:
        raise DispatchError(503, dispatch_disabled_payload())

    feature = unsupported_dispatch_feature(req)
    if feature:
        raise DispatchError(400, unsupported_feature_payload(feature))

    # Service key -> customer (org scope). The global MCP_API_KEY never resolves to a
    # customer: dispatch writes per-org ledgers and needs an org to write them to.
    customer = await resolve_customer()
    if customer is None:
        raise DispatchError(401, dispatch_requires_api_key_payload())

    if cfg.rate_limit_per_minute > 0:
        await deps.cleanup_rate_limits()
        if not await deps.rate_limit_allow(customer.id, customer.tier):
            raise DispatchError(429, dict(RATE_LIMITED_PAYLOAD))

    # Locale: explicit statements only — no IP detection for dispatch.
    policy = req.dispatch
    try:
        locale = resolve_locale(
            currency=policy.currency or header_currency,
            country=policy.country or header_country,
            table=load_rate_table(cfg.exchange_rates_path),
            currency_source="query" if policy.currency else "header",
            country_source="query" if policy.country else "header",
        )
    except UnsupportedCurrencyError as exc:
        raise DispatchError(400, unsupported_currency_payload(exc))

    try:
        svc = deps.get_service()
    except RuntimeError:
        raise DispatchError(503, dispatch_unavailable_payload())

    key_resolver = await build_key_resolver(header_resolver, customer, deps.get_key_store)
    kwargs = dict(
        customer=customer,
        requested_model=req.model,
        messages=req.messages,
        policy=policy,
        key_resolver=key_resolver,
        locale=locale,
        inferred_task_type=infer_task_from_messages(req.messages),
        max_tokens=req.max_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
        stop=normalize_stop(req.stop),
        webhook_secret=cfg.webhook_secret,
    )
    if stream:
        # v1.78.0 — pull the first event before the caller builds a response: every
        # pre-content refusal or exhausted failover is then a plain DispatchError.
        agen = svc.dispatch_stream(**kwargs)
        first = await agen.__anext__()
        deps.get_telemetry().track_feature_usage("dispatch")
        return first, agen
    body = await svc.dispatch(**kwargs)
    deps.get_telemetry().track_feature_usage("dispatch")
    return body
