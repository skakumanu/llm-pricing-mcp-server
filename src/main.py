"""Main FastAPI application for LLM Pricing MCP Server."""
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Query, HTTPException
from typing import Optional
from src.config.settings import settings
from src.models.pricing import PricingResponse, ServerInfo, CostEstimateRequest, CostEstimateResponse
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug
    )
