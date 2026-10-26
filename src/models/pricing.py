"""Pydantic models for pricing data validation."""
from pydantic import BaseModel, Field, ConfigDict, computed_field
from typing import Literal, Optional, List
from datetime import date, datetime, UTC

# A hand-maintained price older than this is flagged stale. Providers change
# per-token rates often enough that a quarter-old number should not be trusted
# silently for budgeting.
PRICE_STALE_AFTER_DAYS = 90


class TokenVolumePrice(BaseModel):
    """Price breakdown for a specific token volume."""

    input_cost: float = Field(..., description="Cost for input tokens in USD")
    output_cost: float = Field(..., description="Cost for output tokens in USD")
    total_cost: float = Field(..., description="Total cost (50/50 input/output split) in USD")


# ---------------------------------------------------------------------------
# v1.76.0 — local-currency presentation. Every field below is ADDITIVE and is
# omitted from the serialised response entirely (not emitted as null) when no
# conversion is active, so a request with no currency/country and no detected
# country produces exactly the pre-v1.76.0 payload. The canonical USD fields
# are never renamed, moved or re-denominated; converted figures live under
# ``local_*`` keys carrying their own ``currency``.
# ---------------------------------------------------------------------------

def _omit_none(v) -> bool:
    return v is None


class CurrencyConversion(BaseModel):
    """How the ``local_*`` figures in a response were derived from USD."""

    currency: str = Field(..., description="ISO-4217 code the local_* figures are denominated in")
    base_currency: str = Field("USD", description="Currency the canonical figures are in (always USD)")
    rate: float = Field(..., description="USD -> currency rate applied (1.0 when currency is USD)")
    rate_as_of: Optional[str] = Field(None, description="ISO date (YYYY-MM-DD) the rate was captured")
    rate_age_days: Optional[int] = Field(None, description="Days since the rate was captured")
    rate_is_stale: bool = Field(False, description="True when the rate is older than 7 days")
    rate_source: Optional[str] = Field(None, description="Where the rate table came from")
    rate_placeholder: bool = Field(
        False, description="True while the vendored table is the authored placeholder, not a refreshed one"
    )
    country: Optional[str] = Field(None, description="ISO-3166 alpha-2 country the request resolved to, if any")
    country_source: Optional[str] = Field(
        None, description="'query' | 'header' | 'platform_header' | 'ip' — how the country was obtained"
    )
    currency_source: str = Field(
        ..., description="'query' | 'header' | 'country' | 'default' — how the currency was chosen"
    )
    fallback_to_usd: bool = Field(
        False, description="True when a country/currency was involved but USD had to be served instead"
    )
    fallback_reason: Optional[str] = Field(
        None, description="'country_not_mapped' | 'rates_unavailable' when fallback_to_usd is true"
    )
    note: str = Field(..., description="Disclaimer: converted amounts are indicative; providers bill in USD")


class LocalVolumePrice(BaseModel):
    """Converted price breakdown for a token volume, in the local currency."""

    input_cost: float = Field(..., description="Cost for input tokens in the local currency")
    output_cost: float = Field(..., description="Cost for output tokens in the local currency")
    total_cost: float = Field(..., description="Total cost (50/50 split) in the local currency")


class LocalPrices(BaseModel):
    """Converted per-token and per-1M prices for one model."""

    currency: str = Field(..., description="ISO-4217 code of these figures")
    cost_per_input_token: float = Field(
        ..., description="Per-token input price, full precision; never 0 by rounding"
    )
    cost_per_output_token: float = Field(
        ..., description="Per-token output price, full precision; never 0 by rounding"
    )
    cost_per_1m_input: float = Field(..., description="Input price per 1M tokens")
    cost_per_1m_output: float = Field(..., description="Output price per 1M tokens")
    cost_at_1m_tokens: LocalVolumePrice = Field(..., description="Cost for 1,000,000 tokens (50/50 split)")


class LocalCost(BaseModel):
    """A converted cost estimate."""

    currency: str = Field(..., description="ISO-4217 code of these figures")
    input_cost: float = Field(..., description="Cost for input tokens in the local currency")
    output_cost: float = Field(..., description="Cost for output tokens in the local currency")
    total_cost: float = Field(..., description="Total cost in the local currency")
    cost_per_1m_tokens: Optional[float] = Field(
        None, exclude_if=_omit_none, description="Blended cost per 1M tokens in the local currency"
    )


