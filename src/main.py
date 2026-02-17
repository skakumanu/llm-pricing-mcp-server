"""Main FastAPI application for LLM Pricing MCP Server."""
import sys
import logging
from pathlib import Path

# Configure logging first
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("Starting application initialization...")

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Query, HTTPException
from typing import Optional
import asyncio
from src.config.settings import settings
from src.models.pricing import (
    PricingResponse, ServerInfo, EndpointInfo, CostEstimateRequest, CostEstimateResponse,
    BatchCostEstimateRequest, BatchCostEstimateResponse, ModelCostComparison,
    PerformanceResponse, PerformanceMetrics, ModelUseCase, UseCaseResponse
)
from src.services.pricing_aggregator import PricingAggregatorService

logger.info("Imports completed successfully")

# Initialize FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.app_description,
)

logger.info(f"FastAPI app created: {app.title} v{app.version}")

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
    
    return PricingResponse(
        models=models,
        total_models=len(models),
        provider_status=provider_status
    )


@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    
    Returns:
        dict: Server health status
    """
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version
    }


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
    if provider:
        all_models, _ = await pricing_aggregator.get_pricing_by_provider_async(provider)
    else:
        all_models, _ = await pricing_aggregator.get_all_pricing_async()
    
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug
    )
