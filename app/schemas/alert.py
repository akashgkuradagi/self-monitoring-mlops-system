"""Alert Management Schemas and Enumerations (ALERT-01, FR-08, NFR-07)."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AlertSeverity(str, Enum):
    """Approved severity levels for threshold breaches (FR-08)."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AlertStatus(str, Enum):
    """Approved alert lifecycle states (SAD Table 6)."""

    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class AlertResponse(BaseModel):
    """Structured Alert domain entity (FR-08, SAD Table 6)."""

    alert_id: str = Field(..., description="Unique alert identifier (e.g. alt-...)")
    fingerprint: str = Field(..., description="Deterministic hash for deduplication")
    metric_name: str = Field(..., description="Monitored metric name (e.g. DRIFT_KS, PREDICTION_LATENCY_P95)")
    feature_name: Optional[str] = Field(default=None, description="Feature affected if drift-related")
    severity: str = Field(..., description="Severity level: LOW, MEDIUM, or HIGH")
    observed_value: float = Field(..., description="Observed numeric metric value")
    threshold: float = Field(..., description="Configured boundary threshold breached")
    status: str = Field(default=AlertStatus.ACTIVE.value, description="Status: ACTIVE, ACKNOWLEDGED, RESOLVED")
    message: str = Field(..., description="Human-readable breach explanation")
    supporting_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Detailed contextual metadata (p-values, sample size, percentiles)",
    )
    dedup_count: int = Field(default=1, description="Number of times breach occurred during deduplication window")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of initial detection",
    )
    last_seen_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of latest equivalent breach",
    )
    acknowledged_at: Optional[str] = Field(default=None, description="Timestamp when alert was acknowledged")
    acknowledged_by: Optional[str] = Field(default=None, description="Username who acknowledged the alert")
    resolved_at: Optional[str] = Field(default=None, description="Timestamp when alert condition was resolved")
    resolved_by: Optional[str] = Field(default=None, description="Username or system actor who resolved the alert")


class AlertAcknowledgeRequest(BaseModel):
    """Request payload for acknowledging an active alert."""

    comment: Optional[str] = Field(default=None, description="Optional triage or investigation note")


class AlertResolveRequest(BaseModel):
    """Request payload for manually resolving an alert."""

    comment: Optional[str] = Field(default=None, description="Optional resolution note or root cause explanation")


class AlertSummaryResponse(BaseModel):
    """Aggregate alert count metrics for observability dashboards (FR-13, NFR-08)."""

    total_alerts: int
    active_alerts: int
    acknowledged_alerts: int
    resolved_alerts: int
    by_severity: Dict[str, int]
    by_metric: Dict[str, int]
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EvaluateAlertsResponse(BaseModel):
    """Result of evaluating monitoring telemetry against configured alert thresholds."""

    evaluated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    alerts_evaluated: int
    new_alerts_count: int
    deduplicated_alerts_count: int
    active_alerts_count: int
    alerts: List[AlertResponse]