class LocalCostRange(BaseModel):
    """Min/max of a batch comparison in the local currency."""

    currency: str = Field(..., description="ISO-4217 code of these figures")
    min: float = Field(..., description="Cheapest total cost in the local currency")
    max: float = Field(..., description="Most expensive total cost in the local currency")


class LocalUseCaseCost(BaseModel):
    """Converted cost figures for a use-case entry (the tier itself stays USD-derived)."""

    currency: str = Field(..., description="ISO-4217 code of these figures")
    avg_cost_per_1m_tokens: float = Field(..., description="Average of input and output per 1M tokens, local currency")
    input_per_1m: float = Field(..., description="Input price per 1M tokens, local currency")
    output_per_1m: float = Field(..., description="Output price per 1M tokens, local currency")
    avg_cost_per_1m_tokens_usd: float = Field(..., description="The same average in USD (what the tier is based on)")


class LocaleResponse(BaseModel):
    """Response for GET /locale."""

    country: Optional[str] = Field(None, description="ISO-3166 alpha-2 country, or null when undetected")
    currency: str = Field(..., description="Currency the request resolved to")
    source: str = Field(..., description="'query' | 'header' | 'platform_header' | 'ip' | 'default'")
    fallback_to_usd: bool = Field(False, description="True when a country was found but its currency is unsupported")
    fallback_reason: Optional[str] = Field(None, description="Why USD was served instead, when fallback_to_usd")
    supported_currencies: List[str] = Field(..., description="The exact set of currencies the API accepts")


