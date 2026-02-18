"""Data sources configuration for fetching live pricing and performance data."""
from dataclasses import dataclass
from typing import Dict, Optional
from enum import Enum


class DataSourceType(Enum):
    """Types of data sources available."""
    API = "api"
    WEB_SCRAPE = "web_scrape"
    STATIC = "static"
    HYBRID = "hybrid"  # API with static fallback


@dataclass
class PricingDataSource:
    """Configuration for a pricing data source."""
    provider: str
    source_type: DataSourceType
    api_endpoint: Optional[str] = None
    pricing_url: Optional[str] = None
    cache_ttl_seconds: int = 3600  # Cache for 1 hour by default
    requires_auth: bool = False
    public_health_check: Optional[str] = None  # Public endpoint for health checks
    

@dataclass
class PerformanceDataSource:
    """Configuration for a performance metrics data source."""
    provider: str
    source_type: DataSourceType
    api_endpoint: Optional[str] = None
    health_check_endpoint: Optional[str] = None
    public_status_page: Optional[str] = None  # Public status page without auth
    cache_ttl_seconds: int = 300  # Cache for 5 minutes
    requires_auth: bool = False


# Pricing data sources for each provider
PRICING_SOURCES = {
    "OpenAI": PricingDataSource(
        provider="OpenAI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.openai.com/v1/models",  # Optional: requires API key
        pricing_url="https://openai.com/api/pricing/",  # Public: no auth needed
        public_health_check="https://status.openai.com/api/v2/status.json",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "Anthropic": PricingDataSource(
        provider="Anthropic",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.anthropic.com/v1/models",  # Optional: requires API key
        pricing_url="https://www.anthropic.com/api",  # Public: no auth needed
        public_health_check="https://www.anthropic.com/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "Google": PricingDataSource(
        provider="Google",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://generativelanguage.googleapis.com/v1beta/models",  # Optional: requires API key
        pricing_url="https://ai.google.dev/pricing",  # Public: no auth needed
        public_health_check="https://status.cloud.google.com/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "Cohere": PricingDataSource(
        provider="Cohere",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.cohere.ai/v1/models",  # Optional: requires API key
        pricing_url="https://cohere.com/pricing",  # Public: no auth needed
        public_health_check="https://status.cohere.com/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "Mistral AI": PricingDataSource(
        provider="Mistral AI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.mistral.ai/v1/models",  # Optional: requires API key
        pricing_url="https://mistral.ai/technology/#pricing",  # Public: no auth needed
        public_health_check="https://status.mistral.ai/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "Groq": PricingDataSource(
        provider="Groq",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.groq.com/openai/v1/models",  # Optional: requires API key
        pricing_url="https://groq.com/pricing/",  # Public: no auth needed
        public_health_check="https://status.groq.com/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "Together AI": PricingDataSource(
        provider="Together AI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.together.xyz/v1/models",  # Optional: requires API key
        pricing_url="https://www.together.ai/pricing",  # Public: no auth needed
        public_health_check="https://status.together.ai/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "Fireworks AI": PricingDataSource(
        provider="Fireworks AI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.fireworks.ai/inference/v1/models",  # Optional: requires API key
        pricing_url="https://fireworks.ai/pricing",  # Public: no auth needed
        public_health_check="https://status.fireworks.ai/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "Perplexity AI": PricingDataSource(
        provider="Perplexity AI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.perplexity.ai/models",  # Optional: requires API key
        pricing_url="https://docs.perplexity.ai/docs/pricing",  # Public: no auth needed
        public_health_check="https://status.perplexity.ai/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "AI21 Labs": PricingDataSource(
        provider="AI21 Labs",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.ai21.com/studio/v1/models",  # Optional: requires API key
        pricing_url="https://www.ai21.com/pricing",  # Public: no auth needed
        public_health_check="https://status.ai21.com/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
    "Anyscale": PricingDataSource(
        provider="Anyscale",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.endpoints.anyscale.com/v1/models",  # Optional: requires API key
        pricing_url="https://www.anyscale.com/pricing",  # Public: no auth needed
        public_health_check="https://status.anyscale.com/",
        requires_auth=False,  # Pricing page is public
        cache_ttl_seconds=7200,
    ),
}

# Performance data sources for each provider
PERFORMANCE_SOURCES = {
    "OpenAI": PerformanceDataSource(
        provider="OpenAI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.openai.com/v1/models",  # Optional: requires API key
        health_check_endpoint="https://status.openai.com/api/v2/status.json",
        public_status_page="https://status.openai.com/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "Anthropic": PerformanceDataSource(
        provider="Anthropic",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.anthropic.com/v1/models",  # Optional: requires API key
        health_check_endpoint="https://www.anthropic.com/api",
        public_status_page="https://www.anthropic.com/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "Google": PerformanceDataSource(
        provider="Google",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://generativelanguage.googleapis.com/v1beta/models",  # Optional: requires API key
        health_check_endpoint="https://www.google.com/",
        public_status_page="https://status.cloud.google.com/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "Cohere": PerformanceDataSource(
        provider="Cohere",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.cohere.ai/v1/models",  # Optional: requires API key
        health_check_endpoint="https://api.cohere.ai/v1/models",
        public_status_page="https://status.cohere.com/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "Mistral AI": PerformanceDataSource(
        provider="Mistral AI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.mistral.ai/v1/models",  # Optional: requires API key
        health_check_endpoint="https://api.mistral.ai/v1/models",
        public_status_page="https://status.mistral.ai/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "Groq": PerformanceDataSource(
        provider="Groq",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.groq.com/openai/v1/models",  # Optional: requires API key
        health_check_endpoint="https://api.groq.com/openai/v1/models",
        public_status_page="https://status.groq.com/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "Together AI": PerformanceDataSource(
        provider="Together AI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.together.xyz/v1/models",  # Optional: requires API key
        health_check_endpoint="https://api.together.xyz/v1/models",
        public_status_page="https://status.together.ai/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "Fireworks AI": PerformanceDataSource(
        provider="Fireworks AI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.fireworks.ai/inference/v1/models",  # Optional: requires API key
        health_check_endpoint="https://api.fireworks.ai/inference/v1/models",
        public_status_page="https://status.fireworks.ai/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "Perplexity AI": PerformanceDataSource(
        provider="Perplexity AI",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.perplexity.ai/models",  # Optional: requires API key
        health_check_endpoint="https://api.perplexity.ai/models",
        public_status_page="https://status.perplexity.ai/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "AI21 Labs": PerformanceDataSource(
        provider="AI21 Labs",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.ai21.com/studio/v1/models",  # Optional: requires API key
        health_check_endpoint="https://api.ai21.com/studio/v1/models",
        public_status_page="https://status.ai21.com/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
    "Anyscale": PerformanceDataSource(
        provider="Anyscale",
        source_type=DataSourceType.HYBRID,
        api_endpoint="https://api.endpoints.anyscale.com/v1/models",  # Optional: requires API key
        health_check_endpoint="https://api.endpoints.anyscale.com/v1/models",
        public_status_page="https://status.anyscale.com/",  # Public: no auth
        cache_ttl_seconds=300,
    ),
}
