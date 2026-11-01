"""Main FastAPI application for LLM Pricing MCP Server."""
import sys
import hashlib
import logging
import signal
import secrets
from pathlib import Path
from datetime import datetime, timezone

UTC = timezone.utc

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Query, HTTPException, Request, Depends  # noqa: E402
import csv  # noqa: E402
import re  # noqa: E402
import ipaddress  # noqa: E402
import textwrap  # noqa: E402
import json  # noqa: E402
from io import StringIO  # noqa: E402
from fastapi.responses import JSONResponse, Response, StreamingResponse, FileResponse, HTMLResponse  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from typing import Any, Literal, Optional, Dict, List  # noqa: E402
import asyncio  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from src import __version__  # noqa: E402
from src.config.settings import settings  # noqa: E402
from src.models.pricing import (  # noqa: E402
    PricingResponse, ServerInfo, EndpointInfo, CostEstimateRequest, CostEstimateResponse,
    BatchCostEstimateRequest, BatchCostEstimateResponse, ModelCostComparison,
    PerformanceResponse, PerformanceMetrics, ModelUseCase, UseCaseResponse, TelemetryResponse,
    EndpointMetricResponse, ProviderAdoptionResponse, FeatureUsageResponse, TelemetryOverallStats,
    ClientLocationStats, BrowserStats,
    PricingHistoryResponse, PricingTrendsResponse, SubscriptionTrendsResponse, SubscriptionTrendRecord,
    PricingAlertRequest, PricingAlertRecord, PricingAlertListResponse,
    PricingAlertRegistration, PricingAlertTokenRequest, PricingAlertSelfDeleteResponse,
    ConversationSummary, ConversationListResponse,
    RouterRequest, RouterResponse, RouterFeedbackRequest, RouterFeedbackResponse,
    SavingsResponse, SavingsRecord,
    IDEBreakdownResponse, IDEBreakdownRecord,
    UsageEventRequest, UsageEventResponse, UsageBatchRequest, UsageBatchResponse,
    UsageSummaryResponse, SessionUsageAnalysisResponse,
    BudgetAlertRequest, BudgetAlertRecord, BudgetAlertListResponse,
    LocaleResponse, ExchangeRatesResponse, LocalCostRange,
)
from src.services.currency import (  # noqa: E402
    CONVERSION_NOTE, RATE_STALE_AFTER_DAYS, SUPPORTED_CURRENCIES, ZERO_DECIMAL_CURRENCIES,
    LocaleContext, UnsupportedCurrencyError, build_conversion, conversion_metadata, convert, load_rate_table,
    localize_cost, localize_metrics, localize_use_case_cost, resolve_locale,
)
from src.services.pricing_history import init_pricing_history_service, get_pricing_history_service  # noqa: E402
from src.services.pricing_alerts import (  # noqa: E402
    ALERT_TOKEN_REFUSAL_MESSAGE, get_pricing_alert_service, init_pricing_alert_service, redact_alert_tokens,
)
from src.services.router import init_router, get_router  # noqa: E402
from src.services.recommendation_cache import RecommendationCache, get_cache_stats  # noqa: E402
from src.services.savings_tracker import init_savings_tracker, get_savings_tracker  # noqa: E402
from src.services.usage_tracker import init_usage_tracker, get_usage_tracker  # noqa: E402
from src.services.session_recommendation import compute_session_recommendation  # noqa: E402
from src.services.budget_alerts import init_budget_alert_service, get_budget_alert_service  # noqa: E402
from src.services.webhook_guard import WebhookURLRejected, warn_if_private_targets_allowed  # noqa: E402
from src.services.billing_service import (  # noqa: E402
    effective_tier as _effective_tier, init_billing_service, get_billing_service,
)
from src.services import tiers as _tiers  # noqa: E402
from src.services.account_data import AccountDataError, build_account_data_service  # noqa: E402
from src.services import legal_pages as _legal_pages  # noqa: E402
from src.services.support_budget import support_action as _support_action  # noqa: E402
from src.models.dispatch import ChatCompletionsRequest  # noqa: E402
from src.services.dispatch_executors import HeaderKeyResolver  # noqa: E402
from src.services.dispatch_receipts import init_dispatch_receipt_store, get_dispatch_receipt_store  # noqa: E402
from src.services.orchestrator_store import (  # noqa: E402
    ACTION_COLUMNS as _ORCH_ACTION_COLUMNS, RUN_COLUMNS as _ORCH_RUN_COLUMNS, init_orchestrator_store,
)
from src.services.orchestrator_service import (  # noqa: E402
    OrchestratorError, OrchestratorService, get_orchestrator_service, init_orchestrator_service,
)
from src.services.orchestrator_workflows import workflow_catalogue as _orchestrator_workflow_catalogue  # noqa: E402
from src.models.audit import AuditRunIn  # noqa: E402
from src.models.documents import DocGenerateIn  # noqa: E402
from src.services.docs_catalogue import ParamError, describe_types  # noqa: E402
from src.services.docs_service import DocsService, get_docs_service, init_docs_service  # noqa: E402
from src.services.docs_store import init_docs_store  # noqa: E402
from src.services.audit_service import AuditService, get_audit_service, init_audit_service  # noqa: E402
from src.services.audit_store import init_audit_store  # noqa: E402
from src.models.orchestrator import (  # noqa: E402
    ActionResultIn, ApprovalDecisionIn, OrchestratorEventIn, OwnerRunIn, RunCloseIn, SettingsIn,
)
from src.services.provider_key_store import (  # noqa: E402
    SECRET_ENV_VAR as _PROVIDER_KEY_SECRET_ENV, InvalidProviderKey, ProviderKeyExists,
    ProviderKeyNotFound, StoredKeysUnavailable, UnsupportedProvider, accepted_providers as _accepted_key_providers,
    canonical_provider as _canonical_key_provider, get_provider_key_store, init_provider_key_store,
)
from src.services.dispatch_service import (  # noqa: E402
    DispatchError, DispatchService, init_dispatch_service, get_dispatch_service,
)
from src.services import dispatch_entry as _dispatch_entry  # noqa: E402
from src.services import rate_limiter as _rate_limiter  # noqa: E402
from src.services.rate_limiter import (  # noqa: E402
    _rate_limit_lock, _rate_limit_store, cleanup_stale_rate_limit_entries,
)
from src.models.billing import (  # noqa: E402
    AccountExportResponse, AccountDeleteRequest, AccountDeleteResponse,
    SignupRequest, SignupResponse, CheckoutRequest, CheckoutResponse,
    BillingPortalResponse, CustomerDashboard, BillingConfigResponse, KeyRotateResponse,
    SupportChatRequest, SupportChatResponse, EscalationStatusRequest,
)
from src.services.benchmark_service import enrich_models, set_cache_ttl  # noqa: E402
from agent.conversation import init_conversation_store, get_conversation_store  # noqa: E402
from src.models.deployment import (  # noqa: E402
    HealthCheckResponse, DeploymentReadiness, DeploymentMetadata, ApiVersionInfo,
    GracefulShutdownRequest, GracefulShutdownStatus
)
from pydantic import BaseModel, Field  # noqa: E402
from src.services.pricing_aggregator import (  # noqa: E402
    PROVIDER_COUNT, PricingAggregatorService, get_pricing_aggregator,
)
from src.services.cost_calculator import compute_token_cost  # noqa: E402
from src.services.base_provider import registry_models_inventory  # noqa: E402
from src.services.price_oracle import get_price_oracle  # noqa: E402
from src.services.data_quality import compute_data_quality_report  # noqa: E402
from src.services.telemetry import get_telemetry_service  # noqa: E402
from src.services.deployment import get_deployment_manager  # noqa: E402
from src.services.geolocation import GeolocationService  # noqa: E402
from agent.pricing_agent import PricingAgent  # noqa: E402
from agent.support_agent import SupportAgent  # noqa: E402
from mcp.tools.tool_manager import ToolManager  # noqa: E402
from mcp.tools import support_chat as _support_chat_tool  # noqa: E402
from mcp.caller import ANONYMOUS, PUBLIC, McpCaller  # noqa: E402
from mcp.sessions import McpSessionStore  # noqa: E402
from mcp.tools._client import handshake_client  # noqa: E402

# Configure logging after settings are available
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("Starting application initialization...")
logger.info("Imports completed successfully")

# Initialize FastAPI app
_openapi_tags = [
    {
        "name": "Pricing",
        "description": (
            f"Live LLM model pricing data across {PROVIDER_COUNT} providers (600+ models). "
            "Supports filtering by provider and capability."
        ),
    },
    {
        "name": "Pricing History",
        "description": "Historical pricing snapshots, trend analysis, and CSV/JSON export.",
    },
    {
        "name": "Pricing Alerts",
        "description": "Register and manage webhook-based price-change alerts for specific models.",
    },
    {
        "name": "Cost Estimation",
        "description": "Estimate and compare token-based costs across models for single or batched workloads.",
    },
    {
        "name": "Performance",
        "description": "Quality scores, benchmark data, and use-case routing recommendations.",
    },
    {
        "name": "Router",
        "description": (
            "Intelligent model routing — recommend the best model for a given prompt, "
            "stream routing events, and submit feedback to improve future recommendations."
        ),
    },
    {
        "name": "Telemetry",
        "description": "API usage metrics, endpoint adoption stats, and per-org cost savings tracking.",
    },
    {
        "name": "Usage",
        "description": (
            "Record and summarize actual LLM usage/spend — the real-cost counterpart to "
            "/cost-estimate's hypothetical numbers. Cost is always computed server-side "
            "from current pricing, never trusted from the caller."
        ),
    },
    {
        "name": "Agent",
        "description": (
            "RAG-powered pricing assistant. Supports streaming and persistent conversation history."
        ),
    },
    {
        "name": "Billing",
        "description": "Customer sign-up, Stripe checkout, subscription portal, and account dashboard.",
    },
    {
        "name": "Health",
        "description": "Liveness, readiness, and detailed health check endpoints for orchestrators.",
    },
    {
        "name": "Deployment",
        "description": "Deployment metadata, API version info, and graceful shutdown control.",
    },
    {
        "name": "API Info",
        "description": "Rate-limit tier definitions and API version negotiation.",
    },
    {
        "name": "Admin",
        "description": "Internal admin endpoints — aggregate stats, rate-limit state, and customer list.",
    },
    {
        "name": "Orchestrator",
        "description": (
            "Operator-only orchestrator core (v1.83.0): idempotent intake, run log, approval queue, parked runs, "
            "budgets and settings. Every route needs the operator key; the three executor routes also accept "
            "ORCHESTRATOR_INTAKE_KEY."
        ),
    },
    {
        "name": "Documents",
        "description": (
            "Operator-only document writer (v1.89.0): decks, one-pagers, release briefs and status reports "
            "from a closed catalogue, every figure traced to a provenance manifest, stored as artefacts that "
            "wait for the owner's publish approval. Every route needs the operator key."
        ),
    },
    {
        "name": "OpenAI Proxy",
        "description": (
            "OpenAI-compatible `/v1/chat/completions` proxy that transparently routes requests "
            "through the LLM router and returns pricing metadata in the response."
        ),
    },
    {
        "name": "Dispatch",
        "description": (
            "Dispatch gateway (v1.77.0): send a `dispatch` policy on `/v1/chat/completions` with your "
            "own provider key to have the best eligible model executed for real, with failover and a "
            "receipt (chosen model, reason, actual cost, savings); add `stream: true` (v1.78.0) for "
            "OpenAI-format chunk SSE with the receipt in a trailing chunk. `/dispatch/providers` lists "
            "what is dispatchable; `/dispatch/receipts` exports an org's metadata-only audit trail; "
            "`/dispatch/keys` (v1.79.0) stores your org's provider keys encrypted so dispatch can use "
            "them when a request carries no `X-Provider-Key-<Provider>` header (a header always wins)."
        ),
    },
]

_api_description = """\
## LLM Pricing MCP Server

Real-time pricing comparison for **12+ LLM providers** (OpenAI, Anthropic, Google, Cohere, \
Mistral, Meta, xAI, DeepSeek, and more).

### Key capabilities
- **Live pricing** — sub-second model cost lookup across all providers
- **Cost estimation** — token-level cost calculator with batch support
- **Performance benchmarks** — quality scores and value-index rankings
- **Intelligent router** — automatically picks the best model for your workload
- **Price alerts** — webhook notifications when a model's price changes
- **RAG agent** — conversational assistant with full pricing context
- **Billing / SaaS** — Stripe-powered free / pro / enterprise tiers

### Authentication
Most read endpoints are **public** (no key required).
Mutating and admin endpoints require `X-API-Key: <your-key>` in the request header.

### Rate limits
| Tier | Limit |
|------|-------|
| Free | 30 req / min |
| Pro | 120 req / min |
| Enterprise | 600 req / min |

Billing API keys get their tier from the billing record. Callers using the global \
`MCP_API_KEY` may pass `X-Api-Key-Tier: free|pro|enterprise` to select a bucket; the \
header is ignored on anonymous requests, which always use the default limit.

### MCP transport
The server exposes an HTTP MCP endpoint at `/mcp` for direct integration with \
Claude Desktop, Cursor, and other MCP-compatible clients.
"""

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=_api_description,
    openapi_tags=_openapi_tags,
    contact={
        "name": "LLM Pricing MCP Server",
        "url": "https://llm-pricing-api.fly.dev",
    },
    license_info={
        "name": "MIT",
        "url": "https://opensource.org/licenses/MIT",
    },
    docs_url=None,
    redoc_url=None,
)

logger.info(f"FastAPI app created: {app.title} v{app.version}")

_static_dir = Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/chat", StaticFiles(directory=str(_static_dir), html=True), name="static")
    _history_dir = _static_dir / "history"
    if _history_dir.exists():
        app.mount("/history", StaticFiles(directory=str(_history_dir), html=True), name="history_static")
    _trends_dir = _static_dir / "trends"
    if _trends_dir.exists():
        app.mount("/trends", StaticFiles(directory=str(_trends_dir), html=True), name="trends_static")
    _conversations_dir = _static_dir / "conversations"
    if _conversations_dir.exists():
        app.mount(
            "/conversations",
            StaticFiles(directory=str(_conversations_dir), html=True),
            name="conversations_static",
        )
    _calculator_dir = _static_dir / "calculator"
    if _calculator_dir.exists():
        app.mount(
            "/calculator",
            StaticFiles(directory=str(_calculator_dir), html=True),
            name="calculator_static",
        )
    _compare_dir = _static_dir / "compare"
    if _compare_dir.exists():
        app.mount(
            "/compare",
            StaticFiles(directory=str(_compare_dir), html=True),
            name="compare_static",
        )
    _ide_compare_dir = _static_dir / "ide-compare"
    if _ide_compare_dir.exists():
        app.mount(
            "/ide-compare",
            StaticFiles(directory=str(_ide_compare_dir), html=True),
            name="ide_compare_static",
        )
    _widget_dir = _static_dir / "widget"
    if _widget_dir.exists():
        app.mount(
            "/widget",
            StaticFiles(directory=str(_widget_dir), html=True),
            name="widget_static",
        )
    _mcp_setup_dir = _static_dir / "mcp-setup"
    if _mcp_setup_dir.exists():
        app.mount(
            "/mcp-setup",
            StaticFiles(directory=str(_mcp_setup_dir), html=True),
            name="mcp_setup_static",
        )
    _api_docs_dir = _static_dir / "api-docs"
    if _api_docs_dir.exists():
        app.mount(
            "/api-docs",
            StaticFiles(directory=str(_api_docs_dir), html=True),
            name="api_docs_static",
        )
    _whats_new_dir = _static_dir / "whats-new"
    if _whats_new_dir.exists():
        app.mount(
            "/whats-new",
            StaticFiles(directory=str(_whats_new_dir), html=True),
            name="whats_new_static",
        )
    # v1.84.0: customer FAQ, generated from docs/FAQ.md by scripts/sync_faq.py
    _faq_dir = _static_dir / "faq"
    if _faq_dir.exists():
        app.mount(
            "/faq",
            StaticFiles(directory=str(_faq_dir), html=True),
            name="faq_static",
        )
    # Admin page served directly (not as a static mount) so /admin/stats
    # and /admin/rate-limits API routes are not shadowed by StaticFiles.

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "x-api-key", "Mcp-Session-Id"],
    expose_headers=["Mcp-Session-Id"],
)

# Initialize deployment manager for blue-green deployment support
deployment_manager = get_deployment_manager(version=settings.app_version)
logger.info("Deployment manager initialized")

# Security controls
# v1.80.0 — the limiter store, lock and cleanup live in src/services/rate_limiter.py so the
# MCP dispatch tool shares the same per-customer bucket; these names are the SAME objects.
_auth_warning_logged = False


async def _rate_limit_allow(bucket_key: str, tier: str) -> bool:
    """Consume one request from `bucket_key`'s per-minute token bucket; False when exhausted.

    Thin wrapper over src.services.rate_limiter.rate_limit_allow that reads this module's
    own ``settings`` at call time (tests replace src.main.settings wholesale). The security
    middleware calls it for every non-bypassed path, and the dispatch branch of
    /v1/chat/completions calls it (via dispatch_entry) with the customer's id and tier.
    """
    return await _rate_limiter.rate_limit_allow(bucket_key, tier, settings)


async def _body_too_large(request: Request) -> Optional[JSONResponse]:
    """Body-size cap for POST/PUT/PATCH; None when the request may proceed.

    Checks the declared Content-Length first (cheap refusal), then the actual bytes so a
    chunked or mis-declared body cannot slip past, and buffers the body on request._body
    for the handler. The one implementation: the security middleware calls it for every
    non-bypassed path and for the public-but-capped paths below.
    """
    if request.method not in {"POST", "PUT", "PATCH"}:
        return None
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_body_bytes:
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length header"})
    body = await request.body()
    if len(body) > settings.max_body_bytes:
        return JSONResponse(status_code=413, content={"detail": "Request body too large"})
    request._body = body
    return None


# Anonymous but throttled: no API key required, yet still subject to the per-IP
# rate limiter and the body-size cap. Distinct from _unauthenticated_paths, whose
# members the security middleware bypasses entirely (no limiter either). Every
# path here either runs an LLM on the operator's key or writes a stored record,
# so unthrottled = unbounded spend or unbounded storage.
_anonymous_rate_limited_paths = {
    "/agent/chat",
    "/agent/chat/stream",
    # v1.78.1: public, but each call can mint a service key and the 200-vs-409
    # reply says whether an email is registered, so it goes through the limiter
    # and body cap instead of the full bypass.
    "/billing/signup",
    # v1.79.2: anonymous price-alert registration (POST) stores a webhook row, so it
    # is throttled and body-capped. GET on this path is operator-only (see
    # _is_operator_only_request), so it never reaches the anonymous branch.
    "/pricing/alerts",
    # v1.81.0: alert_token self-service (token in the JSON body, never the URL). Must be
    # listed here, or the "/pricing" prefix bypass would skip the limiter and body cap.
    "/pricing/alerts/self/view",
    "/pricing/alerts/self/delete",
    # v1.82.0: cost estimation computes from public prices, the same math as the public
    # estimate_cost / compare_costs MCP tools. It needed a key only because it was never
    # listed; now anonymous but throttled and body-capped (batch does one lookup per model).
    "/cost-estimate",
    "/cost-estimate/batch",
    # v1.84.0: the support agent runs an LLM on the operator's key (its own per-caller daily
    # caps apply on top of the limiter); the status lookup reads the orchestrator store.
    "/support/chat",
    "/support/escalations/status",
}

# Anonymous-throttled paths where a valid customer key, if sent, still gets that
# customer's tier limit instead of the shared per-IP bucket. This set affects rate
# limiting only: handlers here must not *require* request.state.customer. The
# cost-estimate handlers ignore it; the v1.84.0 support handlers resolve the caller
# themselves from the connection headers (_resolve_mcp_caller), never from this state.
_customer_tiered_anonymous_paths = {
    "/cost-estimate",
    "/cost-estimate/batch",
    "/support/chat",
    "/support/escalations/status",
}

# Public (no auth, no limiter) but still body-capped: /v1/chat/completions must stay
# anonymous for the plain OpenAI-SDK proxy, yet its dispatch branch parses a policy
# object and persists parts of it per receipt, so the cap has to run *before* FastAPI
# reads the body — an in-handler check would run after the whole body is in memory.
# v1.79.2: /mcp too — tools/call bodies (e.g. import_session_usage batches) are parsed
# and persisted, so the 1 MB cap runs before mcp_http reads the JSON.
_public_body_capped_paths = {
    "/v1/chat/completions",
    "/mcp",
}

_unauthenticated_paths = {
    "/",
    "/health",
    "/health/live",
    "/health/ready",
    "/health/detailed",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/chat",
    "/mcp-setup",
    "/api-docs",
    "/whats-new",
    "/v1/chat/completions",
    # v1.77.0 — public catalogue of executable providers / dispatchable models
    "/dispatch/providers",
    "/rate-limits/tiers",
    "/billing",
    "/billing/webhook",
    # v1.84.0: public tier catalogue + checkout/portal availability for the billing page and agent
    "/billing/config",
    # HTTP MCP transport — public so remote MCP clients (Claude Desktop, Cursor) can connect
    "/mcp",
    # Core pricing data — public read-only endpoints used by the frontend pages
    "/pricing",
    "/pricing/history",
    "/pricing/trends",
    "/pricing/public",
    "/data-quality",
    "/models",
    "/providers",
    # v1.76.0 — locale preset + vendored rate table for the browser currency selector
    "/locale",
    "/currency/rates",
    # Public read-only dashboard endpoints (non-sensitive aggregate stats)
    "/telemetry",
    "/performance",
    "/use-cases",
    "/cache-stats",
}

_sensitive_paths = {
    "/deployment/shutdown",
    "/deployment/shutdown/status",
    # v1.78.1: operator-only. It lists every customer (email, org_id, Stripe id).
    # Before this it accepted any valid customer key, and public signup hands those
    # out, so any free account could read it and use the org_ids against endpoints
    # that take an org_id parameter.
    "/admin/customers",
    # v1.81.4: operator-only. /admin/rate-limits lists the top limiter buckets, which are
    # visitor IPs and customer ids; /admin/stats exposes per-endpoint traffic and provider
    # errors. Both were on the public allow-list despite docstrings saying they need a key.
    # The /admin page already sends the operator key on every data call.
    "/admin/stats",
    "/admin/rate-limits",
}

# v1.79.2: operator-only HTTP routes that live under otherwise-public prefixes
# ("/pricing", "/agent"). Method-aware, so POST /pricing/alerts stays anonymous and
# GET /pricing/alerts/signing-info stays public. Checked before the full bypass.
_OPERATOR_ONLY_EXACT = {
    ("GET", "/pricing/alerts"),
    ("HEAD", "/pricing/alerts"),
    ("GET", "/agent/conversations"),
    ("HEAD", "/agent/conversations"),
}
_OPERATOR_ONLY_DELETE_PREFIXES = ("/pricing/alerts/", "/agent/conversations/")
# v1.83.0: every method under these prefixes is operator-only (run log, approval queue,
# parked runs, settings, budget). Constant refusals, limiter and body cap are inherited.
# v1.84.0: /admin/customers/{id}/key/rotate (owner-side key rotation) joins them; the exact
# path /admin/customers stays in _sensitive_paths.
# v1.88.0: /audit/ (compliance audit reports) joins them; the intake key is NOT accepted there.
# v1.89.0: /documents/ (document writer) joins them. Not "/docs/": GET /docs is the public Swagger page.
_OPERATOR_ONLY_PREFIXES = ("/orchestrator/", "/admin/customers/", "/audit/", "/documents/")

# The three executor routes that also accept the separate ORCHESTRATOR_INTAKE_KEY
# (GitHub Actions). The intake key unlocks nothing else, but through these three routes it can
# assert owner identity on a GitHub event (the workflow derives it from sender == repository
# owner), so it is a secret: a pull_request.opened continuation is additionally bound to the
# repository the run was created from (OrchestratorService._pr_matches_run). The operator key
# still works here.
_ORCHESTRATOR_INTAKE_ROUTES = (
    ("POST", re.compile(r"^/orchestrator/events/?$")),
    ("GET", re.compile(r"^/orchestrator/runs/[^/]+/actions/?$")),
    ("POST", re.compile(r"^/orchestrator/runs/[^/]+/actions/[^/]+/result/?$")),
)


def _is_operator_only_request(method: str, path: str) -> bool:
    """True when (method, path) must carry the operator key (MCP_API_KEY).

    Lists every subscriber's alert webhooks / every visitor's conversations, or
    deletes one of them; or anything under /orchestrator/ (v1.83.0). OPTIONS (CORS
    preflight) is never matched.
    """
    method = (method or "").upper()
    if method == "OPTIONS":
        return False
    if path.startswith(_OPERATOR_ONLY_PREFIXES):
        return True
    if method == "DELETE" and path.startswith(_OPERATOR_ONLY_DELETE_PREFIXES):
        return True
    return (method, path.rstrip("/") or "/") in _OPERATOR_ONLY_EXACT


def _is_orchestrator_intake_route(method: str, path: str) -> bool:
    method = (method or "").upper()
    return any(m == method and rx.match(path) for m, rx in _ORCHESTRATOR_INTAKE_ROUTES)