class ExchangeRatesResponse(BaseModel):
    """Response for GET /currency/rates."""

    base: str = Field(..., description="Base currency of the table (USD)")
    as_of: str = Field(..., description="ISO date the rates were captured")
    rate_age_days: int = Field(..., description="Days since as_of")
    stale: bool = Field(..., description="True when rate_age_days exceeds stale_after_days")
    stale_after_days: int = Field(..., description="Staleness threshold (7)")
    source: str = Field(..., description="Where the table came from")
    placeholder: bool = Field(..., description="True while the table is the authored placeholder")
    note: str = Field(..., description="Disclaimer: converted amounts are indicative; providers bill in USD")
    rates: dict = Field(..., description="USD -> currency rates for every supported code")
    zero_decimal: List[str] = Field(..., description="Currencies conventionally shown without minor units")


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
    # Quality / value
    quality_score: Optional[float] = Field(None, description="Benchmark quality score 0-100")
    # Capability flags
    supports_vision: bool = Field(False, description="Whether the model supports image/vision input")
    supports_function_calling: bool = Field(False, description="Whether the model supports function/tool calling")
    supports_json_mode: bool = Field(False, description="Whether the model supports structured JSON output mode")
    batch_available: bool = Field(False, description="Whether batch processing API is available for this model")
    is_reasoning_model: bool = Field(False, description="Whether this is a reasoning/thinking model (e.g., o1, R1)")
    # IDE and subscription fields
    pricing_model: str = Field(
        default="per_token",
        description="Pricing model type: 'per_token', 'subscription', or 'hybrid'",
    )
    subscription_monthly_usd: Optional[float] = Field(
        None,
        description="Monthly subscription cost in USD (for subscription-based tools like Copilot, Cursor)",
    )
    supports_inline_completion: bool = Field(
        False,
        description="Whether the model/tool supports real-time inline code completion",
    )
    ide_native: bool = Field(
        False,
        description="Whether this is an IDE-native tool (GitHub Copilot, Cursor, Windsurf, etc.)",
    )
    # Price provenance — when this model's per-token price was last confirmed against
    # the provider's published rates. Prices are maintained by hand, so this is the
    # honest signal for how much to trust a number.
    price_as_of: Optional[str] = Field(
        None,
        description="ISO date (YYYY-MM-DD) when this price was last confirmed against the provider",
    )
    price_confirmed: bool = Field(
        True,
        description=(
            "False when the model is known to exist but its per-token price has not been "
            "confirmed. Such models are listed for discoverability but excluded from cost "
            "ranking and budget math, so an unknown price is never mistaken for a cheap one."
        ),
    )
    retired_on: Optional[str] = Field(
        None,
        description=(
            "ISO date (YYYY-MM-DD) on which the provider shut this model down, per the "
            "reference registry. Set automatically once the date has passed; such a model is "
            "withheld from cost math (price_confirmed=False) and listed only so the retirement "
            "is visible and the weekly audit can remove it."
        ),
    )
    # v1.75.0 — where a usable price came from. ``price_confirmed`` keeps meaning
    # "has a usable price" so every consumer keeps working; this carries the
    # provider-vs-registry distinction in a machine-readable form. A
    # registry-sourced price is eligible for estimates, comparisons and routing,
    # but is never counted as provider-confirmed in get_data_quality.
    price_source_kind: Literal["provider", "registry"] = Field(
        "provider",
        description=(
            "'provider' when the price was confirmed against the provider's own published "
            "rate card; 'registry' when it was taken from the reference price registry "
            "(see registry_key) and has not been hand-verified against the provider."
        ),
    )
    registry_key: Optional[str] = Field(
        None,
        description="The reference registry's key this price was taken from, when price_source_kind is 'registry'",
    )
    # v1.76.0 — present only when a non-default locale is active for the request.
    local_prices: Optional[LocalPrices] = Field(
        None,
        exclude_if=_omit_none,
        description="Converted prices in the requested/detected currency; omitted when none is active",
    )

    @computed_field
    @property
    def price_age_days(self) -> Optional[int]:
        """Days since the price was last confirmed. None when unknown."""
        if not self.price_as_of:
            return None
        try:
            confirmed = date.fromisoformat(self.price_as_of)
        except (ValueError, TypeError):
            return None
        return max(0, (datetime.now(UTC).date() - confirmed).days)

    @computed_field
    @property
    def price_is_stale(self) -> bool:
        """True when the price is older than the staleness threshold, or undated.

        An undated price counts as stale: unknown provenance is not a clean bill
        of health, and callers making budget decisions should see that.
        """
        age = self.price_age_days
        if age is None:
            return True
        return age > PRICE_STALE_AFTER_DAYS

    @computed_field
    @property
    def quality_value_score(self) -> Optional[float]:
        """Quality per dollar: quality_score / average_cost_per_1M_tokens."""
        if self.quality_score is None:
            return None
        cost_1m = (self.cost_per_input_token + self.cost_per_output_token) / 2 * 1_000_000
        return round(self.quality_score / max(cost_1m, 1e-9), 4)

    @computed_field
    @property
    def cost_at_10k_tokens(self) -> TokenVolumePrice:
        """Calculate cost for 10,000 tokens (small volume)."""
        input_cost = self.cost_per_input_token * 10000
        output_cost = self.cost_per_output_token * 10000
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
        input_cost = self.cost_per_input_token * 100000
        output_cost = self.cost_per_output_token * 100000
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
        input_cost = self.cost_per_input_token * 1000000
        output_cost = self.cost_per_output_token * 1000000
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
    currency_conversion: Optional[CurrencyConversion] = Field(
        None, exclude_if=_omit_none, description="Present when models[].local_prices is populated"
    )


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
    quick_start_guide: str = Field(
        default="Visit /docs for interactive API documentation",
        description="Quick start guidance"
    )


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
    local_cost: Optional[LocalCost] = Field(
        None, exclude_if=_omit_none, description="The same estimate in the requested/detected currency"
    )
    currency_conversion: Optional[CurrencyConversion] = Field(
        None, exclude_if=_omit_none, description="Present when local_cost is populated"
    )


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
    local_cost: Optional[LocalCost] = Field(
        None, exclude_if=_omit_none, description="The same costs in the requested/detected currency"
    )


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
    local_cost_range: Optional[LocalCostRange] = Field(
        None, exclude_if=_omit_none, description="cost_range in the requested/detected currency"
    )
    currency_conversion: Optional[CurrencyConversion] = Field(
        None, exclude_if=_omit_none, description="Present when models[].local_cost is populated"
    )


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
    performance_score: Optional[float] = Field(
        None, description="Calculated performance score (throughput/cost ratio)"
    )
    value_score: Optional[float] = Field(None, description="Value score (context_window/cost ratio)")
    quality_score: Optional[float] = Field(None, description="Benchmark quality score 0-100")


