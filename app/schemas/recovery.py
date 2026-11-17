"""Recovery Policy, Validation Gates, and Candidate Schemas (REC-01).

Implements:
- FR-09: Recovery policy evaluation and cooldown tracking.
- FR-10: Automated candidate validation gates.
- FR-14: Human-in-the-loop candidate decision API.
- NFR-10: Safety of automation (Dual-Gate promotion control).
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CandidateStatus(str, Enum):
    """Lifecycle statuses for retrained model candidates."""

    PENDING_VALIDATION = "PENDING_VALIDATION"
    VALIDATING = "VALIDATING"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class GateStatus(str, Enum):
    """Validation gate outcome status."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class GateResult(BaseModel):
    """Outcome and diagnostics of a single validation gate."""

    status: GateStatus = Field(description="Gate evaluation result")
    observed_value: Optional[float] = Field(default=None, description="Observed metric value")
    threshold: Optional[float] = Field(default=None, description="Enforced threshold")
    details: str = Field(description="Diagnostic description of the gate evaluation")


class CandidateValidationResponse(BaseModel):
    """Comprehensive validation scorecard response."""

    candidate_id: str = Field(description="Unique candidate model identifier")
    status: str = Field(description="Candidate status post-validation (VALIDATED or VALIDATION_FAILED)")
    all_passed: bool = Field(description="True if all automated validation gates passed")
    checks: Dict[str, str] = Field(default_factory=dict, description="Summary mapping of gate names to PASSED/FAILED")
    gates: Dict[str, GateResult] = Field(default_factory=dict, description="Detailed gate diagnostic results")
    run_id: Optional[str] = Field(default=None, description="MLflow run ID associated with candidate")
    validated_at: str = Field(description="ISO 8601 validation timestamp")
    validated_by: Optional[str] = Field(default=None, description="Username or system agent that executed validation")
    timestamp: Optional[str] = Field(default=None, description="Validation timestamp alias")


class CandidateRecordResponse(BaseModel):
    """Model candidate registry record response."""

    candidate_id: str
    model_version: str
    model_name: str
    run_id: Optional[str] = None
    status: CandidateStatus
    trigger_reason: Optional[str] = None
    validation_report: Optional[Dict[str, Any]] = None
    created_at: str
    validated_at: Optional[str] = None
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None
    decision_comment: Optional[str] = None


class CandidateListResponse(BaseModel):
    """List response for model candidates."""

    candidates: List[CandidateRecordResponse]
    total: int


class CandidateCreateRequest(BaseModel):
    """Request to create / register a new candidate model."""

    candidate_id: str = Field(min_length=3, max_length=64, description="Candidate identifier")
    model_version: str = Field(description="Model version string")
    model_name: Optional[str] = Field(default="ProductionClassifier", description="Target model name")
    run_id: Optional[str] = Field(default=None, description="Associated MLflow run ID")
    trigger_reason: Optional[str] = Field(default="Automated retraining", description="Trigger context")


class PolicyEvaluationRequest(BaseModel):
    """Request to evaluate recovery policy against active system alerts or drift status."""

    drift_detected: bool = Field(default=False, description="True if input/prediction drift is active")
    active_alert_ids: Optional[List[str]] = Field(default=None, description="List of active alert IDs")
    severity: Optional[str] = Field(default=None, description="Highest active alert severity")
    trigger_source: Optional[str] = Field(default="monitoring_service", description="Source of evaluation trigger")


class PolicyEvaluationResponse(BaseModel):
    """Outcome of recovery policy trigger evaluation."""

    status: str = Field(description="Policy evaluation status: TRIGGER_APPROVED, SUPPRESSED_COOLDOWN, INELIGIBLE")
    candidate_eligible: bool = Field(description="Whether retraining / candidate creation should proceed")
    reason: str = Field(description="Detailed rationale")
    cooldown_remaining_seconds: float = Field(description="Seconds remaining in cooldown suppression window")
    evaluated_at: str = Field(description="ISO 8601 evaluation timestamp")


