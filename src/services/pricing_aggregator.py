"""Service for aggregating pricing data from multiple providers."""
import asyncio
from typing import List, Optional
from src.models.pricing import PricingMetrics, ProviderStatusInfo
from src.services.openai_pricing import OpenAIPricingService
from src.services.anthropic_pricing import AnthropicPricingService
from src.services.google_pricing import GooglePricingService
from src.services.cohere_pricing import CoherePricingService
from src.services.mistral_pricing import MistralPricingService
from src.services.groq_pricing import GroqPricingService
from src.services.together_pricing import TogetherPricingService
from src.services.fireworks_pricing import FireworksPricingService
from src.services.perplexity_pricing import PerplexityPricingService
from src.services.ai21_pricing import AI21PricingService
from src.services.anyscale_pricing import AnyscalePricingService
from src.services.bedrock_pricing import BedrockPricingService


class PricingAggregatorService:
    """Service to aggregate pricing data from multiple LLM providers."""
    
    def __init__(self):
        """Initialize the aggregator service."""
        self.openai_service = OpenAIPricingService()
        self.anthropic_service = AnthropicPricingService()
        self.google_service = GooglePricingService()
        self.cohere_service = CoherePricingService()
        self.mistral_service = MistralPricingService()
        self.groq_service = GroqPricingService()
        self.together_service = TogetherPricingService()
        self.fireworks_service = FireworksPricingService()
        self.perplexity_service = PerplexityPricingService()
        self.ai21_service = AI21PricingService()
        self.anyscale_service = AnyscalePricingService()
        self.bedrock_service = BedrockPricingService()
    
    async def get_all_pricing_async(self) -> tuple[List[PricingMetrics], List[ProviderStatusInfo]]:
        """
        Aggregate pricing data from all providers asynchronously.
        
        This method fetches data from all providers concurrently and handles
        partial failures gracefully. If a provider fails, its data is skipped
        but other providers' data is still returned.
        
        Returns:
            Tuple of (all_pricing_data, provider_statuses)
        """
        # Fetch data from all providers concurrently
        # Using return_exceptions=True to handle individual provider failures gracefully
        tasks = [
            self.openai_service.get_pricing_with_status(),
            self.anthropic_service.get_pricing_with_status(),
            self.google_service.get_pricing_with_status(),
            self.cohere_service.get_pricing_with_status(),
            self.mistral_service.get_pricing_with_status(),
            self.groq_service.get_pricing_with_status(),
            self.together_service.get_pricing_with_status(),
            self.fireworks_service.get_pricing_with_status(),
            self.perplexity_service.get_pricing_with_status(),
            self.ai21_service.get_pricing_with_status(),
            self.anyscale_service.get_pricing_with_status(),
            self.bedrock_service.get_pricing_with_status(),
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_pricing = []
        provider_statuses = []
        
        for result in results:
            # Handle exceptions from individual providers
            if isinstance(result, Exception):
                # Provider failed completely - create error status
                provider_status = ProviderStatusInfo(
                    provider_name="Unknown",
                    is_available=False,
                    error_message=str(result),
                    models_count=0
                )
                provider_statuses.append(provider_status)
                continue
            
            pricing_data, status = result
            
            # Convert ProviderStatus to ProviderStatusInfo
            provider_status = ProviderStatusInfo(
                provider_name=status.provider_name,
                is_available=status.is_available,
                error_message=status.error_message,
                models_count=len(pricing_data)
            )
            provider_statuses.append(provider_status)
            
            # Add pricing data if available
            if pricing_data:
                all_pricing.extend(pricing_data)
        
        return all_pricing, provider_statuses
    
    async def get_pricing_by_provider_async(
        self, provider: str
    ) -> tuple[List[PricingMetrics], List[ProviderStatusInfo]]:
        """
        Get pricing data for a specific provider asynchronously.
        
        Args:
            provider: Provider name (case-insensitive)
            
        Returns:
            Tuple of (pricing_data, provider_statuses)
        """
        provider_lower = provider.lower()
        
        if provider_lower == "openai":
            pricing_data, status = await self.openai_service.get_pricing_with_status()
        elif provider_lower == "anthropic":
            pricing_data, status = await self.anthropic_service.get_pricing_with_status()
        elif provider_lower == "google":
            pricing_data, status = await self.google_service.get_pricing_with_status()
        elif provider_lower == "cohere":
            pricing_data, status = await self.cohere_service.get_pricing_with_status()
        elif provider_lower == "mistral" or provider_lower == "mistral ai":
            pricing_data, status = await self.mistral_service.get_pricing_with_status()
        elif provider_lower == "groq":
            pricing_data, status = await self.groq_service.get_pricing_with_status()
        elif provider_lower == "together" or provider_lower == "together ai":
            pricing_data, status = await self.together_service.get_pricing_with_status()
        elif provider_lower == "fireworks" or provider_lower == "fireworks ai":
            pricing_data, status = await self.fireworks_service.get_pricing_with_status()
        elif provider_lower == "perplexity" or provider_lower == "perplexity ai":
            pricing_data, status = await self.perplexity_service.get_pricing_with_status()
        elif provider_lower == "ai21" or provider_lower == "ai21 labs":
            pricing_data, status = await self.ai21_service.get_pricing_with_status()
        elif provider_lower == "anyscale":
            pricing_data, status = await self.anyscale_service.get_pricing_with_status()
        else:
            return [], []
        
        provider_status = ProviderStatusInfo(
            provider_name=status.provider_name,
            is_available=status.is_available,
            error_message=status.error_message,
            models_count=len(pricing_data)
        )
        
        return pricing_data, [provider_status]
    
    def get_all_pricing(self) -> List[PricingMetrics]:
        """
        Aggregate pricing data from all providers (synchronous for backward compatibility).
        
        Returns:
            Combined list of PricingMetrics from all providers
        """
        all_pricing = []
        
        # Fetch OpenAI pricing
        all_pricing.extend(self.openai_service.get_pricing_data())
        
        # Fetch Anthropic pricing
        all_pricing.extend(self.anthropic_service.get_pricing_data())
        
        # Fetch Google pricing
        all_pricing.extend(self.google_service.get_pricing_data())
        
        # Fetch Cohere pricing
        all_pricing.extend(self.cohere_service.get_pricing_data())
        
        # Fetch Mistral pricing
        all_pricing.extend(self.mistral_service.get_pricing_data())
        
        return all_pricing
    
    def get_pricing_by_provider(self, provider: str) -> List[PricingMetrics]:
        """
        Get pricing data for a specific provider (synchronous for backward compatibility).
        
        Args:
            provider: Provider name (case-insensitive)
            
        Returns:
            List of PricingMetrics for the specified provider
        """
        provider_lower = provider.lower()
        
        if provider_lower == "openai":
            return self.openai_service.get_pricing_data()
        elif provider_lower == "anthropic":
            return self.anthropic_service.get_pricing_data()
        elif provider_lower == "google":
            return self.google_service.get_pricing_data()
        elif provider_lower == "cohere":
            return self.cohere_service.get_pricing_data()
        elif provider_lower == "mistral" or provider_lower == "mistral ai":
            return self.mistral_service.get_pricing_data()
        else:
            return []
    
    async def find_model_pricing(self, model_name: str) -> Optional[PricingMetrics]:
        """
        Find pricing information for a specific model across all providers.
        
        Args:
            model_name: Name of the model (case-insensitive)
            
        Returns:
            PricingMetrics for the model if found, None otherwise
        """
        all_pricing, _ = await self.get_all_pricing_async()
        
        # Search for the model (case-insensitive)
        model_name_lower = model_name.lower()
        for pricing in all_pricing:
            if pricing.model_name.lower() == model_name_lower:
                return pricing
        
        return None
