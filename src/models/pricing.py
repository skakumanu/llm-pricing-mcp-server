"""Pydantic models for pricing data validation."""
from pydantic import BaseModel, Field, ConfigDict, computed_field
from typing import Optional, List
from datetime import datetime, UTC


class TokenVolumePrice(BaseModel):
    """Price breakdown for a specific token volume."""
    
    input_cost: float = Field(..., description="Cost for input tokens in USD")
    output_cost: float = Field(..., description="Cost for output tokens in USD")
    total_cost: float = Field(..., description="Total cost (50/50 input/output split) in USD")


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
    # Use case information
    use_cases: Optional[List[str]] = Field(None, description="List of ideal use cases for this model")
    strengths: Optional[List[str]] = Field(None, description="Key strengths of this model")
    best_for: Optional[str] = Field(None, description="Quick summary of what this model is best for")
    
    @computed_field
    @property
    def cost_at_10k_tokens(self) -> TokenVolumePrice:
        """Calculate cost for 10,000 tokens (small volume)."""
        input_cost = (self.cost_per_input_token / 1000) * 10000
        output_cost = (self.cost_per_output_token / 1000) * 10000
        total_cost = (input_cost + output_cost) / 2  # 50/50 split
        return TokenVolumePrice(
            input_cost=round(input_cost, 4),
            output_cost=round(output_cost, 4),
            total_cost=round(total_cost, 4)
        )
    
    @computed_field
    @property
    def cost_at_100k_tokens(self) -> TokenVolumePrice:
        """Calculate cost for 100,000 tokens (medium volume)."""
        input_cost = (self.cost_per_input_token / 1000) * 100000
        output_cost = (self.cost_per_output_token / 1000) * 100000
        total_cost = (input_cost + output_cost) / 2  # 50/50 split
        return TokenVolumePrice(
            input_cost=round(input_cost, 4),
            output_cost=round(output_cost, 4),
            total_cost=round(total_cost, 4)
        )
    
    @computed_field
    @property
    def cost_at_1m_tokens(self) -> TokenVolumePrice:
        """Calculate cost for 1,000,000 tokens (large volume)."""
        input_cost = (self.cost_per_input_token / 1000) * 1000000
        output_cost = (self.cost_per_output_token / 1000) * 1000000
        total_cost = (input_cost + output_cost) / 2  # 50/50 split
        return TokenVolumePrice(
            input_cost=round(input_cost, 2),
            output_cost=round(output_cost, 2),
            total_cost=round(total_cost, 2)
        )
    
    @computed_field
    @property
    def estimated_time_1m_tokens(self) -> Optional[float]:
        """Estimate time to process 1M tokens based on throughput (in seconds)."""
        if self.throughput and self.throughput > 0:
            return round(1000000 / self.throughput, 2)
        return None


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


class EndpointInfo(BaseModel):
    """Endpoint information with method and description."""
    
    path: str = Field(..., description="Endpoint path")
    method: str = Field(..., description="HTTP method (GET, POST, etc.)")
    description: str = Field(..., description="Brief description of endpoint")
    

class ServerInfo(BaseModel):
    """Server information model."""
    
    name: str = Field(..., description="Server name")
    version: str = Field(..., description="Server version")
    description: str = Field(..., description="Server description")
    endpoints: List[EndpointInfo] = Field(..., description="Available API endpoints with methods")
    sample_models: List[str] = Field(default_factory=list, description="Sample model names for testing")
    quick_start_guide: str = Field(default="Visit /docs for interactive API documentation", description="Quick start guidance")


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


class BatchCostEstimateRequest(BaseModel):
    """Request model for batch cost estimation (compare multiple models)."""
    
    model_names: List[str] = Field(..., description="List of LLM model names to compare")
    input_tokens: int = Field(..., ge=0, description="Number of input tokens")
    output_tokens: int = Field(..., ge=0, description="Number of output tokens")