def _orchestrator_intake_key() -> Optional[str]:
    key = settings.orchestrator_intake_key
    return key.get_secret_value() if key else None


def _key_matches(provided: Optional[str], expected: Optional[str]) -> bool:
    """Constant-time compare on UTF-8 bytes (str compare_digest raises on non-ASCII)."""
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _orchestrator_caller_kind(request: Request) -> str:
    """'operator' when the request carries MCP_API_KEY, else 'executor' (intake key)."""
    provided = request.headers.get(settings.mcp_api_key_header)
    return "operator" if _key_matches(provided, settings.mcp_api_key) else "executor"


# Add deployment middleware for request tracking (needed for graceful shutdown)


@app.middleware("http")
async def deployment_middleware(request: Request, call_next):
    """
    Middleware to track active requests for graceful shutdown support.
    Rejects new requests if graceful shutdown is in progress.
    """
    if deployment_manager.is_shutting_down():
        if request.url.path not in ["/health", "/health/live", "/health/ready", "/health/detailed"]:
            # A middleware must return a Response; returning an HTTPException object
            # made Starlette raise and the client saw a 500 instead of this 503.
            return JSONResponse(status_code=503, content={"detail": "Service is shutting down"})

    try:
        await deployment_manager.track_request_start()
    except RuntimeError as e:
        return JSONResponse(status_code=503, content={"detail": str(e)})

    try:
        response = await call_next(request)
    finally:
        await deployment_manager.track_request_end()

    return response

# Add security middleware for auth, rate limits, and size limits


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """
    Enforce API key auth, rate limits, and request size limits.
    """
    global _auth_warning_logged

    path = request.url.path
    # v1.79.2: operator-only routes under public prefixes are decided first, so the
    # "/pricing" prefix bypass below can no longer expose them.
    operator_only = _is_operator_only_request(request.method, path)
    # Full bypass: skips auth, rate limiting AND the body-size cap. Keep this to
    # static UIs and read-only data. /agent/chat is deliberately NOT here — it
    # runs an LLM on the operator's key, so it stays anonymous via
    # _anonymous_rate_limited_paths but still falls through to the rate limiter.
    # OPTIONS (CORS preflight) is always bypassed; _is_operator_only_request never
    # matches it, and anonymous-throttled paths keep an unthrottled preflight.
    if request.method == "OPTIONS" or (
        not operator_only
        and path not in _anonymous_rate_limited_paths
        and (
            path.startswith("/chat")
            or path.startswith("/history") or path.startswith("/trends")
            or path.startswith("/conversations") or path.startswith("/calculator")
            or path.startswith("/compare") or path.startswith("/ide-compare")
            or path.startswith("/widget")
            or path.startswith("/pricing") or path.startswith("/models")
            or path.startswith("/providers") or path.startswith("/landing")
            or path.startswith("/mcp")
            or path.startswith("/mcp-setup") or path.startswith("/api-docs")
            or path.startswith("/whats-new") or path.startswith("/faq")
            or path in _LEGAL_PAGE_PATHS
            or path == "/admin" or path in _unauthenticated_paths
        )
    ):
        if path in _public_body_capped_paths:
            rejected = await _body_too_large(request)
            if rejected is not None:
                return rejected
        return await call_next(request)
    if operator_only or path in _sensitive_paths:
        # v1.83.0: the three orchestrator executor routes also accept ORCHESTRATOR_INTAKE_KEY.
        intake_key = _orchestrator_intake_key() if _is_orchestrator_intake_route(request.method, path) else None
        if not settings.mcp_api_key and not intake_key:
            return JSONResponse(
                status_code=503,
                content={"detail": "Authentication not configured"},
            )
        provided_key = request.headers.get(settings.mcp_api_key_header)
        # Compare as UTF-8 bytes: str compare_digest raises TypeError on non-ASCII
        # input (headers are latin-1 decoded), which would turn a bad key into a 500.
        # The key itself is never logged.
        if not (_key_matches(provided_key, settings.mcp_api_key) or _key_matches(provided_key, intake_key)):
            client_ip = (
                request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or request.headers.get("x-real-ip")
                or (request.client.host if request.client else None)
                or "unknown"
            )
            logger.warning(
                "Authentication failed for path %s from client IP %s",
                path,
                client_ip,
            )
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    elif path not in _unauthenticated_paths and path not in _anonymous_rate_limited_paths:
        provided_key = request.headers.get(settings.mcp_api_key_header)

        # Per-customer billing key lookup (falls back to global key if not found)
        customer = None
        try:
            billing = get_billing_service()
            if provided_key:
                customer = await billing.get_customer_by_api_key(provided_key)
        except RuntimeError:
            pass  # nosec B110 — billing not initialized yet
        request.state.customer = customer

        if settings.mcp_api_key and not customer:
            # Bytes compare: str compare_digest raises TypeError on non-ASCII keys (500 not 401).
            if not provided_key or not secrets.compare_digest(
                provided_key.encode("utf-8"), settings.mcp_api_key.encode("utf-8")
            ):
                client_ip = (
                    request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                    or request.headers.get("x-real-ip")
                    or (request.client.host if request.client else None)
                    or "unknown"
                )
                logger.warning(
                    "Authentication failed for path %s from client IP %s",
                    path,
                    client_ip,
                )
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        elif not settings.mcp_api_key and not customer and not _auth_warning_logged:
            logger.warning("MCP API key not configured; endpoints are unauthenticated.")
            _auth_warning_logged = True
    elif path in _customer_tiered_anonymous_paths:
        # Anonymous allowed; an unknown or wrong key just falls back to the per-IP bucket.
        provided_key = request.headers.get(settings.mcp_api_key_header)
        if provided_key:
            try:
                request.state.customer = await get_billing_service().get_customer_by_api_key(provided_key)
            except RuntimeError:
                pass  # nosec B110 — billing not initialized yet

    if settings.rate_limit_per_minute > 0:
        # Periodically cleanup stale IP entries
        await cleanup_stale_rate_limit_entries()

        # Note: X-Forwarded-For header parsing assumes server is behind a trusted proxy
        # (e.g., Azure App Service, nginx). Without a trusted proxy, this can be spoofed.
        client_ip = (
            request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or request.headers.get("x-real-ip")
            or (request.client.host if request.client else None)
            or "unknown"
        )
        _customer = getattr(request.state, "customer", None)
        if _customer:
            # v1.84.0: a past-due customer past the grace period is served at the free limit.
            tier = _effective_tier(_customer, settings.billing_past_due_grace_days)
            bucket_key = _customer.id
        else:
            # X-Api-Key-Tier is self-declared, so it only counts from a caller
            # who has proven something: the global MCP_API_KEY. Otherwise an
            # anonymous client could claim "enterprise" for 10x the default.
            provided = request.headers.get(settings.mcp_api_key_header)
            holds_global_key = bool(
                settings.mcp_api_key and provided
                and secrets.compare_digest(provided.encode("utf-8"), settings.mcp_api_key.encode("utf-8"))
            )
            tier = request.headers.get("X-Api-Key-Tier", "").lower() if holds_global_key else ""
            bucket_key = f"{client_ip}:{tier}"
        if not await _rate_limit_allow(bucket_key, tier):
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    rejected = await _body_too_large(request)
    if rejected is not None:
        return rejected

    return await call_next(request)

# Add telemetry middleware for automatic request tracking


@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    """
    Middleware to automatically track all HTTP requests for telemetry.
    Measures response time, status code, and client information for each endpoint.
    """
    start_time = time.time()

    # Extract client IP from headers (handles proxies and load balancers)
    client_ip = (
        request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else None)
        or "unknown"
    )

    # Extract user agent
    user_agent = request.headers.get("user-agent")

    # Parse browser info synchronously (fast)
    browser_info = GeolocationService.parse_user_agent(user_agent)
    browser_name = browser_info.get("browser")

    # Get geolocation asynchronously (cached). Geolocate the *trusted* address
    # when there is one (Fly-Client-IP first, syntactically public only — see
    # _client_ip_for_geo) so a client-prepended X-Forwarded-For hop cannot steer
    # the country that v1.76.0's currency preset echoes back; otherwise keep the
    # telemetry address so private/local peers record "Private Network" as before.
    geo_ip = _client_ip_for_geo(request) or client_ip
    try:
        geo_info = await GeolocationService.get_geolocation(geo_ip)
        country = geo_info.get("country") if geo_info else None
        country_code = geo_info.get("country_code") if geo_info else None
    except Exception as e:
        logger.debug("Failed to get geolocation: %s", e)
        country = None
        country_code = None

    # v1.76.0 — let resolve_request_locale reuse this lookup instead of doing a
    # second one. Only the country code is stashed; never the address.
    request.state.geo_country_code = country_code

    try:
        response = await call_next(request)
    except Exception:
        # Handle exceptions, track as error
        elapsed_ms = (time.time() - start_time) * 1000
        telemetry = get_telemetry_service()
        telemetry.track_endpoint_request(
            request.url.path,
            request.method,
            elapsed_ms,
            status_code=500,
            client_ip=client_ip,
            country=country,
            country_code=country_code,
            browser=browser_name,
        )
        raise

    # Track successful response
    elapsed_ms = (time.time() - start_time) * 1000
    telemetry = get_telemetry_service()
    telemetry.track_endpoint_request(
        request.url.path,
        request.method,
        elapsed_ms,
        status_code=response.status_code,
        client_ip=client_ip,
        country=country,
        country_code=country_code,
        browser=browser_name,
    )

    return response

logger.info("Middleware registered: deployment tracking and telemetry")

# Pricing aggregator singleton now lives in src.services.pricing_aggregator
# (imported above) so it can be shared with MCP tools within this same
# process, not just REST endpoints. get_pricing_aggregator()'s signature and
# behavior are unchanged; only its home moved.

logger.info("Application initialization complete")

# ------------------------------------------------------------------
# v1.76.0 — local currency: request-scoped locale resolution
# ------------------------------------------------------------------

# Platform-injected geo headers, first present wins. These are set by the edge
# (Cloudflare, Vercel, CloudFront) and carry only a country code, never an IP.
_PLATFORM_COUNTRY_HEADERS = (
    "cf-ipcountry",
    "x-vercel-ip-country",
    "cloudfront-viewer-country",
    "x-country-code",
)

_LOCALE_DOC = """
**Currency and locale (v1.76.0).** Pass `currency` (query) or `X-Currency` (header) to get every
price converted alongside the canonical USD figures; pass `country` (query) or `X-Country` (header,
ISO-3166 alpha-2) to have the currency derived from the country. Precedence: `currency` query >
`X-Currency` > `country` query > `X-Country` > platform geo header (`CF-IPCountry`,
`X-Vercel-IP-Country`, `CloudFront-Viewer-Country`, `X-Country-Code`) > best-effort IP lookup > USD.
A stated currency always beats a stated country. Supported: USD, EUR, GBP, INR, JPY, CAD, AUD, BRL,
CNY, SGD, CHF, KRW, MXN; any other explicit code is rejected with 400 listing the supported set. A
country whose currency is not supported gets USD with `fallback_to_usd: true`. Converted figures are
additive (`local_*`, `currency_conversion`) and omitted entirely when no currency/country is stated
or detected. Rates come from a vendored table refreshed weekly; a rate older than 7 days is still
served but flagged `rate_is_stale`. Converted amounts are indicative — providers bill in USD.
**Privacy:** the client IP is used transiently to resolve a country and nothing more; it is
never stored, never logged by this feature and never in any response — only the country code may be echoed.
"""


def _locale_documented(func):
    """Append the shared currency/locale text to an endpoint's docstring (its OpenAPI description).

    Apply *below* the ``@app.get/post`` decorator so it runs first and the
    route is created with the augmented docstring. The block is indented like
    a function docstring so ``inspect.cleandoc`` strips it evenly.
    """
    func.__doc__ = (func.__doc__ or "") + "\n" + textwrap.indent(_LOCALE_DOC, "    ")
    return func


def _client_ip_for_geo(request: Request) -> Optional[str]:
    """Best-effort public client IP, or None.

    Fly-Client-IP > first hop of X-Forwarded-For > X-Real-IP > socket peer.
    Returns None for anything that is not a syntactically valid *public*
    address ("testclient", "unknown", loopback, RFC1918, link-local ...), so
    no lookup is attempted for those. The value is only ever held in a local
    variable: not stored on request.state, not logged, not echoed.
    """
    candidates = [
        request.headers.get("fly-client-ip"),
        request.headers.get("x-forwarded-for", "").split(",")[0].strip(),
        request.headers.get("x-real-ip"),
        request.client.host if request.client else None,
    ]
    for raw in candidates:
        if not raw:
            continue
        try:
            addr = ipaddress.ip_address(raw.strip())
        except ValueError:
            continue
        if addr.is_global:
            return str(addr)
    return None


async def resolve_request_locale(
    request: Request,
    currency: Optional[str] = Query(
        None,
        description=(
            "ISO-4217 code to convert prices into (USD, EUR, GBP, INR, JPY, CAD, AUD, BRL, CNY, SGD, "
            "CHF, KRW, MXN). Beats `country`. Unsupported codes are rejected with 400."
        ),
    ),
    country: Optional[str] = Query(
        None,
        description="ISO-3166 alpha-2 country whose currency to use (e.g. DE -> EUR). Unmapped -> USD, flagged.",
    ),
) -> LocaleContext:
    """FastAPI dependency: resolve the request's currency with the documented precedence."""
    table = load_rate_table(settings.exchange_rates_path)

    explicit_currency = currency
    currency_source = "query" if currency else None
    if not explicit_currency:
        header_currency = request.headers.get("x-currency")
        if header_currency:
            explicit_currency, currency_source = header_currency, "header"

    explicit_country = country
    country_source = "query" if country else None
    if not explicit_country:
        header_country = request.headers.get("x-country")
        if header_country:
            explicit_country, country_source = header_country, "header"

    detected_country: Optional[str] = None
    detected_source = "ip"
    if explicit_country is None:
        for name in _PLATFORM_COUNTRY_HEADERS:
            value = request.headers.get(name)
            if value:
                detected_country, detected_source = value, "platform_header"
                break
        if detected_country is None and settings.currency_detection_enabled:
            detected_country = getattr(request.state, "geo_country_code", None)
            if not detected_country:
                ip = _client_ip_for_geo(request)
                if ip:
                    try:
                        geo = await GeolocationService.get_geolocation(
                            ip, timeout=settings.geo_lookup_timeout_seconds
                        )
                        detected_country = geo.get("country_code") if geo else None
                    except Exception:
                        detected_country = None
                del ip

    try:
        return resolve_locale(
            currency=explicit_currency,
            country=explicit_country,
            detected_country=detected_country,
            table=table,
            currency_source=currency_source,
            country_source=country_source,
            detected_source=detected_source,
        )
    except UnsupportedCurrencyError as exc:
        # 400, not 422: the value is syntactically fine; this is a product rule.
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_currency",
                "currency": exc.code,
                "supported_currencies": exc.supported,
                "message": str(exc),
            },
        )


# Every request header that can change the body of a locale-aware response. The
# platform geo headers are included so a CDN that injects them keys one variant
# per country rather than sharing the first visitor's body with everyone.
_LOCALE_VARY_HEADERS = (
    "X-Currency, X-Country, CF-IPCountry, X-Vercel-IP-Country, CloudFront-Viewer-Country, X-Country-Code"
)


def _apply_locale_headers(response: Response, locale: LocaleContext, *, cacheable_public: bool = False) -> None:
    """Cache-safety for the locale-aware endpoints. Call on every response, active or not.

    The plain USD variant must carry ``Vary`` too: otherwise a shared cache that
    stored it for a header-less request would serve it to the next request that
    sends ``X-Currency: EUR`` (same URL, so it matches without a Vary key).
    A *detected* locale (IP or platform header) is visitor-specific and is
    marked ``private`` on every endpoint, not only the one with a public TTL.
    """
    response.headers["Vary"] = _LOCALE_VARY_HEADERS
    if cacheable_public:
        # OWNER-ACCEPTED DEFAULT: explicit query-param requests keep public;
        # only a detected locale makes the cached body visitor-specific.
        response.headers["Cache-Control"] = (
            "private, max-age=300" if locale.detected else "public, max-age=300"
        )
    elif locale.detected:
        response.headers["Cache-Control"] = "private"

# ------------------------------------------------------------------
# Agent / RAG — request & response models
# ------------------------------------------------------------------


class AgentChatRequest(BaseModel):
    """Request body for POST /agent/chat."""

    message: str = Field(
        ..., min_length=1, max_length=10_000,
        description="Natural language question or task (max 10,000 characters)"
    )
    conversation_id: Optional[str] = Field(
        None, max_length=128,
        description="UUID of an existing conversation to continue (optional)"
    )
    autonomous: bool = Field(False, description="Run as autonomous multi-step task (no history)")


class AgentChatResponse(BaseModel):
    """Response body for POST /agent/chat."""

    reply: str
    conversation_id: str
    tool_calls: list = []
    sources: list = []


# Global PricingAgent instance (lazy initialized)
_pricing_agent: Optional[PricingAgent] = None
_agent_lock = asyncio.Lock()
_tool_manager: Optional[ToolManager] = None


async def get_pricing_agent() -> PricingAgent:
    """Lazy-initialize and return the singleton PricingAgent.

    Raises ValueError if the required API key is not configured.
    Raises RuntimeError if the server is in graceful-shutdown mode.
    """
    global _pricing_agent, _tool_manager

    if deployment_manager.is_shutting_down():
        raise RuntimeError("Service is shutting down; agent requests are not accepted.")

    if _pricing_agent is None:
        async with _agent_lock:
            if _pricing_agent is None:
                logger.info("Initializing PricingAgent...")
                try:
                    _tool_manager = ToolManager()
                    _pricing_agent = PricingAgent(tool_manager=_tool_manager)
                    await _pricing_agent.initialize()
                    _tool_manager.set_pricing_agent(_pricing_agent)
                    logger.info("PricingAgent initialized successfully")
                except ValueError:
                    logger.warning("PricingAgent init failed: API key not configured")
                    raise

    return _pricing_agent


_support_agent: Optional[SupportAgent] = None
_support_agent_lock = asyncio.Lock()


async def get_support_agent() -> SupportAgent:
    """Lazy-initialize and return the singleton SupportAgent (v1.84.0).

    Shares the ToolManager with the PricingAgent so the wrapped public pricing tools use one
    pricing cache. Raises ValueError if the LLM key is not configured, RuntimeError while
    shutting down.
    """
    global _support_agent, _tool_manager

    if deployment_manager.is_shutting_down():
        raise RuntimeError("Service is shutting down; agent requests are not accepted.")

    if _support_agent is None:
        async with _support_agent_lock:
            if _support_agent is None:
                logger.info("Initializing SupportAgent...")
                if _tool_manager is None:
                    _tool_manager = ToolManager()
                agent = SupportAgent(tool_manager=_tool_manager)
                try:
                    await agent.initialize()
                except ValueError:
                    logger.warning("SupportAgent init failed: API key not configured")
                    raise
                _tool_manager.set_support_agent(agent)
                _support_agent = agent
                logger.info("SupportAgent initialized successfully")

    return _support_agent


# ---------------------------------------------------------------------------
# Pricing history — background snapshot loop + startup
# ---------------------------------------------------------------------------

async def _prewarm_agent() -> None:
    """Background task: initialize both agents at startup to avoid cold-start latency."""
    try:
        await get_pricing_agent()
        logger.info("PricingAgent pre-warm complete")
    except Exception as exc:
        logger.warning("PricingAgent pre-warm failed (will retry on first request): %s", exc)
    try:
        await get_support_agent()
        logger.info("SupportAgent pre-warm complete")
    except Exception as exc:
        logger.warning("SupportAgent pre-warm failed (will retry on first request): %s", exc)


async def _pricing_snapshot_loop() -> None:
    """Background task: record a pricing snapshot every N hours and fire price-change alerts."""
    interval = settings.pricing_snapshot_interval_hours * 3600
    history_service = get_pricing_history_service()
    alert_service = get_pricing_alert_service()
    pricing_service = PricingAggregatorService()
    while True:
        try:
            models, _ = await pricing_service.get_all_pricing_async()
            count = await history_service.record_snapshot(models)
            logger.info("Pricing snapshot recorded: %d models", count)
            # Check alerts against same-day price changes
            trends = await history_service.get_trends(days=1)
            fired = await alert_service.check_and_fire(trends, secret=settings.webhook_secret)
            if fired:
                logger.info("Price-change alerts fired: %d", fired)
        except Exception as exc:
            logger.warning("Pricing snapshot failed: %s", exc)
        await asyncio.sleep(interval)


@app.on_event("startup")
async def startup_pricing_history() -> None:
    """Initialize the pricing history/alerts/conversation DBs and launch the background snapshot loop."""
    # v1.80.1: loud WARNING when the local-dev webhook opt-out is on (never on Fly).
    warn_if_private_targets_allowed(logger)
    # v1.86.0: scan docs/legal for placeholders; warns if LEGAL_PAGES_LIVE is on while any remain.
    try:
        _legal_pages.refresh(settings)
    except Exception as exc:  # nosec B110
        logger.error("Legal pages scan failed (pages and checkout stay draft/disabled): %s", exc)
    try:
        await init_billing_service(settings.billing_db_path)
        logger.info("Billing service initialized at %s", settings.billing_db_path)
    except Exception as exc:  # nosec B110
        logger.error("Billing service init failed (billing endpoints disabled): %s", exc)
    # v1.79.0 — stored per-org provider keys, in billing.db next to `customers`.
    try:
        _pk_secret = settings.provider_key_encryption_key
        _pk_store = await init_provider_key_store(
            settings.billing_db_path, _pk_secret.get_secret_value() if _pk_secret else None,
        )
        logger.info("Stored provider keys %s", "enabled" if _pk_store.available else "disabled")
    except Exception as exc:  # nosec B110
        logger.error("Provider key store init failed (stored keys disabled): %s", type(exc).__name__)
    await init_pricing_history_service(settings.pricing_history_db_path)
    logger.info("Pricing history service initialized at %s", settings.pricing_history_db_path)
    # v1.83.2: say loudly whether the registry-sourced catalogue is visible at runtime.
    _inv = registry_models_inventory()
    if _inv["models"]:
        logger.info("Registry-sourced models: %d across %d file(s) at %s", _inv["models"], _inv["files"], _inv["dir"])
    else:
        logger.warning("No registry-sourced models visible at %s (exists=%s); the catalogue is curated-only. "
                       "On Fly set REGISTRY_MODELS_DIR to the image copy outside the volume mount.", _inv["dir"], _inv["exists"])
    await init_pricing_alert_service(settings.pricing_history_db_path)
    logger.info("Pricing alert service initialized")
    await init_savings_tracker(settings.pricing_history_db_path)
    logger.info("Savings tracker initialized")
    await init_usage_tracker(settings.pricing_history_db_path)
    logger.info("Usage tracker initialized")
    await init_budget_alert_service(settings.pricing_history_db_path)
    logger.info("Budget alert service initialized")
    set_cache_ttl(settings.benchmark_cache_ttl_hours)
    await init_conversation_store(
        settings.conversation_db_path, settings.agent_max_history_turns, settings.conversation_retention_days
    )
    logger.info(
        "Conversation store initialized (%s)",
        settings.conversation_db_path or "in-memory",
    )
    aggregator = await get_pricing_aggregator()
    router = init_router(aggregator)
    logger.info("Model router initialized")
    # v1.77.0 — dispatch gateway: receipt store + service (reuses the router and ledgers above).
    try:
        receipts = await init_dispatch_receipt_store(
            settings.pricing_history_db_path, retention_days=settings.dispatch_receipt_retention_days,
        )
        init_dispatch_service(DispatchService(
            aggregator=aggregator,
            router=router,
            usage_tracker=get_usage_tracker(),
            savings_tracker=get_savings_tracker(),
            budget_alerts=get_budget_alert_service(),
            receipts=receipts,
            # executor_factory left at the service's default (get_executor with this default_max_tokens)
            default_max_tokens=settings.dispatch_default_max_tokens,
            attempt_timeout_ms=settings.dispatch_attempt_timeout_ms,
            max_attempts=settings.dispatch_max_attempts,
        ))
        logger.info("Dispatch service initialized (enabled=%s)", settings.dispatch_enabled)
    except Exception as exc:  # nosec B110
        logger.error("Dispatch service init failed (dispatch mode disabled): %s", exc)
    # v1.83.0 — orchestrator core: own SQLite file on the volume; no LLM, no outbound calls.
    try:
        orch_store = await init_orchestrator_store(
            settings.orchestrator_db_path,
            run_retention_days=settings.orchestrator_run_retention_days,
            io_retention_days=settings.orchestrator_io_retention_days,
        )
        orch_service = init_orchestrator_service(OrchestratorService(orch_store))
        logger.info("Orchestrator initialized at %s (intake key %s)", settings.orchestrator_db_path,
                    "set" if settings.orchestrator_intake_key else "unset")
        # v1.88.0 — compliance audit: report tables in the same file, handlers registered with the
        # orchestrator. Report-only; /audit routes answer 503 if this fails.
        try:
            audit_store = await init_audit_store(settings.orchestrator_db_path,
                                                 retention_days=settings.audit_report_retention_days)
            audit_service = init_audit_service(AuditService(audit_store, orch_service, settings))
            audit_service.install()
            logger.info("Compliance audit initialized (stripe reconciliation %s, llm summary %s)",
                        "on" if settings.stripe_audit_key else "off", "on" if settings.audit_llm_summary else "off")
        except Exception as exc:  # nosec B110
            logger.error("Compliance audit init failed (/audit routes answer 503): %s", type(exc).__name__)
        # v1.89.0 — document writer: artefact table in the same file, handlers registered with the
        # orchestrator. Operator-only; /documents routes answer 503 if this fails.
        try:
            docs_store = await init_docs_store(settings.orchestrator_db_path,
                                               retention_days=settings.docs_artifact_retention_days)
            docs_service = init_docs_service(DocsService(docs_store, orch_service, settings))
            docs_service.install()
            logger.info("Document writer initialized (default model %s, retention %d days)",
                        settings.docs_default_model, settings.docs_artifact_retention_days)
        except Exception as exc:  # nosec B110
            logger.error("Document writer init failed (/documents routes answer 503): %s", type(exc).__name__)
    except Exception as exc:  # nosec B110
        logger.error("Orchestrator init failed (/orchestrator routes answer 503): %s", type(exc).__name__)
    asyncio.create_task(_pricing_snapshot_loop())
    logger.info(
        "Pricing snapshot loop started (interval=%dh)", settings.pricing_snapshot_interval_hours
    )
    # Pre-warm the PricingAgent so the first /chat request isn't penalised by
    # cold-start initialization (RAG index build + provider pricing fetch).
    asyncio.create_task(_prewarm_agent())


