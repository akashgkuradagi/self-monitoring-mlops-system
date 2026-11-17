"""Metrics Summary Schemas."""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class MetricsSummaryResponse(BaseModel):
    """Aggregated operational telemetry and health summary (NFR-07, TC-02, TC-09)."""

    count: int = Field(..., ge=0, description="Total number of prediction events ingested")
    error_count: int = Field(..., ge=0, description="Total number of failed inference executions")
    error_rate: float = Field(..., ge=0.0, le=1.0, description="Ratio of failed events over total ingested events")
    latency_p50_ms: float = Field(..., ge=0.0, description="50th percentile (median) latency in milliseconds")
    latency_p95_ms: float = Field(..., ge=0.0, description="95th percentile latency in milliseconds")
    latency_p99_ms: float = Field(..., ge=0.0, description="99th percentile latency in milliseconds")
    latency_mean_ms: float = Field(..., ge=0.0, description="Arithmetic mean latency in milliseconds")
    model_version: Optional[str] = Field(default=None, description="Primary active baseline model version")
    drift_status: str = Field(
        default="UNKNOWN",
        description="Current statistical drift state: 'NO_DRIFT', 'DRIFT_DETECTED', or 'UNKNOWN'",
    )
    window_size: int = Field(..., description="Configured rolling sample window buffer size")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of summary generation",
    )
