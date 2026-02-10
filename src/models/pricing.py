"""Pydantic models for pricing data validation."""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime, UTC


class PricingMetrics(BaseModel):
    """Metrics for a specific LLM model."""
    
    model_config = ConfigDict(protected_namespaces=())
    
    model_name: str = Field(..., description="Name of the LLM model")
    provider: str = Field(..., description="Provider of the model (e.g., OpenAI, Anthropic)")
    cost_per_input_token: float = Field(..., description="Cost per input token in USD")
    cost_per_output_token: float = Field(..., description="Cost per output token in USD")
    throughput: Optional[float] = Field(None, description="Tokens per second throughput")
    latency_ms: Optional[float] = Field(None, description="Average latency in milliseconds")
    context_window: Optional[int] = Field(None, description="Maximum context window size")
    currency: str = Field(default="USD", description="Currency for pricing (default: USD)")
    unit: str = Field(default="per_1k_tokens", description="Unit for pricing (default: per 1k tokens)")
    source: Optional[str] = Field(None, description="Source of the pricing data (e.g., API, documentation)")
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")


class ProviderStatusInfo(BaseModel):
    """Provider availability status information."""
    
    provider_name: str = Field(..., description="Name of the provider")
    is_available: bool = Field(..., description="Whether the provider is currently available")
    error_message: Optional[str] = Field(None, description="Error message if provider is unavailable")
    models_count: int = Field(default=0, description="Number of models returned by this provider")


class PricingResponse(BaseModel):
    """Response model for pricing endpoint."""
    
    models: List[PricingMetrics] = Field(..., description="List of model pricing information")
    total_models: int = Field(..., description="Total number of models returned")
    provider_status: List[ProviderStatusInfo] = Field(default_factory=list, description="Status of each provider")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Response timestamp")


class ServerInfo(BaseModel):
    """Server information model."""
    
    name: str = Field(..., description="Server name")
    version: str = Field(..., description="Server version")
    description: str = Field(..., description="Server description")
    endpoints: List[str] = Field(..., description="Available API endpoints")


class CostEstimateRequest(BaseModel):
    """Request model for cost estimation endpoint."""
    
    model_name: str = Field(..., description="Name of the LLM model")
    input_tokens: int = Field(..., ge=0, description="Number of input tokens")
    output_tokens: int = Field(..., ge=0, description="Number of output tokens")


class CostEstimateResponse(BaseModel):
    """Response model for cost estimation endpoint."""
    
    model_name: str = Field(..., description="Name of the LLM model")
    provider: str = Field(..., description="Provider of the model")
    input_tokens: int = Field(..., description="Number of input tokens")
    output_tokens: int = Field(..., description="Number of output tokens")
    input_cost: float = Field(..., description="Cost for input tokens in USD")
    output_cost: float = Field(..., description="Cost for output tokens in USD")
    total_cost: float = Field(..., description="Total cost in USD")
    currency: str = Field(default="USD", description="Currency for cost")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Calculation timestamp")
