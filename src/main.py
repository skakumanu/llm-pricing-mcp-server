"""Main FastAPI application for LLM Pricing MCP Server."""
import sys
import logging
import signal
import secrets
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, deque

UTC = timezone.utc

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Query, HTTPException, Request  # noqa: E402
import csv  # noqa: E402
import json  # noqa: E402
from io import StringIO  # noqa: E402
from fastapi.responses import JSONResponse, Response, StreamingResponse, FileResponse  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from typing import Optional, Deque, Dict  # noqa: E402
import asyncio  # noqa: E402
import time  # noqa: E402
from src.config.settings import settings  # noqa: E402
from src.models.pricing import (  # noqa: E402
    PricingResponse, ServerInfo, EndpointInfo, CostEstimateRequest, CostEstimateResponse,
    BatchCostEstimateRequest, BatchCostEstimateResponse, ModelCostComparison,
    PerformanceResponse, PerformanceMetrics, ModelUseCase, UseCaseResponse, TelemetryResponse,
    EndpointMetricResponse, ProviderAdoptionResponse, FeatureUsageResponse, TelemetryOverallStats,
    ClientLocationStats, BrowserStats,
    PricingHistoryResponse, PricingTrendsResponse,
    PricingAlertRequest, PricingAlertRecord, PricingAlertListResponse,
    ConversationSummary, ConversationListResponse,
)
from src.services.pricing_history import init_pricing_history_service, get_pricing_history_service  # noqa: E402
from src.services.pricing_alerts import init_pricing_alert_service, get_pricing_alert_service  # noqa: E402
from agent.conversation import init_conversation_store, get_conversation_store  # noqa: E402
from src.models.deployment import (  # noqa: E402
    HealthCheckResponse, DeploymentReadiness, DeploymentMetadata, ApiVersionInfo,
    GracefulShutdownRequest, GracefulShutdownStatus
)
from pydantic import BaseModel, Field  # noqa: E402
from src.services.pricing_aggregator import PricingAggregatorService  # noqa: E402
from src.services.telemetry import get_telemetry_service  # noqa: E402
from src.services.deployment import get_deployment_manager  # noqa: E402
from src.services.geolocation import GeolocationService  # noqa: E402
from agent.pricing_agent import PricingAgent  # noqa: E402
from mcp.tools.tool_manager import ToolManager  # noqa: E402

# Configure logging after settings are available
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("Starting application initialization...")
logger.info("Imports completed successfully")

# Initialize FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.app_description,
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
    _widget_dir = _static_dir / "widget"
    if _widget_dir.exists():
        app.mount(
            "/widget",
            StaticFiles(directory=str(_widget_dir), html=True),
            name="widget_static",
        )
    # Admin page served directly (not as a static mount) so /admin/stats
    # and /admin/rate-limits API routes are not shadowed by StaticFiles.

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "x-api-key"],
)

# Initialize deployment manager for blue-green deployment support
deployment_manager = get_deployment_manager(version=settings.app_version)
logger.info("Deployment manager initialized")

# Security controls
_rate_limit_store: Dict[str, Deque[float]] = defaultdict(deque)
_rate_limit_lock = asyncio.Lock()
_auth_warning_logged = False
_last_rate_limit_cleanup = time.time()


async def cleanup_stale_rate_limit_entries():
    """Periodically remove IP entries with no recent requests to prevent memory leak."""
    global _last_rate_limit_cleanup
    now = time.time()
    if now - _last_rate_limit_cleanup > 3600:  # Cleanup every hour
        async with _rate_limit_lock:
            stale_threshold = now - 3600
            to_remove = [
                ip for ip, bucket in _rate_limit_store.items()
                if not bucket or bucket[-1] < stale_threshold
            ]
            for ip in to_remove:
                del _rate_limit_store[ip]
            if to_remove:
                logger.debug("Rate limit cleanup: removed %d stale IP entries", len(to_remove))
        _last_rate_limit_cleanup = now

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
}

_sensitive_paths = {
    "/telemetry",
    "/deployment/shutdown",
    "/deployment/shutdown/status",
}

# Add deployment middleware for request tracking (needed for graceful shutdown)


