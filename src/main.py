"""Main FastAPI application for LLM Pricing MCP Server."""
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Query
from typing import Optional
from src.config.settings import settings
from src.models.pricing import PricingResponse, ServerInfo
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug
    )
