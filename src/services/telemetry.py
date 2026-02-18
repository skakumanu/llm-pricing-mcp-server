"""Telemetry service for tracking API usage and adoption metrics."""
import logging
from datetime import datetime, UTC
from typing import Dict, List, Optional, Set
from collections import defaultdict
import threading
from dataclasses import dataclass, asdict, field

logger = logging.getLogger(__name__)


@dataclass
class EndpointMetric:
    """Metrics for a single endpoint."""
    path: str
    method: str
    call_count: int = 0
    error_count: int = 0
    total_response_time_ms: float = 0.0
    min_response_time_ms: float = float('inf')
    max_response_time_ms: float = 0.0
    last_called: Optional[str] = None
    first_called: Optional[str] = None
    
    @property
    def avg_response_time_ms(self) -> float:
        """Calculate average response time."""
        if self.call_count == 0:
            return 0.0
        return round(self.total_response_time_ms / self.call_count, 2)
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate as percentage."""
        if self.call_count == 0:
            return 0.0
        success_count = self.call_count - self.error_count
        return round((success_count / self.call_count) * 100, 2)
    
    def to_dict(self) -> dict:
        """Convert to dictionary, including computed properties."""
        d = asdict(self)
        d['avg_response_time_ms'] = self.avg_response_time_ms
        d['success_rate'] = self.success_rate
        d['min_response_time_ms'] = (
            self.min_response_time_ms 
            if self.min_response_time_ms != float('inf') 
            else 0.0
        )
        return d


@dataclass
class ProviderAdoption:
    """Adoption metrics for a specific provider."""
    provider_name: str
    model_requests: int = 0
    total_cost_estimated: float = 0.0
    last_requested: Optional[str] = None
    unique_models_requested: set = field(default_factory=set)
    
    @property
    def model_count(self) -> int:
        """Return count of unique models requested."""
        return len(self.unique_models_requested)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "provider_name": self.provider_name,
            "model_requests": self.model_requests,
            "total_cost_estimated": round(self.total_cost_estimated, 4),
            "unique_models_requested": self.model_count,
            "last_requested": self.last_requested,
        }


@dataclass
class FeatureUsage:
    """Usage metrics for specific features."""
    feature_name: str
    usage_count: int = 0
    last_used: Optional[str] = None


@dataclass
class ClientLocation:
    """Geographic location data for clients."""
    country: str
    country_code: str
    request_count: int = 0
    unique_clients: Set[str] = field(default_factory=set)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "country": self.country,
            "country_code": self.country_code,
            "request_count": self.request_count,
            "unique_clients": len(self.unique_clients),
        }


@dataclass
class BrowserUsage:
    """Browser usage statistics."""
    browser_name: str
    request_count: int = 0
    unique_clients: Set[str] = field(default_factory=set)
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "browser_name": self.browser_name,
            "request_count": self.request_count,
            "unique_clients": len(self.unique_clients),
        }


class TelemetryService:
    """Service for tracking API usage and adoption metrics."""
    
    def __init__(self):
        """Initialize telemetry service."""
        self.endpoints: Dict[str, EndpointMetric] = {}
        self.providers: Dict[str, ProviderAdoption] = {}
        self.features: Dict[str, FeatureUsage] = {}
        self.client_locations: Dict[str, ClientLocation] = {}  # keyed by country_code
        self.browser_usage: Dict[str, BrowserUsage] = {}  # keyed by browser_name
        self.unique_clients: Set[str] = set()  # unique client IPs
        self.total_requests: int = 0
        self.total_errors: int = 0
        self.start_time: str = datetime.now(UTC).isoformat()
        self._lock = threading.Lock()
        logger.info("Telemetry service initialized")
    
    def track_endpoint_request(
        self,
        path: str,
        method: str,
        response_time_ms: float,
        status_code: int = 200,
        client_ip: Optional[str] = None,
        country: Optional[str] = None,
        country_code: Optional[str] = None,
        browser: Optional[str] = None,
    ) -> None:
        """
        Track an endpoint request with optional client information.
        
        Args:
            path: Request path
            method: HTTP method
            response_time_ms: Response time in milliseconds
            status_code: HTTP status code
            client_ip: Client IP address
            country: Country name
            country_code: ISO country code
            browser: Browser name
        """
        with self._lock:
            endpoint_key = f"{method} {path}"
            
            if endpoint_key not in self.endpoints:
                self.endpoints[endpoint_key] = EndpointMetric(
                    path=path,
                    method=method,
                    first_called=datetime.now(UTC).isoformat()
                )
            
            metric = self.endpoints[endpoint_key]
            metric.call_count += 1
            metric.last_called = datetime.now(UTC).isoformat()
            metric.total_response_time_ms += response_time_ms
            metric.min_response_time_ms = min(metric.min_response_time_ms, response_time_ms)
            metric.max_response_time_ms = max(metric.max_response_time_ms, response_time_ms)
            
            # Track errors (4xx, 5xx)
            if status_code >= 400:
                metric.error_count += 1
                self.total_errors += 1
            
            self.total_requests += 1
            
            # Track client information
            if client_ip:
                self.unique_clients.add(client_ip)
            
            # Track geolocation
            if country_code and country:
                if country_code not in self.client_locations:
                    self.client_locations[country_code] = ClientLocation(
                        country=country,
                        country_code=country_code
                    )
                loc = self.client_locations[country_code]
                loc.request_count += 1
                if client_ip:
                    loc.unique_clients.add(client_ip)
            
            # Track browser usage
            if browser and browser != "Unknown":
                if browser not in self.browser_usage:
                    self.browser_usage[browser] = BrowserUsage(browser_name=browser)
                usage = self.browser_usage[browser]
                usage.request_count += 1
                if client_ip:
                    usage.unique_clients.add(client_ip)
    
    def track_provider_usage(
        self,
        provider_name: str,
        model_name: str,
        estimated_cost: float = 0.0
    ) -> None:
        """
        Track provider adoption and usage.
        
        Args:
            provider_name: Name of the provider
            model_name: Name of the model used
            estimated_cost: Estimated cost for the request
        """
        with self._lock:
            if provider_name not in self.providers:
                self.providers[provider_name] = ProviderAdoption(provider_name=provider_name)
            
            provider = self.providers[provider_name]
            provider.model_requests += 1
            provider.total_cost_estimated += estimated_cost
            provider.unique_models_requested.add(model_name)
            provider.last_requested = datetime.now(UTC).isoformat()
    
    def track_feature_usage(self, feature_name: str) -> None:
        """
        Track usage of specific features.
        
        Args:
            feature_name: Name of the feature used
        """
        with self._lock:
            if feature_name not in self.features:
                self.features[feature_name] = FeatureUsage(feature_name=feature_name)
            
            feature = self.features[feature_name]
            feature.usage_count += 1
            feature.last_used = datetime.now(UTC).isoformat()
    
    def get_endpoint_stats(self) -> List[dict]:
        """Get statistics for all endpoints."""
        with self._lock:
            return [
                {
                    **metric.to_dict(),
                    "endpoint": endpoint_key
                }
                for endpoint_key, metric in sorted(self.endpoints.items())
            ]
    
    def get_provider_adoption(self) -> List[dict]:
        """Get adoption statistics for all providers."""
        with self._lock:
            return [
                provider.to_dict()
                for provider in sorted(
                    self.providers.values(),
                    key=lambda x: x.model_requests,
                    reverse=True
                )
            ]
    
    def get_feature_usage(self) -> List[dict]:
        """Get usage statistics for all features."""
        with self._lock:
            return [
                {
                    "feature_name": feature.feature_name,
                    "usage_count": feature.usage_count,
                    "last_used": feature.last_used
                }
                for feature in sorted(
                    self.features.values(),
                    key=lambda x: x.usage_count,
                    reverse=True
                )
            ]
    
    def get_overall_stats(self) -> dict:
        """Get overall telemetry statistics."""
        with self._lock:
            total_response_time = sum(
                m.total_response_time_ms for m in self.endpoints.values()
            )
            avg_response_time = (
                round(total_response_time / self.total_requests, 2)
                if self.total_requests > 0 else 0.0
            )
            
            return {
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "error_rate": round(
                    (self.total_errors / self.total_requests * 100) if self.total_requests > 0 else 0,
                    2
                ),
                "total_endpoints": len(self.endpoints),
                "total_providers_adopted": len(self.providers),
                "total_features_used": len(self.features),
                "avg_response_time_ms": avg_response_time,
                "unique_clients": len(self.unique_clients),
                "unique_countries": len(self.client_locations),
                "uptime_since": self.start_time,
                "timestamp": datetime.now(UTC).isoformat()
            }
    
    def get_client_locations(self, limit: int = 10) -> List[dict]:
        """
        Get client locations sorted by request count.
        
        Args:
            limit: Max number of locations to return
            
        Returns:
            List of client location dicts
        """
        with self._lock:
            return [
                loc.to_dict()
                for loc in sorted(
                    self.client_locations.values(),
                    key=lambda x: x.request_count,
                    reverse=True
                )[:limit]
            ]
    
    def get_browser_stats(self, limit: int = 10) -> List[dict]:
        """
        Get browser usage stats sorted by request count.
        
        Args:
            limit: Max number of browsers to return
            
        Returns:
            List of browser stats dicts
        """
        with self._lock:
            return [
                browser.to_dict()
                for browser in sorted(
                    self.browser_usage.values(),
                    key=lambda x: x.request_count,
                    reverse=True
                )[:limit]
            ]
    
    def reset_telemetry(self) -> None:
        """Reset all telemetry data (useful for testing)."""
        with self._lock:
            self.endpoints.clear()
            self.providers.clear()
            self.features.clear()
            self.client_locations.clear()
            self.browser_usage.clear()
            self.unique_clients.clear()
            self.total_requests = 0
            self.total_errors = 0
            self.start_time = datetime.now(UTC).isoformat()
            logger.info("Telemetry data reset")



# Global telemetry instance
_telemetry_service: Optional[TelemetryService] = None


def get_telemetry_service() -> TelemetryService:
    """Get or create the global telemetry service."""
    global _telemetry_service
    if _telemetry_service is None:
        _telemetry_service = TelemetryService()
    return _telemetry_service