class PerformanceResponse(BaseModel):
    """Response model for performance comparison endpoint."""

    models: List[PerformanceMetrics] = Field(..., description="Performance metrics for each model")
    total_models: int = Field(..., description="Total number of models")
    best_throughput: Optional[str] = Field(None, description="Model with best throughput")
    lowest_latency: Optional[str] = Field(None, description="Model with lowest latency")
    largest_context: Optional[str] = Field(None, description="Model with largest context window")
    best_value: Optional[str] = Field(None, description="Model with best value score")
    best_quality_value: Optional[str] = Field(None, description="Model with best quality/cost ratio")
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
    local_cost: Optional[LocalUseCaseCost] = Field(
        None,
        exclude_if=_omit_none,
        description="Cost figures in the requested/detected currency; cost_tier itself stays USD-derived",
    )


class UseCaseResponse(BaseModel):
    """Response model for use cases endpoint."""

    models: List[ModelUseCase] = Field(..., description="Use case information for each model")
    total_models: int = Field(..., description="Total number of models")
    providers: List[str] = Field(..., description="List of providers included")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Response timestamp")
    currency_conversion: Optional[CurrencyConversion] = Field(
        None, exclude_if=_omit_none, description="Present when models[].local_cost is populated"
    )


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


class PricingSnapshotRecord(BaseModel):
    """A single pricing snapshot row."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str = Field(..., description="LLM model name")
    provider: str = Field(..., description="Provider name")
    cost_per_input_token: float = Field(..., description="Input token cost in USD")
    cost_per_output_token: float = Field(..., description="Output token cost in USD")
    captured_at: float = Field(..., description="Unix timestamp of the snapshot")
    subscription_monthly_usd: Optional[float] = Field(None, description="Monthly subscription cost in USD (for subscription-based tools)")


class PricingHistoryResponse(BaseModel):
    """Response for GET /pricing/history."""

    snapshots: List[PricingSnapshotRecord] = Field(..., description="Snapshot rows")
    total: int = Field(..., description="Total matching rows (before limit)")


class PricingTrendRecord(BaseModel):
    """Price trend for a single model over a time period."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str = Field(..., description="LLM model name")
    provider: str = Field(..., description="Provider name")
    input_change_pct: float = Field(..., description="Input price change percentage")
    output_change_pct: float = Field(..., description="Output price change percentage")
    direction: str = Field(..., description="'increased', 'decreased', or 'unchanged'")
    first_seen: float = Field(..., description="Unix timestamp of first snapshot in range")
    last_seen: float = Field(..., description="Unix timestamp of most recent snapshot")
    first_input: float = Field(..., description="Input price at first snapshot")
    last_input: float = Field(..., description="Input price at most recent snapshot")
    first_output: float = Field(..., description="Output price at first snapshot")
    last_output: float = Field(..., description="Output price at most recent snapshot")


class PricingTrendsResponse(BaseModel):
    """Response for GET /pricing/trends."""

    trends: List[PricingTrendRecord] = Field(..., description="Models with largest price changes")
    days: int = Field(..., description="Look-back window in days")


class SubscriptionTrendRecord(BaseModel):
    """Price trend for a single subscription-based tool over a time period."""

    model_config = ConfigDict(protected_namespaces=())

    model_name: str
    provider: str
    subscription_change_pct: float
    direction: str  # 'increased', 'decreased', 'unchanged'
    first_seen: float
    last_seen: float
    first_price: float
    last_price: float


class SubscriptionTrendsResponse(BaseModel):
    """Response for GET /pricing/subscription-trends."""

    trends: List[SubscriptionTrendRecord]
    days: int


class PricingAlertRequest(BaseModel):
    """Request body for POST /pricing/alerts."""

    url: str = Field(..., description="Webhook URL to POST when the alert fires")
    threshold_pct: float = Field(
        5.0, gt=0, description="Minimum absolute % price change that triggers the alert"
    )
    provider: Optional[str] = Field(None, description="Limit to a specific provider (None = all)")
    model_name: Optional[str] = Field(
        None, description="Limit to a specific model name (None = all)"
    )


class PricingAlertRecord(BaseModel):
    """A stored price-change alert."""

    model_config = ConfigDict(protected_namespaces=())

    id: int = Field(..., description="Unique alert ID")
    url: str = Field(..., description="Webhook URL")
    threshold_pct: float = Field(..., description="Trigger threshold in percentage")
    provider: Optional[str] = Field(None, description="Provider filter (None = all)")
    model_name: Optional[str] = Field(None, description="Model filter (None = all)")
    created_at: float = Field(..., description="Unix timestamp when the alert was registered")