# v1.86.0: legal pages and the security statement. Generated by scripts/sync_legal.py into
# static/<page>/index.html and served by these handlers (not a static mount) so the draft
# notice can be substituted server-side while the pages are not in force. Anonymous, like /faq.
_LEGAL_PAGES = {
    "/terms": ("terms", True),
    "/privacy": ("privacy", True),
    "/refunds": ("refunds", True),
    "/dpa": ("dpa", True),
    "/subprocessors": ("subprocessors", True),
    "/security": ("security", False),   # a statement of facts: never a draft
}
_LEGAL_PAGE_PATHS = frozenset(_LEGAL_PAGES)


def _make_legal_page_handler(folder: str, gated: bool):
    async def legal_page():
        html_path = _static_dir / folder / "index.html"
        if not html_path.exists():
            raise HTTPException(status_code=404, detail="Page not found")
        body = html_path.read_text(encoding="utf-8")
        notice = _legal_pages.DRAFT_NOTICE_HTML if gated and not _legal_pages.is_live(settings) else ""
        return HTMLResponse(body.replace(_legal_pages.NOTICE_MARKER, notice))
    legal_page.__name__ = f"legal_page_{folder}"
    return legal_page


for _legal_path, (_legal_folder, _legal_gated) in _LEGAL_PAGES.items():
    app.add_api_route(
        _legal_path, _make_legal_page_handler(_legal_folder, _legal_gated),
        methods=["GET"], include_in_schema=False,
    )


@app.get("/", include_in_schema=False)
async def root():
    """Serve the marketing landing page."""
    html_path = _static_dir / "landing" / "index.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    # Fallback: plain JSON info if landing page not present
    return JSONResponse({"name": settings.app_name, "version": settings.app_version})


_DARK_SWAGGER_CSS = """
body{background:#0f1117!important}
.swagger-ui .topbar{display:none!important}
.swagger-ui,.swagger-ui .wrapper,.swagger-ui .info{background:#0f1117;color:#e2e8f0}
.swagger-ui .info .title,.swagger-ui .info h1,.swagger-ui .info h2,.swagger-ui .info h3{color:#e2e8f0}
.swagger-ui .info a{color:#7c6af7}
.swagger-ui .info .description p,.swagger-ui .renderedMarkdown p{color:#8892a4}
.swagger-ui .info .description code,.swagger-ui .renderedMarkdown code{background:#22263a;color:#4ecdc4;border-radius:4px;padding:.1em .4em}
.swagger-ui .scheme-container{background:#1a1d27;border:1px solid #2e3347;border-radius:8px;box-shadow:none;padding:1rem;margin-bottom:1rem}
.swagger-ui select{background:#1a1d27;border:1px solid #2e3347;color:#e2e8f0;border-radius:4px}
.swagger-ui .opblock{border:1px solid #2e3347;border-radius:8px;margin-bottom:6px;background:#1a1d27}
.swagger-ui .opblock.opblock-get{border-color:rgba(72,187,120,.3)}
.swagger-ui .opblock.opblock-post{border-color:rgba(237,137,54,.3)}
.swagger-ui .opblock.opblock-put{border-color:rgba(99,179,237,.3)}
.swagger-ui .opblock.opblock-delete{border-color:rgba(245,101,101,.3)}
.swagger-ui .opblock.opblock-patch{border-color:rgba(78,205,196,.3)}
.swagger-ui .opblock.opblock-get .opblock-summary-method{background:rgba(72,187,120,.15);color:#48bb78}
.swagger-ui .opblock.opblock-post .opblock-summary-method{background:rgba(237,137,54,.15);color:#ed8936}
.swagger-ui .opblock.opblock-put .opblock-summary-method{background:rgba(99,179,237,.15);color:#63b3ed}
.swagger-ui .opblock.opblock-delete .opblock-summary-method{background:rgba(245,101,101,.15);color:#f56565}
.swagger-ui .opblock.opblock-patch .opblock-summary-method{background:rgba(78,205,196,.15);color:#4ecdc4}
.swagger-ui .opblock .opblock-summary{border-bottom:1px solid #2e3347}
.swagger-ui .opblock .opblock-summary-path{color:#e2e8f0}
.swagger-ui .opblock .opblock-summary-description{color:#8892a4}
.swagger-ui .opblock-description-wrapper p,.swagger-ui .opblock-section-header h4{color:#8892a4}
.swagger-ui .opblock-section-header{border-bottom:1px solid #2e3347;background:#22263a}
.swagger-ui .opblock-section-header label{color:#8892a4}
.swagger-ui .opblock-body{background:#0f1117}
.swagger-ui .opblock-body pre{background:#0d0f18;color:#e2e8f0;border:1px solid #2e3347;border-radius:4px}
.swagger-ui table thead tr th{color:#8892a4;border-bottom:1px solid #2e3347;background:#22263a}
.swagger-ui table tbody tr td{border-bottom:1px solid #2e3347;color:#e2e8f0}
.swagger-ui table tbody tr:last-child td{border-bottom:none}
.swagger-ui .parameter__name{color:#e2e8f0}
.swagger-ui .parameter__type{color:#4ecdc4}
.swagger-ui .parameter__in{color:#8892a4}
.swagger-ui .parameters-col_description{color:#8892a4}
.swagger-ui .parameters-col_description input,.swagger-ui textarea,
.swagger-ui input[type=text],.swagger-ui input[type=password],.swagger-ui input[type=search]{
  background:#1a1d27;border:1px solid #2e3347;color:#e2e8f0;border-radius:4px}
.swagger-ui .response-col_status{color:#48bb78}
.swagger-ui .response-col_description{color:#8892a4}
.swagger-ui .responses-inner h4,.swagger-ui .responses-inner h5{color:#8892a4}
.swagger-ui .highlight-code,.swagger-ui .microlight{background:#0d0f18!important}
.swagger-ui code{background:#22263a;color:#4ecdc4;border-radius:4px}
.swagger-ui .opblock-tag{color:#e2e8f0;border-bottom:1px solid #2e3347}
.swagger-ui .opblock-tag:hover{background:rgba(124,106,247,.05)}
.swagger-ui section.models{border:1px solid #2e3347;border-radius:8px}
.swagger-ui section.models h4{color:#e2e8f0;border-bottom:1px solid #2e3347}
.swagger-ui .model-title,.swagger-ui .model,.swagger-ui span.model-title__text{color:#e2e8f0}
.swagger-ui .model-box{background:#22263a;border-radius:4px}
.swagger-ui .prop-type{color:#4ecdc4}
.swagger-ui .prop-format{color:#8892a4}
.swagger-ui .btn{background:#22263a;border:1px solid #2e3347;color:#e2e8f0;border-radius:6px}
.swagger-ui .btn:hover{border-color:#7c6af7;color:#7c6af7}
.swagger-ui .btn.authorize{border-color:rgba(72,187,120,.4);color:#48bb78}
.swagger-ui .btn.authorize svg{fill:#48bb78}
.swagger-ui .btn.execute{background:#7c6af7;border-color:#7c6af7;color:#fff}
.swagger-ui .btn.cancel,.swagger-ui .btn.btn-clear{background:transparent;border-color:#f56565;color:#f56565}
.swagger-ui .tab li button.tablinks{color:#8892a4}
.swagger-ui .tab li.active button.tablinks{color:#7c6af7;border-bottom:2px solid #7c6af7}
.swagger-ui .arrow{fill:#8892a4}
.swagger-ui dialog.dialog-ux,.swagger-ui .dialog-ux .modal-ux{background:#1a1d27;border:1px solid #2e3347}
.swagger-ui .dialog-ux .modal-ux-header{border-bottom:1px solid #2e3347}
.swagger-ui .dialog-ux .modal-ux-header h3,.swagger-ui .auth-container{color:#e2e8f0}
.swagger-ui .auth-container label{color:#8892a4}
.swagger-ui .auth-container code{background:#22263a;color:#4ecdc4}
"""

_NAV_HTML = """
<nav class="main-nav">
  <a href="/" class="nav-brand">&#9889; LLM Pricing</a>
  <span class="nav-spacer"></span>
  <div class="nav-links" id="navLinks">
    <a href="/chat">&#128172; Chat</a>
    <a href="/calculator">&#129518; Calc</a>
    <a href="/compare">&#9878; Compare</a>
    <a href="/history">&#128200; History</a>
    <a href="/trends">&#128293; Trends</a>
    <a href="/widget">&#128268; Widget</a>
    <a href="/billing">&#128179; Billing</a>
    <a href="/mcp-setup">&#9889; MCP</a>
    <a href="/api-docs">&#128214; Docs</a>
    <a href="/admin">&#9881; Admin</a>
  </div>
  <button class="nav-toggle" id="navToggle" aria-label="Menu">&#9776;</button>
</nav>
<script>
(function(){
  var t=document.getElementById('navToggle'),l=document.getElementById('navLinks');
  if(t)t.onclick=function(){l.classList.toggle('open');};
  var p=location.pathname.replace(/\\/$/,'');
  l.querySelectorAll('a').forEach(function(a){
    if(a.getAttribute('href')===p)a.classList.add('active');
    a.addEventListener('click',function(){l.classList.remove('open');});
  });
})();
</script>
"""

_NAV_CSS = """
.main-nav{display:flex;align-items:center;background:#0f1117;padding:0 1.25rem;height:52px;
  border-bottom:1px solid #23263a;position:sticky;top:0;z-index:9999;gap:.5rem}
.nav-brand{font-weight:700;color:#7c6af7;text-decoration:none;font-size:1rem;white-space:nowrap;flex-shrink:0}
.nav-spacer{flex:1}
.nav-links{display:flex;align-items:center;gap:.125rem;overflow-x:auto;-webkit-overflow-scrolling:touch}
.nav-links::-webkit-scrollbar{display:none}
.nav-links a{color:#8b93a5;text-decoration:none;padding:.35rem .6rem;border-radius:6px;
  font-size:.8rem;white-space:nowrap;transition:all .15s;border:none}
.nav-links a:hover{background:#1a1d2e;color:#e5e7eb}
.nav-links a.active{background:#1a1d2e;color:#7c6af7;font-weight:600}
.nav-toggle{display:none;background:none;border:1px solid #2a2d3a;color:#9ca3af;
  font-size:1.1rem;cursor:pointer;padding:.25rem .5rem;border-radius:6px;line-height:1}
@media(max-width:640px){
  .nav-toggle{display:block}
  .nav-links{display:none;position:absolute;top:52px;left:0;right:0;flex-direction:column;
    background:#0f1117;border-bottom:1px solid #23263a;padding:.5rem 1rem;gap:.25rem;z-index:199}
  .nav-links.open{display:flex}
  .nav-links a{padding:.6rem .75rem;font-size:.875rem}
}
"""


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    """Dark-themed Swagger UI with site navigation."""
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{settings.app_name} — Swagger UI</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>
    {_NAV_CSS}
    {_DARK_SWAGGER_CSS}
    #swagger-ui {{ padding-top: .5rem; }}
  </style>
</head>
<body>
{_NAV_HTML}
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>
  SwaggerUIBundle({{
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
    layout: "BaseLayout",
    deepLinking: true,
    displayRequestDuration: true,
    filter: true,
  }});
</script>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/redoc", include_in_schema=False)
async def custom_redoc():
    """Dark-themed ReDoc with site navigation."""
    from fastapi.responses import HTMLResponse
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{settings.app_name} — ReDoc</title>
  <style>
    {_NAV_CSS}
    body {{ margin: 0; background: #0f1117; }}
  </style>
</head>
<body>
{_NAV_HTML}
<div id="redoc-container"></div>
<script src="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js"></script>
<script>
  Redoc.init("/openapi.json", {{
    theme: {{
      colors: {{
        primary: {{ main: "#7c6af7" }},
        text: {{ primary: "#e2e8f0", secondary: "#8892a4" }},
        border: {{ dark: "#2e3347", light: "#2e3347" }},
        http: {{
          get: "#48bb78", post: "#ed8936", put: "#63b3ed",
          options: "#8892a4", patch: "#4ecdc4", delete: "#f56565"
        }}
      }},
      typography: {{
        fontSize: "14px",
        fontFamily: "'Segoe UI', system-ui, sans-serif",
        code: {{
          fontSize: "13px",
          fontFamily: "'Consolas', 'Cascadia Code', monospace",
          color: "#4ecdc4",
          backgroundColor: "#22263a"
        }}
      }},
      sidebar: {{
        backgroundColor: "#0f1117",
        textColor: "#e2e8f0",
        activeTextColor: "#7c6af7"
      }},
      rightPanel: {{ backgroundColor: "#0d0f18" }},
      schema: {{
        linesColor: "#2e3347",
        typeNameColor: "#4ecdc4",
        typeTitleColor: "#7c6af7",
        requireLabelColor: "#f56565"
      }}
    }}
  }}, document.getElementById("redoc-container"));
</script>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/models", tags=["Pricing"])
async def get_models(
    provider: Optional[str] = Query(
        None,
        description="Filter by provider (e.g., 'openai', 'anthropic', 'google', 'cohere', 'mistral')"
    )
):
    """
    Get a list of all available model names.

    This is a lightweight endpoint for quick model discovery without full pricing data.
    Use this to find valid model names for cost estimation or performance comparison.

    Supported providers: OpenAI, Anthropic, Google, Cohere, Mistral AI

    Args:
        provider: Optional provider filter

    Returns:
        dict: List of model names organized by provider
    """
    aggregator = await get_pricing_aggregator()
    if provider:
        models, _ = await aggregator.get_pricing_by_provider_async(provider)
    else:
        models, _ = await aggregator.get_all_pricing_async()

    # Group models by provider
    models_by_provider = {}
    for model in models:
        if model.provider not in models_by_provider:
            models_by_provider[model.provider] = []
        models_by_provider[model.provider].append(model.model_name)

    return {
        "total_models": len(models),
        "providers": list(models_by_provider.keys()),
        "models_by_provider": models_by_provider,
        "all_models": [model.model_name for model in models]
    }


@app.get("/pricing", response_model=PricingResponse, tags=["Pricing"])
@_locale_documented
async def get_pricing(
    response: Response,
    provider: Optional[str] = Query(
        None,
        description="Filter by provider (e.g., 'openai', 'anthropic', 'google', 'azure', 'vertex ai')"
    ),
    supports_vision: Optional[bool] = Query(None, description="Filter to vision-capable models"),
    supports_function_calling: Optional[bool] = Query(None, description="Filter to function-calling models"),
    supports_json_mode: Optional[bool] = Query(None, description="Filter to JSON-mode models"),
    batch_available: Optional[bool] = Query(None, description="Filter to batch-API models"),
    is_reasoning_model: Optional[bool] = Query(None, description="Filter to reasoning/chain-of-thought models"),
    locale: LocaleContext = Depends(resolve_request_locale),
):
    """
    Get aggregated pricing data from multiple LLM providers.

    This endpoint fetches real-time pricing data from all configured providers
    asynchronously. If a provider is unavailable, partial data is returned with
    status information about each provider.

    Supported providers: OpenAI, Anthropic, Google, Cohere, Mistral AI

    Args:
        provider: Optional provider filter
        supports_vision: Filter to vision-capable models
        supports_function_calling: Filter to function-calling models
        supports_json_mode: Filter to JSON-mode models
        batch_available: Filter to batch-API models
        is_reasoning_model: Filter to reasoning models

    Returns:
        PricingResponse: Aggregated pricing data with metrics and provider status.
        With a currency/country stated or detected, every model also carries
        `local_prices` and the response carries `currency_conversion`.
    """
    aggregator = await get_pricing_aggregator()
    if provider:
        models, provider_status = await aggregator.get_pricing_by_provider_async(provider)
    else:
        models, provider_status = await aggregator.get_all_pricing_async()

    # Apply capability filters
    if supports_vision is not None:
        models = [m for m in models if m.supports_vision == supports_vision]
    if supports_function_calling is not None:
        models = [m for m in models if m.supports_function_calling == supports_function_calling]
    if supports_json_mode is not None:
        models = [m for m in models if m.supports_json_mode == supports_json_mode]
    if batch_available is not None:
        models = [m for m in models if m.batch_available == batch_available]
    if is_reasoning_model is not None:
        models = [m for m in models if m.is_reasoning_model == is_reasoning_model]

    # Track provider usage
    telemetry = get_telemetry_service()
    telemetry.track_feature_usage("get_pricing")
    for model in models:
        # Estimate cost per 1M tokens (rough estimate using average tokens)
        estimated_cost = (
            (model.cost_per_input_token + model.cost_per_output_token) / 2 * 1_000_000
        )
        telemetry.track_provider_usage(model.provider, model.model_name, estimated_cost)

    # Sets Cache-Control (public, or private when the locale was detected) and Vary.
    _apply_locale_headers(response, locale, cacheable_public=True)
    if locale.active:
        # Per-request copies: the aggregator's cached PricingMetrics are never touched.
        models = [localize_metrics(m, locale) for m in models]
    return PricingResponse(
        models=models,
        total_models=len(models),
        provider_status=provider_status,
        currency_conversion=build_conversion(locale),
    )


@app.get("/data-quality", tags=["Pricing"])
async def get_data_quality():
    """
    Get an aggregate accuracy summary for the pricing catalogue.

    Reports what percentage of models have a confirmed, fresh price, how many
    are currently withheld from serving due to price drift against the
    reference registry, how many are newly discovered and not yet priced, and
    how many confirmed prices are over 90 days old. A single trust signal for
    the whole catalogue — see `/pricing`'s `price_confirmed`/`price_as_of`
    fields or the `check_price_drift` MCP tool for per-model detail.

    Returns:
        dict: Aggregate data-quality report
    """
    aggregator = await get_pricing_aggregator()
    oracle = get_price_oracle()
    await oracle.load()

    models, _ = await aggregator.get_all_pricing_async(include_unconfirmed=True)
    report = compute_data_quality_report(models, oracle)
    return report.as_dict()


@app.get("/health", tags=["Health"])
async def health_check():
    """
    Simple health check endpoint (backwards compatible).

    Returns basic health status. For production orchestrators (K8s, ECS),
    use /health/live or /health/ready instead.

    Returns:
        dict: Server health status
    """
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version
    }


@app.get("/health/detailed", response_model=HealthCheckResponse, tags=["Health"])
async def health_check_detailed():
    """
    Detailed health check for monitoring and load balancers.

    Includes:
    - Service status and version
    - Environment and deployment information
    - Active request count and uptime
    - Dependency health (if available)
    - Graceful shutdown status

    Use this endpoint for comprehensive monitoring of the service.

    Returns:
        HealthCheckResponse: Detailed health information
    """
    return await deployment_manager.get_health_check(
        include_metrics=True,
        include_dependencies=True,
    )


@app.get("/health/ready", response_model=DeploymentReadiness, tags=["Health"])
async def health_ready():
    """
    Kubernetes readinessProbe endpoint.

    Returns ready=true if the service is ready to accept traffic.
    Returns ready=false during graceful shutdown.

    Load balancers should use this to determine if traffic should be routed here.

    Returns:
        DeploymentReadiness: Ready status for traffic routing
    """
    return await deployment_manager.get_readiness_check()


@app.get("/health/live", tags=["Health"])
async def health_live():
    """
    Kubernetes livenessProbe endpoint.

    Returns alive=true if the process should continue running.
    Orchestrators will restart the container if this returns false.

    Returns:
        dict: Alive status
    """
    return await deployment_manager.get_liveness_check()


@app.get("/deployment/metadata", response_model=DeploymentMetadata, tags=["Deployment"])
async def get_deployment_metadata():
    """
    Get deployment metadata including version and API versioning information.

    Includes:
    - Current application version
    - Supported API versions
    - Breaking changes documentation
    - Backwards compatibility information

    Use this endpoint to:
    - Detect breaking changes before updating clients
    - Determine API version compatibility
    - Understand deprecation timeline

    Returns:
        DeploymentMetadata: Version and breaking change information
    """
    return deployment_manager.get_deployment_metadata()


@app.get("/deployment/info", tags=["Deployment"])
async def get_deployment_info():
    """
    Get deployment and blue-green environment information.

    Includes:
    - Environment (production/staging)
    - Deployment group (blue/green)
    - Region and availability zone
    - Instance/container ID
    - Deployment timestamp

    Use this endpoint to:
    - Verify blue/green deployment during traffic switching
    - Identify which instance handled a request
    - Monitor deployment consistency
    - Debug region/zone issues

    Returns:
        dict: Deployment environment information
    """
    env = deployment_manager.environment
    return {
        "environment": env.environment,
        "deployment_group": env.deployment_group,
        "region": env.region,
        "availability_zone": env.availability_zone,
        "instance_id": env.instance_id,
        "deployment_timestamp": env.deployment_timestamp,
        "service_uptime_seconds": deployment_manager.get_uptime_seconds(),
    }


@app.post("/deployment/shutdown", response_model=GracefulShutdownStatus, tags=["Deployment"])
async def initiate_graceful_shutdown(request: GracefulShutdownRequest):
    """
    Initiate graceful shutdown of the service.

    Blue-Green Deployment Use Case:
    1. Load balancer removes this instance from traffic routing
    2. Load balancer calls this endpoint with drain_timeout_seconds
    3. Service stops accepting new requests
    4. Service waits up to drain_timeout_seconds for in-flight requests to complete
    5. Service exits cleanly
    6. Orchestrator (K8s, ECS) replaces the instance

    **Important**: This endpoint should only be called by orchestrators/load balancers,
    not by end users. Implement proper access controls.

    Args:
        request: GracefulShutdownRequest with drain timeout

    Returns:
        GracefulShutdownStatus: Current shutdown status

    Security Note:
        In production, restrict access to this endpoint using:
        - Network policies (allow only from orchestrator/load balancer IP)
        - API gateway authentication
        - Service-to-service authentication
    """
    logger.warning("Graceful shutdown requested via API")
    await deployment_manager.initiate_graceful_shutdown(request.drain_timeout_seconds)
    return await deployment_manager.get_shutdown_status()


@app.get("/deployment/shutdown/status", response_model=GracefulShutdownStatus, tags=["Deployment"])
async def get_shutdown_status():
    """
    Get current graceful shutdown status.

    Returns:
        GracefulShutdownStatus: Current shutdown state, active requests count, timeout
    """
    return await deployment_manager.get_shutdown_status()


@app.get("/api/versions", response_model=ApiVersionInfo, tags=["API Info"])
async def get_api_versions():
    """
    Get API version information and migration guides.

    Helps clients understand:
    - Current stable API version
    - All available versions
    - Deprecated versions still supported
    - Breaking changes in each version

    Use this to plan API version upgrades.

    Returns:
        ApiVersionInfo: Version compatibility information
    """
    metadata = deployment_manager.get_deployment_metadata()
    return ApiVersionInfo(
        current_version="v1",
        all_versions=metadata.api_versions,
        deprecated_versions=[],
        migration_guide_url="https://github.com/skakumanu/llm-pricing-mcp-server/blob/master/docs/BACKWARDS_COMPATIBILITY.md",
    )


@app.post("/cost-estimate", response_model=CostEstimateResponse, tags=["Cost Estimation"])
@_locale_documented
async def estimate_cost(
    request: CostEstimateRequest,
    response: Response,
    locale: LocaleContext = Depends(resolve_request_locale),
):
    """
    Estimate the cost for a specific model based on token usage.

    This endpoint calculates the total cost for using a specific LLM model
    given the number of input and output tokens. It fetches the latest pricing
    data for the model and computes the costs.

    Args:
        request: CostEstimateRequest containing model name and token counts

    Returns:
        CostEstimateResponse: Detailed cost breakdown in USD, plus `local_cost`
        and `currency_conversion` when a currency/country is stated or detected

    Raises:
        HTTPException: If the model is not found (404)
    """
    # Find the model pricing
    aggregator = await get_pricing_aggregator()
    model_pricing = await aggregator.find_model_pricing(request.model_name)

    if not model_pricing:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{request.model_name}' not found. Please check the /pricing endpoint for available models."
        )

    # Calculate costs
    token_cost = compute_token_cost(
        model_pricing.cost_per_input_token,
        model_pricing.cost_per_output_token,
        request.input_tokens,
        request.output_tokens,
    )
    input_cost = token_cost.input_cost
    output_cost = token_cost.output_cost
    total_cost = token_cost.total_cost

    # Track telemetry
    telemetry = get_telemetry_service()
    telemetry.track_feature_usage("cost_estimation")
    telemetry.track_provider_usage(model_pricing.provider, model_pricing.model_name, total_cost)

    _apply_locale_headers(response, locale)
    return CostEstimateResponse(
        model_name=model_pricing.model_name,
        provider=model_pricing.provider,
        input_tokens=request.input_tokens,
        output_tokens=request.output_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        currency=model_pricing.currency,
        local_cost=localize_cost(input_cost, output_cost, total_cost, locale),
        currency_conversion=build_conversion(locale),
    )


