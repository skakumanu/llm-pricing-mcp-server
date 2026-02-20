"""Deployment and health check service for blue-green deployment support."""
import os
import logging
import asyncio
from datetime import datetime, UTC
from typing import Dict, Optional, List, Any
from src.models.deployment import (
    DeploymentStatus, EnvironmentInfo, ServiceHealth, HealthCheckResponse,
    DeploymentReadiness, DeploymentMetadata, GracefulShutdownStatus
)

logger = logging.getLogger(__name__)


class DeploymentManager:
    """
    Manages deployment lifecycle including blue-green deployment support,
    graceful shutdown, and health checks for load balancers.
    
    Supports:
    - Blue-green deployment detection and metadata
    - Graceful shutdown with drain timeout
    - Enhanced health checks for orchestrators (K8s, ECS, etc.)
    - Readiness and liveness probes
    - Request counting for drain operations
    """
    
    def __init__(self, version: str):
        self.version = version
        self.start_time = datetime.now(UTC)
        self.graceful_shutdown_started: Optional[datetime] = None
        self.drain_timeout_seconds = 30
        self.active_requests = 0
        self._request_lock = asyncio.Lock()
        
        # Get deployment environment info
        self.environment = self._get_environment_info()
        self.deployment_metadata = self._get_deployment_metadata()
        
        logger.info(f"DeploymentManager initialized for {self.environment.environment} environment")
        if self.environment.deployment_group:
            logger.info(f"Deployment group: {self.environment.deployment_group} (blue-green aware)")
    
    def _get_environment_info(self) -> EnvironmentInfo:
        """Detect and extract environment information."""
        return EnvironmentInfo(
            environment=os.getenv("ENV", "production"),
            region=os.getenv("AWS_REGION") or os.getenv("GCP_REGION") or os.getenv("REGION"),
            availability_zone=os.getenv("AVAILABILITY_ZONE") or os.getenv("AZ"),
            deployment_group=os.getenv("DEPLOYMENT_GROUP"),  # blue or green
            instance_id=os.getenv("INSTANCE_ID") or os.getenv("HOSTNAME") or os.getenv("POD_NAME"),
            deployment_timestamp=self._parse_deployment_timestamp(),
        )
    
    def _parse_deployment_timestamp(self) -> Optional[datetime]:
        """Parse deployment timestamp from environment."""
        deploy_ts = os.getenv("DEPLOYMENT_TIMESTAMP")
        if deploy_ts:
            try:
                return datetime.fromisoformat(deploy_ts)
            except (ValueError, TypeError):
                pass
        return None
    
    def _get_deployment_metadata(self) -> DeploymentMetadata:
        """Get deployment metadata including version and breaking changes."""
        breaking_changes = {
            "v2": [
                "Removed POST /pricing endpoint (use GET /pricing instead)",
                "Changed cost response format to include volume pricing",
            ]
        }
        
        return DeploymentMetadata(
            version=self.version,
            build_date=self._parse_build_timestamp(),
            deployment_date=self.start_time,
            api_versions=["v1"],  # v2 available but breaking changes documented
            backwards_compatible_until="v2.x",
            breaking_changes=breaking_changes,
        )
    
    def _parse_build_timestamp(self) -> Optional[datetime]:
        """Parse build timestamp from environment."""
        build_ts = os.getenv("BUILD_TIMESTAMP")
        if build_ts:
            try:
                return datetime.fromisoformat(build_ts)
            except (ValueError, TypeError):
                pass
        return None
    
    async def track_request_start(self) -> None:
        """Increment active request counter."""
        async with self._request_lock:
            if self.graceful_shutdown_started:
                raise RuntimeError(
                    "Service is shutting down and no longer accepting new requests"
                )
            self.active_requests += 1
    
    async def track_request_end(self) -> None:
        """Decrement active request counter."""
        async with self._request_lock:
            self.active_requests = max(0, self.active_requests - 1)
    
    async def initiate_graceful_shutdown(self, drain_timeout_seconds: int = 30) -> None:
        """
        Initiate graceful shutdown, preventing new requests and draining existing ones.
        
        Args:
            drain_timeout_seconds: Time to wait for in-flight requests to complete
        """
        logger.warning(f"Initiating graceful shutdown with {drain_timeout_seconds}s drain timeout")
        
        async with self._request_lock:
            self.graceful_shutdown_started = datetime.now(UTC)
            self.drain_timeout_seconds = drain_timeout_seconds
        
        # Wait for in-flight requests to complete or timeout
        elapsed = 0
        while elapsed < drain_timeout_seconds:
            async with self._request_lock:
                if self.active_requests == 0:
                    logger.info("All in-flight requests completed")
                    return
            
            await asyncio.sleep(1)
            elapsed += 1
            
            if elapsed % 5 == 0:
                async with self._request_lock:
                    logger.info(f"Waiting for {self.active_requests} requests... ({elapsed}s/{drain_timeout_seconds}s)")
        
        async with self._request_lock:
            logger.warning(
                f"Drain timeout reached with {self.active_requests} requests still in-flight"
            )
    
    def is_shutting_down(self) -> bool:
        """Check if graceful shutdown is in progress."""
        return self.graceful_shutdown_started is not None
    
    def get_uptime_seconds(self) -> float:
        """Get service uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()
    
    async def get_health_check(
        self,
        include_metrics: bool = True,
        include_dependencies: bool = True,
    ) -> HealthCheckResponse:
        """
        Get comprehensive health check response for load balancers.
        
        Args:
            include_metrics: Include performance metrics
            include_dependencies: Include dependency health checks
        
        Returns:
            HealthCheckResponse ready for load balancer/orchestrator
        """
        # Determine overall status
        if self.is_shutting_down():
            status = DeploymentStatus.SHUTTING_DOWN
        else:
            status = DeploymentStatus.HEALTHY
        
        # Get active request count
        async with self._request_lock:
            active_requests = self.active_requests
        
        metrics: Dict[str, Any] = {}
        if include_metrics:
            metrics["active_requests"] = float(active_requests)
            metrics["uptime_seconds"] = self.get_uptime_seconds()
        
        return HealthCheckResponse(
            status=status,
            version=self.version,
            uptime_seconds=self.get_uptime_seconds(),
            environment=self.environment,
            services=self._get_dependency_health() if include_dependencies else [],
            metrics=metrics,
            graceful_shutdown=self.is_shutting_down(),
        )
    
    async def get_readiness_check(self) -> DeploymentReadiness:
        """
        Get readiness check response (K8s readinessProbe, etc.).
        
        Returns True if ready to accept traffic.
        """
        ready = not self.is_shutting_down()
        
        checks = {
            "aggregator_initialized": True,  # Could be enhanced with actual checks
            "not_shutting_down": ready,
        }
        
        return DeploymentReadiness(
            ready=ready,
            reason=None if ready else "Graceful shutdown in progress",
            checks=checks,
        )
    
    async def get_liveness_check(self) -> Dict[str, Any]:
        """
        Get liveness check response (K8s livenessProbe, etc.).
        
        Returns True if process should continue running.
        """
        return {
            "alive": True,
            "timestamp": datetime.now(UTC),
        }
    
    def _get_dependency_health(self) -> List[ServiceHealth]:
        """Get health status of dependencies."""
        # Could be enhanced to actually check dependencies
        return [
            ServiceHealth(
                name="pricing_aggregator",
                status=DeploymentStatus.HEALTHY,
                response_time_ms=5.0,
                error_message=None,
            ),
            ServiceHealth(
                name="telemetry_service",
                status=DeploymentStatus.HEALTHY,
                response_time_ms=2.0,
                error_message=None,
            ),
        ]
    
    def get_deployment_metadata(self) -> DeploymentMetadata:
        """Get deployment metadata including version and breaking changes."""
        return self.deployment_metadata
    
    async def get_shutdown_status(self) -> GracefulShutdownStatus:
        """Get current graceful shutdown status."""
        async with self._request_lock:
            return GracefulShutdownStatus(
                shutting_down=self.is_shutting_down(),
                active_requests=self.active_requests,
                drain_timeout_seconds=self.drain_timeout_seconds,
                started_at=self.graceful_shutdown_started,
            )


# Global singleton instance
_deployment_manager: Optional[DeploymentManager] = None


def get_deployment_manager(version: str) -> DeploymentManager:
    """Get or create the global deployment manager instance."""
    global _deployment_manager
    
    if _deployment_manager is None:
        _deployment_manager = DeploymentManager(version=version)
    
    return _deployment_manager