@app.middleware("http")
async def deployment_middleware(request: Request, call_next):
    """
    Middleware to track active requests for graceful shutdown support.
    Rejects new requests if graceful shutdown is in progress.
    """
    if deployment_manager.is_shutting_down():
        if request.url.path not in ["/health", "/health/live", "/health/ready", "/health/detailed"]:
            return HTTPException(
                status_code=503,
                detail="Service is shutting down",
            )

    try:
        await deployment_manager.track_request_start()
    except RuntimeError as e:
        return HTTPException(status_code=503, detail=str(e))

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
    if path.startswith("/chat") or path.startswith("/history") or path.startswith("/trends") or path.startswith("/conversations") or path.startswith("/calculator") or path.startswith("/compare") or path.startswith("/widget") or path == "/admin" or path == "/pricing/public" or request.method == "OPTIONS":
        return await call_next(request)
    if path in _sensitive_paths:
        if not settings.mcp_api_key:
            return JSONResponse(
                status_code=503,
                content={"detail": "Authentication not configured"},
            )
        provided_key = request.headers.get(settings.mcp_api_key_header)
        if not provided_key or not secrets.compare_digest(provided_key, settings.mcp_api_key):
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
    elif path not in _unauthenticated_paths:
        if settings.mcp_api_key:
            provided_key = request.headers.get(settings.mcp_api_key_header)
            if not provided_key or not secrets.compare_digest(provided_key, settings.mcp_api_key):
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
        elif not _auth_warning_logged:
            logger.warning("MCP API key not configured; endpoints are unauthenticated.")
            _auth_warning_logged = True

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
        now = time.time()
        window_start = now - 60
        async with _rate_limit_lock:
            bucket = _rate_limit_store[client_ip]
            while bucket and bucket[0] < window_start:
                bucket.popleft()
            if len(bucket) >= settings.rate_limit_per_minute:
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
            bucket.append(now)

    if request.method in {"POST", "PUT", "PATCH"}:
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

    # Get geolocation asynchronously (cached)
    try:
        geo_info = await GeolocationService.get_geolocation(client_ip)
        country = geo_info.get("country") if geo_info else None
        country_code = geo_info.get("country_code") if geo_info else None
    except Exception as e:
        logger.debug("Failed to get geolocation: %s", e)
        country = None
        country_code = None

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

# Global pricing aggregator instance (lazy initialized)
pricing_aggregator: Optional[PricingAggregatorService] = None
_aggregator_lock = asyncio.Lock()


async def get_pricing_aggregator() -> PricingAggregatorService:
    """
    Lazy-load the pricing aggregator on first use.
    Uses a lock to ensure thread-safe initialization.
    """
    global pricing_aggregator

    if pricing_aggregator is None:
        logger.info("First request - initializing pricing aggregator...")
        async with _aggregator_lock:
            # Double-check in case another coroutine initialized while we waited
            if pricing_aggregator is None:
                pricing_aggregator = PricingAggregatorService()
                logger.info("Pricing aggregator initialized successfully")

    return pricing_aggregator

logger.info("Application initialization complete")

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
                    from src.models.deployment import DeploymentStatus
                    _tool_manager = ToolManager()
                    _pricing_agent = PricingAgent(tool_manager=_tool_manager)
                    await _pricing_agent.initialize()
                    _tool_manager.set_pricing_agent(_pricing_agent)
                    # Surface agent and RAG health to the deployment health system
                    deployment_manager.register_service_health(
                        "agent_service", DeploymentStatus.HEALTHY
                    )
                    deployment_manager.register_service_health(
                        "rag_pipeline", DeploymentStatus.HEALTHY
                    )
                    deployment_manager.set_component_ready("agent_initialized", True)
                    logger.info("PricingAgent initialized successfully")
                except ValueError:
                    # Missing API key — report degraded; server remains up for other endpoints
                    from src.models.deployment import DeploymentStatus
                    deployment_manager.register_service_health(
                        "agent_service", DeploymentStatus.DEGRADED,
                        error_message="API key not configured"
                    )
                    deployment_manager.set_component_ready("agent_initialized", False)
                    raise

    return _pricing_agent


# ---------------------------------------------------------------------------
# Pricing history — background snapshot loop + startup
# ---------------------------------------------------------------------------

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
    await init_pricing_history_service(settings.pricing_history_db_path)
    logger.info("Pricing history service initialized at %s", settings.pricing_history_db_path)
    await init_pricing_alert_service(settings.pricing_history_db_path)
    logger.info("Pricing alert service initialized")
    await init_conversation_store(settings.conversation_db_path, settings.agent_max_history_turns)
    logger.info(
        "Conversation store initialized (%s)",
        settings.conversation_db_path or "in-memory",
    )
    asyncio.create_task(_pricing_snapshot_loop())
    logger.info(
        "Pricing snapshot loop started (interval=%dh)", settings.pricing_snapshot_interval_hours
    )