@app.post("/cost-estimate/batch", response_model=BatchCostEstimateResponse, tags=["Cost Estimation"])
@_locale_documented
async def estimate_cost_batch(
    request: BatchCostEstimateRequest,
    response: Response,
    locale: LocaleContext = Depends(resolve_request_locale),
):
    """
    Compare cost estimates across multiple models.

    This endpoint calculates and compares costs for multiple LLM models
    given the same token usage. Useful for finding the most cost-effective
    model for your use case.

    Args:
        request: BatchCostEstimateRequest with model names and token counts

    Returns:
        BatchCostEstimateResponse: Cost comparison across all requested models.
        `cheapest_model`, `most_expensive_model` and `cost_range` are always
        computed from USD; with a locale active each available model also
        carries `local_cost` and the response `local_cost_range` + `currency_conversion`.
    """
    comparisons = []
    aggregator = await get_pricing_aggregator()

    # Calculate cost for each model
    for model_name in request.model_names:
        model_pricing = await aggregator.find_model_pricing(model_name)

        if not model_pricing:
            comparisons.append(ModelCostComparison(
                model_name=model_name,
                provider="unknown",
                input_cost=0.0,
                output_cost=0.0,
                total_cost=0.0,
                cost_per_1m_tokens=0.0,
                is_available=False,
                error_message=f"Model '{model_name}' not found"
            ))
            continue

        # Calculate costs
        token_cost = compute_token_cost(
            model_pricing.cost_per_input_token,
            model_pricing.cost_per_output_token,
            request.input_tokens,
            request.output_tokens,
        )
        input_cost = token_cost.input_cost
        output_cost = token_cost.output_cost
        total_cost = token_cost.total_cost

        # Calculate cost per 1M tokens (average of input and output)
        total_tokens = request.input_tokens + request.output_tokens
        if total_tokens > 0:
            cost_per_1m = (total_cost / total_tokens) * 1_000_000
        else:
            cost_per_1m = 0.0

        comparisons.append(ModelCostComparison(
            model_name=model_pricing.model_name,
            provider=model_pricing.provider,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
            cost_per_1m_tokens=cost_per_1m,
            is_available=True,
            local_cost=localize_cost(input_cost, output_cost, total_cost, locale, cost_per_1m),
        ))

    # Find cheapest and most expensive
    available_comparisons = [c for c in comparisons if c.is_available]

    cheapest = None
    most_expensive = None
    cost_range = None

    if available_comparisons:
        cheapest = min(available_comparisons, key=lambda x: x.total_cost).model_name
        most_expensive = max(available_comparisons, key=lambda x: x.total_cost).model_name

        min_cost = min(c.total_cost for c in available_comparisons)
        max_cost = max(c.total_cost for c in available_comparisons)
        cost_range = {"min": min_cost, "max": max_cost}

    # Track telemetry
    telemetry = get_telemetry_service()
    telemetry.track_feature_usage("batch_cost_estimation")
    for comparison in available_comparisons:
        telemetry.track_provider_usage(comparison.provider, comparison.model_name, comparison.total_cost)

    _apply_locale_headers(response, locale)
    local_cost_range = None
    if locale.active and cost_range is not None:
        local_cost_range = LocalCostRange(
            currency=locale.currency,
            min=convert(cost_range["min"], locale),
            max=convert(cost_range["max"], locale),
        )
    return BatchCostEstimateResponse(
        input_tokens=request.input_tokens,
        output_tokens=request.output_tokens,
        models=comparisons,
        cheapest_model=cheapest,
        most_expensive_model=most_expensive,
        cost_range=cost_range,
        local_cost_range=local_cost_range,
        currency_conversion=build_conversion(locale),
    )


@app.get("/performance", response_model=PerformanceResponse, tags=["Performance"])
async def get_performance(
    provider: Optional[str] = Query(
        None,
        description="Filter by provider (e.g., 'openai', 'anthropic')"
    ),
    sort_by: Optional[str] = Query(
        None,
        description="Sort by metric: 'throughput', 'latency', 'context_window', 'cost', 'value'"
    )
):
    """
    Get performance metrics and comparisons for LLM models.

    This endpoint provides performance data including throughput, latency,
    context window sizes, and calculated performance scores. Use this to
    compare models based on performance characteristics rather than just cost.

    Args:
        provider: Optional provider filter
        sort_by: Optional sort criteria

    Returns:
        PerformanceResponse: Performance metrics with comparisons
    """
    # Get all pricing data (includes performance metrics)
    aggregator = await get_pricing_aggregator()
    if provider:
        models, provider_status = await aggregator.get_pricing_by_provider_async(provider)
    else:
        models, provider_status = await aggregator.get_all_pricing_async()

    # Enrich with benchmark quality scores
    models = await enrich_models(models)

    # Convert to performance metrics and calculate scores
    performance_metrics = []
    for model in models:
        # Calculate performance score (throughput per dollar)
        perf_score = None
        if model.throughput and model.cost_per_input_token > 0:
            # Higher throughput and lower cost = better score
            avg_cost = (model.cost_per_input_token + model.cost_per_output_token) / 2
            perf_score = model.throughput / (avg_cost * 1000) if avg_cost > 0 else None

        # Calculate value score (context window per dollar)
        value_score = None
        if model.context_window and model.cost_per_input_token > 0:
            avg_cost = (model.cost_per_input_token + model.cost_per_output_token) / 2
            value_score = model.context_window / (avg_cost * 1000) if avg_cost > 0 else None

        performance_metrics.append(PerformanceMetrics(
            model_name=model.model_name,
            provider=model.provider,
            throughput=model.throughput,
            latency_ms=model.latency_ms,
            context_window=model.context_window,
            cost_per_input_token=model.cost_per_input_token,
            cost_per_output_token=model.cost_per_output_token,
            performance_score=perf_score,
            value_score=value_score,
            quality_score=model.quality_score,
        ))

    # Sort if requested
    if sort_by:
        if sort_by == "throughput":
            performance_metrics.sort(key=lambda x: x.throughput or 0, reverse=True)
        elif sort_by == "latency":
            performance_metrics.sort(key=lambda x: x.latency_ms or float('inf'))
        elif sort_by == "context_window":
            performance_metrics.sort(key=lambda x: x.context_window or 0, reverse=True)
        elif sort_by == "cost":
            performance_metrics.sort(
                key=lambda x: (x.cost_per_input_token + x.cost_per_output_token) / 2
            )
        elif sort_by == "value":
            performance_metrics.sort(key=lambda x: x.value_score or 0, reverse=True)

    # Find best performers
    models_with_throughput = [m for m in performance_metrics if m.throughput]
    models_with_latency = [m for m in performance_metrics if m.latency_ms]
    models_with_context = [m for m in performance_metrics if m.context_window]
    models_with_value = [m for m in performance_metrics if m.value_score]
    models_with_quality_score = [m for m in performance_metrics if m.quality_score]

    best_throughput = (
        max(models_with_throughput, key=lambda x: x.throughput).model_name
        if models_with_throughput else None
    )
    lowest_latency = (
        min(models_with_latency, key=lambda x: x.latency_ms).model_name
        if models_with_latency else None
    )
    largest_context = (
        max(models_with_context, key=lambda x: x.context_window).model_name
        if models_with_context else None
    )
    best_value = (
        max(models_with_value, key=lambda x: x.value_score).model_name
        if models_with_value else None
    )
    # best quality/cost: use quality_score / avg_cost_per_1M
    def _qv(m):
        avg_cost_1m = (m.cost_per_input_token + m.cost_per_output_token) / 2 * 1_000_000
        return m.quality_score / max(avg_cost_1m, 1e-9)

    best_quality_value = (
        max(models_with_quality_score, key=_qv).model_name
        if models_with_quality_score else None
    )

    # Track telemetry
    telemetry = get_telemetry_service()
    telemetry.track_feature_usage("performance_comparison")

    return PerformanceResponse(
        models=performance_metrics,
        total_models=len(performance_metrics),
        best_throughput=best_throughput,
        lowest_latency=lowest_latency,
        largest_context=largest_context,
        best_value=best_value,
        best_quality_value=best_quality_value,
        provider_status=provider_status
    )


@app.get("/use-cases", response_model=UseCaseResponse, tags=["Performance"])
@_locale_documented
async def get_use_cases(
    response: Response,
    provider: Optional[str] = Query(
        None,
        description="Filter by provider (e.g., 'openai', 'anthropic', 'google', 'cohere', 'mistral')"
    ),
    locale: LocaleContext = Depends(resolve_request_locale),
):
    """
    Get recommended use cases and strengths for each LLM model.

    This endpoint provides curated use case recommendations, key strengths,
    and ideal application areas for each available model. Use this to find
    the best model for your specific use case.

    Supported providers: OpenAI, Anthropic, Google, Cohere, Mistral AI

    Args:
        provider: Optional provider filter

    Returns:
        UseCaseResponse: Use case information for each model. The set and order
        of models never depends on country; `cost_tier` is always derived from
        USD bands. With a locale active each model carries `local_cost`.
    """
    aggregator = await get_pricing_aggregator()
    if provider:
        all_models, _ = await aggregator.get_pricing_by_provider_async(provider)
    else:
        all_models, _ = await aggregator.get_all_pricing_async()

    # Determine cost tier based on token costs (costs are per token)
    def get_cost_tier(input_cost: float, output_cost: float) -> str:
        # Bands on the average of input and output price per 1M tokens:
        # ultra-low < $1.50, low < $10, medium < $40, high otherwise. The
        # pre-v1.75.1 bands were 100x looser and called a $2.50/$10 model
        # "ultra-low" and a $30/$60 one "low".
        avg_per_1m = (input_cost + output_cost) / 2 * 1_000_000
        if avg_per_1m < 1.5:
            return "ultra-low"
        elif avg_per_1m < 10:
            return "low"
        elif avg_per_1m < 40:
            return "medium"
        else:
            return "high"

    # Convert to use case models
    use_cases = []
    for model in all_models:
        use_cases.append(
            ModelUseCase(
                model_name=model.model_name,
                provider=model.provider,
                best_for=model.best_for or "General-purpose LLM tasks",
                use_cases=model.use_cases or ["General tasks"],
                strengths=model.strengths or ["Reliable", "Versatile"],
                context_window=model.context_window,
                cost_tier=get_cost_tier(model.cost_per_input_token, model.cost_per_output_token),
                local_cost=localize_use_case_cost(model, locale),
            )
        )

    # Get unique providers
    providers = sorted(list(set(model.provider for model in use_cases)))

    _apply_locale_headers(response, locale)
    return UseCaseResponse(
        models=use_cases,
        total_models=len(use_cases),
        providers=providers,
        currency_conversion=build_conversion(locale),
    )


# ~5 minute in-memory cache for POST /router/recommend decisions. Deliberately
# a separate instance from recommend_model's cache (mcp/tools/recommend_model.py)
# so a hit on one endpoint never leaks into the other. POST /router/recommend/stream
# and /v1/chat/completions call ModelRouter.get_optimal_model() directly and are
# unaffected by this cache. name="router_recommend" opts this into the
# process-lifetime hit/miss registry exposed by GET /cache-stats / get_cache_stats.
_router_recommend_cache = RecommendationCache(name="router_recommend")


@app.get("/cache-stats", tags=["Router"])
async def cache_stats():
    """
    Report process-lifetime hit/miss counters for the recommendation cache.

    Covers the two call sites the ~5-minute recommendation cache
    (introduced alongside `recommend_model` / `POST /router/recommend`) is
    wired into: the `recommend_model` MCP tool and this `POST /router/recommend`
    endpoint. Reported separately per call site, never combined — a hit via
    one call site never affects the other's counts. Counts reset on process
    restart; not persisted, not shared across replicas. Reading this endpoint
    never itself changes the counts.

    Returns:
        dict: `{"recommend_model": {...}, "router_recommend": {...}}`, each
        with `hits`, `misses`, `total`, and `hit_rate` (percentage).
    """
    return get_cache_stats()


def _rest_client_fields(raw: Optional[str]):
    """Resolve a REST `client` / `ide_context` value.

    Returns (entry_or_None, raw_or_None, response_fields). REST never infers a
    client, so `client_source` is only ever 'explicit'; with no value at all the
    fields are empty and the response is exactly what it was before v1.85.0.
    """
    from src.services.client_models import recognised_client_ids, resolve_client, restriction_label
    if not (isinstance(raw, str) and raw.strip()):
        return None, None, {}
    entry = resolve_client(raw)
    if entry is None:
        return None, raw, {
            "client_source": "explicit",
            "client_note": (
                f"Unknown client '{raw}'; results are unfiltered. "
                f"Recognised clients: {', '.join(recognised_client_ids())}."
            ),
        }
    return entry, raw, {
        "applied_client": entry.id,
        "client_source": "explicit",
        "client_as_of": entry.as_of,
        "client_note": (
            f"Restricted to models {entry.display_name} can run "
            f"({restriction_label(entry)}); client table as of {entry.as_of}."
            + (" " + entry.notes if entry.menu == "open" else "")
        ),
    }


def _no_match_detail(entry) -> str:
    if entry is None:
        return "No model matched the given constraints. Try relaxing cost or quality filters."
    from src.services.client_models import restriction_label
    return (
        f"No model matched within what {entry.display_name} can run "
        f"({restriction_label(entry)}). Relax cost/quality/context filters, "
        "or omit client to search every model."
    )


# RouterRequest fields copied 1:1 onto RouterConstraints by both REST router
# surfaces (/router/recommend and its stream variant) so the two cannot drift.
_ROUTER_CONSTRAINT_FIELDS = (
    "max_cost_per_1m_tokens",
    "min_quality_score",
    "min_context_window",
    "preferred_provider",
    "task_type",
    "prefer_low_latency",
    "exclude_reasoning_models",
    "monthly_budget_usd",
    "estimated_monthly_requests",
    "avg_input_tokens",
    "avg_output_tokens",
)


def _router_constraints_from_request(req: RouterRequest, client_entry, client_raw: Optional[str]):
    """Build RouterConstraints for a REST router call (shared by both surfaces).

    `client` is the canonical client id when the value resolved, else the raw
    (unrecognised) value so the recommendation cache distinguishes it.
    """
    from src.services.router import RouterConstraints
    kwargs = {name: getattr(req, name) for name in _ROUTER_CONSTRAINT_FIELDS}
    kwargs["client"] = client_entry.id if client_entry else client_raw
    return RouterConstraints(**kwargs)


@app.post("/router/recommend", response_model=RouterResponse, tags=["Router"])
@_locale_documented
async def router_recommend(
    req: RouterRequest,
    request: Request,
    response: Response,
    locale: LocaleContext = Depends(resolve_request_locale),
):
    """
    Recommend the optimal LLM model for the caller's constraints.

    Fetches live pricing, enriches with benchmark quality scores, applies hard
    filters (cost, quality, context window), and scores survivors by
    quality_value_score.  Returns the best match and up to 3 alternatives.

    Identical (normalized-equivalent) requests made within ~5 minutes of each
    other are served from an in-memory cache instead of being recomputed;
    the response's `cached` field indicates which happened.

    Optionally pass `X-Organization-Id` and `X-Api-Key-Tier` headers to enable
    per-org savings tracking.

    Constraints (`max_cost_per_1m_tokens`, `monthly_budget_usd`) are always USD;
    the recommendation and its order never depend on country. With a locale
    active, `recommended` and each alternative carry `local_prices`.
    """
    client_entry, client_raw, client_fields = _rest_client_fields(req.client or req.ide_context)
    constraints = _router_constraints_from_request(req, client_entry, client_raw)
    router = get_router()
    result = await _router_recommend_cache.get_or_compute(
        constraints, lambda: router.get_optimal_model(constraints)
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=_no_match_detail(client_entry),
        )

    routing_id = str(uuid.uuid4())

    # Record routing decision for telemetry/savings
    try:
        org_id = (
            getattr(request.state, "customer", None) and request.state.customer.org_id
        ) or request.headers.get("X-Organization-Id")
        api_key_tier = request.headers.get("X-Api-Key-Tier")
        recommended_cost_1m = (
            result.recommended.cost_per_input_token + result.recommended.cost_per_output_token
        ) / 2 * 1_000_000
        baseline_model = None
        baseline_cost_1m = None
        if result.alternatives:
            # Use most expensive alternative as baseline
            alts_with_cost = [
                (a, (a.cost_per_input_token + a.cost_per_output_token) / 2 * 1_000_000)
                for a in result.alternatives
            ]
            baseline_alt, baseline_cost_1m = max(alts_with_cost, key=lambda x: x[1])
            baseline_model = baseline_alt.model_name
        tracker = get_savings_tracker()
        await tracker.record_routing(
            recommended_model=result.recommended.model_name,
            recommended_provider=result.recommended.provider,
            recommended_cost_per_1m=recommended_cost_1m,
            org_id=org_id,
            api_key_tier=api_key_tier,
            baseline_model=baseline_model,
            baseline_cost_per_1m=baseline_cost_1m,
            task_type=req.task_type,
            routing_id=routing_id,
            ide_context=client_entry.id if client_entry else client_raw,
        )
    except Exception as exc:
        logger.warning("Failed to record routing savings: %s", exc)

    get_telemetry_service().track_feature_usage("router_recommend")
    recommended = result.recommended
    alternatives = result.alternatives
    _apply_locale_headers(response, locale)
    if locale.active:
        # Copies only: the cached RouterResult must stay USD-only.
        recommended = localize_metrics(recommended, locale)
        alternatives = [localize_metrics(a, locale) for a in alternatives]
    return RouterResponse(
        recommended=recommended,
        score=result.score,
        reason=result.reason,
        alternatives=alternatives,
        routing_id=routing_id,
        cached=result.cached,
        currency_conversion=build_conversion(locale),
        **client_fields,
    )


@app.get("/telemetry/savings", response_model=SavingsResponse, tags=["Telemetry"])
async def telemetry_savings(
    org_id: Optional[str] = Query(None, description="Filter by organisation ID"),
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    limit: int = Query(100, ge=1, le=1000, description="Max rows to return"),
):
    """
    Return routing decisions and estimated cost savings per organisation.

    Pass `X-Organization-Id` header (or `org_id` query param) when calling
    `/router/recommend` to enable per-org tracking.  This endpoint aggregates
    those records and computes the total savings vs. the most expensive
    alternative considered at the time of each routing decision.
    """
    tracker = get_savings_tracker()
    result = await tracker.get_savings(org_id=org_id, days=days, limit=limit)
    records = [SavingsRecord(**r) for r in result["records"]]
    return SavingsResponse(
        records=records,
        total=result["total"],
        total_savings_per_1m=result["total_savings_per_1m"],
        org_id=org_id,
        days=days,
        acceptance_rate=result.get("acceptance_rate"),
    )


@app.get("/telemetry/ide-savings", tags=["Telemetry"])
async def ide_savings_breakdown(
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
):
    """Return routing decision counts, total savings, and acceptance rate broken down by IDE context."""
    tracker = get_savings_tracker()
    breakdown = await tracker.get_ide_breakdown(days=days)
    return IDEBreakdownResponse(
        breakdown=[IDEBreakdownRecord(**r) for r in breakdown],
        days=days,
    )


def _resolve_org_id(request: Request) -> Optional[str]:
    """Attribute a request to an org: authenticated customer first, else the caller-supplied header.

    Same trust model as /router/recommend's org attribution — the header is
    self-reported, not access-controlled, matching how /telemetry/savings' org_id
    filter already works for this codebase.
    """
    customer = getattr(request.state, "customer", None)
    if customer is not None:
        return customer.org_id
    return request.headers.get("X-Organization-Id")


@app.post("/usage", response_model=UsageEventResponse, tags=["Usage"])
async def record_usage(req: UsageEventRequest, request: Request):
    """
    Record an actual LLM usage event and compute its cost from current pricing.

    This is the "actual spend" counterpart to /cost-estimate's hypothetical numbers —
    call this after an LLM request completes with the real token counts, and this
    endpoint looks up current pricing itself rather than trusting a client-supplied cost.
    Resubmitting the same `request_id` for the same org is a no-op (idempotent).
    """
    aggregator = await get_pricing_aggregator()
    model_pricing = await aggregator.find_model_pricing(req.model_name)
    if not model_pricing:
        raise HTTPException(
            status_code=404,
            detail=f"Model '{req.model_name}' not found. Please check the /pricing endpoint for available models."
        )

    cost_usd = compute_token_cost(
        model_pricing.cost_per_input_token,
        model_pricing.cost_per_output_token,
        req.input_tokens,
        req.output_tokens,
    ).total_cost
    org_id = _resolve_org_id(request)
    tracker = get_usage_tracker()
    result = await tracker.record_event(
        provider=model_pricing.provider,
        model_name=model_pricing.model_name,
        input_tokens=req.input_tokens,
        output_tokens=req.output_tokens,
        cost_usd=cost_usd,
        org_id=org_id,
        occurred_at=req.occurred_at,
        request_id=req.request_id,
        session_id=req.session_id,
    )
    get_telemetry_service().track_feature_usage("usage_ingestion")

    try:
        budget_alerts = get_budget_alert_service()
        await budget_alerts.check_and_fire(tracker, secret=settings.webhook_secret)
    except Exception as exc:
        logger.warning("Budget alert check failed: %s", exc)

    return UsageEventResponse(
        recorded=True,
        duplicate=result["duplicate"],
        model_name=model_pricing.model_name,
        provider=model_pricing.provider,
        input_tokens=req.input_tokens,
        output_tokens=req.output_tokens,
        cost_usd=round(cost_usd, 6),
        org_id=org_id,
        session_id=req.session_id,
    )


@app.post("/usage/batch", response_model=UsageBatchResponse, tags=["Usage"])
async def record_usage_batch(req: UsageBatchRequest, request: Request):
    """
    Record multiple usage events in one call. Best-effort: an unknown model in one
    event does not fail the rest of the batch.
    """
    aggregator = await get_pricing_aggregator()
    org_id = _resolve_org_id(request)
    tracker = get_usage_tracker()

    recorded = 0
    duplicates = 0
    failed = 0
    errors: List[str] = []
    total_cost_usd = 0.0

    for event in req.events:
        model_pricing = await aggregator.find_model_pricing(event.model_name)
        if not model_pricing:
            failed += 1
            errors.append(f"Model '{event.model_name}' not found")
            continue

        cost_usd = (
            event.input_tokens * model_pricing.cost_per_input_token
            + event.output_tokens * model_pricing.cost_per_output_token
        )
        result = await tracker.record_event(
            provider=model_pricing.provider,
            model_name=model_pricing.model_name,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            cost_usd=cost_usd,
            org_id=org_id,
            occurred_at=event.occurred_at,
            request_id=event.request_id,
            session_id=event.session_id,
        )
        if result["duplicate"]:
            duplicates += 1
        else:
            recorded += 1
            total_cost_usd += cost_usd

    get_telemetry_service().track_feature_usage("usage_ingestion_batch")

    if recorded:
        try:
            budget_alerts = get_budget_alert_service()
            await budget_alerts.check_and_fire(tracker, secret=settings.webhook_secret)
        except Exception as exc:
            logger.warning("Budget alert check failed: %s", exc)

    return UsageBatchResponse(
        recorded=recorded,
        duplicates=duplicates,
        failed=failed,
        errors=errors,
        total_cost_usd=round(total_cost_usd, 6),
    )


@app.get("/usage/summary", response_model=UsageSummaryResponse, tags=["Usage"])
async def usage_summary(
    org_id: Optional[str] = Query(None, description="Filter by organisation ID"),
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
):
    """
    Return actual recorded spend for the last `days` days, optionally filtered by org.

    Pass `X-Organization-Id` header (or `org_id` query param) when calling
    POST /usage to enable per-org tracking, the same way /router/recommend and
    /telemetry/savings already work.
    """
    tracker = get_usage_tracker()
    result = await tracker.get_summary(org_id=org_id, days=days)
    return UsageSummaryResponse(**result)


