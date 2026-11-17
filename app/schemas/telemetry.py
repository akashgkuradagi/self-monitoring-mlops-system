"""Prediction Telemetry Event Schemas."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid
from pydantic import BaseModel, Field


class PredictionEventRequest(BaseModel):
    """Schema for incoming prediction telemetry events (FR-02, TC-02)."""

    request_id: str = Field(..., min_length=1, max_length=128, description="Unique client/system request identifier")
    model_version: str = Field(..., min_length=1, max_length=64, description="Target model version identifier")
    latency_ms: float = Field(..., ge=0.0, description="Inference latency in milliseconds")
    success: bool = Field(default=True, description="True if inference succeeded, False on model/pipeline error")
    feature_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Aggregated statistical summary of input features (not raw sensitive records)",
    )
    timestamp: Optional[str] = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of inference observation",
    )


class PredictionEventResponse(BaseModel):
    """Asynchronous acceptance response for ingested telemetry (HTTP 202)."""

    status: str = Field(default="accepted", description="Ingestion processing status")
    event_id: str = Field(
        default_factory=lambda: f"evt-{uuid.uuid4().hex[:12]}",
        description="Assigned unique telemetry event identifier",
    )
    message: str = Field(
        default="Telemetry event accepted for ingestion",
        description="Human-readable processing confirmation",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of acceptance",
    )