class ModelCostComparison(BaseModel):
    """Cost comparison for a single model."""
    
    model_name: str = Field(..., description="Name of the LLM model")
    provider: str = Field(..., description="Provider of the model")
    input_cost: float = Field(..., description="Cost for input tokens in USD")
    output_cost: float = Field(..., description="Cost for output tokens in USD")
    total_cost: float = Field(..., description="Total cost in USD")
    cost_per_1m_tokens: float = Field(..., description="Estimated cost per 1M tokens (average of input/output)")
    is_available: bool = Field(default=True, description="Whether the model pricing was found")
    error_message: Optional[str] = Field(None, description="Error message if model not found")


class BatchCostEstimateResponse(BaseModel):
    """Response model for batch cost estimation endpoint."""
    
    input_tokens: int = Field(..., description="Number of input tokens used for comparison")
    output_tokens: int = Field(..., description="Number of output tokens used for comparison")
    models: List[ModelCostComparison] = Field(..., description="Cost comparison for each model")
    cheapest_model: Optional[str] = Field(None, description="Name of the cheapest model")
    most_expensive_model: Optional[str] = Field(None, description="Name of the most expensive model")
    cost_range: Optional[dict] = Field(None, description="Min and max costs across all models")
    currency: str = Field(default="USD", description="Currency for costs")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Calculation timestamp")


class PerformanceMetrics(BaseModel):
    """Performance metrics for an LLM model."""
    
    model_config = ConfigDict(protected_namespaces=())
    
    model_name: str = Field(..., description="Name of the LLM model")
    provider: str = Field(..., description="Provider of the model")
    throughput: Optional[float] = Field(None, description="Tokens per second throughput")
    latency_ms: Optional[float] = Field(None, description="Average latency in milliseconds")
    context_window: Optional[int] = Field(None, description="Maximum context window size")
    cost_per_input_token: float = Field(..., description="Cost per input token in USD")
    cost_per_output_token: float = Field(..., description="Cost per output token in USD")
    performance_score: Optional[float] = Field(None, description="Calculated performance score (throughput/cost ratio)")
    value_score: Optional[float] = Field(None, description="Value score (context_window/cost ratio)")


class PerformanceResponse(BaseModel):
    """Response model for performance comparison endpoint."""
    
    models: List[PerformanceMetrics] = Field(..., description="Performance metrics for each model")
    total_models: int = Field(..., description="Total number of models")
    best_throughput: Optional[str] = Field(None, description="Model with best throughput")
    lowest_latency: Optional[str] = Field(None, description="Model with lowest latency")
    largest_context: Optional[str] = Field(None, description="Model with largest context window")
    best_value: Optional[str] = Field(None, description="Model with best value score")
    provider_status: List[ProviderStatusInfo] = Field(default_factory=list, description="Status of each provider")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Response timestamp")


class ModelUseCase(BaseModel):
    """Use case information for a specific LLM model."""
    
    model_config = ConfigDict(protected_namespaces=())
    
    model_name: str = Field(..., description="Name of the LLM model")
    provider: str = Field(..., description="Provider of the model")
    best_for: str = Field(..., description="Quick summary of what this model is best for")
    use_cases: List[str] = Field(..., description="List of ideal use cases")
    strengths: List[str] = Field(..., description="Key strengths of this model")
    context_window: Optional[int] = Field(None, description="Maximum context window size")
    cost_tier: str = Field(..., description="Cost tier: low, medium, high")


class UseCaseResponse(BaseModel):
    """Response model for use cases endpoint."""
    
    models: List[ModelUseCase] = Field(..., description="Use case information for each model")
    total_models: int = Field(..., description="Total number of models")
    providers: List[str] = Field(..., description="List of providers included")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Response timestamp")


class EndpointMetricResponse(BaseModel):
    """Metrics for a single endpoint."""
    
    model_config = ConfigDict(protected_namespaces=())
    
    endpoint: str = Field(..., description="Endpoint path and method (method path)")
    path: str = Field(..., description="Request path")
    method: str = Field(..., description="HTTP method")
    call_count: int = Field(..., description="Total number of calls to this endpoint")
    error_count: int = Field(..., description="Number of failed requests")
    success_rate: float = Field(..., description="Success rate as percentage")
    avg_response_time_ms: float = Field(..., description="Average response time in milliseconds")
    min_response_time_ms: float = Field(..., description="Minimum response time in milliseconds")
    max_response_time_ms: float = Field(..., description="Maximum response time in milliseconds")
    first_called: Optional[str] = Field(None, description="ISO timestamp of first call")
    last_called: Optional[str] = Field(None, description="ISO timestamp of last call")