class PricingAlertListResponse(BaseModel):
    """Response for GET /pricing/alerts."""

    alerts: List[PricingAlertRecord] = Field(..., description="All registered alerts")
    total: int = Field(..., description="Number of registered alerts")


class BudgetAlertRequest(BaseModel):
    """Request body for POST /usage/alerts."""

    url: str = Field(..., description="Webhook URL to POST when spend crosses threshold_usd")
    threshold_usd: float = Field(..., gt=0, description="Spend level in USD that triggers the alert")
    org_id: Optional[str] = Field(
        None, description="Organisation to monitor (None = total spend across all orgs)"
    )
    period_days: int = Field(
        30, ge=1, le=365, description="Rolling look-back window for the spend check, in days"
    )
    hard_stop: bool = Field(
        False,
        description=(
            "v1.77.0: when true and org_id is set, the dispatch gateway refuses new dispatches "
            "for that org (402 budget_hard_stop) once recorded spend over period_days has reached "
            "threshold_usd. Org-less alerts are notify-only even with this flag."
        ),
    )


class BudgetAlertRecord(BaseModel):
    """A stored budget alert."""

    id: int = Field(..., description="Unique alert ID")
    url: str = Field(..., description="Webhook URL")
    org_id: Optional[str] = Field(None, description="Organisation filter (None = all orgs)")
    threshold_usd: float = Field(..., description="Spend threshold in USD")
    period_days: int = Field(..., description="Rolling look-back window in days")
    created_at: float = Field(..., description="Unix timestamp when the alert was registered")
    last_fired_at: Optional[float] = Field(None, description="Unix timestamp this alert last fired, if ever")
    hard_stop: bool = Field(False, description="Whether this alert also blocks dispatch for its org once reached")


class BudgetAlertListResponse(BaseModel):
    """Response for GET /usage/alerts."""

    alerts: List[BudgetAlertRecord] = Field(..., description="All registered budget alerts")
    total: int = Field(..., description="Number of registered budget alerts")


class RouterRequest(BaseModel):
    """Request body for POST /router/recommend."""

    max_cost_per_1m_tokens: Optional[float] = Field(
        None, description="Maximum acceptable average cost per 1M tokens (USD)"
    )
    min_quality_score: Optional[float] = Field(
        None, ge=0, le=100, description="Minimum required quality score (0-100)"
    )
    min_context_window: Optional[int] = Field(
        None, ge=0, description="Minimum required context window size (tokens)"
    )
    preferred_provider: Optional[str] = Field(
        None, description="Preferred provider name (10% score boost applied)"
    )
    task_type: Optional[str] = Field(
        None,
        description=(
            "Task type hint: 'code' | 'chat' | 'analysis' | 'summarization' | "
            "'code_completion' | 'code_chat' | 'code_refactor' | 'agentic_coding'"
        ),
    )
    prefer_low_latency: bool = Field(
        False,
        description="Prioritise lower-latency models; important for inline code completions",
    )
    exclude_reasoning_models: bool = Field(
        False,
        description="Exclude slow thinking/reasoning models (e.g. o1, o3, R1, claude-3-7-sonnet)",
    )
    ide_context: Optional[str] = Field(
        None,
        description=(
            "IDE context hint — boosts IDE-native tools when set: "
            "'copilot' | 'cursor' | 'windsurf' | 'claude_code' | 'jetbrains' | 'amazon_q'"
        ),
    )
    monthly_budget_usd: Optional[float] = Field(
        None,
        description=(
            "Monthly spend ceiling in USD. Combined with estimated_monthly_requests, "
            "avg_input_tokens, and avg_output_tokens to compute projected monthly cost "
            "and hard-filter models that exceed it. For subscription tools, filters by "
            "subscription_monthly_usd <= monthly_budget_usd."
        ),
    )
    estimated_monthly_requests: int = Field(
        1000,
        ge=1,
        description="Estimated number of API requests per month (used with monthly_budget_usd)",
    )
    avg_input_tokens: int = Field(
        500,
        ge=1,
        description="Average input tokens per request (used with monthly_budget_usd)",
    )
    avg_output_tokens: int = Field(
        200,
        ge=1,
        description="Average output tokens per request (used with monthly_budget_usd)",
    )