@app.get("/", response_model=ServerInfo)
async def root():
    """
    Root endpoint providing server information.

    Returns:
        ServerInfo: Information about the server and available endpoints with usage guidance
    """
    return ServerInfo(
        name=settings.app_name,
        version=settings.app_version,
        description=settings.app_description,
        endpoints=[
            EndpointInfo(
                path="/",
                method="GET",
                description="Server information and API overview"
            ),
            EndpointInfo(
                path="/pricing",
                method="GET",
                description=(
                    "Get pricing data for all models "
                    "(optional ?provider=openai|anthropic|google|cohere|mistral filter)"
                )
            ),
            EndpointInfo(
                path="/models",
                method="GET",
                description=(
                    "List all available model names "
                    "(optional ?provider=openai|anthropic|google|cohere|mistral filter)"
                )
            ),
            EndpointInfo(
                path="/cost-estimate",
                method="POST",
                description="Estimate cost for a single model (requires JSON body)"
            ),
            EndpointInfo(
                path="/cost-estimate/batch",
                method="POST",
                description="Compare costs across multiple models (requires JSON body)"
            ),
            EndpointInfo(
                path="/performance",
                method="GET",
                description="Get performance metrics for models (optional filters)"
            ),
            EndpointInfo(
                path="/health",
                method="GET",
                description="Health check endpoint"
            ),
            EndpointInfo(
                path="/docs",
                method="GET",
                description="Interactive API documentation (Swagger UI)"
            ),
            EndpointInfo(
                path="/redoc",
                method="GET",
                description="Alternative API documentation (ReDoc)"
            ),
            EndpointInfo(
                path="/use-cases",
                method="GET",
                description="Get recommended use cases for each LLM model"
            ),
            EndpointInfo(
                path="/telemetry",
                method="GET",
                description="Get telemetry data including endpoint usage, provider adoption, and feature usage"
            ),
            EndpointInfo(
                path="/agent/chat",
                method="POST",
                description=(
                    "Natural language interface: ask questions about LLM pricing "
                    "and get AI-sourced answers"
                )
            ),
        ],
        sample_models=[
            "gpt-4", "gpt-4-turbo", "gpt-3.5-turbo",
            "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307",
            "gemini-1.5-pro", "gemini-1.5-flash",
            "command-r-plus", "command-r",
            "mistral-large-latest", "mistral-small-latest"
        ],
        quick_start_guide=(
            "1. GET /models to see all available models | "
            "2. GET /pricing to see pricing data | "
            "3. POST /cost-estimate with JSON body to calculate costs | "
            "4. POST /agent/chat with JSON body to ask natural language questions | "
            "5. Visit /docs for interactive testing"
        )
    )


@app.get("/models")
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


@app.get("/pricing", response_model=PricingResponse)
async def get_pricing(
    provider: Optional[str] = Query(
        None,
        description="Filter by provider (e.g., 'openai', 'anthropic', 'google', 'cohere', 'mistral')"
    )
):
    """
    Get aggregated pricing data from multiple LLM providers.

    This endpoint fetches real-time pricing data from all configured providers
    asynchronously. If a provider is unavailable, partial data is returned with
    status information about each provider.

    Supported providers: OpenAI, Anthropic, Google, Cohere, Mistral AI

    Args:
        provider: Optional provider filter

    Returns:
        PricingResponse: Aggregated pricing data with metrics and provider status
    """
    aggregator = await get_pricing_aggregator()
    if provider:
        models, provider_status = await aggregator.get_pricing_by_provider_async(provider)
    else:
        models, provider_status = await aggregator.get_all_pricing_async()

    # Track provider usage
    telemetry = get_telemetry_service()
    telemetry.track_feature_usage("get_pricing")
    for model in models:
        # Estimate cost per 1M tokens (rough estimate using average tokens)
        estimated_cost = (
            (model.cost_per_input_token + model.cost_per_output_token) / 2 * 1_000_000
        )
        telemetry.track_provider_usage(model.provider, model.model_name, estimated_cost)

    return PricingResponse(
        models=models,
        total_models=len(models),
        provider_status=provider_status
    )


@app.get("/health")
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


@app.get("/health/detailed", response_model=HealthCheckResponse)
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


@app.get("/health/ready", response_model=DeploymentReadiness)
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


@app.get("/health/live")
async def health_live():
    """
    Kubernetes livenessProbe endpoint.

    Returns alive=true if the process should continue running.
    Orchestrators will restart the container if this returns false.

    Returns:
        dict: Alive status
    """
    return await deployment_manager.get_liveness_check()


