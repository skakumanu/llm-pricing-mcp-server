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
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")


class PricingResponse(BaseModel):
    """Response model for pricing endpoint."""
    
    models: List[PricingMetrics] = Field(..., description="List of model pricing information")
    total_models: int = Field(..., description="Total number of models returned")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Response timestamp")


class ServerInfo(BaseModel):
    """Server information model."""
    
    name: str = Field(..., description="Server name")
    version: str = Field(..., description="Server version")
    description: str = Field(..., description="Server description")
    endpoints: List[str] = Field(..., description="Available API endpoints")
