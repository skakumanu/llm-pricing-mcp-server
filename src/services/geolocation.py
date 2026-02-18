"""Geolocation and client tracking service."""
import logging
import asyncio
from typing import Optional, Dict
from datetime import datetime, UTC
import httpx
from functools import lru_cache
from user_agents import parse as parse_user_agent

logger = logging.getLogger(__name__)


class ClientInfo:
    """Information about the client making the request."""
    
    def __init__(
        self,
        ip_address: str,
        browser: Optional[str] = None,
        browser_version: Optional[str] = None,
        os: Optional[str] = None,
        os_version: Optional[str] = None,
        device_type: Optional[str] = None,
        country: Optional[str] = None,
        country_code: Optional[str] = None,
        city: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ):
        """Initialize client info."""
        self.ip_address = ip_address
        self.browser = browser
        self.browser_version = browser_version
        self.os = os
        self.os_version = os_version
        self.device_type = device_type
        self.country = country
        self.country_code = country_code
        self.city = city
        self.latitude = latitude
        self.longitude = longitude
    
    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "ip_address": self.ip_address,
            "browser": self.browser,
            "browser_version": self.browser_version,
            "os": self.os,
            "os_version": self.os_version,
            "device_type": self.device_type,
            "country": self.country,
            "country_code": self.country_code,
            "city": self.city,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


class GeolocationService:
    """Service for tracking client geolocation and browser information."""
    
    # Cache IP geolocation lookups to avoid excessive API calls
    _geolocation_cache: Dict[str, Optional[Dict[str, any]]] = {}
    _cache_max_size = 1000
    
    @staticmethod
    def parse_user_agent(user_agent_string: Optional[str]) -> Dict[str, Optional[str]]:
        """
        Parse user agent string to extract browser and OS information.
        
        Args:
            user_agent_string: User-Agent header value
            
        Returns:
            Dictionary with browser, os, and device info
        """
        if not user_agent_string:
            return {
                "browser": None,
                "browser_version": None,
                "os": None,
                "os_version": None,
                "device_type": None,
            }
        
        try:
            ua = parse_user_agent(user_agent_string)
            
            # Determine device type
            device_type = "unknown"
            if ua.is_mobile:
                device_type = "mobile"
            elif ua.is_tablet:
                device_type = "tablet"
            elif ua.is_pc:
                device_type = "desktop"
            
            return {
                "browser": ua.browser.family if ua.browser else None,
                "browser_version": ua.browser.version_string if ua.browser else None,
                "os": ua.os.family if ua.os else None,
                "os_version": ua.os.version_string if ua.os else None,
                "device_type": device_type,
            }
        except Exception as e:
            logger.debug(f"Error parsing user agent: {e}")
            return {
                "browser": None,
                "browser_version": None,
                "os": None,
                "os_version": None,
                "device_type": None,
            }
    
    @staticmethod
    async def get_geolocation(ip_address: str) -> Optional[Dict[str, any]]:
        """
        Get geolocation information for an IP address.
        
        Args:
            ip_address: IPv4 or IPv6 address
            
        Returns:
            Dictionary with country, city, lat/lon or None if lookup fails
        """
        # Check cache first
        if ip_address in GeolocationService._geolocation_cache:
            return GeolocationService._geolocation_cache[ip_address]
        
        # Skip private/local IPs
        if GeolocationService._is_private_ip(ip_address):
            result = {
                "country": "Private Network",
                "country_code": "PRIVATE",
                "city": "Local",
                "latitude": None,
                "longitude": None,
            }
            GeolocationService._geolocation_cache[ip_address] = result
            return result
        
        try:
            # Use ip-api.com free tier (45 requests per minute)
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"http://ip-api.com/json/{ip_address}",
                    params={"fields": "country,countryCode,city,lat,lon,status,message"}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get("status") == "success":
                        result = {
                            "country": data.get("country"),
                            "country_code": data.get("countryCode"),
                            "city": data.get("city"),
                            "latitude": data.get("lat"),
                            "longitude": data.get("lon"),
                        }
                        
                        # Cache the result (limit cache size)
                        if len(GeolocationService._geolocation_cache) < GeolocationService._cache_max_size:
                            GeolocationService._geolocation_cache[ip_address] = result
                        
                        return result
                    else:
                        logger.debug(f"Geolocation lookup failed for {ip_address}: {data.get('message')}")
                        GeolocationService._geolocation_cache[ip_address] = None
                        return None
                else:
                    logger.debug(f"Geolocation API error: {response.status_code}")
                    GeolocationService._geolocation_cache[ip_address] = None
                    return None
                    
        except asyncio.TimeoutError:
            logger.debug(f"Geolocation lookup timeout for {ip_address}")
            GeolocationService._geolocation_cache[ip_address] = None
            return None
        except Exception as e:
            logger.debug(f"Error during geolocation lookup for {ip_address}: {e}")
            GeolocationService._geolocation_cache[ip_address] = None
            return None
    
    @staticmethod
    def _is_private_ip(ip_address: str) -> bool:
        """Check if IP is private/local."""
        # Handle IPv4
        if ":" not in ip_address:  # Not IPv6
            try:
                parts = ip_address.split(".")
                if len(parts) == 4:
                    first_octet = int(parts[0])
                    
                    # 10.x.x.x, 172.16-31.x.x, 192.168.x.x, 127.x.x.x
                    if first_octet == 10 or first_octet == 127:
                        return True
                    if first_octet == 172:
                        second = int(parts[1])
                        if 16 <= second <= 31:
                            return True
                    if first_octet == 192 and int(parts[1]) == 168:
                        return True
            except (ValueError, IndexError):
                pass
        
        # Handle IPv6
        if ip_address == "::1" or ip_address.startswith("fe80:"):
            return True
        
        return False
    
    @staticmethod
    async def get_client_info(
        ip_address: str,
        user_agent_string: Optional[str] = None
    ) -> ClientInfo:
        """
        Get complete client information from IP and user agent.
        
        Args:
            ip_address: Client IP address
            user_agent_string: User-Agent header value
            
        Returns:
            ClientInfo object with all available information
        """
        # Parse user agent (synchronous, fast)
        ua_info = GeolocationService.parse_user_agent(user_agent_string)
        
        # Get geolocation (asynchronous, may be cached)
        geo_info = await GeolocationService.get_geolocation(ip_address)
        
        return ClientInfo(
            ip_address=ip_address,
            browser=ua_info.get("browser"),
            browser_version=ua_info.get("browser_version"),
            os=ua_info.get("os"),
            os_version=ua_info.get("os_version"),
            device_type=ua_info.get("device_type"),
            country=geo_info.get("country") if geo_info else None,
            country_code=geo_info.get("country_code") if geo_info else None,
            city=geo_info.get("city") if geo_info else None,
            latitude=geo_info.get("latitude") if geo_info else None,
            longitude=geo_info.get("longitude") if geo_info else None,
        )
    
    @staticmethod
    def clear_cache():
        """Clear the geolocation cache."""
        GeolocationService._geolocation_cache.clear()
        logger.info("Geolocation cache cleared")
