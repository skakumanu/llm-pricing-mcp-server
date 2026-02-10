"""Base provider interface for pricing services."""
from abc import ABC, abstractmethod
from typing import List, Optional
from dataclasses import dataclass
from src.models.pricing import PricingMetrics


@dataclass
class ProviderStatus:
    """Status information for a provider."""
    
    provider_name: str
    is_available: bool
    error_message: Optional[str] = None
    last_updated: Optional[str] = None


class BasePricingProvider(ABC):
    """Abstract base class for pricing providers."""
    
    def __init__(self, provider_name: str):
        """Initialize the provider.
        
        Args:
            provider_name: Name of the provider
        """
        self.provider_name = provider_name
    
    @abstractmethod
    async def fetch_pricing_data(self) -> List[PricingMetrics]:
        """
        Fetch pricing data from the provider.
        
        Returns:
            List of PricingMetrics for the provider's models
            
        Raises:
            Exception: If the provider is unreachable or returns invalid data
        """
        pass
    
    async def get_pricing_with_status(self) -> tuple[List[PricingMetrics], ProviderStatus]:
        """
        Fetch pricing data and return with provider status.
        
        Returns:
            Tuple of (pricing_data, provider_status)
        """
        try:
            pricing_data = await self.fetch_pricing_data()
            status = ProviderStatus(
                provider_name=self.provider_name,
                is_available=True,
                error_message=None
            )
            return pricing_data, status
        except Exception as e:
            status = ProviderStatus(
                provider_name=self.provider_name,
                is_available=False,
                error_message=str(e)
            )
            return [], status
