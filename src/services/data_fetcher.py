"""Utilities for fetching live pricing and performance data."""
import time
import logging
from typing import Dict, Optional, Any, List
from datetime import datetime, timedelta
import httpx
from bs4 import BeautifulSoup
import json

logger = logging.getLogger(__name__)


class CachedData:
    """Simple cache for data with TTL."""
    
    def __init__(self, data: Any, ttl_seconds: int):
        """Initialize cached data.
        
        Args:
            data: The data to cache
            ttl_seconds: Time to live in seconds
        """
        self.data = data
        self.created_at = datetime.now()
        self.ttl = timedelta(seconds=ttl_seconds)
    
    def is_valid(self) -> bool:
        """Check if cached data is still valid."""
        return datetime.now() < self.created_at + self.ttl


class DataFetcher:
    """Utility class for fetching live pricing and performance data."""
    
    _cache: Dict[str, CachedData] = {}
    _request_timeout = 10.0
    
    @classmethod
    async def fetch_with_cache(
        cls,
        cache_key: str,
        fetch_func,
        ttl_seconds: int = 3600,
        fallback_data: Optional[Any] = None
    ) -> Optional[Any]:
        """Fetch data with caching and fallback.
        
        Args:
            cache_key: Unique identifier for cached data
            fetch_func: Async function to call to fetch data
            ttl_seconds: Cache time to live
            fallback_data: Data to return if fetch fails
            
        Returns:
            Fetched data, cached data, or fallback data
        """
        # Check cache
        if cache_key in cls._cache and cls._cache[cache_key].is_valid():
            logger.debug(f"Cache hit for {cache_key}")
            return cls._cache[cache_key].data
        
        # Fetch new data
        try:
            logger.debug(f"Fetching live data for {cache_key}")
            data = await fetch_func()
            cls._cache[cache_key] = CachedData(data, ttl_seconds)
            return data
        except Exception as e:
            logger.warning(f"Failed to fetch {cache_key}: {str(e)}")
            if fallback_data is not None:
                return fallback_data
            return None
    
    @classmethod
    async def fetch_api_models(
        cls,
        api_endpoint: str,
        api_key: Optional[str] = None,
        require_auth: bool = False
    ) -> Optional[List[str]]:
        """Fetch available models from an API endpoint.
        
        Args:
            api_endpoint: API endpoint URL
            api_key: Optional API key for authentication
            require_auth: Whether authentication is required
            
        Returns:
            List of model names or None if fetch failed
        """
        if require_auth and not api_key:
            logger.warning(f"API key required but not provided for {api_endpoint}")
            return None
        
        try:
            async with httpx.AsyncClient(timeout=cls._request_timeout) as client:
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                
                response = await client.get(api_endpoint, headers=headers)
                response.raise_for_status()
                
                data = response.json()
                
                # Extract model names based on API response format
                models = []
                if "data" in data:  # OpenAI format
                    models = [m.get("id") for m in data["data"] if isinstance(m, dict)]
                elif "models" in data:  # Anthropic-like format
                    models = [m.get("id") if isinstance(m, dict) else m for m in data["models"]]
                
                logger.info(f"Successfully fetched {len(models)} models from {api_endpoint}")
                return models
                
        except Exception as e:
            logger.warning(f"Failed to fetch models from {api_endpoint}: {str(e)}")
            return None
    
    @classmethod
    async def fetch_pricing_from_website(
        cls,
        url: str,
        parser_func=None
    ) -> Optional[Dict[str, Dict[str, float]]]:
        """Fetch pricing data from a website using web scraping.
        
        Args:
            url: Website URL to scrape
            parser_func: Optional custom parser function
            
        Returns:
            Dict of model pricing data or None if fetch failed
        """
        try:
            async with httpx.AsyncClient(timeout=cls._request_timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                if parser_func:
                    return parser_func(response.text)
                else:
                    return await cls._default_parse_pricing(response.text, url)
                    
        except Exception as e:
            logger.warning(f"Failed to scrape pricing from {url}: {str(e)}")
            return None
    
    @classmethod
    async def _default_parse_pricing(cls, html: str, url: str) -> Optional[Dict]:
        """Default HTML parser for pricing pages.
        
        Args:
            html: HTML content
            url: Source URL (for provider detection)
            
        Returns:
            Parsed pricing data or None
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')
            
            pricing_data = {}
            
            # Look for common pricing table patterns
            tables = soup.find_all('table')
            
            if not tables:
                logger.warning(f"No pricing tables found on {url}")
                return None
            
            # Try to parse the first table
            for row in tables[0].find_all('tr')[1:]:  # Skip header
                cells = row.find_all('td')
                if len(cells) >= 3:
                    model_name = cells[0].get_text(strip=True)
                    input_cost = cells[1].get_text(strip=True)
                    output_cost = cells[2].get_text(strip=True)
                    
                    try:
                        pricing_data[model_name] = {
                            "input": float(input_cost.replace('$', '').replace(',', '')),
                            "output": float(output_cost.replace('$', '').replace(',', ''))
                        }
                    except ValueError:
                        continue
            
            return pricing_data if pricing_data else None
            
        except Exception as e:
            logger.warning(f"Error parsing pricing HTML from {url}: {str(e)}")
            return None
    
    @classmethod
    async def check_api_health(
        cls,
        endpoint: str,
        api_key: Optional[str] = None,
        public_endpoint: Optional[str] = None
    ) -> Dict[str, Any]:
        """Check API health and get basic performance metrics.
        
        Tries public endpoint first (no auth needed), then falls back to 
        authenticated endpoint if API key is provided.
        
        Args:
            endpoint: API endpoint to check
            api_key: Optional API key for authenticated checks
            public_endpoint: Optional public status page to check instead
            
        Returns:
            Dict with health status and latency info
        """
        # Try public endpoint first (no auth needed)
        if public_endpoint:
            health = await cls._check_endpoint(public_endpoint, None)
            if health.get("healthy"):
                return health
        
        # Fall back to authenticated endpoint if API key available
        if api_key:
            return await cls._check_endpoint(endpoint, api_key)
        
        # Final fallback: try without auth
        return await cls._check_endpoint(endpoint, None)
    
    @classmethod
    async def _check_endpoint(
        cls,
        endpoint: str,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """Helper method to check a single endpoint.
        
        Args:
            endpoint: Endpoint URL to check
            api_key: Optional API key for authorization
            
        Returns:
            Dict with health check results
        """
        try:
            start_time = time.time()
            
            async with httpx.AsyncClient(timeout=cls._request_timeout) as client:
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                
                response = await client.head(endpoint, headers=headers)
                latency_ms = (time.time() - start_time) * 1000
                
                return {
                    "healthy": response.status_code < 400,
                    "latency_ms": latency_ms,
                    "status_code": response.status_code,
                    "timestamp": datetime.now().isoformat()
                }
                
        except Exception as e:
            logger.warning(f"Health check failed for {endpoint}: {str(e)}")
            return {
                "healthy": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    @classmethod
    def clear_cache(cls, cache_key: Optional[str] = None) -> None:
        """Clear cached data.
        
        Args:
            cache_key: Specific key to clear, or None to clear all
        """
        if cache_key:
            cls._cache.pop(cache_key, None)
            logger.info(f"Cleared cache for {cache_key}")
        else:
            cls._cache.clear()
            logger.info("Cleared all cache")