class RouterResponse(BaseModel):
    """Response body for POST /router/recommend."""

    recommended: PricingMetrics = Field(..., description="Best matching model")
    score: float = Field(..., description="Composite routing score")
    reason: str = Field(..., description="Human-readable explanation of the recommendation")
    alternatives: List[PricingMetrics] = Field(
        default_factory=list, description="Up to 3 alternative models"
    )
    routing_id: str = Field(..., description="Unique routing decision ID (UUID4) for feedback")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    cached: bool = Field(
        False,
        description=(
            "True if this recommendation was served from the ~5 minute "
            "in-memory recommendation cache rather than freshly computed"
        ),
    )
    currency_conversion: Optional[CurrencyConversion] = Field(
        None,
        exclude_if=_omit_none,
        description="Present when recommended.local_prices / alternatives[].local_prices are populated",
    )


class RouterFeedbackRequest(BaseModel):
    """Request body for POST /router/feedback."""

    routing_id: str = Field(..., description="routing_id from a prior /router/recommend response")
    was_used: bool = Field(..., description="Whether the recommended model was actually used")
    actual_model_used: Optional[str] = Field(
        None, description="Model actually used if different from recommendation"
    )
    notes: Optional[str] = Field(None, description="Free-text notes about the routing decision")


class RouterFeedbackResponse(BaseModel):
    """Response body for POST /router/feedback."""

    routing_id: str = Field(..., description="The routing ID feedback was recorded for")
    recorded: bool = Field(..., description="Whether feedback was recorded successfully")


class SavingsRecord(BaseModel):
    """A single routing savings record."""

    model_config = ConfigDict(protected_namespaces=())

    id: int = Field(..., description="Unique record ID")
    org_id: Optional[str] = Field(None, description="Organisation identifier")
    api_key_tier: Optional[str] = Field(None, description="API key tier")
    requested_at: float = Field(..., description="Unix timestamp of the routing request")
    recommended_model: str = Field(..., description="Recommended model name")
    recommended_provider: str = Field(..., description="Recommended model provider")
    recommended_cost_per_1m: float = Field(..., description="Recommended model cost per 1M tokens")
    baseline_model: Optional[str] = Field(None, description="Most expensive alternative considered")
    baseline_cost_per_1m: Optional[float] = Field(None, description="Baseline model cost per 1M tokens")
    savings_per_1m: Optional[float] = Field(None, description="Cost savings per 1M tokens vs baseline")
    task_type: Optional[str] = Field(None, description="Task type hint provided by caller")
    ide_context: Optional[str] = Field(None, description="IDE context hint used in the routing request")


class SavingsResponse(BaseModel):
    """Response for GET /telemetry/savings."""

    records: List[SavingsRecord] = Field(..., description="Routing savings records")
    total: int = Field(..., description="Total matching records")
    total_savings_per_1m: float = Field(..., description="Sum of savings_per_1m across records")
    org_id: Optional[str] = Field(None, description="Organisation filter applied")
    days: int = Field(..., description="Look-back window in days")
    acceptance_rate: Optional[float] = Field(
        None, description="Fraction of routing decisions where the recommendation was used (0-1)"
    )


class IDEBreakdownRecord(BaseModel):
    """Routing analytics broken down by IDE context."""
    ide_context: Optional[str] = Field(None, description="IDE context value (None = no context provided)")
    total_decisions: int = Field(..., description="Total routing decisions with this ide_context")
    total_savings_per_1m: float = Field(..., description="Sum of savings_per_1m for this ide_context")
    acceptance_rate: Optional[float] = Field(None, description="Fraction of decisions where recommendation was used")


class IDEBreakdownResponse(BaseModel):
    """Response for GET /telemetry/ide-savings."""
    breakdown: List[IDEBreakdownRecord] = Field(..., description="Per-IDE-context analytics")
    days: int = Field(..., description="Look-back window in days")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationSummary(BaseModel):
    """Metadata summary for a single stored conversation."""

    id: str = Field(..., description="Unique conversation ID (UUID)")
    updated_at: float = Field(..., description="Unix timestamp of the last update (0.0 for in-memory)")
    turn_count: int = Field(..., description="Total number of messages (user + assistant turns)")
    preview: Optional[str] = Field(None, description="First 120 chars of the most recent user message")


class ConversationListResponse(BaseModel):
    """Response for GET /agent/conversations."""

    conversations: List[ConversationSummary] = Field(..., description="Stored conversations, newest first")
    total: int = Field(..., description="Total number of stored conversations")