@app.get("/deployment/metadata", response_model=DeploymentMetadata)
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


@app.get("/deployment/info")
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


@app.post("/deployment/shutdown", response_model=GracefulShutdownStatus)
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


@app.get("/deployment/shutdown/status", response_model=GracefulShutdownStatus)
async def get_shutdown_status():
    """
    Get current graceful shutdown status.

    Returns:
        GracefulShutdownStatus: Current shutdown state, active requests count, timeout
    """
    return await deployment_manager.get_shutdown_status()


@app.get("/api/versions", response_model=ApiVersionInfo)
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
        migration_guide_url="https://github.com/skakumanu/llm-pricing-mcp-server/docs/MIGRATION.md",
    )


@app.post("/cost-estimate", response_model=CostEstimateResponse)
async def estimate_cost(request: CostEstimateRequest):
    """
    Estimate the cost for a specific model based on token usage.

    This endpoint calculates the total cost for using a specific LLM model
    given the number of input and output tokens. It fetches the latest pricing
    data for the model and computes the costs.

    Args:
        request: CostEstimateRequest containing model name and token counts

    Returns:
        CostEstimateResponse: Detailed cost breakdown

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
    input_cost = request.input_tokens * model_pricing.cost_per_input_token
    output_cost = request.output_tokens * model_pricing.cost_per_output_token
    total_cost = input_cost + output_cost

    # Track telemetry
    telemetry = get_telemetry_service()
    telemetry.track_feature_usage("cost_estimation")
    telemetry.track_provider_usage(model_pricing.provider, model_pricing.model_name, total_cost)

    return CostEstimateResponse(
        model_name=model_pricing.model_name,
        provider=model_pricing.provider,
        input_tokens=request.input_tokens,
        output_tokens=request.output_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=total_cost,
        currency=model_pricing.currency
    )


@app.post("/cost-estimate/batch", response_model=BatchCostEstimateResponse)
async def estimate_cost_batch(request: BatchCostEstimateRequest):
    """
    Compare cost estimates across multiple models.

    This endpoint calculates and compares costs for multiple LLM models
    given the same token usage. Useful for finding the most cost-effective
    model for your use case.

    Args:
        request: BatchCostEstimateRequest with model names and token counts

    Returns:
        BatchCostEstimateResponse: Cost comparison across all requested models
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
        input_cost = request.input_tokens * model_pricing.cost_per_input_token
        output_cost = request.output_tokens * model_pricing.cost_per_output_token
        total_cost = input_cost + output_cost

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
            is_available=True
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

    return BatchCostEstimateResponse(
        input_tokens=request.input_tokens,
        output_tokens=request.output_tokens,
        models=comparisons,
        cheapest_model=cheapest,
        most_expensive_model=most_expensive,
        cost_range=cost_range
    )


@app.get("/performance", response_model=PerformanceResponse)
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
            value_score=value_score
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
        provider_status=provider_status
    )


