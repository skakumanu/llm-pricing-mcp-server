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

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Optional, Deque, Dict
import asyncio
import time
from src.config.settings import settings
from src.models.pricing import (
    PricingResponse, ServerInfo, EndpointInfo, CostEstimateRequest, CostEstimateResponse,
    BatchCostEstimateRequest, BatchCostEstimateResponse, ModelCostComparison,
    PerformanceResponse, PerformanceMetrics, ModelUseCase, UseCaseResponse, TelemetryResponse,
    EndpointMetricResponse, ProviderAdoptionResponse, FeatureUsageResponse, TelemetryOverallStats,
    ClientLocationStats, BrowserStats, ClientInfo
)
from src.models.deployment import (
    HealthCheckResponse, DeploymentReadiness, DeploymentMetadata, ApiVersionInfo,
    GracefulShutdownRequest, GracefulShutdownStatus
)
from src.services.pricing_aggregator import PricingAggregatorService
from src.services.telemetry import get_telemetry_service
from src.services.deployment import get_deployment_manager
from src.services.geolocation import GeolocationService

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

# Initialize deployment manager for blue-green deployment support
deployment_manager = get_deployment_manager(version=settings.app_version)
logger.info("Deployment manager initialized")

# Security controls
_rate_limit_store: Dict[str, Deque[float]] = defaultdict(deque)
_rate_limit_lock = asyncio.Lock()
_auth_warning_logged = False

_unauthenticated_paths = {
    "/",
    "/health",
    "/health/live",
    "/health/ready",
    "/health/detailed",
    "/docs",
    "/redoc",
    "/openapi.json",
}

# Add deployment middleware for request tracking (needed for graceful shutdown)
@app.middleware("http")
async def deployment_middleware(request: Request, call_next):
    """
    Middleware to track active requests for graceful shutdown support.
    Rejects new requests if graceful shutdown is in progress.
    """
    # Check if we're shutting down
    if deployment_manager.is_shutting_down():
        # Still allow health check endpoints during shutdown
        if request.url.path not in ["/health", "/health/live", "/health/ready", "/health/detailed"]:
            return HTTPException(
                status_code=503,
                detail="Service is shutting down",
            )
    
    # Track request start
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
    if path not in _unauthenticated_paths:
        if settings.mcp_api_key:
            provided_key = request.headers.get(settings.mcp_api_key_header)
            if not provided_key or not secrets.compare_digest(provided_key, settings.mcp_api_key):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
        elif not _auth_warning_logged:
            logger.warning("MCP API key not configured; endpoints are unauthenticated.")
            _auth_warning_logged = True

    if settings.rate_limit_per_minute > 0:
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
        if content_length and int(content_length) > settings.max_body_bytes:
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})
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
    except Exception as e:
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
                description="Get pricing data for all models (optional ?provider=openai|anthropic|google|cohere|mistral filter)"
            ),
            EndpointInfo(
                path="/models",
                method="GET",
                description="List all available model names (optional ?provider=openai|anthropic|google|cohere|mistral filter)"
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
            "4. Visit /docs for interactive testing"
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
    
    best_throughput = max(models_with_throughput, key=lambda x: x.throughput).model_name if models_with_throughput else None
    lowest_latency = min(models_with_latency, key=lambda x: x.latency_ms).model_name if models_with_latency else None
    largest_context = max(models_with_context, key=lambda x: x.context_window).model_name if models_with_context else None
    best_value = max(models_with_value, key=lambda x: x.value_score).model_name if models_with_value else None
    
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
