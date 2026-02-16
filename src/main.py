"""Main FastAPI application for LLM Pricing MCP Server."""
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Query, HTTPException
from typing import Optional
from src.config.settings import settings
from src.models.pricing import (
    PricingResponse, ServerInfo, CostEstimateRequest, CostEstimateResponse,
    BatchCostEstimateRequest, BatchCostEstimateResponse, ModelCostComparison,
    PerformanceResponse, PerformanceMetrics
)
from src.services.pricing_aggregator import PricingAggregatorService

# Initialize FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.app_description,
)

# Initialize pricing aggregator service
pricing_aggregator = PricingAggregatorService()


@app.get("/", response_model=ServerInfo)
async def root():
    """
    Root endpoint providing server information.
    
    Returns:
        ServerInfo: Information about the server and available endpoints
    """
    return ServerInfo(
        name=settings.app_name,
        version=settings.app_version,
        description=settings.app_description,
        endpoints=[
            "/",
            "/pricing",
            "/cost-estimate",
            "/cost-estimate/batch",
            "/performance",
            "/health",
            "/docs",
            "/redoc",
        ]
    )


@app.get("/pricing", response_model=PricingResponse)
async def get_pricing(
    provider: Optional[str] = Query(
        None,
        description="Filter by provider (e.g., 'openai', 'anthropic')"
    )
):
    """
    Get aggregated pricing data from multiple LLM providers.
    
    This endpoint fetches real-time pricing data from all configured providers
    asynchronously. If a provider is unavailable, partial data is returned with
    status information about each provider.
    
    Args:
        provider: Optional provider filter
        
    Returns:
        PricingResponse: Aggregated pricing data with metrics and provider status
    """
    if provider:
        models, provider_status = await pricing_aggregator.get_pricing_by_provider_async(provider)
    else:
        models, provider_status = await pricing_aggregator.get_all_pricing_async()
    
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
    model_pricing = await pricing_aggregator.find_model_pricing(request.model_name)
    
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
    
    # Calculate cost for each model
    for model_name in request.model_names:
        model_pricing = await pricing_aggregator.find_model_pricing(model_name)
        
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
    if provider:
        models, provider_status = await pricing_aggregator.get_pricing_by_provider_async(provider)
    else:
        models, provider_status = await pricing_aggregator.get_all_pricing_async()
    
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug
    )