@app.get("/usage/session/{session_id}", response_model=SessionUsageAnalysisResponse, tags=["Usage"])
async def usage_session_analysis(session_id: str, request: Request):
    """
    Analyze one specific session's actual recorded usage, on demand.

    Reads back the rows previously recorded via POST /usage or POST /usage/batch with
    this `session_id`, reports the session's real token/cost/model-mix totals, and runs
    the session's own observed token pattern through the same routing engine behind
    POST /router/recommend to produce a grounded recommendation (or confirm the current
    choice is already optimal) with the estimated dollar savings/increase versus this
    session's actual cost. Returns `has_data: false` with no `recommendation` if nothing
    has been recorded yet for this session_id. Advisory only — never re-prices history
    or switches anything.

    session_id values are caller-generated and not guaranteed unguessable, so an
    authenticated request is scoped to the caller's own org_id (resolved the same way as
    POST /usage — see `_resolve_org_id`), the same as an authenticated caller can never
    see another org's data. A caller without a per-customer API key (or a self-reported
    `X-Organization-Id` header) is not scoped, matching /usage/summary's existing
    trust model for unauthenticated/global-key access.
    """
    org_id = _resolve_org_id(request)
    tracker = get_usage_tracker()
    usage = await tracker.get_session_usage(session_id, org_id=org_id)

    total_requests = usage["total_requests"]
    if total_requests == 0:
        return SessionUsageAnalysisResponse(
            success=False,
            has_data=False,
            session_id=session_id,
            error=f"No usage recorded for session_id '{session_id}'",
        )

    total_input_tokens = usage["total_input_tokens"]
    total_output_tokens = usage["total_output_tokens"]
    total_cost_usd = usage["total_cost_usd"]
    by_model = usage["by_model"]

    response_data = {
        "success": True,
        "has_data": True,
        "session_id": session_id,
        "total_requests": total_requests,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_cost_usd": total_cost_usd,
        "by_model": by_model,
        "first_occurred_at": usage["first_occurred_at"],
        "last_occurred_at": usage["last_occurred_at"],
        "estimated_request_count": usage.get("estimated_request_count", 0),
        "has_estimated_usage": usage.get("has_estimated_usage", False),
    }

    # Recommendation math/rationale lives in session_recommendation.py, shared with the
    # analyze_session_usage MCP tool, so the two entry points can't drift apart.
    router = get_router()
    recommendation = await compute_session_recommendation(usage, router)
    if recommendation is None:
        return SessionUsageAnalysisResponse(**response_data)

    response_data["recommendation"] = recommendation
    return SessionUsageAnalysisResponse(**response_data)


# v1.80.1 — webhook URL rejected by src/services/webhook_guard.py (SSRF guard). The
# reason is a fixed category; the message never contains resolved IPs or DNS detail.
_INVALID_WEBHOOK_URL_RESPONSE = {
    422: {
        "description": (
            "invalid_webhook_url — the webhook URL must be a public https URL "
            "(reason: scheme_not_allowed, malformed_url, private_destination or unresolvable_host)"
        ),
    },
}


def _invalid_webhook_url_response(exc: WebhookURLRejected) -> JSONResponse:
    return JSONResponse(status_code=422, content={
        "error": "invalid_webhook_url",
        "reason": exc.reason,
        "message": exc.message,
    })


@app.post(
    "/usage/alerts", response_model=BudgetAlertRecord, status_code=201, tags=["Usage"],
    responses=_INVALID_WEBHOOK_URL_RESPONSE,
)
async def create_budget_alert(body: BudgetAlertRequest, request: Request):
    """
    Register a webhook alert for actual spend crossing a USD threshold.

    The webhook URL will receive a `POST` request whenever recorded spend (from
    POST /usage) for `org_id` (or across all orgs, if omitted) reaches
    `threshold_usd` within the trailing `period_days`. Checked inline after every
    usage event is recorded, and fires at most once per `period_days` while spend
    stays over threshold. Uses the same HMAC-SHA256 signing as /pricing/alerts —
    see GET /pricing/alerts/signing-info.

    **Org binding (v1.77.0).** A customer-key caller's alert is always bound to that
    key's own org (same rule as `_resolve_org_id` for POST /usage); naming another
    org is refused with 403. `hard_stop: true` additionally requires a customer key,
    because a hard stop blocks every dispatch for its org — it must only ever be set by
    that org.
    """
    customer = getattr(request.state, "customer", None)
    org_id = body.org_id
    if customer is not None:
        if org_id is not None and org_id != customer.org_id:
            return JSONResponse(status_code=403, content={
                "error": "org_mismatch",
                "message": "A customer key can only register budget alerts for its own org.",
            })
        org_id = customer.org_id
    elif body.hard_stop:
        return JSONResponse(status_code=403, content={
            "error": "customer_key_required",
            "message": (
                "hard_stop alerts block dispatch for an org, so they can only be registered with "
                "that org's customer API key (POST /billing/signup)."
            ),
        })
    svc = get_budget_alert_service()
    try:
        record = await svc.register(
            url=body.url,
            threshold_usd=body.threshold_usd,
            org_id=org_id,
            period_days=body.period_days,
            hard_stop=body.hard_stop,
        )
    except WebhookURLRejected as exc:
        return _invalid_webhook_url_response(exc)
    return BudgetAlertRecord(**record)


@app.get("/usage/alerts", response_model=BudgetAlertListResponse, tags=["Usage"])
async def list_budget_alerts(request: Request):
    """Return registered budget-spend alert webhooks (a customer key sees only its own org's)."""
    svc = get_budget_alert_service()
    customer = getattr(request.state, "customer", None)
    alerts = await svc.list_alerts(org_id=customer.org_id) if customer is not None else await svc.list_alerts()
    return BudgetAlertListResponse(alerts=alerts, total=len(alerts))


@app.delete("/usage/alerts/{alert_id}", status_code=204, tags=["Usage"])
async def delete_budget_alert(alert_id: int, request: Request):
    """Delete a registered budget alert by ID (a customer key can only delete its own org's)."""
    svc = get_budget_alert_service()
    customer = getattr(request.state, "customer", None)
    deleted = (
        await svc.delete(alert_id, org_id=customer.org_id) if customer is not None else await svc.delete(alert_id)
    )
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")


@app.post("/router/feedback", response_model=RouterFeedbackResponse, tags=["Router"])
async def router_feedback(req: RouterFeedbackRequest):
    """
    Record feedback for a prior routing recommendation.

    Pass the `routing_id` returned by `/router/recommend` along with whether
    the recommended model was actually used.  Duplicate feedback for the same
    `routing_id` is silently ignored.
    """
    tracker = get_savings_tracker()
    await tracker.record_feedback(
        routing_id=req.routing_id,
        was_used=req.was_used,
        actual_model_used=req.actual_model_used,
        notes=req.notes,
    )
    get_telemetry_service().track_feature_usage("router_feedback")
    return RouterFeedbackResponse(routing_id=req.routing_id, recorded=True)


@app.post("/router/recommend/stream", tags=["Router"])
async def router_recommend_stream(req: RouterRequest, request: Request):
    """
    Streaming version of /router/recommend — emits SSE progress events.

    Events: start → fetching_models → models_loaded → enriching_quality →
    quality_enriched → filtering → client (only when `client` was passed:
    applied_client, client_source, client_as_of, client_note) →
    candidates_ready → recommendation → alternatives → done.
    """
    def _event(type_: str, data: Optional[dict] = None) -> str:
        payload: dict = {"type": type_}
        if data:
            payload.update(data)
        return f"data: {json.dumps(payload, default=str)}\n\n"

    async def generate():
        yield _event("start", {"constraints": req.model_dump(exclude_none=True)})
        yield _event("fetching_models")
        try:
            aggregator = await get_pricing_aggregator()
            models, _ = await aggregator.get_all_pricing_async()
            yield _event("models_loaded", {"count": len(models)})
            yield _event("enriching_quality", {"count": len(models)})
            models = await enrich_models(models)
            yield _event("quality_enriched")
            yield _event("filtering")

            # --- Hard filters + scoring: the same ModelRouter.rank_models() the
            # other recommendation surfaces use, so they cannot drift. ---
            from src.services.router import ModelRouter
            client_entry, client_raw, client_fields = _rest_client_fields(
                req.client or req.ide_context
            )
            constraints = _router_constraints_from_request(req, client_entry, client_raw)
            stream_router = ModelRouter(aggregator)
            if client_fields:
                yield _event("client", client_fields)
            candidates = stream_router.rank_models(models, constraints)

            if not candidates:
                yield _event(
                    "error",
                    {"detail": _no_match_detail(client_entry) if client_entry
                     else "No model matched constraints"},
                )
                yield _event("done")
                return

            yield _event("candidates_ready", {"count": len(candidates)})

            best = candidates[0]
            best_score = stream_router._score_model(best, constraints)
            avg_cost_1m = (best.cost_per_input_token + best.cost_per_output_token) / 2 * 1_000_000
            alternatives = candidates[1:4]

            stream_routing_id = str(uuid.uuid4())
            yield _event("recommendation", {
                "model": best.model_name,
                "provider": best.provider,
                "score": round(best_score, 4),
                "reason": (
                    f"{best.model_name} ({best.provider}); "
                    f"quality_value_score={best_score:.2f}; "
                    f"cost=${avg_cost_1m:.2f}/1M tokens"
                ),
            })
            yield _event("alternatives", {
                "models": [
                    {"model_name": a.model_name, "provider": a.provider}
                    for a in alternatives
                ],
            })
            yield _event("done", {"routing_id": stream_routing_id})

            # Fire-and-forget: persist routing decision
            try:
                org_id = request.headers.get("X-Organization-Id")
                api_key_tier = request.headers.get("X-Api-Key-Tier")
                baseline_model = alternatives[0].model_name if alternatives else None
                baseline_cost_1m = (
                    (alternatives[0].cost_per_input_token + alternatives[0].cost_per_output_token)
                    / 2 * 1_000_000
                ) if alternatives else None
                tracker = get_savings_tracker()
                await tracker.record_routing(
                    recommended_model=best.model_name,
                    recommended_provider=best.provider,
                    recommended_cost_per_1m=avg_cost_1m,
                    org_id=org_id,
                    api_key_tier=api_key_tier,
                    baseline_model=baseline_model,
                    baseline_cost_per_1m=baseline_cost_1m,
                    task_type=req.task_type,
                    routing_id=stream_routing_id,
                    ide_context=client_entry.id if client_entry else client_raw,
                )
            except Exception as exc:
                logger.warning("Failed to record stream routing savings: %s", exc)

            get_telemetry_service().track_feature_usage("router_recommend_stream")

        except Exception as exc:
            logger.error("Stream router error: %s", exc, exc_info=True)
            yield _event("error", {"detail": str(exc)})
            yield _event("done")

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/rate-limits/tiers", tags=["API Info"])
async def rate_limit_tiers():
    """Return the rate limit (requests/minute) for each API key tier."""
    return {
        **{tier: _tiers.rate_limit_for(tier, settings) for tier in _tiers.TIER_IDS},
        "default": settings.rate_limit_per_minute,
    }


# ---------------------------------------------------------------------------
# Billing / Stripe endpoints
# ---------------------------------------------------------------------------

_PRICE_ID_TO_TIER = {}  # populated lazily from settings


def _price_tier_map():
    m = {}
    if settings.stripe_price_id_pro:
        m[settings.stripe_price_id_pro] = "pro"
    if settings.stripe_price_id_enterprise:
        m[settings.stripe_price_id_enterprise] = "enterprise"
    return m


@app.get("/billing")
async def billing_index():
    """Serve the self-serve billing dashboard SPA."""
    html_path = Path(__file__).parent.parent / "static" / "billing" / "index.html"
    return FileResponse(str(html_path))


@app.post("/billing/signup", response_model=SignupResponse, tags=["Billing"])
async def billing_signup(req: SignupRequest):
    """
    Free-tier signup — create a customer record for a new email and return its API key.

    The key is shown once, here. Signing up again with an email that is already
    registered returns 409 and no key: this endpoint is public, so returning the
    existing key would hand any org's service key to whoever knows its email.
    """
    billing = get_billing_service()
    customer = await billing.create_customer(req.email)
    if customer is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "An account with this email already exists. For security, an API key is "
                "only shown once, at signup. Use your saved key (Dashboard tab, or the "
                "x-api-key header)."
            ),
        )
    return SignupResponse(
        api_key=customer.api_key,
        org_id=customer.org_id,
        tier=customer.tier,
        message=(
            "Welcome! Your free-tier API key is ready. Save it now: it is shown only once. "
            "Upgrade anytime via POST /billing/checkout."
        ),
    )


@app.get("/billing/config", response_model=BillingConfigResponse, tags=["Billing"])
async def billing_config():
    """
    Public tier catalogue and what this server can sell (v1.84.0).

    `portal_enabled` is true when Stripe is configured. `checkout_enabled` additionally needs
    the legal pages in force (v1.86.0); when it is false `checkout_disabled_reason` says why
    (`stripe_not_configured` | `legal_pages_not_live`). Each tier's `checkout_available`
    additionally needs its Stripe price id. The entitlement
    strings are the exact copy shown on /billing, the landing page and by the support agent.
    """
    return BillingConfigResponse(**_tiers.catalog_payload(settings))


@app.post("/billing/checkout", response_model=CheckoutResponse, tags=["Billing"])
async def billing_checkout(req: CheckoutRequest, request: Request):
    """
    Create a Stripe Checkout session for the requested tier (pro|enterprise).

    Requires authentication (x-api-key header with a billing API key).
    Returns 503 if Stripe is not configured or the legal pages are not yet in force.
    """
    if not _tiers.checkout_enabled(settings):
        _why = {"stripe_not_configured": "Stripe not configured",
                "legal_pages_not_live": "legal pages not in force"}.get(
            _tiers.checkout_disabled_reason(settings), "checkout disabled")
        raise HTTPException(status_code=503, detail=f"Checkout is not enabled on this server ({_why})")

    customer = getattr(request.state, "customer", None)
    if customer is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    tier = req.tier.lower()
    price_map = _price_tier_map()
    price_id = {v: k for k, v in price_map.items()}.get(tier)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Unknown tier: {tier}")

    import stripe  # local import — optional dependency
    stripe.api_key = settings.stripe_secret_key
    session = stripe.checkout.Session.create(
        customer_email=customer.email,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"{settings.billing_base_url}/billing?success=1",
        cancel_url=f"{settings.billing_base_url}/billing?cancel=1",
        metadata={"customer_id": customer.id, "tier": tier},
    )
    return CheckoutResponse(checkout_url=session.url)


@app.post("/billing/webhook", tags=["Billing"])
async def billing_webhook(request: Request):
    """
    Stripe webhook receiver.

    Verifies the Stripe-Signature header and processes subscription events.
    Returns 400 on signature failure, 200 on success.
    """
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe webhook not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    import stripe  # local import — optional dependency
    stripe.api_key = settings.stripe_secret_key
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid Stripe signature")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    billing = get_billing_service()
    price_map = _price_tier_map()
    event_type = str(event["type"])
    event_id = event.get("id")
    obj = (event.get("data") or {}).get("object") or {}

    # Action-record target for events with no resolvable customer: an opaque digest of the Stripe
    # event id (the id itself is never stored outside stripe_events).
    event_ref = f"stripe_event:{hashlib.sha256(str(event_id).encode('utf-8')).hexdigest()[:12]}" if event_id else None

    # v1.84.0: Stripe redelivers; the event id is recorded first (atomic INSERT OR IGNORE) so a
    # duplicate does nothing. If applying the event then fails, the id is forgotten again so
    # Stripe's retry can complete the work instead of being swallowed as a duplicate.
    if event_id and not await billing.record_stripe_event(event_id, event_type):
        await _support_action(f"billing.{event_type}", "duplicate", actor="stripe", target=event_ref,
                              trigger="stripe:webhook")
        return {"ok": True, "duplicate": True}
    try:
        return await _apply_stripe_event(billing, price_map, event_id, event_type, obj, event_ref)
    except Exception:
        if event_id:
            await billing.forget_stripe_event(event_id)
        raise


async def _apply_stripe_event(billing, price_map, event_id, event_type: str, obj: dict, event_ref) -> dict:
    outcome = "ignored"
    customer = None
    if event_type == "checkout.session.completed":
        customer_id = (obj.get("metadata") or {}).get("customer_id")
        tier = (obj.get("metadata") or {}).get("tier", "pro")
        if customer_id:
            await billing.update_tier(
                customer_id, tier,
                stripe_customer_id=obj.get("customer"),
                stripe_subscription_id=obj.get("subscription"),
                billing_status="active",
            )
            customer = await billing.get_customer_by_id(customer_id)
            outcome = f"applied:{tier}"

    elif event_type == "customer.subscription.updated":
        price_id = obj["items"]["data"][0]["price"]["id"] if (obj.get("items") or {}).get("data") else None
        tier = price_map.get(price_id, "free")
        customer = await _stripe_customer(billing, obj.get("customer"))
        if customer:
            status = "past_due" if obj.get("status") in ("past_due", "unpaid") else "active"
            if status == "past_due":
                await billing.update_tier(customer.id, tier)
                if customer.billing_status != "past_due":
                    await billing.set_billing_status(customer.id, "past_due")
            else:
                await billing.update_tier(customer.id, tier, billing_status="active")
            outcome = f"applied:{tier}:{status}"

    elif event_type == "customer.subscription.deleted":
        customer = await _stripe_customer(billing, obj.get("customer"))
        if customer:
            await billing.update_tier(customer.id, "free", billing_status="active")
            outcome = "applied:free"

    elif event_type == "invoice.paid":
        customer = await _stripe_customer(billing, obj.get("customer"))
        if customer:
            await billing.set_billing_status(customer.id, "active")
            outcome = "applied:active"

    elif event_type == "invoice.payment_failed":
        customer = await _stripe_customer(billing, obj.get("customer"))
        if customer:
            if customer.billing_status != "past_due":
                await billing.set_billing_status(customer.id, "past_due")
            await _stripe_escalation(event_id, event_type, customer, "payment_failure")
            outcome = "escalated:past_due"

    elif event_type == "charge.refunded":
        customer = await _stripe_customer(billing, obj.get("customer"))
        if customer:
            await _stripe_escalation(event_id, event_type, customer, "refund")
            outcome = "escalated"

    elif event_type == "charge.dispute.created":
        # A Dispute object carries no customer field, only the disputed charge.
        customer = await _stripe_customer(billing, await _stripe_customer_id_for_dispute(obj))
        if customer:
            await _stripe_escalation(event_id, event_type, customer, "billing_dispute")
            outcome = "escalated"

    await _support_action(
        f"billing.{event_type}", outcome, actor="stripe", trigger="stripe:webhook",
        target=f"org:{customer.org_id[:8]}" if customer else event_ref,
    )
    return {"ok": True}


async def _stripe_customer(billing, stripe_customer_id):
    if not stripe_customer_id:
        return None
    if isinstance(stripe_customer_id, dict):  # expanded object
        stripe_customer_id = stripe_customer_id.get("id")
    return await billing.get_customer_by_stripe_id(stripe_customer_id)


async def _stripe_customer_id_for_dispute(dispute: dict):
    """Stripe customer id behind a Dispute: its own ``customer`` if present (not in the real
    object), else read from the disputed Charge. A failed lookup raises so the webhook returns
    500 and Stripe retries (the event id is forgotten by the caller)."""
    if dispute.get("customer"):
        return dispute["customer"]
    charge_id = dispute.get("charge")
    if isinstance(charge_id, dict):
        return charge_id.get("customer")
    if not charge_id:
        return None
    import stripe  # local import — optional dependency
    charge = await asyncio.to_thread(stripe.Charge.retrieve, str(charge_id))
    return charge.get("customer")


async def _stripe_escalation(event_id, event_type: str, customer, category: str) -> None:
    """v1.84.0: open a support_escalation run for a Stripe payment event (refund, dispute, failed
    renewal). Trigger class payment_event, actor stripe, dedup on the Stripe event id. The artifact
    names the customer by org prefix only."""
    try:
        svc = get_orchestrator_service()
    except RuntimeError:
        logger.error("Stripe %s for org %s not escalated: orchestrator not initialised", event_type,
                     customer.org_id[:8])
        return
    dedup = f"stripe:{event_id}" if event_id else f"stripe:{event_type}:{customer.org_id[:8]}:{int(time.time())}"
    tier = _effective_tier(customer, settings.billing_past_due_grace_days)
    payload = {
        "trigger_class": "payment_event",
        "event_type": event_type,
        "dedup_key": dedup,
        "actor": "stripe",
        "actor_is_owner": False,
        "source": {"kind": "stripe", "ref": event_type},
        "data": {
            "title": f"stripe {event_type}",
            "body": f"Stripe reported {event_type} for customer {customer.org_id[:8]} (plan {customer.tier}).",
            "category": category,
            "priority": _tiers.escalation_priority_for(customer.tier),
            "tier": tier,
            "identified": True,
            "customer_ref": customer.org_id[:8],
        },
    }
    try:
        await svc.intake_event(payload, actor_kind="stripe")
    except OrchestratorError as exc:
        logger.error("Stripe %s escalation refused: %s", event_type, exc.code)


@app.get("/billing/portal", response_model=BillingPortalResponse, tags=["Billing"])
async def billing_portal(request: Request):
    """
    Create a Stripe Customer Portal session for the authenticated customer.

    Returns 503 if Stripe is not configured.
    """
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503,
                            detail="The billing portal is not enabled on this server (Stripe not configured)")

    customer = getattr(request.state, "customer", None)
    if customer is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not customer.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer associated with this account")

    import stripe  # local import — optional dependency
    stripe.api_key = settings.stripe_secret_key
    portal = stripe.billing_portal.Session.create(
        customer=customer.stripe_customer_id,
        return_url=f"{settings.billing_base_url}/billing",
    )
    return BillingPortalResponse(portal_url=portal.url)


@app.get("/billing/me", response_model=CustomerDashboard, tags=["Billing"])
async def billing_me(request: Request):
    """
    Return the authenticated customer's dashboard data.

    Requires a valid billing API key in the x-api-key header.
    """
    customer = getattr(request.state, "customer", None)
    if customer is None:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Pull 30-day savings/router stats
    try:
        tracker = get_savings_tracker()
        savings = await tracker.get_savings(org_id=customer.org_id, days=30)
        router_calls = savings.get("total", 0)
        savings_per_1m = savings.get("total_savings_per_1m", 0.0)
        acceptance_rate = savings.get("acceptance_rate")
    except Exception:
        router_calls = 0
        savings_per_1m = 0.0
        acceptance_rate = None

    eff = _effective_tier(customer, settings.billing_past_due_grace_days)
    return CustomerDashboard(
        email=customer.email,
        tier=customer.tier,
        org_id=customer.org_id,
        api_key_preview=(customer.api_key_prefix or "") + "...",
        router_calls_30d=router_calls,
        savings_per_1m_30d=savings_per_1m,
        acceptance_rate=acceptance_rate,
        effective_tier=eff,
        billing_status=getattr(customer, "billing_status", "active") or "active",
        past_due_since=getattr(customer, "past_due_since", None),
        rate_limit_per_minute=_tiers.rate_limit_for(eff, settings),
    )


@app.post("/billing/key/rotate", response_model=KeyRotateResponse, tags=["Billing"])
async def billing_key_rotate(request: Request):
    """
    Rotate the calling customer's service key (v1.84.0).

    Needs the *current* key in `x-api-key` (the operator key alone is 401, like `/billing/me`).
    The old key stops working immediately; the new one is shown once, here. Without the
    current key there is no self-serve recovery: ask the support assistant to escalate.
    """
    customer = getattr(request.state, "customer", None)
    if customer is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    new_key = await get_billing_service().rotate_api_key(customer.id)
    if not new_key:
        raise HTTPException(status_code=404, detail="Customer not found")
    await _support_action("billing.key_rotate", "rotated", actor="customer", target=f"org:{customer.org_id[:8]}",
                          trigger="http:/billing/key/rotate")
    return KeyRotateResponse(
        api_key=new_key, org_id=customer.org_id,
        message="Your service key was rotated. Save the new key now: it is shown only once and the old key "
                "no longer works.",
    )


@app.get("/billing/account/export", response_model=AccountExportResponse, tags=["Billing"])
async def billing_account_export(request: Request):
    """
    Download everything the service holds about your organisation (v1.87.0).

    Customer key only (anonymous and operator-key-only requests are 401). One JSON document:
    account record, stored provider-key metadata (never key material), usage events, dispatch
    receipts, budget alerts, routing savings and feedback, and support conversations. Never the
    service key and never another org's rows. Each table is capped; `truncated` says if one was.
    """
    customer = getattr(request.state, "customer", None)
    if customer is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        data = await build_account_data_service(settings).export(customer)
    except AccountDataError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    return JSONResponse(
        content=data,
        headers={"Content-Disposition": f'attachment; filename="llm-pricing-export-{customer.org_id[:8]}.json"'},
    )


