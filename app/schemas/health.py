"""Health Check Schemas."""

from datetime import datetime, timezone
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Payload schema for service health status (FR-01, TC-01)."""

    status: str = Field(default="healthy", description="Current service health state")
    app_version: str = Field(..., description="Semantic application version")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of the health check observation",
    )
    environment: str = Field(default="development", description="Runtime environment")