@app.get("/use-cases", response_model=UseCaseResponse)
async def get_use_cases(
    provider: Optional[str] = Query(
        None,
        description="Filter by provider (e.g., 'openai', 'anthropic', 'google', 'cohere', 'mistral')"
    )
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
        UseCaseResponse: Use case information for each model
    """
    aggregator = await get_pricing_aggregator()
    if provider:
        all_models, _ = await aggregator.get_pricing_by_provider_async(provider)
    else:
        all_models, _ = await aggregator.get_all_pricing_async()

    # Determine cost tier based on token costs (costs are per token)
    def get_cost_tier(input_cost: float, output_cost: float) -> str:
        avg_cost = (input_cost + output_cost) / 2
        if avg_cost < 0.00001:
            return "ultra-low"
        elif avg_cost < 0.0001:
            return "low"
        elif avg_cost < 0.001:
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
                cost_tier=get_cost_tier(model.cost_per_input_token, model.cost_per_output_token)
            )
        )

    # Get unique providers
    providers = sorted(list(set(model.provider for model in use_cases)))

    return UseCaseResponse(
        models=use_cases,
        total_models=len(use_cases),
        providers=providers
    )


@app.get("/telemetry", response_model=TelemetryResponse)
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


@app.post("/agent/chat", response_model=AgentChatResponse)
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
        logger.exception("PricingAgent error")
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


@app.post("/agent/chat/stream")
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
            logger.error("Stream error: %s", exc)
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Internal server error'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Historical pricing endpoints
# ---------------------------------------------------------------------------

@app.get("/pricing/history", response_model=PricingHistoryResponse)
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


@app.get("/pricing/trends", response_model=PricingTrendsResponse)
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


# ---------------------------------------------------------------------------
# Pricing history export endpoint
# ---------------------------------------------------------------------------

@app.get("/pricing/history/export")
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

@app.post("/pricing/alerts", response_model=PricingAlertRecord, status_code=201)
async def create_pricing_alert(request: PricingAlertRequest):
    """
    Register a webhook alert for price changes.

    The webhook URL will receive a `POST` request with a JSON body whenever a
    pricing snapshot detects that a model's input or output price has changed by
    more than `threshold_pct` percent since the previous snapshot taken on the
    same day.  Use `provider` and `model_name` to limit the alert to specific
    models.
    """
    svc = get_pricing_alert_service()
    record = await svc.register(
        url=request.url,
        threshold_pct=request.threshold_pct,
        provider=request.provider,
        model_name=request.model_name,
    )
    return PricingAlertRecord(**record)


@app.get("/pricing/alerts", response_model=PricingAlertListResponse)
async def list_pricing_alerts():
    """Return all registered price-change alert webhooks."""
    svc = get_pricing_alert_service()
    alerts = await svc.list_alerts()
    return PricingAlertListResponse(alerts=alerts, total=len(alerts))


@app.delete("/pricing/alerts/{alert_id}", status_code=204)
async def delete_pricing_alert(alert_id: int):
    """Delete a registered alert by ID."""
    svc = get_pricing_alert_service()
    deleted = await svc.delete(alert_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")


# ---------------------------------------------------------------------------
# Conversation management endpoints
# ---------------------------------------------------------------------------


@app.get("/agent/conversations", response_model=ConversationListResponse)
async def list_conversations():
    """
    List all stored chat conversations with metadata.

    Returns each conversation's ID, last-updated timestamp, turn count, and a
    short preview of the most recent user message.  Conversations are sorted
    newest first.  When the conversation store is in-memory (no
    ``CONVERSATION_DB_PATH`` configured) the list reflects only conversations
    created since the server started.
    """
    store = get_conversation_store()
    conversations = await store.list_conversations()
    items = [ConversationSummary(**c) for c in conversations]
    return ConversationListResponse(conversations=items, total=len(items))


@app.delete("/agent/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str):
    """
    Delete a stored conversation by its ID.

    Removes the conversation from both the in-process cache and the persistent
    SQLite database (if configured).  Returns **204 No Content** on success and
    **404 Not Found** if the conversation does not exist.
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


@app.get("/pricing/alerts/signing-info")
async def webhook_signing_info():
    """
    Return information about webhook payload signing.

    When ``WEBHOOK_SECRET`` is configured, every price-change webhook POST will
    carry an ``X-LLM-Pricing-Signature: sha256=<hex>`` header.  Use the
    ``verify_webhook_signature`` helper (from
    ``src.services.pricing_alerts``) on the receiving end to verify it.

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


@app.get("/pricing/public")
async def get_public_pricing(
    models: Optional[str] = Query(
        None,
        description="Comma-separated model names to include (omit for all models)",
    ),
    provider: Optional[str] = Query(
        None,
        description="Filter by provider name",
    ),
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
        JSON with ``models`` list and ``updated_at`` timestamp.
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
    result.sort(key=lambda x: (x["provider"], x["model_name"]))
    return {"models": result, "total": len(result), "updated_at": datetime.now(UTC).isoformat()}


# ---------------------------------------------------------------------------
# Admin endpoints (require API key via standard middleware)
# ---------------------------------------------------------------------------

@app.get("/admin", include_in_schema=False)
async def admin_index():
    """Serve the admin dashboard HTML page (no auth required for the static page)."""
    html_path = _static_dir / "admin" / "index.html"
    return FileResponse(str(html_path), media_type="text/html")


@app.get("/admin/stats")
async def admin_stats():
    """
    Aggregated server statistics for the admin dashboard.

    Returns overall telemetry, per-endpoint metrics, provider health,
    feature usage counts, deployment uptime, and the server version.
    Requires a valid ``x-api-key`` header when ``MCP_API_KEY`` is configured.
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


@app.get("/admin/rate-limits")
async def admin_rate_limits():
    """
    Current rate-limit consumer snapshot.

    Returns the number of tracked IPs and the top consumers ranked by
    requests in the last 60 seconds.  Requires a valid ``x-api-key`` header.
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