@app.post("/billing/account/delete", response_model=AccountDeleteResponse, tags=["Billing"])
async def billing_account_delete(request: Request, req: Optional[AccountDeleteRequest] = None):
    """
    Permanently delete your account and every record the service holds about it (v1.87.0).

    Customer key only (401 otherwise). IRREVERSIBLE: send `{"confirm": "DELETE", "org_id": "<your org id>"}`;
    anything else is 400 and changes nothing. A paying account's Stripe subscription is ended
    immediately first (a Stripe failure aborts with 502 and deletes nothing). Only a dated,
    non-reversible marker remains. Deleting within the refund window opens a refund review for the
    owner and returns its reference; no refund is automatic.
    """
    customer = getattr(request.state, "customer", None)
    if customer is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        result = await build_account_data_service(settings).delete(
            customer, req.confirm if req else None, req.org_id if req else None
        )
    except AccountDataError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message)
    return AccountDeleteResponse(**result)


@app.post("/admin/customers/{customer_id}/key/rotate", response_model=KeyRotateResponse, tags=["Admin"])
async def admin_customer_key_rotate(customer_id: str):
    """
    Operator-side key rotation for a customer who lost their key (v1.84.0).

    Operator key only (constant 401 for anyone else). The new key is shown once, here; the
    owner relays it to the customer by a channel they have verified themselves. Audited as
    `billing.key_rotate` with actor `owner`.
    """
    try:
        billing = get_billing_service()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Billing service not initialized")
    customer = await billing.get_customer_by_id(customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    new_key = await billing.rotate_api_key(customer.id)
    if not new_key:
        raise HTTPException(status_code=404, detail="Customer not found")
    await _support_action("billing.key_rotate", "rotated", actor="owner", target=f"org:{customer.org_id[:8]}",
                          trigger="http:/admin/customers/{id}/key/rotate")
    return KeyRotateResponse(
        api_key=new_key, org_id=customer.org_id,
        message="Key rotated by the operator. Relay it to the customer once; the old key no longer works.",
    )


@app.get("/telemetry", response_model=TelemetryResponse, tags=["Telemetry"])
async def get_telemetry():
    """
    Get real-time telemetry data including endpoint usage, provider adoption, feature usage,
    and client geolocation/browser statistics.

    This endpoint provides comprehensive metrics about API usage patterns, which providers
    and models are most requested, response times, error rates, geographic distribution,
    browser usage, and overall system health.

    Returns:
        TelemetryResponse: Comprehensive telemetry data with overall stats, endpoint metrics,
                         provider adoption metrics, feature usage statistics, client locations,
                         and browser statistics
    """
    telemetry = get_telemetry_service()

    # Track feature usage for telemetry endpoint itself
    telemetry.track_feature_usage("telemetry_access")

    # Get all metrics
    overall_stats = telemetry.get_overall_stats()
    endpoint_stats = telemetry.get_endpoint_stats()
    provider_adoption = telemetry.get_provider_adoption()
    feature_usage = telemetry.get_feature_usage()
    client_locations = telemetry.get_client_locations(limit=20)
    browser_stats = telemetry.get_browser_stats(limit=20)

    # Convert uptime_since to ISO string if it's a datetime
    uptime_since = overall_stats.get("uptime_since")
    if uptime_since and isinstance(uptime_since, datetime):
        uptime_since = uptime_since.isoformat()
    elif uptime_since is None:
        uptime_since = datetime.now(UTC).isoformat()

    # Build response
    return TelemetryResponse(
        overall_stats=TelemetryOverallStats(
            total_requests=overall_stats.get("total_requests", 0),
            total_errors=overall_stats.get("total_errors", 0),
            error_rate=overall_stats.get("error_rate", 0.0),
            total_endpoints=overall_stats.get("total_endpoints", 0),
            total_providers_adopted=overall_stats.get("total_providers_adopted", 0),
            total_features_used=overall_stats.get("total_features_used", 0),
            avg_response_time_ms=overall_stats.get("avg_response_time_ms", 0.0),
            unique_clients=overall_stats.get("unique_clients", 0),
            unique_countries=overall_stats.get("unique_countries", 0),
            uptime_since=uptime_since,
            timestamp=datetime.now(UTC).isoformat()
        ),
        endpoints=[
            EndpointMetricResponse(
                endpoint=stat.get("endpoint", ""),
                path=stat.get("path", ""),
                method=stat.get("method", ""),
                call_count=stat.get("call_count", 0),
                error_count=stat.get("error_count", 0),
                success_rate=stat.get("success_rate", 0.0),
                avg_response_time_ms=stat.get("avg_response_time_ms", 0.0),
                min_response_time_ms=stat.get("min_response_time_ms", 0.0),
                max_response_time_ms=stat.get("max_response_time_ms", 0.0),
                first_called=stat.get("first_called", None),
                last_called=stat.get("last_called", None)
            )
            for stat in endpoint_stats
        ],
        provider_adoption=[
            ProviderAdoptionResponse(
                provider_name=adoption.get("provider_name", ""),
                model_requests=adoption.get("model_requests", 0),
                unique_models_requested=adoption.get("unique_models_requested", 0),
                total_cost_estimated=adoption.get("total_cost_estimated", 0.0),
                last_requested=adoption.get("last_requested", None)
            )
            for adoption in provider_adoption
        ],
        features=[
            FeatureUsageResponse(
                feature_name=feature.get("feature_name", ""),
                usage_count=feature.get("usage_count", 0),
                last_used=feature.get("last_used", None)
            )
            for feature in feature_usage
        ],
        client_locations=[
            ClientLocationStats(
                country=loc.get("country", "Unknown"),
                country_code=loc.get("country_code", "XX"),
                request_count=loc.get("request_count", 0),
                unique_clients=loc.get("unique_clients", 0)
            )
            for loc in client_locations
        ],
        top_browsers=[
            BrowserStats(
                browser_name=browser.get("browser_name", "Unknown"),
                request_count=browser.get("request_count", 0),
                unique_clients=browser.get("unique_clients", 0)
            )
            for browser in browser_stats
        ],
        timestamp=datetime.now()
    )


# ------------------------------------------------------------------
# Agent chat endpoint
# ------------------------------------------------------------------


@app.post("/agent/chat", response_model=AgentChatResponse, tags=["Agent"])
async def agent_chat(request: AgentChatRequest):
    """
    Natural language interface to the LLM Pricing Agent.

    The agent uses RAG (indexed docs + live pricing) and existing pricing tools
    to answer questions about LLM costs, capabilities, and use cases.

    - Set `autonomous: true` to run a multi-step autonomous workflow (no history).
    - Provide `conversation_id` from a previous response to continue a conversation.
    """
    try:
        agent = await get_pricing_agent()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        if request.autonomous:
            response = await asyncio.wait_for(
                agent.run_task(request.message), timeout=120.0
            )
        else:
            response = await asyncio.wait_for(
                agent.chat(request.message, request.conversation_id), timeout=120.0
            )
    except asyncio.TimeoutError:
        logger.warning("Agent request timed out after 120s")
        raise HTTPException(
            status_code=504,
            detail="The agent request timed out. Please try a simpler query or try again later.",
        )
    except Exception as exc:
        # v1.81.0: no traceback — provider/tool text can quote a user's alert_token,
        # and exc_info would log str(exc) unredacted. Matches /agent/chat/stream.
        logger.error("PricingAgent error (%s): %s", type(exc).__name__, redact_alert_tokens(str(exc)))
        raise HTTPException(
            status_code=500,
            detail="The agent encountered an internal error. Check server logs for details.",
        ) from exc

    return AgentChatResponse(
        reply=response.reply,
        conversation_id=response.conversation_id,
        tool_calls=response.tool_calls,
        sources=response.sources,
    )


# ------------------------------------------------------------------
# Support agent (v1.84.0)
# ------------------------------------------------------------------

def _client_ip_for_support(request: Request) -> str:
    """Bucket key for the anonymous support spend cap and daily escalation limit.

    Fly-Client-IP first (set by the Fly proxy, not spoofable by the client), then the same
    fallback chain as the rate limiter. A client-prepended X-Forwarded-For hop must not mint a
    fresh bucket per request, or the anonymous caps would not bound anything. Only ever an
    in-memory key: not stored, not logged, not echoed.
    """
    return (
        request.headers.get("fly-client-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or request.headers.get("x-real-ip")
        or (request.client.host if request.client else None)
        or "unknown"
    )


@app.post("/support/chat", response_model=SupportChatResponse, tags=["Support"])
async def support_chat(body: SupportChatRequest, request: Request):
    """
    Customer support assistant: plans, billing, keys, rate limits, how-to, FAQ.

    Anonymous but rate-limited. A customer service key in `x-api-key` (or `Authorization:
    Bearer`) identifies the caller, so the assistant can describe *that* customer's own plan,
    payment status, rate limit and key preview; anonymous callers get general answers and are
    told how to identify themselves. Answers are grounded in /faq and the plan catalogue; the
    assistant never changes billing, never reveals keys, and escalates anything outside its
    authority to the owner with a reference (`escalation`). Per-caller daily LLM caps by tier;
    a cap hit returns 200 with `fallback: "spend_cap"` and FAQ links. `conversation_id` must be
    one this endpoint minted (400 otherwise). 503 when the LLM key is not configured.
    """
    try:
        agent = await get_support_agent()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    caller = await _resolve_mcp_caller(request)
    try:
        response = await asyncio.wait_for(
            agent.chat(body.message, body.conversation_id, caller=caller,
                       client_ip=_client_ip_for_support(request), trigger="http:/support/chat"),
            timeout=120.0,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="conversation_id must be one returned by a previous reply")
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="The support assistant timed out. Please try again.")
    except Exception as exc:
        # No traceback and no message text: the exception may quote the customer's message.
        logger.error("SupportAgent error (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=500,
            detail="The support assistant encountered an internal error. Please try again or see /faq.",
        ) from exc
    return SupportChatResponse(
        reply=response.reply, conversation_id=response.conversation_id, identified=response.identified,
        tier=response.tier, escalation=response.escalation, fallback=response.fallback, sources=response.sources,
    )


@app.post("/support/escalations/status", tags=["Support"])
async def support_escalation_status(body: EscalationStatusRequest, request: Request):
    """
    Status of an escalation by its reference (in the body, never the URL).

    Returns `{reference, status, owner_reply}`; `owner_reply` is the note the owner left when
    answering. Unknown, malformed or non-escalation references all get the same 404.
    """
    from src.services.orchestrator_workflows import RUN_ID_RE
    reference = body.reference.strip()
    view = None
    if RUN_ID_RE.match(reference):
        try:
            view = await get_orchestrator_service().public_escalation_view(reference)
        except RuntimeError:
            view = None
    caller = await _resolve_mcp_caller(request)
    await _support_action("support.escalation_status", "found" if view else "unknown",
                          actor="customer" if caller.is_customer else "anonymous",
                          target=reference if view else None, trigger="http:/support/escalations/status",
                          provenance={"identified": caller.is_customer})
    if view is None:
        raise HTTPException(status_code=404, detail="No escalation matches this reference.")
    return view


@app.post("/agent/chat/stream", tags=["Agent"])
async def agent_chat_stream(request: AgentChatRequest):
    """SSE streaming version of /agent/chat. Emits progress events as the ReAct loop runs."""
    async def generate():
        try:
            agent = await get_pricing_agent()
        except (ValueError, RuntimeError) as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
            return

        try:
            async with asyncio.timeout(120.0):
                async for event in agent.chat_stream(request.message, request.conversation_id):
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Request timed out after 120s'})}\n\n"
        except Exception as exc:
            # v1.81.0: provider/tool text can quote a user's alert_token; redact it.
            logger.error("Stream error (%s): %s", type(exc).__name__, redact_alert_tokens(str(exc)))
            exc_type = type(exc).__name__
            if any(k in exc_type for k in ("ConnectionError", "ConnectError", "TimeoutError")):
                detail = "Could not reach the AI provider — please try again"
            elif "RateLimit" in exc_type:
                detail = "AI provider rate limit reached — please wait a moment and try again"
            elif "AuthenticationError" in exc_type or "PermissionDenied" in exc_type:
                detail = "AI provider authentication error — check server configuration"
            elif "BadRequestError" in exc_type:
                exc_str = str(exc)
                if "credit balance is too low" in exc_str or "billing" in exc_str.lower():
                    detail = "AI provider account has insufficient credits — please contact the server administrator"
                else:
                    # Include the Anthropic message for other 400 errors (useful for debugging)
                    detail = f"Bad request to AI provider: {redact_alert_tokens(exc_str)[:300]}"
            else:
                detail = f"Internal server error ({exc_type})"
            yield f"data: {json.dumps({'type': 'error', 'detail': detail})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Historical pricing endpoints
# ---------------------------------------------------------------------------

@app.get("/pricing/history", response_model=PricingHistoryResponse, tags=["Pricing History"])
async def pricing_history(
    model_name: Optional[str] = Query(None, description="Filter by model name"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    limit: int = Query(100, ge=1, le=1000, description="Max rows to return"),
):
    """
    Return historical pricing snapshots.

    Snapshots are captured automatically every `pricing_snapshot_interval_hours` hours
    and stored locally in SQLite.  Use `days`, `model_name`, and `provider` to narrow
    the results.
    """
    svc = get_pricing_history_service()
    result = await svc.get_history(
        model_name=model_name, provider=provider, days=days, limit=limit
    )
    return PricingHistoryResponse(**result)


@app.get("/pricing/trends", response_model=PricingTrendsResponse, tags=["Pricing History"])
async def pricing_trends(
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    limit: int = Query(20, ge=1, le=100, description="Max models to return"),
):
    """
    Return models with the largest price change over the last `days` days.

    The response is sorted by absolute percentage change (largest first) and
    includes both input and output price deltas along with a human-readable
    `direction` field (`'increased'`, `'decreased'`, or `'unchanged'`).
    """
    svc = get_pricing_history_service()
    trends = await svc.get_trends(days=days, limit=limit)
    return PricingTrendsResponse(trends=trends, days=days)


@app.get("/pricing/subscription-trends", response_model=SubscriptionTrendsResponse, tags=["Pricing"])
async def subscription_price_trends(
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    limit: int = Query(20, ge=1, le=100, description="Max models to return"),
):
    """Return IDE/subscription tools whose monthly price changed the most."""
    svc = get_pricing_history_service()
    trends = await svc.get_subscription_trends(days=days, limit=limit)
    return SubscriptionTrendsResponse(trends=trends, days=days)


# ---------------------------------------------------------------------------
# Pricing history export endpoint
# ---------------------------------------------------------------------------

@app.get("/pricing/history/export", tags=["Pricing History"])
async def pricing_history_export(
    format: str = Query("csv", pattern="^(csv|json)$", description="Output format: 'csv' or 'json'"),
    model_name: Optional[str] = Query(None, description="Filter by model name"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    limit: int = Query(10_000, ge=1, le=100_000, description="Max rows to export"),
):
    """
    Export pricing snapshot history as CSV or JSON.

    Returns a downloadable file with all snapshot rows matching the supplied
    filters.  The CSV variant includes a computed `cost_per_*_per_1m_usd`
    column for convenience.  Set `format=json` for a machine-readable export
    with metadata (`exported_at`, `filters`, `count`).
    """
    svc = get_pricing_history_service()
    result = await svc.get_history(
        model_name=model_name, provider=provider, days=days, limit=limit
    )
    snapshots = result["snapshots"]

    parts = []
    if model_name:
        parts.append(model_name.replace(" ", "_"))
    if provider:
        parts.append(provider)
    parts.append(f"{days}d")
    base_name = "pricing_history_" + "_".join(parts)

    if format == "csv":
        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "captured_at",
            "captured_at_iso",
            "model_name",
            "provider",
            "cost_per_input_token",
            "cost_per_output_token",
            "cost_per_input_per_1m_usd",
            "cost_per_output_per_1m_usd",
        ])
        for s in snapshots:
            writer.writerow([
                s["captured_at"],
                datetime.fromtimestamp(s["captured_at"], tz=UTC).isoformat(),
                s["model_name"],
                s["provider"],
                s["cost_per_input_token"],
                s["cost_per_output_token"],
                round(s["cost_per_input_token"] * 1_000_000, 6),
                round(s["cost_per_output_token"] * 1_000_000, 6),
            ])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.csv"'},
        )

    # JSON export
    export_payload = {
        "exported_at": datetime.now(UTC).isoformat(),
        "filters": {"model_name": model_name, "provider": provider, "days": days},
        "count": len(snapshots),
        "snapshots": snapshots,
    }
    return Response(
        content=json.dumps(export_payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{base_name}.json"'},
    )


# ---------------------------------------------------------------------------
# Pricing alert endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/pricing/alerts", response_model=PricingAlertRegistration, status_code=201, tags=["Pricing Alerts"],
    responses=_INVALID_WEBHOOK_URL_RESPONSE,
)
async def create_pricing_alert(request: PricingAlertRequest):
    """
    Register a webhook alert for price changes.

    The webhook URL will receive a `POST` request with a JSON body whenever a
    pricing snapshot detects that a model's input or output price has changed by
    more than `threshold_pct` percent since the previous snapshot taken on the
    same day.  Use `provider` and `model_name` to limit the alert to specific
    models.

    Public (no key), but per-IP rate-limited and body-capped (v1.79.2).

    v1.81.0: the response adds a one-time ``alert_token`` (and ``alert_token_notice``).
    Save it: it is shown only once, stored only as a hash, and is the only way to view
    or delete this alert yourself, via POST /pricing/alerts/self/view or /self/delete.
    """
    svc = get_pricing_alert_service()
    try:
        record = await svc.register(
            url=request.url,
            threshold_pct=request.threshold_pct,
            provider=request.provider,
            model_name=request.model_name,
        )
    except WebhookURLRejected as exc:
        return _invalid_webhook_url_response(exc)
    return PricingAlertRegistration(**record)


# v1.81.0: one refusal, byte-identical for wrong, malformed, missing, deleted, unknown
# and legacy (tokenless) alerts, on both self routes. Never echoes the token.
_ALERT_TOKEN_REFUSAL_BODY = {"error": "invalid_alert_token", "message": ALERT_TOKEN_REFUSAL_MESSAGE}
_ALERT_TOKEN_REFUSAL_RESPONSE = {
    404: {
        "description": "No alert matches this alert_token (identical whether or not any alert exists)",
        "content": {"application/json": {"example": _ALERT_TOKEN_REFUSAL_BODY}},
    },
}


def _alert_token_refusal() -> JSONResponse:
    return JSONResponse(status_code=404, content=dict(_ALERT_TOKEN_REFUSAL_BODY))


# The body is parsed by hand rather than via a Pydantic parameter so that every
# malformed input — a non-string alert_token, a body that is not a JSON object, or
# invalid JSON — reaches the same 404 refusal instead of a 422 that echoes the input.
# PricingAlertTokenRequest still documents the body in OpenAPI.
_ALERT_TOKEN_OPENAPI_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": PricingAlertTokenRequest.model_json_schema()}},
    },
}


async def _alert_token_from_body(request: Request) -> Any:
    """The raw ``alert_token`` value from a JSON object body, else None. Never raises."""
    try:
        payload = await request.json()
    except Exception:  # any unparseable body is just "no token"
        return None
    if isinstance(payload, dict):
        return payload.get("alert_token")
    return None


@app.post(
    "/pricing/alerts/self/view", response_model=PricingAlertRecord, tags=["Pricing Alerts"],
    responses=_ALERT_TOKEN_REFUSAL_RESPONSE, openapi_extra=_ALERT_TOKEN_OPENAPI_BODY,
)
async def view_own_pricing_alert(request: Request):
    """View the one alert your ``alert_token`` manages (v1.81.0).

    No API key: send ``{"alert_token": "lpa_..."}`` in the JSON body (never in the URL).
    Returns that alert's id, url, threshold_pct, provider, model_name and created_at.
    Any token that does not match gets the same 404 ``invalid_alert_token``. Per-IP
    rate-limited and body-capped like registration.
    """
    svc = get_pricing_alert_service()
    record = await svc.get_by_token(await _alert_token_from_body(request))
    if record is None:
        return _alert_token_refusal()
    return PricingAlertRecord(**record)


@app.post(
    "/pricing/alerts/self/delete", response_model=PricingAlertSelfDeleteResponse, tags=["Pricing Alerts"],
    responses=_ALERT_TOKEN_REFUSAL_RESPONSE, openapi_extra=_ALERT_TOKEN_OPENAPI_BODY,
)
async def delete_own_pricing_alert(request: Request):
    """Delete the one alert your ``alert_token`` manages (v1.81.0).

    No API key: send ``{"alert_token": "lpa_..."}`` in the JSON body (never in the URL).
    The alert stops firing and leaves the operator listing. Any token that does not
    match gets the same 404 ``invalid_alert_token``. Per-IP rate-limited and body-capped.
    Alerts registered before v1.81.0 have no token and are removed via the operator.
    """
    svc = get_pricing_alert_service()
    alert_id = await svc.delete_by_token(await _alert_token_from_body(request))
    if alert_id is None:
        return _alert_token_refusal()
    return PricingAlertSelfDeleteResponse(deleted=True, id=alert_id)


@app.get("/pricing/alerts", response_model=PricingAlertListResponse, tags=["Pricing Alerts"])
async def list_pricing_alerts():
    """Return all registered price-change alert webhooks.

    Operator only (v1.79.2): needs the operator key (MCP_API_KEY) in x-api-key;
    any other caller gets 401 (503 when MCP_API_KEY is unset). Records never include
    an alert_token or its hash.
    """
    svc = get_pricing_alert_service()
    alerts = await svc.list_alerts()
    return PricingAlertListResponse(alerts=alerts, total=len(alerts))


