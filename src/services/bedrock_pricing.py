"""Service for retrieving Amazon Bedrock model pricing data."""
from typing import List, Optional
import logging
from src.models.pricing import PricingMetrics
from src.services.base_provider import BasePricingProvider
from src.services.data_fetcher import DataFetcher
from src.services.data_sources import PRICING_SOURCES, PERFORMANCE_SOURCES
from src.config.settings import settings

logger = logging.getLogger(__name__)


class BedrockPricingService(BasePricingProvider):
    """Service to fetch and manage Amazon Bedrock model pricing."""
    
    # Amazon Bedrock pricing data (per 1k tokens in USD) - us-east-1 region
    # Source: https://aws.amazon.com/bedrock/pricing/
    STATIC_PRICING = {
        "anthropic.claude-3-5-sonnet-20241022-v2:0": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 200000,
            "use_cases": ["Advanced coding", "Agentic workflows", "Complex analysis", "Enterprise AI"],
            "strengths": ["Best Sonnet on Bedrock", "Computer use", "AWS integration", "Enterprise support"],
            "best_for": "Production AWS workloads requiring Claude 3.5 with enterprise reliability"
        },
        "anthropic.claude-3-5-sonnet-20240620-v1:0": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 200000,
            "use_cases": ["Complex tasks", "Vision + text", "Coding", "Research"],
            "strengths": ["Strong reasoning", "Multimodal", "AWS native", "Reliable"],
            "best_for": "AWS applications needing powerful multimodal Claude capabilities"
        },
        "anthropic.claude-3-opus-20240229-v1:0": {
            "input": 0.015,
            "output": 0.075,
            "context_window": 200000,
            "use_cases": ["Strategic analysis", "Research", "Complex reasoning", "High-stakes decisions"],
            "strengths": ["Highest intelligence", "Deep analysis", "Enterprise-grade", "AWS secure"],
            "best_for": "Mission-critical AWS applications demanding maximum intelligence"
        },
        "anthropic.claude-3-sonnet-20240229-v1:0": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 200000,
            "use_cases": ["Content creation", "Analysis", "Code review", "Customer support"],
            "strengths": ["Balanced", "AWS integration", "Cost-effective", "Reliable"],
            "best_for": "General-purpose AWS workloads with balanced performance needs"
        },
        "anthropic.claude-3-haiku-20240307-v1:0": {
            "input": 0.00025,
            "output": 0.00125,
            "context_window": 200000,
            "use_cases": ["Real-time chat", "High-volume processing", "Quick analysis", "Moderation"],
            "strengths": ["Ultra-fast", "Lowest cost Claude", "Large context", "AWS native"],
            "best_for": "High-throughput AWS applications requiring speed and affordability"
        },
        "meta.llama3-1-405b-instruct-v1:0": {
            "input": 0.00532,
            "output": 0.016,
            "context_window": 128000,
            "use_cases": ["Complex reasoning", "Long documents", "Research", "Enterprise chat"],
            "strengths": ["Largest Llama", "Open source", "AWS managed", "Long context"],
            "best_for": "AWS workloads needing largest open-source model with managed infrastructure"
        },
        "meta.llama3-1-70b-instruct-v1:0": {
            "input": 0.00099,
            "output": 0.00099,
            "context_window": 128000,
            "use_cases": ["General purpose", "Code generation", "Analysis", "Creative work"],
            "strengths": ["Well-balanced", "Cost-effective", "AWS managed", "Long context"],
            "best_for": "Balanced AWS applications requiring open-source flexibility"
        },
        "meta.llama3-1-8b-instruct-v1:0": {
            "input": 0.00022,
            "output": 0.00022,
            "context_window": 128000,
            "use_cases": ["High-volume", "Simple tasks", "Real-time processing", "Edge deployment"],
            "strengths": ["Very affordable", "Fast", "AWS managed", "Scalable"],
            "best_for": "High-volume AWS workloads with cost constraints"
        },
        "mistral.mistral-large-2407-v1:0": {
            "input": 0.003,
            "output": 0.009,
            "context_window": 128000,
            "use_cases": ["Enterprise tasks", "Code generation", "Reasoning", "Multilingual"],
            "strengths": ["Mistral flagship", "128K context", "AWS integration", "Strong reasoning"],
            "best_for": "Enterprise AWS applications needing Mistral's latest capabilities"
        },
        "mistral.mistral-small-2402-v1:0": {
            "input": 0.001,
            "output": 0.003,
            "context_window": 32000,
            "use_cases": ["Cost-effective tasks", "Customer support", "Classification", "Simple reasoning"],
            "strengths": ["Affordable", "Fast", "AWS managed", "Good quality"],
            "best_for": "Budget-conscious AWS workloads with moderate intelligence needs"
        },
        "cohere.command-r-plus-v1:0": {
            "input": 0.003,
            "output": 0.015,
            "context_window": 128000,
            "use_cases": ["Enterprise RAG", "Search", "Document analysis", "Long context"],
            "strengths": ["RAG-optimized", "AWS native", "Tool use", "Citations"],
            "best_for": "AWS RAG applications requiring enterprise-grade retrieval"
        },
        "cohere.command-r-v1:0": {
            "input": 0.0005,
            "output": 0.0015,
            "context_window": 128000,
            "use_cases": ["Cost-effective RAG", "FAQ systems", "Document Q&A", "Search"],
            "strengths": ["Affordable RAG", "Good retrieval", "AWS managed", "Long context"],
            "best_for": "Cost-effective AWS RAG systems with strong retrieval needs"
        },
        "amazon.titan-text-premier-v1:0": {
            "input": 0.0005,
            "output": 0.0015,
            "context_window": 32000,
            "use_cases": ["Enterprise content", "Summarization", "AWS-native tasks", "RAG"],
            "strengths": ["AWS native", "Enterprise support", "RAG-optimized", "Secure"],
            "best_for": "AWS-first applications needing native Amazon AI with full support"
        },
        "amazon.titan-text-express-v1": {
            "input": 0.0002,
            "output": 0.0006,
            "context_window": 8000,
            "use_cases": ["Simple tasks", "High-volume", "Cost optimization", "Text generation"],
            "strengths": ["Very affordable", "Fast", "AWS native", "Simple deployment"],
            "best_for": "High-volume AWS workloads requiring minimal cost"
        },
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Amazon Bedrock pricing service.
        
        Args:
            api_key: Optional AWS credentials (not used for pricing fetch, but for context)
        """
        super().__init__("Amazon Bedrock")
        self.api_key = api_key or getattr(settings, 'aws_access_key', None)
    
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch Amazon Bedrock model pricing data.
        
        This method attempts to fetch live data from:
        1. AWS Bedrock pricing page
        2. AWS pricing API
        
        Falls back to curated static pricing data if live fetch fails.
        
        Returns:
            List of PricingMetrics for Bedrock models
            
        Raises:
            Exception: If unable to fetch or parse pricing data
        """
        try:
            # Get pricing source for Bedrock
            bedrock_source = PRICING_SOURCES.get("Amazon Bedrock")
            
            # Fetch pricing from AWS pricing page (live data)
            live_pricing_data = None
            if bedrock_source and bedrock_source.pricing_url:
                try:
                    live_pricing_data = await DataFetcher.fetch_with_cache(
                        cache_key="bedrock_pricing_web",
                        fetch_func=lambda: DataFetcher.fetch_pricing_from_website(
                            url=bedrock_source.pricing_url
                        ),
                        ttl_seconds=bedrock_source.cache_ttl_seconds,
                        fallback_data=None
                    )
                except Exception as e:
                    logger.warning(f"Failed to fetch live Bedrock pricing: {e}")
            
            # Fetch performance metrics
            performance_data = await self._fetch_performance_metrics()
            
            # Build pricing list
            pricing_list = []
            
            for model_id, pricing_info in self.STATIC_PRICING.items():
                # Try live pricing data first, fall back to static pricing
                if live_pricing_data and model_id in live_pricing_data:
                    pricing_data = live_pricing_data[model_id]
                    input_cost = pricing_data.get("input", 0.0)
                    output_cost = pricing_data.get("output", 0.0)
                    source = "AWS Bedrock Official API"
                else:
                    input_cost = pricing_info["input"] / 1000
                    output_cost = pricing_info["output"] / 1000
                    source = "AWS Bedrock Official Pricing (Cached)"
                
                # Get performance metrics
                metrics = performance_data.get(model_id, {
                    "throughput": 50.0,
                    "latency_ms": 600.0
                })
                
                pricing_list.append(
                    PricingMetrics(
                        model_name=model_id,
                        provider=self.provider_name,
                        cost_per_input_token=input_cost,
                        cost_per_output_token=output_cost,
                        context_window=pricing_info.get("context_window"),
                        currency="USD",
                        unit="per_token",
                        source=source,
                        throughput=metrics.get("throughput", 50.0),
                        latency_ms=metrics.get("latency_ms", 600.0),
                        use_cases=pricing_info.get("use_cases"),
                        strengths=pricing_info.get("strengths"),
                        best_for=pricing_info.get("best_for")
                    )
                )
            
            if not pricing_list:
                raise Exception("No pricing data available for Bedrock models")
            
            return pricing_list
            
        except Exception as e:
            logger.error(f"Error fetching pricing data for {self.provider_name}: {e}")
            # Last resort: return static data
            return self._get_static_pricing()
    
    def _get_static_pricing(self) -> List[PricingMetrics]:
        """Convert static pricing dictionary to PricingMetrics objects."""
        metrics_list = []
        
        for model_id, pricing_info in self.STATIC_PRICING.items():
            try:
                metrics = PricingMetrics(
                    model_name=model_id,
                    provider=self.provider_name,
                    cost_per_input_token=pricing_info["input"] / 1000,
                    cost_per_output_token=pricing_info["output"] / 1000,
                    context_window=pricing_info.get("context_window"),
                    use_cases=pricing_info.get("use_cases", []),
                    strengths=pricing_info.get("strengths", []),
                    best_for=pricing_info.get("best_for"),
                    source=f"{self.provider_name} Official Pricing (Fallback - Static)",
                    throughput=self._estimate_throughput(model_id),
                    latency_ms=self._estimate_latency(model_id),
                    currency="USD",
                    unit="per_token"
                )
                metrics_list.append(metrics)
            except Exception as e:
                logger.error(f"Error creating metrics for model {model_id}: {e}")
                continue
        
        return metrics_list
    
    def _estimate_throughput(self, model_id: str) -> Optional[float]:
        """Estimate throughput based on model size and Bedrock infrastructure."""
        if "haiku" in model_id or "8b" in model_id or "express" in model_id:
            return 80.0  # Faster models
        elif "70b" in model_id or "command-r" in model_id:
            return 50.0  # Medium models
        elif "405b" in model_id or "opus" in model_id or "plus" in model_id:
            return 30.0  # Large models
        return 45.0  # Default
    
    async def _fetch_performance_metrics(self) -> dict:
        """Fetch live performance metrics for Bedrock models.
        
        Returns:
            Dict with model names as keys and {throughput, latency_ms} as values
        """
        try:
            # For Bedrock, we don't have a public performance endpoint
            # Return default estimated metrics
            return {
                model_id: {
                    "latency_ms": self._estimate_latency(model_id),
                    "throughput": self._estimate_throughput(model_id)
                }
                for model_id in self.STATIC_PRICING.keys()
            }
        except Exception as e:
            logger.warning(f"Failed to fetch performance metrics for Bedrock: {e}")
            # Return defaults
            return {
                model_id: {"throughput": 50.0, "latency_ms": 600.0}
                for model_id in self.STATIC_PRICING.keys()
            }
    
    def _estimate_latency(self, model_id: str) -> Optional[float]:
        """Estimate latency based on model size and Bedrock infrastructure."""
        base_latency = 600.0  # Base latency in ms
        
        # Adjust based on model size
        if "haiku" in model_id or "8b" in model_id or "express" in model_id:
            return base_latency * 0.6  # Faster for small models
        elif "405b" in model_id or "opus" in model_id:
            return base_latency * 1.5  # Slower for large models
        
        return base_latency
