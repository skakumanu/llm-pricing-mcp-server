"""Pydantic models for deployment and health checks."""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, UTC
from enum import Enum


class DeploymentStatus(str, Enum):
    """Deployment status enumeration."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    SHUTTING_DOWN = "shutting_down"


class EnvironmentInfo(BaseModel):
    """Information about the deployment environment."""
    
    environment: str = Field(..., description="Deployment environment (e.g., production, staging)")
    region: Optional[str] = Field(None, description="Geographic region or deployment region")
    availability_zone: Optional[str] = Field(None, description="Availability zone or datacenter")
    deployment_group: Optional[str] = Field(None, description="Deployment group (e.g., blue, green)")
    instance_id: Optional[str] = Field(None, description="Instance or container ID")
    deployment_timestamp: Optional[datetime] = Field(None, description="When this instance was deployed")


class ServiceHealth(BaseModel):
    """Health status of a service/dependency."""
    
    name: str = Field(..., description="Name of the service")
    status: DeploymentStatus = Field(..., description="Health status")
    response_time_ms: Optional[float] = Field(None, description="Response time in milliseconds")
    last_checked: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_message: Optional[str] = Field(None, description="Error details if unhealthy")


class DeploymentReadiness(BaseModel):
    """Deployment readiness check response."""
    
    ready: bool = Field(..., description="Whether the service is ready to receive traffic")
    reason: Optional[str] = Field(None, description="Reason if not ready")
    checks: Dict[str, bool] = Field(default_factory=dict, description="Individual readiness checks")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class HealthCheckResponse(BaseModel):
    """Comprehensive health check response for load balancers and orchestrators."""
    
    status: DeploymentStatus = Field(..., description="Overall service status")
    version: str = Field(..., description="Application version")
    uptime_seconds: float = Field(..., description="Uptime in seconds")
    environment: EnvironmentInfo = Field(..., description="Environment information")
    services: List[ServiceHealth] = Field(default_factory=list, description="Health of dependencies")
    metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Current performance metrics (requests/sec, avg response time, etc.)"
    )
    graceful_shutdown: bool = Field(default=False, description="Whether graceful shutdown is in progress")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeploymentMetadata(BaseModel):
    """Metadata about the current deployment for blue-green awareness."""
    
    version: str = Field(..., description="Application version")
    build_date: Optional[datetime] = Field(None, description="Build timestamp")
    deployment_date: datetime = Field(default_factory=lambda: datetime.now(UTC))
    api_versions: List[str] = Field(
        default_factory=lambda: ["v1"],
        description="Supported API versions"
    )
    backwards_compatible_until: Optional[str] = Field(
        None,
        description="Version until which backwards compatibility is maintained"
    )
    breaking_changes: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Breaking changes by version (e.g., {'v2': ['removed endpoint X']})"
    )


class ApiVersionInfo(BaseModel):
    """Information about API versioning."""
    
    current_version: str = Field(..., description="Current stable API version")
    all_versions: List[str] = Field(..., description="All available API versions")
    deprecated_versions: List[str] = Field(
        default_factory=list,
        description="Deprecated API versions still supported"
    )
    migration_guide_url: Optional[str] = Field(
        None,
        description="URL to migration guide for old API versions"
    )


class GracefulShutdownRequest(BaseModel):
    """Request to initiate graceful shutdown."""
    
    drain_timeout_seconds: int = Field(
        default=30,
        description="Time to wait for in-flight requests to complete"
    )


class GracefulShutdownStatus(BaseModel):
    """Status of graceful shutdown process."""
    
    shutting_down: bool = Field(..., description="Whether shutdown is in progress")
    active_requests: int = Field(..., description="Number of active requests")
    drain_timeout_seconds: int = Field(..., description="Drain timeout configured")
    started_at: Optional[datetime] = Field(None, description="When shutdown started")