@app.delete("/pricing/alerts/{alert_id}", status_code=204, tags=["Pricing Alerts"])
async def delete_pricing_alert(alert_id: int):
    """Delete a registered alert by ID.

    Operator only (v1.79.2): needs the operator key (MCP_API_KEY) in x-api-key;
    any other caller gets 401 and the alert stays. To delete an alert you registered
    yourself, use POST /pricing/alerts/self/delete with its alert_token (v1.81.0).
    """
    svc = get_pricing_alert_service()
    deleted = await svc.delete(alert_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")


# ---------------------------------------------------------------------------
# Conversation management endpoints
# ---------------------------------------------------------------------------


@app.get("/agent/conversations", response_model=ConversationListResponse, tags=["Agent"])
async def list_conversations():
    """
    List all stored chat conversations with metadata.

    Returns each conversation's ID, last-updated timestamp, turn count, and a
    short preview of the most recent user message.  Conversations are sorted
    newest first.  When the conversation store is in-memory (no
    ``CONVERSATION_DB_PATH`` configured) the list reflects only conversations
    created since the server started.

    Operator only (v1.79.2): needs the operator key (MCP_API_KEY) in x-api-key;
    any other caller (including a customer key) gets 401.
    """
    store = get_conversation_store()
    conversations = await store.list_conversations()
    items = [ConversationSummary(**c) for c in conversations]
    return ConversationListResponse(conversations=items, total=len(items))


@app.delete("/agent/conversations/{conversation_id}", status_code=204, tags=["Agent"])
async def delete_conversation(conversation_id: str):
    """
    Delete a stored conversation by its ID.

    Removes the conversation from both the in-process cache and the persistent
    SQLite database (if configured).  Returns **204 No Content** on success and
    **404 Not Found** if the conversation does not exist.

    Operator only (v1.79.2): needs the operator key (MCP_API_KEY) in x-api-key;
    a customer key no longer suffices (401, the conversation stays).
    """
    store = get_conversation_store()
    deleted = await store.delete(conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Conversation '{conversation_id}' not found",
        )


# ---------------------------------------------------------------------------
# Webhook signing info endpoint
# ---------------------------------------------------------------------------


@app.get("/pricing/alerts/signing-info", tags=["Pricing Alerts"])
async def webhook_signing_info():
    """
    Return information about webhook payload signing.

    When ``WEBHOOK_SECRET`` is configured, every price-change webhook (from
    /pricing/alerts) and budget-spend webhook (from /usage/alerts) POST will
    carry an ``X-LLM-Pricing-Signature: sha256=<hex>`` header.  Use the
    ``verify_webhook_signature`` helper (from
    ``src.services.pricing_alerts``) on the receiving end to verify it —
    both alert types use the same signing helper.

    Returns whether signing is active and the header name to look for.
    Intentionally never returns the secret itself.
    """
    signing_enabled = bool(settings.webhook_secret)
    return {
        "signing_enabled": signing_enabled,
        "algorithm": "hmac-sha256" if signing_enabled else None,
        "header": "X-LLM-Pricing-Signature" if signing_enabled else None,
        "format": "sha256=<hex_digest>" if signing_enabled else None,
        "note": (
            "Set WEBHOOK_SECRET in your environment to enable payload signing."
            if not signing_enabled
            else "Verify signatures using verify_webhook_signature() from src.services.pricing_alerts."
        ),
    }


@app.get("/pricing/public", tags=["Pricing"])
@_locale_documented
async def get_public_pricing(
    response: Response,
    models: Optional[str] = Query(
        None,
        description="Comma-separated model names to include (omit for all models)",
    ),
    provider: Optional[str] = Query(
        None,
        description="Filter by provider name",
    ),
    locale: LocaleContext = Depends(resolve_request_locale),
):
    """
    Public pricing endpoint — no API key required.

    Returns a simplified list of models with per-1M-token prices suitable for
    embedding in third-party pages via the LLM Pricing widget.

    Args:
        models:   Comma-separated model names to include (e.g. ``gpt-4o,claude-sonnet-4-6``).
                  When omitted all available models are returned.
        provider: Optional provider filter (e.g. ``openai``).

    Returns:
        JSON with ``models`` list and ``updated_at`` timestamp. The ``*_usd``
        keys are always present; with a locale active each model also carries
        ``input_per_1m_local``, ``output_per_1m_local`` and ``local_currency``,
        and the response a top-level ``currency_conversion``.
    """
    aggregator = await get_pricing_aggregator()
    if provider:
        all_models, _ = await aggregator.get_pricing_by_provider_async(provider)
    else:
        all_models, _ = await aggregator.get_all_pricing_async()

    # Filter by model names if requested
    if models:
        requested = {m.strip().lower() for m in models.split(",") if m.strip()}
        all_models = [m for m in all_models if m.model_name.lower() in requested]

    result = [
        {
            "model_name": m.model_name,
            "provider": m.provider,
            "input_per_1m_usd": round(m.cost_per_input_token * 1_000_000, 6),
            "output_per_1m_usd": round(m.cost_per_output_token * 1_000_000, 6),
            "context_window": m.context_window,
        }
        for m in all_models
    ]
    if locale.active:
        for row in result:
            row["input_per_1m_local"] = convert(row["input_per_1m_usd"], locale)
            row["output_per_1m_local"] = convert(row["output_per_1m_usd"], locale)
            row["local_currency"] = locale.currency
    _apply_locale_headers(response, locale)
    result.sort(key=lambda x: (x["provider"], x["model_name"]))
    payload = {"models": result, "total": len(result), "updated_at": datetime.now(UTC).isoformat()}
    conversion = conversion_metadata(locale)  # None when the locale is inactive
    if conversion is not None:
        payload["currency_conversion"] = conversion
    return payload


@app.get("/locale", response_model=LocaleResponse, tags=["Pricing"])
@_locale_documented
async def get_locale(response: Response, locale: LocaleContext = Depends(resolve_request_locale)):
    """
    What currency and country this request resolves to — the browser pages'
    preset for their currency selector.

    Honours the same overrides and precedence as the pricing endpoints
    (`currency`/`X-Currency`, `country`/`X-Country`, platform geo headers,
    best-effort IP lookup). `source` says which one applied, or `default` when
    nothing was stated or detected (USD). The response never contains an IP;
    the only location datum it may echo is the ISO country code.
    """
    _apply_locale_headers(response, locale)
    response.headers["Cache-Control"] = "no-store"  # after the helper: a preset is never cached
    return LocaleResponse(
        country=locale.country,
        currency=locale.currency,
        source=locale.source,
        fallback_to_usd=locale.fallback_to_usd,
        fallback_reason=locale.fallback_reason,
        supported_currencies=list(SUPPORTED_CURRENCIES),
    )


@app.get("/currency/rates", response_model=ExchangeRatesResponse, tags=["Pricing"])
async def get_currency_rates(response: Response):
    """
    The vendored USD exchange-rate table the server converts with.

    Rates are not fetched per request: `data/exchange_rates.json` is refreshed
    weekly by `scripts/refresh_exchange_rates.py` through a human-merged PR
    (Frankfurter, falling back to ECB reference rates). `as_of` is the capture
    date; `stale` is true once it is more than `stale_after_days` (7) old —
    the rates are still served, just flagged. `placeholder` is true while the
    table is still the authored one and has not yet been refreshed. Converted
    amounts are indicative — providers bill in USD.
    """
    table = load_rate_table(settings.exchange_rates_path)
    if table is None:
        raise HTTPException(status_code=503, detail="Exchange-rate table unavailable; prices are served in USD only")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return ExchangeRatesResponse(
        base=table.base,
        as_of=table.as_of.isoformat(),
        rate_age_days=table.age_days(),
        stale=table.is_stale(),
        stale_after_days=RATE_STALE_AFTER_DAYS,
        source=table.source,
        placeholder=table.placeholder,
        note=CONVERSION_NOTE,
        rates={code: table.rates[code] for code in SUPPORTED_CURRENCIES if code in table.rates},
        zero_decimal=sorted(ZERO_DECIMAL_CURRENCIES),
    )


# ---------------------------------------------------------------------------
# Admin endpoints (require API key via standard middleware)
# ---------------------------------------------------------------------------

@app.get("/admin", include_in_schema=False)
async def admin_index():
    """Serve the admin dashboard HTML page (no auth required for the static page)."""
    html_path = _static_dir / "admin" / "index.html"
    return FileResponse(str(html_path), media_type="text/html")


@app.get("/admin/stats", tags=["Admin"])
async def admin_stats():
    """
    Aggregated server statistics for the admin dashboard.

    Returns overall telemetry, per-endpoint metrics, provider health,
    feature usage counts, deployment uptime, and the server version.
    Operator-only: requires the ``MCP_API_KEY`` value in ``x-api-key`` (503 if unset).
    """
    telemetry = get_telemetry_service()
    overall = telemetry.get_overall_stats()
    endpoints = telemetry.get_endpoint_stats()
    features = telemetry.get_feature_usage()

    # Provider health — fetch latest status snapshot
    try:
        aggregator = await get_pricing_aggregator()
        _, provider_statuses = await aggregator.get_all_pricing_async()
        providers = [
            {
                "provider_name": ps.provider_name,
                "is_available": ps.is_available,
                "models_count": ps.models_count,
                "error_message": ps.error_message,
            }
            for ps in provider_statuses
        ]
    except Exception:
        providers = []

    # Deployment metadata
    deploy_meta = deployment_manager.get_deployment_metadata()
    uptime_seconds = deployment_manager.get_uptime_seconds()

    # Normalise uptime_since to a plain string
    uptime_since = overall.get("uptime_since")
    if hasattr(uptime_since, "isoformat"):
        uptime_since = uptime_since.isoformat()

    return {
        "version": settings.app_version,
        "overall": {**overall, "uptime_since": uptime_since},
        "endpoints": endpoints,
        "providers": providers,
        "features": features,
        "deployment": {
            "version": deploy_meta.version if deploy_meta else None,
            "uptime_seconds": uptime_seconds,
        },
    }


@app.get("/admin/rate-limits", tags=["Admin"])
async def admin_rate_limits():
    """
    Current rate-limit consumer snapshot.

    Returns the number of tracked IPs and the top consumers ranked by
    requests in the last 60 seconds.  Operator-only: requires the ``MCP_API_KEY``
    value in ``x-api-key`` (503 if unset).
    """
    now = time.time()
    window = 60.0
    async with _rate_limit_lock:
        consumers = []
        for ip, bucket in _rate_limit_store.items():
            recent = sum(1 for t in bucket if now - t < window)
            if recent > 0:
                consumers.append({"ip": ip, "requests_last_minute": recent})

    consumers.sort(key=lambda c: c["requests_last_minute"], reverse=True)
    return {
        "tracked_ips": len(consumers),
        "limit_per_minute": settings.rate_limit_per_minute,
        "top_consumers": consumers[:20],
        "snapshot_at": datetime.now(UTC).isoformat(),
    }


@app.get("/admin/customers", tags=["Admin"])
async def admin_customers():
    """
    List all billing customers for the admin dashboard.

    Returns customer count by tier, plus a full customer list (api_key
    masked to first 8 chars).  Requires a valid ``x-api-key`` header.
    """
    try:
        billing = get_billing_service()
        customers = await billing.get_all_customers()
    except RuntimeError:
        customers = []

    tier_counts: Dict[str, int] = {}
    rows = []
    for c in customers:
        tier_counts[c.tier] = tier_counts.get(c.tier, 0) + 1
        rows.append({
            "id": c.id,
            "email": c.email,
            "tier": c.tier,
            "effective_tier": _effective_tier(c, settings.billing_past_due_grace_days),
            "billing_status": getattr(c, "billing_status", "active") or "active",
            "org_id": c.org_id,
            "api_key_preview": (c.api_key_prefix or "") + "...",
            "stripe_customer_id": c.stripe_customer_id,
            "created_at": datetime.fromtimestamp(c.created_at, tz=UTC).isoformat(),
        })

    return {
        "total": len(rows),
        "by_tier": tier_counts,
        "customers": rows,
        "snapshot_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Orchestrator core (v1.83.0) — every route is operator-only via _OPERATOR_ONLY_PREFIXES;
# the three executor routes also accept ORCHESTRATOR_INTAKE_KEY (see the middleware).
# ---------------------------------------------------------------------------

def _orchestrator() -> OrchestratorService:
    try:
        return get_orchestrator_service()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Orchestrator is not initialized")


def _orchestrator_error(exc: OrchestratorError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content={"error": exc.code, "message": exc.message})


@app.post("/orchestrator/events", tags=["Orchestrator"])
async def orchestrator_intake_event(body: OrchestratorEventIn, request: Request):
    """
    Idempotent intake of one inbound item (GitHub event, schedule slot, ...).

    Routing uses only the trigger class, the event type and owner-controlled signals;
    `data.title` / `data.body` are stored redacted and truncated as data and never
    interpreted. A repeated `dedup_key` attaches to the existing run (`duplicate: true`)
    and causes no new action. Operator key or `ORCHESTRATOR_INTAKE_KEY`.
    """
    svc = _orchestrator()
    try:
        return await svc.intake_event(body.model_dump(), actor_kind=_orchestrator_caller_kind(request))
    except OrchestratorError as exc:
        return _orchestrator_error(exc)


@app.post("/orchestrator/runs", tags=["Orchestrator"])
async def orchestrator_owner_run(body: OwnerRunIn):
    """
    Owner starts a workflow directly (owner_request trigger class; `support_triage` records
    the customer_message class). `request_id` is the idempotency key. Operator key only.
    """
    svc = _orchestrator()
    try:
        return await svc.intake_owner_request(body.workflow_id, body.request_id, body.inputs, mode=body.mode)
    except OrchestratorError as exc:
        return _orchestrator_error(exc)


@app.get("/orchestrator/runs", tags=["Orchestrator"])
async def orchestrator_list_runs(
    status: Optional[str] = Query(None, max_length=30),
    workflow_id: Optional[str] = Query(None, max_length=60),
    trigger_class: Optional[str] = Query(None, max_length=30),
    since: Optional[str] = Query(None, description="unix or ISO-8601"),
    until: Optional[str] = Query(None, description="unix or ISO-8601"),
    limit: int = Query(200, ge=1, le=10_000),
    format: str = Query("json", pattern="^(csv|json)$"),
):
    """The run log (operator-only): filterable, exportable as CSV (formula prefixes neutralised)."""
    svc = _orchestrator()
    rows = await svc.store.list_runs(
        status=status, workflow_id=workflow_id, trigger_class=trigger_class,
        since=_parse_receipt_time(since, "since"), until=_parse_receipt_time(until, "until"), limit=limit,
    )
    if format == "csv":
        return Response(content=svc.store.to_csv(rows, _ORCH_RUN_COLUMNS), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="orchestrator_runs.csv"'})
    return {"count": len(rows), "runs": rows}


@app.get("/orchestrator/runs/{run_id}", tags=["Orchestrator"])
async def orchestrator_get_run(run_id: str):
    """One run with its steps, approvals, events and action records (redacted I/O summaries)."""
    svc = _orchestrator()
    try:
        return await svc.describe_run(run_id)
    except OrchestratorError as exc:
        return _orchestrator_error(exc)


@app.get("/orchestrator/runs/{run_id}/actions", tags=["Orchestrator"])
async def orchestrator_pending_actions(run_id: str, executor: Optional[str] = Query(None, max_length=30)):
    """
    Approved external actions waiting for an executor. Empty while the global pause is on
    or the run is not active. Operator key or `ORCHESTRATOR_INTAKE_KEY`.
    """
    svc = _orchestrator()
    try:
        actions = await svc.pending_actions(run_id, executor=executor)
    except OrchestratorError as exc:
        return _orchestrator_error(exc)
    return {"run_id": run_id, "count": len(actions), "actions": actions}


@app.post("/orchestrator/runs/{run_id}/actions/{action_id}/result", tags=["Orchestrator"])
async def orchestrator_action_result(run_id: str, action_id: str, body: ActionResultIn, request: Request):
    """
    Executor reports an action's outcome. Requires the provenance echo (422 otherwise) and
    the dispatched artifact hash (409 and the run parks on a mismatch). Idempotent per action.
    """
    svc = _orchestrator()
    try:
        return await svc.record_action_result(run_id, action_id, body.model_dump(),
                                              actor=_orchestrator_caller_kind(request))
    except OrchestratorError as exc:
        return _orchestrator_error(exc)


@app.get("/orchestrator/approvals", tags=["Orchestrator"])
async def orchestrator_approvals(status: Optional[str] = Query("waiting", max_length=30)):
    """The approval queue (system of record). `status=` filters; empty string lists all."""
    svc = _orchestrator()
    items = await svc.approval_queue(status=status or None)
    return {"count": len(items), "approvals": items}


@app.post("/orchestrator/approvals/{approval_id}", tags=["Orchestrator"])
async def orchestrator_decide(approval_id: str, body: ApprovalDecisionIn):
    """approve | reject | edit_approve | done_manually | skip — recorded with the exact artifact hash."""
    svc = _orchestrator()
    try:
        return await svc.decide_approval(approval_id, body.decision, note=body.note, artifact=body.artifact,
                                         expected_hash=body.artifact_hash)
    except OrchestratorError as exc:
        return _orchestrator_error(exc)


@app.get("/orchestrator/parked", tags=["Orchestrator"])
async def orchestrator_parked():
    """Runs in the 'needs owner' state with their reason and prior outputs."""
    svc = _orchestrator()
    runs = await svc.parked_runs()
    return {"count": len(runs), "runs": runs}


@app.post("/orchestrator/runs/{run_id}/resume", tags=["Orchestrator"])
async def orchestrator_resume(run_id: str):
    svc = _orchestrator()
    try:
        return await svc.resume_run(run_id)
    except OrchestratorError as exc:
        return _orchestrator_error(exc)


@app.post("/orchestrator/runs/{run_id}/close", tags=["Orchestrator"])
async def orchestrator_close(run_id: str, body: Optional[RunCloseIn] = None):
    svc = _orchestrator()
    body = body or RunCloseIn()
    try:
        return await svc.close_run(run_id, note=body.note, result=body.result)
    except OrchestratorError as exc:
        return _orchestrator_error(exc)


@app.get("/orchestrator/settings", tags=["Orchestrator"])
async def orchestrator_get_settings():
    svc = _orchestrator()
    return {"settings": await svc.effective_settings(), "workflows": _orchestrator_workflow_catalogue(svc.registry)}


@app.put("/orchestrator/settings", tags=["Orchestrator"])
async def orchestrator_put_settings(body: SettingsIn):
    """Owner control surface: global_pause, mode defaults, budgets. 422 `specialist_missing` when
    a non-dry_run mode is set on a workflow whose specialist is not built."""
    svc = _orchestrator()
    try:
        return {"settings": await svc.update_settings(body.changes, updated_by="operator")}
    except OrchestratorError as exc:
        return _orchestrator_error(exc)


@app.get("/orchestrator/budget", tags=["Orchestrator"])
async def orchestrator_budget():
    svc = _orchestrator()
    return await svc.budget_view()


@app.get("/orchestrator/workflows", tags=["Orchestrator"])
async def orchestrator_workflows():
    svc = _orchestrator()
    return {"workflows": _orchestrator_workflow_catalogue(svc.registry)}


@app.get("/orchestrator/actions", tags=["Orchestrator"])
async def orchestrator_actions(
    since: Optional[str] = Query(None, description="unix or ISO-8601"),
    run_id: Optional[str] = Query(None, max_length=40),
    limit: int = Query(500, ge=1, le=10_000),
    format: str = Query("json", pattern="^(csv|json)$"),
):
    """Append-only action records (what happened, by whom, with which artifact hash)."""
    svc = _orchestrator()
    rows = await svc.store.list_actions(run_id=run_id, since=_parse_receipt_time(since, "since"), limit=limit)
    if format == "csv":
        return Response(content=svc.store.to_csv(rows, _ORCH_ACTION_COLUMNS), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="orchestrator_actions.csv"'})
    return {"count": len(rows), "actions": rows}


# ---------------------------------------------------------------------------
# Compliance audit (v1.88.0) — operator-only via _OPERATOR_ONLY_PREFIXES ("/audit/"); report-only.
# ---------------------------------------------------------------------------

def _audit() -> AuditService:
    try:
        return get_audit_service()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Compliance audit is not initialized")


def _audit_report_summary(r: Dict[str, Any]) -> Dict[str, Any]:
    return {k: r.get(k) for k in ("report_id", "run_id", "created_at", "trigger_class", "status", "counts",
                                  "numbers", "summary_source")}


@app.post("/audit/run", tags=["Orchestrator"])
async def audit_run(body: Optional[AuditRunIn] = None):
    """
    Start an on-demand compliance audit (owner request, operator key only). Runs the mechanical check
    catalogue, persists one report and, when a check fails, opens one `audit_flag` approval in the
    orchestrator queue. Omit `request_id` to run a fresh audit every time. Report-only: nothing audited
    is changed. 503 when the audit is not initialised.
    """
    svc = _audit()
    request_id = (body.request_id if body else None) or time.strftime("audit-%Y%m%dT%H%M%S", time.gmtime()) \
        + "-" + secrets.token_hex(3)
    try:
        return await svc.run_now(request_id)
    except OrchestratorError as exc:
        return _orchestrator_error(exc)


@app.get("/audit/reports", tags=["Orchestrator"])
async def audit_reports(limit: int = Query(50, ge=1, le=500)):
    """Audit reports, newest first: status, counts, the data-quality numbers and where the summary came from."""
    svc = _audit()
    rows = await svc.store.list_reports(limit=limit)
    return {"count": len(rows), "reports": [_audit_report_summary(r) for r in rows]}


@app.get("/audit/reports/{report_id}", tags=["Orchestrator"])
async def audit_report(report_id: str):
    """One report with every check row (status, severity, evidence, launch-checklist ids) and its summary.
    `latest` is an alias for the newest report."""
    svc = _audit()
    report = await (svc.store.latest_report() if report_id == "latest" else svc.store.get_report(report_id))
    if report is None:
        return JSONResponse(status_code=404, content={"error": "report_not_found", "message": "no such report"})
    return report


# ---------------------------------------------------------------------------
# Document writer (v1.89.0) — operator-only via _OPERATOR_ONLY_PREFIXES ("/documents/"); stores only.
# ---------------------------------------------------------------------------

def _docs() -> DocsService:
    try:
        return get_docs_service()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Document writer is not initialized")


def _doc_summary(r: Dict[str, Any], svc: DocsService) -> Dict[str, Any]:
    out = {k: r.get(k) for k in ("artifact_id", "run_id", "created_at", "doc_type", "kind", "title", "status",
                                 "corrections", "expires_at", "size_bytes", "format")}
    out["llm_mode"] = svc.llm_mode(r)
    return out


@app.get("/documents/types", tags=["Documents"])
async def documents_types():
    """The closed document catalogue: four types with their parameters, defaults, sections and data sources."""
    _docs()
    return {"count": len(describe_types()), "types": describe_types()}


@app.post("/documents/generate", tags=["Documents"])
async def documents_generate(body: DocGenerateIn):
    """
    Generate one document (owner request, operator key only). Gathers the figures from the product's own data,
    drafts the narrative (one bounded model call, or data-only), cross-checks every number against the
    provenance manifest, stores the artefact and waits at the publish checkpoint for the owner. Nothing is
    sent anywhere. 422 for an unknown type or parameter, or a document that fails validation (no artefact).
    """
    svc = _docs()
    try:
        result = await svc.generate(body.doc_type, body.params, body.format, body.data_only, body.premium,
                                    body.request_id)
    except ParamError as exc:
        return JSONResponse(status_code=422, content={"error": "invalid_params", "field": exc.field,
                                                      "message": exc.message})
    except OrchestratorError as exc:
        return _orchestrator_error(exc)
    if result.get("artifact_status") == "failed" or not result.get("artifact_id"):
        return JSONResponse(status_code=422, content={"error": "document_failed", "run_id": result.get("run_id"),
                                                      "message": result.get("failure") or "generation failed"})
    return result


@app.get("/documents/artifacts", tags=["Documents"])
async def documents_artifacts(limit: int = Query(50, ge=1, le=500), status: Optional[str] = Query(None)):
    """Stored artefacts, newest first: type, title, status, corrections and model mode. Never the content."""
    svc = _docs()
    rows = await svc.list_artifacts(limit=limit, status=status)
    return {"count": len(rows), "artifacts": [_doc_summary(r, svc) for r in rows]}


@app.get("/documents/artifacts/{artifact_id}", tags=["Documents"])
async def documents_artifact(artifact_id: str):
    """One artefact's metadata, resolved parameters, outline and provenance manifest (no rendered body)."""
    svc = _docs()
    row = await svc.get_artifact(artifact_id)
    if row is None:
        return JSONResponse(status_code=404, content={"error": "artifact_not_found", "message": "no such artifact"})
    doc = row.get("document") or {}
    return {**_doc_summary(row, svc), "params": row.get("params"), "manifest": row.get("manifest"),
            "llm": row.get("llm"), "error": row.get("error"),
            "outline": [{"id": s["id"], "title": s["title"]} for s in doc.get("sections", [])]}


@app.get("/documents/artifacts/{artifact_id}/download", tags=["Documents"])
async def documents_download(artifact_id: str, format: Literal["html", "md", "markdown"] = Query("html")):
    """Download the artefact as self-contained HTML or Markdown, rendered now with its current status banner
    (a draft carries a DRAFT banner). Allowed in every state that has a document."""
    svc = _docs()
    row = await svc.get_artifact(artifact_id)
    if row is None:
        return JSONResponse(status_code=404, content={"error": "artifact_not_found", "message": "no such artifact"})
    if not row.get("document"):
        return JSONResponse(status_code=404, content={"error": "artifact_has_no_document",
                                                      "message": "generation failed; there is nothing to download"})
    body, media, name = await svc.render(row, "html" if format == "html" else "md")
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------------------
# OpenAI-compatible proxy endpoint
# ---------------------------------------------------------------------------

class _ProxyRequest(ChatCompletionsRequest):
    """POST /v1/chat/completions body. Fields live in src.models.dispatch.ChatCompletionsRequest
    (v1.80.0) so the MCP dispatch tool validates through the same model; this subclass keeps the
    FastAPI/OpenAPI component name."""


# Single source in src/services/dispatch_entry.py (v1.80.0); kept here as an alias because the
# plain proxy uses it and tests import it from src.main.
_infer_task_from_messages = _dispatch_entry.infer_task_from_messages


def _format_routing_recommendation(requested_model: str, result) -> str:
    """Build a human-readable routing recommendation text."""
    if result is None:
        return (
            f"No model found matching your constraints for '{requested_model}'. "
            "Try relaxing cost or quality filters."
        )
    rec = result.recommended
    avg_cost = (rec.cost_per_input_token + rec.cost_per_output_token) / 2 * 1_000_000
    lines = [
        f"Routing recommendation for '{requested_model}':",
        f"  Recommended model: {rec.model_name} ({rec.provider})",
        f"  Quality score: {rec.quality_score if rec.quality_score is not None else 'N/A'}/100",
        f"  Avg cost: ${avg_cost:.4f} / 1M tokens",
        f"  Reason: {result.reason}",
    ]
    if result.alternatives:
        alt_names = ", ".join(a.model_name for a in result.alternatives)
        lines.append(f"  Alternatives: {alt_names}")
    return "\n".join(lines)


def _dispatch_service_key(request: Request) -> Optional[str]:
    """Service API key from x-api-key or Authorization: Bearer (the OpenAI SDK default)."""
    key = request.headers.get(settings.mcp_api_key_header)
    if key:
        return key.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def _http_dispatch_deps() -> _dispatch_entry.DispatchDeps:
    """Dispatch collaborators from this module's globals, read at call time so existing
    patch("src.main.get_dispatch_service") / patch.object(main_module.settings, ...) tests
    keep steering the HTTP path."""
    return _dispatch_entry.DispatchDeps(
        settings=settings,
        get_service=get_dispatch_service,
        get_key_store=get_provider_key_store,
        rate_limit_allow=_rate_limit_allow,
        cleanup_rate_limits=cleanup_stale_rate_limit_entries,
        get_telemetry=get_telemetry_service,
    )


async def _dispatch_chat_completion(req: _ProxyRequest, request: Request):
    """Dispatch mode of POST /v1/chat/completions (v1.77.0): a real provider call + receipt.

    Reached only when the body carries a `dispatch` object. The path stays in
    _unauthenticated_paths so the plain proxy remains public, which is why this
    branch authenticates and rate-limits in-handler. The body-size cap is applied by
    the security middleware (via _public_body_capped_paths) before the body is parsed.

    v1.80.0 — every step between the body and DispatchService lives in
    src/services/dispatch_entry.prepare_and_dispatch, shared with the MCP `dispatch` tool;
    status codes, bodies and their order are unchanged.
    """

    async def _resolve_customer():
        # Service key -> customer (org scope). The global MCP_API_KEY is deliberately not
        # consulted: dispatch writes per-org ledgers and needs an org to write them to.
        service_key = _dispatch_service_key(request)
        if not service_key:
            return None
        try:
            return await get_billing_service().get_customer_by_api_key(service_key)
        except RuntimeError:
            return None

    try:
        out = await _dispatch_entry.prepare_and_dispatch(
            req,
            resolve_customer=_resolve_customer,
            header_resolver=HeaderKeyResolver(request.headers),
            header_currency=request.headers.get("X-Currency"),
            header_country=request.headers.get("X-Country"),
            deps=_http_dispatch_deps(),
            stream=bool(req.stream),
        )
    except DispatchError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.payload)
    if req.stream:
        first, agen = out
        return StreamingResponse(
            _relay_dispatch_stream(first, agen, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return out


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'), default=str)}\n\n"


async def _relay_dispatch_stream(first, agen, request: Request):
    """Translate DispatchService.dispatch_stream() events into OpenAI chat.completion.chunk SSE (v1.78.0).

    Shape (OpenAI's own convention): every delta is forwarded the instant it arrives with
    ``finish_reason: null``; a final choices-bearing chunk with an empty delta carries the
    finish_reason; then one trailing chunk with ``choices: []`` and ``usage`` (what OpenAI's
    ``include_usage`` sends) that additionally carries ``routing_metadata`` and the full
    ``dispatch_receipt``; then ``data: [DONE]``. A failure after content ends the stream with a
    ``data:`` object whose top-level ``error`` member OpenAI SDK stream readers already check (no
    ``[DONE]`` follows). Event text is never logged here.
    """
    meta = first.payload or {}
    chat_id = f"chatcmpl-{uuid.UUID(meta['dispatch_id']).hex[:24]}"
    created, model = meta.get("created"), meta.get("model")
    role_sent = False

    def chunk(choices: list, **extra) -> dict:
        return {"id": chat_id, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": choices, **extra}

    event = first
    try:
        while True:
            if await request.is_disconnected():
                return
            if event.kind == "delta":
                delta = {"content": event.text}
                if not role_sent:
                    delta = {"role": "assistant", **delta}
                    role_sent = True
                yield _sse_data(chunk([{"index": 0, "delta": delta, "finish_reason": None}]))
            elif event.kind == "end":
                p = event.payload
                final_delta = {} if role_sent else {"role": "assistant"}
                yield _sse_data(chunk([{"index": 0, "delta": final_delta, "finish_reason": p["finish_reason"]}]))
                yield _sse_data(chunk(
                    [], usage=p["usage"], routing_metadata=p["routing_metadata"],
                    dispatch_receipt=p["dispatch_receipt"],
                ))
                yield "data: [DONE]\n\n"
                return
            else:  # "error": the stream failed after content was sent
                p = dict(event.payload)
                outcome = p.pop("outcome")
                message = p.pop("message", outcome)
                yield _sse_data({"error": {"message": message, "type": "dispatch_error", "code": outcome,
                                           "outcome": outcome, **p}})
                return
            try:
                event = await agen.__anext__()
            except StopAsyncIteration:
                return
    finally:
        await agen.aclose()


@app.post("/v1/chat/completions", tags=["OpenAI Proxy"])
async def openai_proxy(req: _ProxyRequest, request: Request):
    """
    OpenAI-compatible routing proxy.

    Accepts an OpenAI-format chat completions request and returns a routing
    recommendation in OpenAI response format.  No live LLM call is made.
    Point your OpenAI SDK ``base_url`` here to get intelligent model routing
    without any code changes.

    This endpoint is **unauthenticated** for broad client compatibility.

    **Dispatch mode (v1.77.0)** — add a `dispatch` object to the body (OpenAI SDK:
    `extra_body={"dispatch": {...}}`), your free-tier service key as `x-api-key` or
    `Authorization: Bearer`, and your own provider keys — as `X-Provider-Key-<Provider>`
    headers, or stored once for your org with `POST /dispatch/keys` (v1.79.0; a header key
    always wins) — and the best eligible model is executed for real: the response is a normal
    `chat.completion` with real `usage` plus an additive `dispatch_receipt`
    (chosen model, reason, actual cost, savings, attempts). See `GET /dispatch/providers`
    for what is dispatchable. Without `dispatch` the behaviour is exactly the proxy above.

    **Streaming (v1.78.0)** — add `stream: true` in dispatch mode and the completion arrives
    as OpenAI-format `chat.completion.chunk` SSE: content deltas as they arrive, a final
    empty-delta chunk carrying `finish_reason`, one trailing chunk with `usage`,
    `routing_metadata` and the full `dispatch_receipt`, then `data: [DONE]`. Failover
    happens only before the first delta; a failure after that ends the stream with a
    `data: {"error": ...}` event. Pre-provider refusals are the same JSON errors as without
    `stream`. Without `dispatch`, `stream` is ignored as before.
    """
    if req.dispatch is not None:
        return await _dispatch_chat_completion(req, request)
    import uuid
    from src.services.router import RouterConstraints
    task_type = _infer_task_from_messages(req.messages)
    constraints = RouterConstraints(task_type=task_type)
    try:
        router = get_router()
        result = await router.get_optimal_model(constraints)
    except RuntimeError:
        result = None

    recommendation_text = _format_routing_recommendation(req.model, result)
    recommended_model = result.recommended.model_name if result else req.model

    return {
        "id": f"chatcmpl-routing-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": recommended_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": recommendation_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "routing_metadata": {
            "requested_model": req.model,
            "recommended_model": recommended_model,
            "actual_forwarding": False,
            "task_type_inferred": task_type,
        },
    }


# ---------------------------------------------------------------------------
# Dispatch gateway (v1.77.0) — discoverability + audit export
# ---------------------------------------------------------------------------

@app.get("/dispatch/providers", tags=["Dispatch"])
async def dispatch_providers(
    provider: Optional[str] = Query(None, description="Only this provider's models"),
    dispatchable_only: bool = Query(False, description="Only models that can be dispatched"),
):
    """
    List executable providers and which catalogue models are dispatchable.

    Public. A model is dispatchable when it has a provider-confirmed per-token price
    (drift-withheld, never-priced and retired models are not) and belongs to one of the
    executable providers; everything else is recommend-only and says why. Each executable
    provider row names the `X-Provider-Key-*` header to send its key in, and the
    `/dispatch/keys/<slug>` path to store it for your org instead (v1.79.0). Nothing here
    depends on, or reveals, whether any org has stored keys.
    """
    try:
        svc = get_dispatch_service()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Dispatch service is not initialized")
    return await svc.describe_executable(provider=provider, dispatchable_only=dispatchable_only)


def _parse_receipt_time(value: Optional[str], name: str) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} must be a unix timestamp or ISO-8601 datetime")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


@app.get("/dispatch/receipts", tags=["Dispatch"])
async def dispatch_receipts_export(
    request: Request,
    format: str = Query("json", pattern="^(csv|json)$", description="Output format: 'json' or 'csv'"),
    since: Optional[str] = Query(None, description="Only receipts created at/after this time (unix or ISO-8601)"),
    until: Optional[str] = Query(None, description="Only receipts created at/before this time (unix or ISO-8601)"),
    outcome: Optional[str] = Query(None, pattern="^(success|failed)$", description="Filter by outcome"),
    limit: int = Query(1000, ge=1, le=10_000, description="Max rows to export"),
):
    """
    Export the caller's org's dispatch receipts (metadata only) as JSON or CSV.

    Requires a customer API key: rows are always scoped to that key's org, and the global
    operator key is refused (it has no org). Rows carry ids, model/provider names, outcomes,
    token counts, costs and latencies — never prompts, completions or provider keys.
    """
    customer = getattr(request.state, "customer", None)
    if customer is None:
        return JSONResponse(status_code=403, content={
            "error": "customer_key_required",
            "message": "Receipt export requires a customer API key (receipts are scoped to its org).",
        })
    try:
        store = get_dispatch_receipt_store()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Dispatch receipt store is not initialized")

    since_ts = _parse_receipt_time(since, "since")
    until_ts = _parse_receipt_time(until, "until")
    rows = await store.list_receipts(
        org_id=customer.org_id, since=since_ts, until=until_ts, outcome=outcome, limit=limit,
    )
    base_name = f"dispatch_receipts_{customer.org_id}"
    if format == "csv":
        return Response(
            content=store.to_csv(rows),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{base_name}.csv"'},
        )
    payload = {
        "exported_at": datetime.now(UTC).isoformat(),
        "org_id": customer.org_id,
        "filters": {"since": since, "until": until, "outcome": outcome, "limit": limit},
        "count": len(rows),
        "receipts": rows,
    }
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{base_name}.json"'},
    )


# ---------------------------------------------------------------------------
# Stored provider keys (v1.79.0) — org-scoped, encrypted at rest, never returned
# ---------------------------------------------------------------------------
# Request bodies are parsed by hand rather than declared as pydantic parameters: FastAPI's
# default 422 echoes the offending `input`, which for a missing field is the whole body —
# key included. No response or error below ever contains a key value.

_STORED_KEY_BODY_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {
            "type": "object",
            "required": ["provider", "api_key"],
            "properties": {
                "provider": {"type": "string", "description": "Executable provider name or slug, e.g. 'OpenAI'"},
                "api_key": {"type": "string", "description": "Your provider key (write-only, never returned)"},
            },
        }}},
    },
}