class ProviderAdoptionResponse(BaseModel):
    """Adoption metrics for a provider."""
    
    provider_name: str = Field(..., description="Name of the provider")
    model_requests: int = Field(..., description="Total number of model requests for this provider")
    unique_models_requested: int = Field(..., description="Number of unique models requested")
    total_cost_estimated: float = Field(..., description="Total estimated cost (USD)")
    last_requested: Optional[str] = Field(None, description="ISO timestamp of last request")


class FeatureUsageResponse(BaseModel):
    """Usage metrics for a feature."""
    
    feature_name: str = Field(..., description="Name of the feature")
    usage_count: int = Field(..., description="Total number of times feature was used")
    last_used: Optional[str] = Field(None, description="ISO timestamp of last usage")


class ClientLocationStats(BaseModel):
    """Statistics about client locations."""
    
    country: str = Field(..., description="Country name")
    country_code: str = Field(..., description="ISO country code")
    request_count: int = Field(..., description="Number of requests from this country")
    unique_clients: int = Field(..., description="Number of unique IP addresses from this country")


class BrowserStats(BaseModel):
    """Statistics about browsers used by clients."""
    
    browser_name: str = Field(..., description="Browser name (e.g., Chrome, Firefox)")
    request_count: int = Field(..., description="Number of requests from this browser")
    unique_clients: int = Field(..., description="Number of unique clients using this browser")


class ClientInfo(BaseModel):
    """Information about a client request."""
    
    ip_address: str = Field(..., description="Client IP address")
    browser: Optional[str] = Field(None, description="Browser name")
    browser_version: Optional[str] = Field(None, description="Browser version")
    os: Optional[str] = Field(None, description="Operating system")
    os_version: Optional[str] = Field(None, description="Operating system version")
    device_type: Optional[str] = Field(None, description="Device type (desktop, mobile, tablet)")
    country: Optional[str] = Field(None, description="Country name")
    country_code: Optional[str] = Field(None, description="ISO country code")
    city: Optional[str] = Field(None, description="City name")
    latitude: Optional[float] = Field(None, description="Latitude coordinate")
    longitude: Optional[float] = Field(None, description="Longitude coordinate")


class TelemetryOverallStats(BaseModel):
    """Overall telemetry statistics."""
    
    total_requests: int = Field(..., description="Total API requests since startup")
    total_errors: int = Field(..., description="Total failed requests")
    error_rate: float = Field(..., description="Error rate as percentage")
    total_endpoints: int = Field(..., description="Number of unique endpoints called")
    total_providers_adopted: int = Field(..., description="Number of providers adopted")
    total_features_used: int = Field(..., description="Number of distinct features used")
    avg_response_time_ms: float = Field(..., description="Average response time across all endpoints")
    unique_clients: int = Field(..., description="Number of unique client IP addresses")
    unique_countries: int = Field(..., description="Number of unique countries")
    uptime_since: str = Field(..., description="ISO timestamp when telemetry tracking started")
    timestamp: str = Field(..., description="ISO timestamp of this response")


class TelemetryResponse(BaseModel):
    """Complete telemetry data response."""
    
    overall_stats: TelemetryOverallStats = Field(..., description="Overall statistics")
    endpoints: List[EndpointMetricResponse] = Field(..., description="Per-endpoint metrics")
    provider_adoption: List[ProviderAdoptionResponse] = Field(..., description="Provider adoption metrics")
    features: List[FeatureUsageResponse] = Field(..., description="Feature usage metrics")
    client_locations: List[ClientLocationStats] = Field(default_factory=list, description="Geographic distribution of requests")
    top_browsers: List[BrowserStats] = Field(default_factory=list, description="Top browsers used by clients")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Response timestamp")