class TelemetryResponse(BaseModel):
    """Complete telemetry data response."""

    overall_stats: TelemetryOverallStats = Field(..., description="Overall statistics")
    endpoints: List[EndpointMetricResponse] = Field(..., description="Per-endpoint metrics")
    provider_adoption: List[ProviderAdoptionResponse] = Field(..., description="Provider adoption metrics")
    features: List[FeatureUsageResponse] = Field(..., description="Feature usage metrics")
    client_locations: List[ClientLocationStats] = Field(
        default_factory=list,
        description="Geographic distribution of requests"
    )
    top_browsers: List[BrowserStats] = Field(
        default_factory=list,
        description="Top browsers used by clients"
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Response timestamp")


class UsageEventRequest(BaseModel):
    """Request model for reporting a single actual LLM usage event."""

    model_name: str = Field(..., description="Name of the LLM model that was called")
    input_tokens: int = Field(..., ge=0, description="Number of input tokens actually used")
    output_tokens: int = Field(..., ge=0, description="Number of output tokens actually used")
    occurred_at: Optional[float] = Field(
        None, description="Unix timestamp when the call happened; defaults to ingestion time"
    )
    request_id: Optional[str] = Field(
        None, description="Idempotency key — resubmitting the same request_id for the same org is a no-op"
    )
    session_id: Optional[str] = Field(
        None,
        description=(
            "Optional session ID to tag this event with, so it can later be grouped and "
            "retrieved via GET /usage/session/{session_id} — e.g. a bounded burst of calls "
            "like a coding-agent run or a chat session"
        ),
    )


class UsageEventResponse(BaseModel):
    """Response for POST /usage."""

    recorded: bool = Field(..., description="Whether a new row was recorded")
    duplicate: bool = Field(..., description="True if this request_id was already recorded for this org")
    model_name: str = Field(..., description="Resolved model name")
    provider: str = Field(..., description="Resolved provider")
    input_tokens: int = Field(..., description="Input tokens as submitted")
    output_tokens: int = Field(..., description="Output tokens as submitted")
    cost_usd: float = Field(..., description="Cost computed server-side from current pricing, in USD")
    org_id: Optional[str] = Field(None, description="Organisation this event was attributed to")
    session_id: Optional[str] = Field(None, description="Session this event was tagged with, if any")


class UsageBatchRequest(BaseModel):
    """Request model for reporting multiple usage events at once."""

    events: List[UsageEventRequest] = Field(..., min_length=1, max_length=500, description="Usage events to record")


class UsageBatchResponse(BaseModel):
    """Response for POST /usage/batch."""

    recorded: int = Field(..., description="Number of new events recorded")
    duplicates: int = Field(..., description="Number of events skipped as duplicates")
    failed: int = Field(..., description="Number of events that failed (e.g. unknown model)")
    errors: List[str] = Field(default_factory=list, description="Error messages for failed events")
    total_cost_usd: float = Field(..., description="Sum of cost_usd across newly recorded events")


class UsageModelBreakdown(BaseModel):
    """Spend breakdown for a single model within a usage summary."""

    model_name: str = Field(..., description="Model name")
    provider: str = Field(..., description="Provider")
    request_count: int = Field(..., description="Number of usage events recorded for this model")
    total_cost_usd: float = Field(..., description="Total cost in USD for this model")


class UsageProviderBreakdown(BaseModel):
    """Spend breakdown for a single provider within a usage summary."""

    provider: str = Field(..., description="Provider")
    request_count: int = Field(..., description="Number of usage events recorded for this provider")
    total_cost_usd: float = Field(..., description="Total cost in USD for this provider")


class UsageSummaryResponse(BaseModel):
    """Response for GET /usage/summary."""

    org_id: Optional[str] = Field(None, description="Organisation filter applied")
    days: int = Field(..., description="Look-back window in days")
    total_requests: int = Field(..., description="Total usage events in the window")
    total_cost_usd: float = Field(..., description="Total actual spend in USD across all recorded events")
    total_input_tokens: int = Field(..., description="Total input tokens across all recorded events")
    total_output_tokens: int = Field(..., description="Total output tokens across all recorded events")
    by_model: List[UsageModelBreakdown] = Field(default_factory=list, description="Spend broken down by model")
    by_provider: List[UsageProviderBreakdown] = Field(
        default_factory=list, description="Spend broken down by provider"
    )
    estimated_request_count: int = Field(
        0, description="Number of usage events in this window whose tokens were text-derived estimates"
    )
    has_estimated_usage: bool = Field(
        False, description="True if any usage event in this window was recorded from an estimate, not exact counts"
    )


class SessionModelBreakdown(BaseModel):
    """Actual usage breakdown for a single model within one session."""

    model_name: str = Field(..., description="Model name")
    provider: str = Field(..., description="Provider")
    request_count: int = Field(..., description="Number of usage events recorded for this model in the session")
    input_tokens: int = Field(..., description="Input tokens for this model in the session")
    output_tokens: int = Field(..., description="Output tokens for this model in the session")
    cost_usd: float = Field(..., description="Actual cost in USD for this model in the session")
    estimated_request_count: int = Field(
        0, description="Of this model's request_count, how many were recorded from text-derived estimates"
    )


class SessionRecommendationCaveat(BaseModel):
    """A caveat attached to a SessionRecommendation, e.g. a quality/cost tradeoff warning."""

    type: str = Field(..., description="Caveat type, e.g. 'quality_tradeoff'")
    message: str = Field(..., description="Plain-language description of the tradeoff")
    quality_score: float = Field(..., description="The recommended model's quality_score")
    quality_threshold: float = Field(..., description="The quality_score threshold that triggered this caveat")


class SessionRecommendation(BaseModel):
    """A model recommendation grounded in one session's actual observed usage."""

    is_optimal: bool = Field(
        ..., description="True if the session already used exactly the model the router would recommend"
    )
    recommended_model: str = Field(..., description="Recommended model name")
    recommended_provider: str = Field(..., description="Recommended model's provider")
    recommended_cost_per_1m_tokens: float = Field(
        ..., description="Recommended model's blended $/1M tokens rate (equal input/output weight)"
    )
    session_actual_cost_per_1m_tokens: float = Field(
        ..., description="This session's actual blended $/1M tokens, from its real token mix and cost"
    )
    estimated_cost_if_recommended_usd: float = Field(
        ..., description="What this session's actual token volumes would have cost on the recommended model"
    )
    savings_usd: float = Field(
        ...,
        description=(
            "total_cost_usd minus estimated_cost_if_recommended_usd. Positive means the "
            "recommended model would have been cheaper; negative means more expensive; 0 "
            "when is_optimal is true."
        ),
    )
    rationale: str = Field(
        ..., description="Plain-language reasoning grounded in this session's request count, tokens, and cost"
    )
    caveats: Optional[List[SessionRecommendationCaveat]] = Field(
        None,
        description=(
            "Present only when a caveat applies, e.g. a quality_tradeoff warning when the "
            "recommended model's quality_score is below the shared quality-tradeoff threshold. "
            "Never present when is_optimal is true."
        ),
    )


class SessionUsageAnalysisResponse(BaseModel):
    """Response for GET /usage/session/{session_id}."""

    success: bool = Field(..., description="False if no usage was recorded for this session_id")
    has_data: bool = Field(..., description="False when session_id has zero recorded usage events")
    session_id: str = Field(..., description="The session_id analyzed")
    error: Optional[str] = Field(None, description="Present when has_data is false")
    total_requests: Optional[int] = Field(None, description="Total usage events recorded for this session")
    total_input_tokens: Optional[int] = Field(None, description="Total input tokens for this session")
    total_output_tokens: Optional[int] = Field(None, description="Total output tokens for this session")
    total_cost_usd: Optional[float] = Field(None, description="Total actual cost in USD for this session")
    by_model: List[SessionModelBreakdown] = Field(
        default_factory=list, description="Per-model breakdown; more than one entry if the session mixed models"
    )
    first_occurred_at: Optional[float] = Field(None, description="Unix timestamp of the earliest event")
    last_occurred_at: Optional[float] = Field(None, description="Unix timestamp of the latest event")
    estimated_request_count: Optional[int] = Field(
        None, description="Of total_requests, how many were recorded from text-derived estimates (e.g. via import_session_usage)"
    )
    has_estimated_usage: Optional[bool] = Field(
        None, description="True if any of this session's usage was recorded from an estimate, not exact counts"
    )
    recommendation: Optional[SessionRecommendation] = Field(
        None, description="Absent when has_data is false or no model could be matched"
    )