_ROTATE_KEY_BODY_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {
            "type": "object",
            "required": ["api_key"],
            "properties": {"api_key": {"type": "string", "description": "The replacement key (write-only)"}},
        }}},
    },
}


def _stored_keys_context(request: Request):
    """(customer, store) or the JSONResponse refusing the call: 403 without a customer key
    (anonymous or the global operator key), 503 when stored keys are not configured."""
    customer = getattr(request.state, "customer", None)
    if customer is None:
        return None, JSONResponse(status_code=403, content={
            "error": "customer_key_required",
            "message": "Stored provider keys require a customer API key (keys are scoped to its org).",
        })
    try:
        store = get_provider_key_store()
    except RuntimeError:
        store = None
    if store is None or not store.available:
        return None, _stored_keys_unavailable_response()
    return (customer, store), None


def _stored_keys_unavailable_response() -> JSONResponse:
    """Names the operator setting, never its value."""
    return JSONResponse(status_code=503, content={
        "error": "stored_keys_unavailable",
        "message": (
            f"Stored provider keys are not enabled on this server (operator setting {_PROVIDER_KEY_SECRET_ENV}). "
            "Send X-Provider-Key-<Provider> headers instead."
        ),
    })


def _unsupported_provider_response() -> JSONResponse:
    accepted = _accepted_key_providers()
    return JSONResponse(status_code=422, content={
        "error": "unsupported_provider",
        "message": (
            "Stored keys are accepted only for providers dispatch can execute: "
            + ", ".join(a["provider"] for a in accepted) + "."
        ),
        "accepted_providers": accepted,
    })


def _invalid_provider_key_response(exc: InvalidProviderKey) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": "invalid_provider_key", "message": str(exc)})


async def _read_json_object(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return None, JSONResponse(status_code=400, content={
            "error": "invalid_json", "message": "Request body must be a JSON object.",
        })
    return body, None


@app.post("/dispatch/keys", tags=["Dispatch"], status_code=201, openapi_extra=_STORED_KEY_BODY_SCHEMA)
async def stored_provider_key_register(request: Request):
    """
    Store a provider key for your org (v1.79.0).

    Body `{"provider": "OpenAI", "api_key": "..."}`. Requires a customer API key in
    `x-api-key`; the global operator key is refused. One key per provider per org (409 if one
    exists — rotate it with `PUT /dispatch/keys/{provider}`). The key is encrypted at rest and
    never returned: the response carries provider, slug, a last-4 fingerprint and timestamps.
    No provider call is made; a bad key surfaces as `auth_error` on first use.
    """
    ctx, refusal = _stored_keys_context(request)
    if refusal is not None:
        return refusal
    customer, store = ctx
    body, refusal = await _read_json_object(request)
    if refusal is not None:
        return refusal
    try:
        provider = _canonical_key_provider(body.get("provider"))
    except UnsupportedProvider:
        return _unsupported_provider_response()
    try:
        # The store validates the key (single validation layer); its errors are mapped here.
        info = await store.register(customer.org_id, provider, body.get("api_key"))
    except UnsupportedProvider:
        return _unsupported_provider_response()
    except InvalidProviderKey as exc:
        return _invalid_provider_key_response(exc)
    except ProviderKeyExists:
        return JSONResponse(status_code=409, content={
            "error": "provider_key_exists",
            "message": f"A key for {provider} is already stored for this org; use PUT to rotate it.",
            "provider": provider,
        })
    except StoredKeysUnavailable:
        return _stored_keys_unavailable_response()
    return JSONResponse(status_code=201, content=info.as_dict())


@app.get("/dispatch/keys", tags=["Dispatch"])
async def stored_provider_key_list(request: Request):
    """
    List your org's stored provider keys (v1.79.0) — metadata only.

    Each entry: provider, slug, fingerprint (last 4 characters), created_at, rotated_at,
    last_used_at (null until dispatch first uses it) and status (`active`, or
    `needs_reregistration` when the key can no longer be decrypted — rotate it with PUT).
    """
    ctx, refusal = _stored_keys_context(request)
    if refusal is not None:
        return refusal
    customer, store = ctx
    keys = [k.as_dict() for k in await store.list(customer.org_id)]
    return {"keys": keys, "count": len(keys)}


@app.put("/dispatch/keys/{provider}", tags=["Dispatch"], openapi_extra=_ROTATE_KEY_BODY_SCHEMA)
async def stored_provider_key_rotate(provider: str, request: Request):
    """
    Rotate your org's stored key for one provider (v1.79.0). Body `{"api_key": "..."}`.

    `provider` is the slug from `GET /dispatch/providers` (e.g. `openai`, `together`) or the
    display name. 404 when your org stores no key for it. The old value is never used again.
    """
    ctx, refusal = _stored_keys_context(request)
    if refusal is not None:
        return refusal
    customer, store = ctx
    try:
        name = _canonical_key_provider(provider)
    except UnsupportedProvider:
        return _unsupported_provider_response()
    body, refusal = await _read_json_object(request)
    if refusal is not None:
        return refusal
    try:
        info = await store.rotate(customer.org_id, name, body.get("api_key"))
    except UnsupportedProvider:
        return _unsupported_provider_response()
    except InvalidProviderKey as exc:
        return _invalid_provider_key_response(exc)
    except ProviderKeyNotFound:
        return JSONResponse(status_code=404, content={
            "error": "provider_key_not_found",
            "message": f"No key for {name} is stored for this org.",
            "provider": name,
        })
    except StoredKeysUnavailable:
        return _stored_keys_unavailable_response()
    return info.as_dict()


@app.delete("/dispatch/keys/{provider}", tags=["Dispatch"], status_code=204)
async def stored_provider_key_delete(provider: str, request: Request):
    """
    Delete your org's stored key for one provider (v1.79.0). 204 on success, 404 when your org
    stores no key for it. Dispatch stops using it immediately.
    """
    ctx, refusal = _stored_keys_context(request)
    if refusal is not None:
        return refusal
    customer, store = ctx
    try:
        name = _canonical_key_provider(provider)
    except UnsupportedProvider:
        return _unsupported_provider_response()
    try:
        deleted = await store.delete(customer.org_id, name)
    except UnsupportedProvider:
        return _unsupported_provider_response()
    except StoredKeysUnavailable:
        return _stored_keys_unavailable_response()
    if not deleted:
        return JSONResponse(status_code=404, content={
            "error": "provider_key_not_found",
            "message": f"No key for {name} is stored for this org.",
            "provider": name,
        })
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# HTTP MCP transport — JSON-RPC 2.0 over HTTP POST
# Compatible with Claude Desktop (remote URL config), Cursor, and other
# MCP clients that support the HTTP transport.
# ---------------------------------------------------------------------------

_MCP_SERVER_NAME = "LLM Pricing MCP Server"
# Single-sourced from src/__init__.py — MCP clients see the real release version.
_MCP_SERVER_VERSION = __version__
_MCP_PROTOCOL_VERSION = "2024-11-05"

# Lazily initialized ToolManager — shared across requests. Built with the
# same process-wide pricing aggregator singleton REST endpoints use (via
# get_pricing_aggregator()), so an MCP tool call and a REST call within the
# same cache window read from one shared, already-warm pricing cache instead
# of each transport holding its own.
_http_mcp_tool_manager: Optional[ToolManager] = None
_http_mcp_tool_manager_lock = asyncio.Lock()


async def _get_http_mcp_tool_manager() -> ToolManager:
    global _http_mcp_tool_manager
    if _http_mcp_tool_manager is None:
        async with _http_mcp_tool_manager_lock:
            # Double-check in case another coroutine initialized while we waited
            if _http_mcp_tool_manager is None:
                aggregator = await get_pricing_aggregator()
                _http_mcp_tool_manager = ToolManager(aggregator=aggregator)
    return _http_mcp_tool_manager


# Agent-backed MCP tools and the singleton getter that supplies their agent. The hosted /mcp
# ToolManager is separate from the agents' own, so the agent is bound here on first use;
# a getter that raises (LLM key missing) leaves the tool reporting *_unavailable itself.
_HTTP_MCP_AGENT_TOOLS = {"support_chat": lambda: get_support_agent(), "ask_agent": lambda: get_pricing_agent()}


async def _bind_http_mcp_agent(tm: ToolManager, name: str) -> None:
    getter = _HTTP_MCP_AGENT_TOOLS.get(name)
    if getter is None or tm.agent_bound(name):
        return
    try:
        tm.set_agent(name, await getter())
    except Exception as exc:
        logger.warning("MCP %s: agent not available (%s)", name, type(exc).__name__)


def _mcp_ok(request_id, result: Any) -> dict:
    resp: dict = {"jsonrpc": "2.0", "result": result}
    if request_id is not None:
        resp["id"] = request_id
    return resp


def _mcp_err(request_id, code: int, message: str) -> dict:
    resp: dict = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
    if request_id is not None:
        resp["id"] = request_id
    return resp


async def _resolve_mcp_caller(request: Optional[Request]) -> McpCaller:
    """Who is calling POST /mcp (v1.79.1), from the connection's headers.

    Same credential rule as dispatch (_dispatch_service_key: x-api-key, else
    Authorization: Bearer). The global MCP_API_KEY is the operator; a key matching a
    billing customer is that customer; no key, an unknown key, or an uninitialised
    billing service is anonymous. Never raises, never logs, and never puts the key
    in any message — an unknown key is indistinguishable from no key.
    """
    if request is None:
        return ANONYMOUS
    key = _dispatch_service_key(request)
    if not key:
        return ANONYMOUS
    # Compare as UTF-8 bytes: str compare_digest raises TypeError on non-ASCII input
    # (headers are latin-1 decoded), which would turn /mcp into a 500.
    if settings.mcp_api_key and secrets.compare_digest(
        key.encode("utf-8"), settings.mcp_api_key.encode("utf-8")
    ):
        return McpCaller.operator()
    try:
        customer = await get_billing_service().get_customer_by_api_key(key)
    except Exception:
        customer = None  # billing not initialised or lookup failed: anonymous, silently
    if customer is None:
        return ANONYMOUS
    # v1.84.0: the caller's tier is the effective one (free once past_due exceeds the grace period).
    return McpCaller.from_customer(customer, tier=_effective_tier(customer, settings.billing_past_due_grace_days))


_mcp_sessions = McpSessionStore()  # v1.85.0: Mcp-Session-Id -> clientInfo.name (in-memory, best effort)


async def _handle_mcp_request(
    body: dict,
    request: Optional[Request] = None,
    response_headers: Optional[dict] = None,
) -> Optional[dict]:
    """Dispatch a JSON-RPC 2.0 MCP request; returns None for notifications.

    ``request`` (v1.79.1) carries the connection headers used to resolve the caller.
    Resolution is lazy: only a tools/call of a non-PUBLIC tool looks the key up, so
    public tools, initialize and tools/list never touch the billing service. Callers
    passing only ``body`` are anonymous.

    v1.85.0: ``initialize`` mints an ``Mcp-Session-Id`` (written into ``response_headers``
    when given) remembering only ``clientInfo.name``; a later tools/call that echoes the
    header exposes that name to the tools via the ``handshake_client`` ContextVar so they can
    infer the client. An unknown or missing session is never rejected: it just means no client
    was inferred.
    """
    import json as _json

    if body.get("jsonrpc") != "2.0":
        return _mcp_err(body.get("id"), -32600, "Invalid Request: jsonrpc must be '2.0'")

    method = body.get("method")
    if not method:
        return _mcp_err(body.get("id"), -32600, "Invalid Request: method is required")

    request_id = body.get("id")
    params = body.get("params", {})

    if method == "initialize":
        client_info = params.get("clientInfo") if isinstance(params, dict) else None
        client_name = client_info.get("name") if isinstance(client_info, dict) else None
        if response_headers is not None:
            response_headers["Mcp-Session-Id"] = _mcp_sessions.create(
                client_name if isinstance(client_name, str) else None
            )
        return _mcp_ok(request_id, {
            "protocolVersion": _MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": _MCP_SERVER_NAME, "version": _MCP_SERVER_VERSION},
        })

    if method in ("initialized", "notifications/initialized"):
        return None  # notification — no response required

    if method == "tools/list":
        tm = await _get_http_mcp_tool_manager()
        return _mcp_ok(request_id, {"tools": tm.list_tools()})

    if method == "tools/call":
        if not isinstance(params, dict) or "name" not in params:
            return _mcp_err(request_id, -32602, "Invalid params: name is required")
        tm = await _get_http_mcp_tool_manager()
        name = params["name"]
        await _bind_http_mcp_agent(tm, name)
        session_id = request.headers.get("mcp-session-id") if request is not None else None
        handshake_token = handshake_client.set(_mcp_sessions.client_name(session_id))
        try:
            if tm.caller_access(name) == PUBLIC:
                result = await tm.execute_tool(name, params.get("arguments", {}))
            else:
                caller = await _resolve_mcp_caller(request)
                # v1.84.0: the connection's address keys the anonymous support spend bucket (same rule
                # as /support/chat); set for this call only and reset right after.
                token = _support_chat_tool.client_address.set(
                    _client_ip_for_support(request) if request is not None else None)
                try:
                    result = await tm.execute_tool(name, params.get("arguments", {}), caller=caller)
                finally:
                    _support_chat_tool.client_address.reset(token)
        finally:
            handshake_client.reset(handshake_token)
        return _mcp_ok(request_id, {
            "content": [{"type": "text", "text": _json.dumps(result, indent=2)}],
        })

    return _mcp_err(request_id, -32601, f"Method not found: {method}")


@app.get("/mcp", include_in_schema=False)
async def mcp_info():
    """MCP server info — health check and configuration reference."""
    return {
        "name": _MCP_SERVER_NAME,
        "version": _MCP_SERVER_VERSION,
        "protocol": "JSON-RPC 2.0",
        "transport": "HTTP POST",
        "endpoint": "/mcp",
        "protocolVersion": _MCP_PROTOCOL_VERSION,
        "session_header": "Mcp-Session-Id",
        "config_example": {
            "mcpServers": {
                "llm-pricing": {"url": "https://llm-pricing-api.fly.dev/mcp"}
            }
        },
    }


@app.post("/mcp", include_in_schema=False)
async def mcp_http(request: Request):
    """
    HTTP MCP transport endpoint (JSON-RPC 2.0 over POST).

    Supports: initialize, tools/list, tools/call.

    Claude Desktop / Cursor remote config:
        {
          "mcpServers": {
            "llm-pricing": { "url": "https://llm-pricing-api.fly.dev/mcp" }
          }
        }

    Optional service key (v1.79.1): send it as x-api-key or Authorization: Bearer on
    the connection. The 7 org-scoped usage/budget tools need it (they act on the
    key's own org); the four operator-only tools (v1.79.2: list/delete_price_alert,
    list/delete_conversation) need the operator key (MCP_API_KEY); the v1.80.0
    `dispatch` tool needs a customer key (a real gateway call on the org's stored
    provider keys, sharing the per-customer dispatch rate-limit bucket); the other 19
    stay anonymous. Headers are never logged. Bodies over settings.max_body_bytes
    are refused with 413 by the security middleware before this handler runs.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content=_mcp_err(None, -32700, "Parse error: invalid JSON"),
        )

    out_headers: dict = {}
    response = await _handle_mcp_request(body, request, out_headers)

    if response is None:
        return Response(status_code=204)

    return JSONResponse(content=response, headers=out_headers or None)


if __name__ == "__main__":
    import uvicorn

    # Register signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        """Handle termination signals for graceful shutdown."""
        sig_name = signal.Signals(signum).name
        logger.warning(f"Received signal {sig_name} - initiating graceful shutdown")

        # Trigger graceful shutdown
        asyncio.create_task(deployment_manager.initiate_graceful_shutdown(drain_timeout_seconds=30))

    # Register SIGTERM (from orchestrators like K8s) and SIGINT (Ctrl+C)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Server: {settings.server_host}:{settings.server_port}")
    if deployment_manager.environment.deployment_group:
        logger.info(f"Deployment group: {deployment_manager.environment.deployment_group} (blue-green mode)")

    uvicorn.run(
        "main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug
    )
