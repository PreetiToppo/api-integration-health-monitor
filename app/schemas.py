from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EndpointCreate(BaseModel):
    """Payload for creating a monitored endpoint."""

    url: str = Field(min_length=1, max_length=2048)
    name: str = Field(min_length=1, max_length=255)
    expected_status_code: int = Field(default=200, ge=100, le=599)
    method: str = Field(default="GET", min_length=1, max_length=10)
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)


class EndpointResponse(BaseModel):
    """Serialized monitored endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    method: str
    expected_status_code: int
    timeout_seconds: float


class EndpointMetrics(BaseModel):
    """Aggregated uptime/latency analytics for one endpoint."""

    endpoint_id: int
    total_checks: int
    successful_checks: int
    uptime_percentage: float
    avg_response_time_ms: float


class EndpointCheckResponse(BaseModel):
    """Result of a single health check."""

    model_config = ConfigDict(from_attributes=True)

    endpoint_id: int
    status_code: int | None
    response_time_ms: float | None
    is_success: bool
    checked_at: datetime