class PolicyStatusResponse(BaseModel):
    """Current configuration and operational status of the Recovery Policy Engine."""

    cooldown_seconds: int = Field(description="Configured cooldown duration in seconds")
    is_cooldown_active: bool = Field(description="Whether cooldown suppression is currently active")
    cooldown_remaining_seconds: float = Field(description="Remaining cooldown duration in seconds")
    last_trigger_time: Optional[str] = Field(default=None, description="ISO 8601 timestamp of last retraining trigger")
    total_triggers: int = Field(default=0, description="Cumulative count of approved triggers")
    total_suppressions: int = Field(default=0, description="Cumulative count of suppressed triggers")


class HealthStatus(str, Enum):
    """Post-promotion canary health state."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class RollbackTriggerType(str, Enum):
    """Origin of rollback trigger."""

    AUTOMATED = "AUTOMATED"
    MANUAL = "MANUAL"


class PostPromotionHealthResponse(BaseModel):
    """Post-promotion health evaluation report."""

    model_version: str = Field(description="Currently active model version under observation")
    health_status: HealthStatus = Field(description="Canary health state")
    is_canary_active: bool = Field(description="True if model is within post-promotion canary window")
    promoted_at: Optional[str] = Field(default=None, description="ISO 8601 timestamp of promotion")
    canary_remaining_seconds: float = Field(default=0.0, description="Remaining canary monitoring duration")
    sample_count: int = Field(default=0, description="Telemetry sample count evaluated")
    error_rate: float = Field(default=0.0, description="Observed prediction error rate (0.0 to 1.0)")
    latency_p95_ms: float = Field(default=0.0, description="Observed p95 prediction latency in milliseconds")
    drift_detected: bool = Field(default=False, description="True if severe drift detected during canary")
    reasons: List[str] = Field(default_factory=list, description="Diagnostic reasons for health determination")
    recommended_action: str = Field(default="NONE", description="NONE, ROLLBACK, or OBSERVE")
    evaluated_at: str = Field(description="ISO 8601 evaluation timestamp")


class PostPromotionEvaluateRequest(BaseModel):
    """Request to trigger or simulate post-promotion health evaluation."""

    simulate_error_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Simulate an error rate spike")
    simulate_latency_p95_ms: Optional[float] = Field(default=None, ge=0.0, description="Simulate a p95 latency spike")
    simulate_drift: Optional[bool] = Field(default=None, description="Simulate a drift alert")
    auto_rollback_on_failure: bool = Field(default=True, description="Autonomously execute rollback if UNHEALTHY")


class RollbackExecutionResponse(BaseModel):
    """Outcome of model rollback execution."""

    rollback_id: str = Field(description="Unique rollback operation identifier")
    status: str = Field(description="Outcome: ROLLED_BACK, SKIPPED_IDEMPOTENT, or FAILED")
    source_model_version: str = Field(description="Failed or replaced model version")
    restored_model_version: str = Field(description="Restored previous stable model version")
    restored_run_id: Optional[str] = Field(default=None, description="Associated MLflow run ID")
    trigger_type: str = Field(description="AUTOMATED or MANUAL")
    reason: str = Field(description="Detailed reason for rollback")
    executed_at: str = Field(description="ISO 8601 timestamp of rollback")
    audit_event_id: Optional[str] = Field(default=None, description="Cryptographic audit event ID")


class RollbackRecordResponse(BaseModel):
    """Historical rollback audit record."""

    rollback_id: str
    source_model_version: str
    restored_model_version: str
    trigger_type: str
    reason: str
    health_evidence: Optional[Dict[str, Any]] = None
    actor: str
    executed_at: str
    status: str


class RollbackListResponse(BaseModel):
    """List response for historical rollback events."""

    rollbacks: List[RollbackRecordResponse]
    total: int


class ActiveModelStatusResponse(BaseModel):
    """Authoritative active and fallback production model state."""

    active_version: str = Field(description="Currently active champion model version")
    active_run_id: str = Field(description="MLflow run ID for active champion")
    state: str = Field(description="Lifecycle state (PROMOTED, ROLLED_BACK, BASELINE)")
    model_name: str = Field(default="ProductionClassifier", description="Target model name")
    promoted_at: Optional[str] = Field(default=None, description="ISO 8601 promotion timestamp")
    previous_stable_version: Optional[str] = Field(default=None, description="Known-good fallback model version")
    previous_stable_run_id: Optional[str] = Field(default=None, description="Known-good fallback run ID")
    is_canary_active: bool = Field(description="Whether model is within active post-promotion canary window")
    canary_remaining_seconds: float = Field(description="Remaining canary window in seconds")
