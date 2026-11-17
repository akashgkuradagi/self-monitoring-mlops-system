"""REST API Route Handlers for Health, Telemetry, Protected Lifecycle, Model Inference, and Audit (CORE-01, SEC-01, SEC-02, ML-01)."""

from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
import pandas as pd

from app.config import settings
from app.models.baseline import baseline_classifier
from app.models.dl_monitor import dl_sequence_monitor
from app.models.ml_monitor import isolation_forest_detector
from app.models.qml_benchmark import qml_benchmark
from app.monitoring.alerting import alert_manager
from app.monitoring.collector import telemetry_collector
from app.monitoring.drift import drift_analyzer
from app.recovery.health_guard import post_promotion_health_guard
from app.recovery.orchestrator import recovery_orchestrator
from app.recovery.policy import recovery_policy_engine
from app.recovery.registry import candidate_registry
from app.recovery.validator import model_validator
from app.registry.mlflow_adapter import mlflow_adapter
from app.schemas.alert import (
    AlertAcknowledgeRequest,
    AlertResolveRequest,
    AlertResponse,
    AlertSummaryResponse,
    EvaluateAlertsResponse,
)
from app.schemas.anomaly import AnomalyBatchResponse, EvaluateAnomalyRequest
from app.schemas.audit import AuditEventResponse, AuditIntegrityResponse
from app.schemas.drift import DriftReportResponse, EvaluateDriftRequest
from app.schemas.health import HealthResponse
from app.schemas.inference import (
    ActiveModelResponse,
    PredictionRequest,
    PredictionResponse,
)
from app.schemas.metrics import MetricsSummaryResponse
from app.schemas.qml import QMLBenchmarkRequest, QMLBenchmarkResponse
from app.schemas.recovery import (
    ActiveModelStatusResponse,
    CandidateCreateRequest,
    CandidateListResponse,
    CandidateRecordResponse,
    CandidateValidationResponse,
    PolicyEvaluationRequest,
    PolicyEvaluationResponse,
    PolicyStatusResponse,
    PostPromotionEvaluateRequest,
    PostPromotionHealthResponse,
    RollbackListResponse,
    RollbackRecordResponse,
)
from app.schemas.security import (
    CandidateDecisionRequest,
    LoginRequest,
    RollbackRequest,
    TokenResponse,
)
from app.schemas.sequence import (
    EvaluateSequenceBatchRequest,
    EvaluateSequenceRequest,
    SequenceBatchResponse,
    SequenceScoreResponse,
)
from app.schemas.telemetry import PredictionEventRequest, PredictionEventResponse
from app.security.audit import audit_logger
from app.security.auth import (
    AuthenticatedUser,
    Role,
    create_access_token,
    require_admin,
    require_ml_engineer,
    require_viewer,
)

logger = logging.getLogger("mlops.api")
router = APIRouter()

# In-memory demo credentials for authentication verification (SR-01)
# Production deployments use external identity providers or secrets manager.
DEMO_USERS = {
    "admin": ("admin123", Role.ADMINISTRATOR),
    "engineer": ("engineer123", Role.ML_ENGINEER),
    "viewer": ("viewer123", Role.VIEWER),
}


# ==============================================================================
# Authentication Endpoint (SEC-01, SR-01, SEC-02)
# ==============================================================================


@router.post(
    "/api/v1/auth/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Obtain JWT Access Token",
    description="Authenticates credentials and returns a signed JWT bearer token containing RBAC claims.",
    tags=["Authentication"],
)
async def login_for_access_token(request: Request, form_data: LoginRequest) -> TokenResponse:
    """Authenticate username and password and issue signed JWT."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
    user_record = DEMO_USERS.get(form_data.username)

    if not user_record or user_record[0] != form_data.password:
        audit_logger.log_event(
            actor=form_data.username,
            role="ANONYMOUS",
            action="LOGIN_FAILED",
            resource="/api/v1/auth/token",
            outcome="FAILURE",
            correlation_id=corr_id,
            metadata={"reason": "Incorrect username or password"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _, role = user_record
    token = create_access_token(subject=form_data.username, role=role)
    expires_in = settings.jwt_access_token_expire_minutes * 60

    audit_logger.log_event(
        actor=form_data.username,
        role=role.value,
        action="LOGIN_SUCCESS",
        resource="/api/v1/auth/token",
        outcome="SUCCESS",
        correlation_id=corr_id,
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_seconds=expires_in,
        role=role,
        username=form_data.username,
    )


# ==============================================================================
# Public Health & Operational Telemetry Endpoints (CORE-01, FR-01, FR-02, NFR-07)
# ==============================================================================


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Service Health Check",
    description="Returns liveness/readiness status and application version (FR-01, TC-01). Public access.",
    tags=["Health"],
)
async def get_health() -> HealthResponse:
    """Return service health summary."""
    return HealthResponse(
        status="healthy",
        app_version=settings.app_version,
        environment=settings.environment,
    )


@router.post(
    "/api/v1/events/prediction",
    response_model=PredictionEventResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record Prediction Telemetry Event",
    description="Asynchronously ingests prediction telemetry (FR-02, TC-02). Service identity or client.",
    tags=["Telemetry"],
)
async def record_prediction_event(event: PredictionEventRequest) -> PredictionEventResponse:
    """Ingest a prediction telemetry event."""
    return telemetry_collector.record_event(event)


@router.get(
    "/api/v1/metrics/summary",
    response_model=MetricsSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Operational Metrics Summary",
    description="Returns aggregate throughput, error rates, percentile latencies, and drift status (NFR-07, TC-02).",
    tags=["Telemetry"],
)
async def get_metrics_summary() -> MetricsSummaryResponse:
    """Return aggregated operational telemetry metrics."""
    return telemetry_collector.get_metrics_summary()


# ==============================================================================
# Model Inference Endpoint (ML-01, FR-02, FR-04)
# ==============================================================================


@router.post(
    "/api/v1/models/predict",
    response_model=PredictionResponse,
    status_code=status.HTTP_200_OK,
    summary="Model Prediction & Telemetry Ingestion",
    description="Scores candidate tabular features and records telemetry event (ML-01, FR-02). Requires authenticated Viewer role.",
    tags=["Inference"],
)
async def predict_credit_risk(
    request: Request,
    payload: PredictionRequest,
    user: AuthenticatedUser = Depends(require_viewer),
) -> PredictionResponse:
    """Execute tabular credit risk prediction and pipe telemetry to collector."""
    # Ensure baseline model is fitted
    if not baseline_classifier.is_fitted:
        model_path = Path(settings.model_dir) / "baseline_model.joblib"
        if model_path.is_file():
            baseline_classifier.load(model_path)
        else:
            train_path = Path("data/reference/train.csv")
            if train_path.is_file():
                train_df = pd.read_csv(train_path)
                baseline_classifier.train(train_df)
                baseline_classifier.save(model_path)
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Baseline model is not fitted and reference training data is unavailable.",
                )

    corr_id = payload.request_id or request.headers.get("X-Correlation-ID") or f"req-{uuid.uuid4().hex[:10]}"
    features_dict = payload.model_dump(exclude={"request_id"})
    input_df = pd.DataFrame([features_dict])

    start_time = time.perf_counter()
    try:
        preds, probs = baseline_classifier.predict(input_df)
        latency_ms = (time.perf_counter() - start_time) * 1000.0
    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        telemetry_collector.record_event(
            PredictionEventRequest(
                request_id=corr_id,
                model_version=baseline_classifier.model_version,
                latency_ms=round(latency_ms, 2),
                success=False,
                feature_summary={"error": str(e)},
            )
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Inference failed: {str(e)}",
        )

    pred_class = int(preds[0])
    prob_default = round(float(probs[0]), 4)
    risk_label = "HIGH_RISK" if pred_class == 1 else "LOW_RISK"

    # Ingest prediction telemetry event (FR-02)
    telemetry_collector.record_event(
        PredictionEventRequest(
            request_id=corr_id,
            model_version=baseline_classifier.model_version,
            latency_ms=round(latency_ms, 2),
            success=True,
            feature_summary={
                "age": payload.age,
                "income": payload.income,
                "credit_score": payload.credit_score,
                "debt_to_income": payload.debt_to_income,
                "prediction": pred_class,
                "prob_default": prob_default,
            },
        )
    )

    return PredictionResponse(
        request_id=corr_id,
        prediction=pred_class,
        default_probability=prob_default,
        risk_label=risk_label,
        model_version=baseline_classifier.model_version,
        latency_ms=round(latency_ms, 2),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/api/v1/models/active",
    response_model=ActiveModelResponse,
    status_code=status.HTTP_200_OK,
    summary="Active Production Model Metadata",
    description="Returns metadata describing the active champion model in production (ML-01).",
    tags=["Model Lifecycle"],
)
async def get_active_model(user: AuthenticatedUser = Depends(require_viewer)) -> ActiveModelResponse:
    """Retrieve metadata for currently active production champion."""
    state = mlflow_adapter.get_active_model_state()
    if not state:
        return ActiveModelResponse(
            active_version=baseline_classifier.model_version,
            active_run_id="local-baseline",
            promoted_at=datetime.now(timezone.utc).isoformat(),
            previous_stable_version=None,
            state="PROMOTED",
            model_name=settings.mlflow_model_name,
        )
    return ActiveModelResponse(
        active_version=state["active_version"],
        active_run_id=state["active_run_id"],
        promoted_at=state["promoted_at"],
        previous_stable_version=state.get("previous_stable_version"),
        state=state["state"],
        model_name=settings.mlflow_model_name,
    )


# ==============================================================================
# Protected Model Lifecycle Endpoints (SEC-01, SEC-02, SR-01, SR-02, FR-11, SR-05)
# ==============================================================================


# ==============================================================================
# Recovery Policy & Candidate Lifecycle Endpoints (REC-01, FR-09, FR-10, FR-14, NFR-10, TC-16, TC-19)
# ==============================================================================


@router.get(
    "/api/v1/recovery/policy",
    response_model=PolicyStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Recovery Policy Engine Status",
    description="Returns current recovery policy configuration and cooldown suppression state (FR-09, TC-16).",
    tags=["Recovery"],
)
async def get_recovery_policy(
    user: AuthenticatedUser = Depends(require_viewer),
):
    """Retrieve operational status of the Recovery Policy Engine and cooldown window."""
    return recovery_policy_engine.get_status()


@router.post(
    "/api/v1/recovery/evaluate",
    response_model=PolicyEvaluationResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate Recovery Policy Trigger",
    description="Evaluates whether incoming drift or alert condition warrants model retraining, enforcing cooldown suppression (FR-09, TC-16).",
    tags=["Recovery"],
)
async def evaluate_recovery_policy(
    request: Request,
    body: PolicyEvaluationRequest,
    user: AuthenticatedUser = Depends(require_ml_engineer),
):
    """Evaluate recovery retraining trigger against active alerts and cooldown window."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
    now_iso = datetime.now(timezone.utc).isoformat()

    eval_status, eligible, reason, remaining = recovery_policy_engine.evaluate_trigger(
        drift_detected=body.drift_detected,
        severity=body.severity,
        context={
            "alert_ids": body.active_alert_ids,
            "trigger_source": body.trigger_source,
            "actor": user.username,
        },
    )

    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="RECOVERY_POLICY_EVALUATED",
        resource="policy:recovery",
        outcome=eval_status,
        correlation_id=corr_id,
        metadata={
            "candidate_eligible": eligible,
            "reason": reason,
            "cooldown_remaining_seconds": remaining,
            "drift_detected": body.drift_detected,
            "severity": body.severity,
        },
    )

    return PolicyEvaluationResponse(
        status=eval_status,
        candidate_eligible=eligible,
        reason=reason,
        cooldown_remaining_seconds=remaining,
        evaluated_at=now_iso,
    )


@router.get(
    "/api/v1/candidates",
    response_model=CandidateListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Model Candidates",
    description="Lists candidate model records and their current lifecycle statuses (FR-10, FR-14).",
    tags=["Model Lifecycle"],
)
async def list_candidates(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by candidate status"),
    limit: int = Query(50, ge=1, le=200),
    user: AuthenticatedUser = Depends(require_viewer),
):
    """List candidate model lifecycle records."""
    records = candidate_registry.list_candidates(status=status_filter, limit=limit)
    return CandidateListResponse(
        candidates=[CandidateRecordResponse(**r) for r in records],
        total=len(records),
    )


@router.get(
    "/api/v1/candidates/{candidate_id}",
    response_model=CandidateRecordResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Model Candidate Details",
    description="Fetches single model candidate by ID (FR-10, FR-14).",
    tags=["Model Lifecycle"],
)
async def get_candidate_details(
    candidate_id: str,
    user: AuthenticatedUser = Depends(require_viewer),
):
    """Retrieve details for a specific model candidate."""
    cand = candidate_registry.get_candidate(candidate_id)
    if not cand:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate '{candidate_id}' not found.",
        )
    return CandidateRecordResponse(**cand)


@router.post(
    "/api/v1/candidates",
    response_model=CandidateRecordResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register New Model Candidate",
    description="Registers a new candidate model in PENDING_VALIDATION state (FR-09, FR-10).",
    tags=["Model Lifecycle"],
)
async def create_candidate(
    request: Request,
    body: CandidateCreateRequest,
    user: AuthenticatedUser = Depends(require_ml_engineer),
):
    """Register a new retrained candidate model in SQLite registry."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    # Architectural Firewall Enforcement (EXP-03, FR-07, TC-19):
    if "qml" in body.candidate_id.lower() or "qml" in body.model_version.lower():
        audit_logger.log_event(
            actor=user.username,
            role=user.role.value,
            action="FIREWALL_BLOCKED_CANDIDATE_CREATION",
            resource=f"candidate:{body.candidate_id}",
            outcome="BLOCKED",
            correlation_id=corr_id,
            metadata={"reason": "QML models are benchmark-only and cannot be registered as candidates"},
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="QML models are benchmark-only and cannot be registered as candidates",
        )

    cand = candidate_registry.create_candidate(
        candidate_id=body.candidate_id,
        model_version=body.model_version,
        model_name=body.model_name or "ProductionClassifier",
        run_id=body.run_id,
        trigger_reason=body.trigger_reason,
        status="PENDING_VALIDATION",
    )

    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="CANDIDATE_CREATED",
        resource=f"candidate:{body.candidate_id}",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={"model_version": body.model_version, "run_id": body.run_id},
    )

    return CandidateRecordResponse(**cand)


@router.post(
    "/api/v1/candidates/{candidate_id}/validate",
    response_model=CandidateValidationResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate Model Candidate",
    description="Executes automated validation gates on candidate model. Requires ML_Engineer or Administrator role (SR-01, SR-02, FR-10).",
    tags=["Model Lifecycle"],
)
async def validate_candidate(
    request: Request,
    candidate_id: str,
    user: AuthenticatedUser = Depends(require_ml_engineer),
):
    """Validate candidate against golden test fixtures, schema, and latency limits."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    # Architectural Firewall Enforcement (EXP-03, FR-07, TC-19):
    if "qml" in candidate_id.lower():
        audit_logger.log_event(
            actor=user.username,
            role=user.role.value,
            action="FIREWALL_BLOCKED_VALIDATION",
            resource=f"candidate:{candidate_id}",
            outcome="BLOCKED",
            correlation_id=corr_id,
            metadata={
                "reason": "QML models are benchmark-only and cannot be validated for candidate promotion",
                "violation": "TC-19_FIREWALL_BREACH",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="QML models are benchmark-only and cannot be validated for candidate promotion",
        )

    # Ensure candidate exists in registry
    cand = candidate_registry.get_candidate(candidate_id)
    if not cand:
        candidate_registry.create_candidate(
            candidate_id=candidate_id,
            model_version=candidate_id,
            trigger_reason="On-demand validation",
            status="PENDING_VALIDATION",
        )

    # Run automated validation gates (FR-10, NFR-10)
    validation_resp = model_validator.validate_candidate(
        candidate_id=candidate_id,
        validated_by=user.username,
    )

    outcome = "SUCCESS" if validation_resp.all_passed else "FAILED"

    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="CANDIDATE_VALIDATE",
        resource=f"candidate:{candidate_id}",
        outcome=outcome,
        correlation_id=corr_id,
        metadata={
            "run_id": validation_resp.run_id,
            "checks": validation_resp.checks,
            "all_passed": validation_resp.all_passed,
            "status": validation_resp.status,
        },
    )

    return validation_resp


@router.post(
    "/api/v1/candidates/{candidate_id}/decision",
    status_code=status.HTTP_200_OK,
    summary="Approve or Reject Validated Candidate",
    description="Enforces dual-gate human-in-the-loop candidate decision. Requires ML_Engineer or Administrator role (FR-10, FR-14, SR-01, SR-02, TC-07, TC-11).",
    tags=["Model Lifecycle"],
)
async def decide_candidate(
    request: Request,
    candidate_id: str,
    body: CandidateDecisionRequest,
    user: AuthenticatedUser = Depends(require_ml_engineer),
):
    """Record human approval or rejection of a validated candidate."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    # Architectural Firewall Enforcement (EXP-03, FR-07, TC-19):
    if "qml" in candidate_id.lower():
        audit_logger.log_event(
            actor=user.username,
            role=user.role.value,
            action="FIREWALL_BLOCKED_PROMOTION",
            resource=f"candidate:{candidate_id}",
            outcome="BLOCKED",
            correlation_id=corr_id,
            metadata={
                "reason": "QML models are benchmark-only and cannot be promoted",
                "violation": "TC-19_FIREWALL_BREACH",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="QML models are benchmark-only and cannot be promoted",
        )

    cand = candidate_registry.get_candidate(candidate_id)
    decision_norm = "PROMOTED" if body.decision.upper().startswith("APPROV") else "REJECTED"

    # Automated Validation Gate Enforcement (FR-10, NFR-10, TC-07):
    # Promotion is strictly blocked if the candidate has not passed automated validation gates.
    if decision_norm == "PROMOTED":
        if not cand or cand.get("status") not in ("PENDING_APPROVAL", "VALIDATED", "PROMOTED"):
            audit_logger.log_event(
                actor=user.username,
                role=user.role.value,
                action="PROMOTION_BLOCKED_UNVALIDATED",
                resource=f"candidate:{candidate_id}",
                outcome="BLOCKED",
                correlation_id=corr_id,
                metadata={
                    "reason": "Candidate cannot be promoted without passing automated validation gates",
                    "current_status": cand.get("status") if cand else "NOT_FOUND",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Candidate cannot be promoted without passing automated validation gates.",
            )

        run_id = (cand.get("run_id") if cand else None) or f"run-{uuid.uuid4().hex[:8]}"
        model_version = (cand.get("model_version") if cand else None) or candidate_id
        mlflow_adapter.promote_to_champion(run_id=run_id, model_version=model_version)
        candidate_registry.update_decision(
            candidate_id=candidate_id,
            status="PROMOTED",
            decided_by=user.username,
            comment=body.comment,
        )
        action = "CANDIDATE_PROMOTED"
    else:
        if cand:
            candidate_registry.update_decision(
                candidate_id=candidate_id,
                status="REJECTED",
                decided_by=user.username,
                comment=body.comment,
            )
        action = "CANDIDATE_REJECTED"

    audit_event = audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action=action,
        resource=f"candidate:{candidate_id}",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={"comment": body.comment, "decision": decision_norm},
    )

    return {
        "candidate_id": candidate_id,
        "state": decision_norm,
        "comment": body.comment,
        "approver": user.username,
        "role": user.role.value,
        "audit_event_id": audit_event.event_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/api/v1/models/active",
    response_model=ActiveModelStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Active Production Model State",
    description="Returns current champion model, previous stable fallback, and canary monitoring state (FR-12, REC-02).",
    tags=["Model Lifecycle"],
)
async def get_active_model_info(
    user: AuthenticatedUser = Depends(require_viewer),
):
    """Retrieve authoritative active model state and canary window status."""
    state = mlflow_adapter.get_active_model_state()
    canary = post_promotion_health_guard.get_canary_status()

    if not state:
        return ActiveModelStatusResponse(
            active_version=settings.default_model_version,
            active_run_id="baseline-run-001",
            state="BASELINE",
            promoted_at=None,
            previous_stable_version=None,
            previous_stable_run_id=None,
            is_canary_active=False,
            canary_remaining_seconds=0.0,
        )

    return ActiveModelStatusResponse(
        active_version=state.get("active_version", settings.default_model_version),
        active_run_id=state.get("active_run_id", "baseline-run-001"),
        state=state.get("state", "PROMOTED"),
        model_name=settings.mlflow_model_name,
        promoted_at=state.get("promoted_at"),
        previous_stable_version=state.get("previous_stable_version"),
        previous_stable_run_id=state.get("previous_stable_run_id"),
        is_canary_active=canary["is_canary_active"],
        canary_remaining_seconds=canary["canary_remaining_seconds"],
    )


@router.get(
    "/api/v1/recovery/health",
    response_model=PostPromotionHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Post-Promotion Health Report",
    description="Returns the post-promotion canary health status and signals for the active model (FR-12, REC-02).",
    tags=["Recovery"],
)
async def get_post_promotion_health(
    user: AuthenticatedUser = Depends(require_viewer),
):
    """Retrieve post-promotion canary health diagnostics for the active model."""
    return post_promotion_health_guard.evaluate_health()


@router.post(
    "/api/v1/recovery/health/evaluate",
    status_code=status.HTTP_200_OK,
    summary="Evaluate Post-Promotion Health & Automated Rollback",
    description="Evaluates live or simulated canary health and autonomously executes rollback if UNHEALTHY (FR-12, TC-08, TC-20).",
    tags=["Recovery"],
)
async def evaluate_post_promotion_health(
    request: Request,
    body: PostPromotionEvaluateRequest,
    user: AuthenticatedUser = Depends(require_ml_engineer),
):
    """Evaluate canary health with optional fault simulation and automated rollback trigger."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    health_report = post_promotion_health_guard.evaluate_health(
        simulate_error_rate=body.simulate_error_rate,
        simulate_latency_p95_ms=body.simulate_latency_p95_ms,
        simulate_drift=body.simulate_drift,
    )

    rollback_res = None
    if body.auto_rollback_on_failure and health_report.recommended_action == "ROLLBACK":
        reasons_str = "; ".join(health_report.reasons)
        try:
            rollback_res = recovery_orchestrator.execute_rollback(
                reason=f"Automated post-promotion guard failure: {reasons_str}",
                actor=f"{user.username} (automated_guard)",
                trigger_type="AUTOMATED",
                health_evidence=health_report.model_dump(),
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.error("Auto-rollback execution failed: %s", e)

    return {
        "health_report": health_report,
        "rollback_triggered": rollback_res is not None,
        "rollback_result": rollback_res,
        "evaluated_by": user.username,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/api/v1/recovery/rollbacks",
    response_model=RollbackListResponse,
    status_code=status.HTTP_200_OK,
    summary="List Rollback History",
    description="Returns chronological audit history of all manual and automated rollback events (FR-12, TC-08).",
    tags=["Recovery"],
)
async def list_rollbacks(
    limit: int = Query(50, ge=1, le=200),
    user: AuthenticatedUser = Depends(require_viewer),
):
    """Retrieve historical rollback events."""
    records = recovery_orchestrator.list_rollbacks(limit=limit)
    return RollbackListResponse(
        rollbacks=[RollbackRecordResponse(**r) for r in records],
        total=len(records),
    )


@router.post(
    "/api/v1/models/rollback",
    status_code=status.HTTP_200_OK,
    summary="Rollback Model to Stable Baseline",
    description="Triggers rollback of active production model. Requires ML_Engineer or Administrator role (SR-01, SR-02, TC-08).",
    tags=["Model Lifecycle"],
)
async def rollback_model(
    request: Request,
    body: RollbackRequest,
    user: AuthenticatedUser = Depends(require_ml_engineer),
):
    """Roll back model to previous stable reference baseline."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    try:
        res = recovery_orchestrator.execute_rollback(
            target_version=body.target_version,
            reason=body.reason,
            actor=user.username,
            trigger_type="MANUAL",
            correlation_id=corr_id,
        )
    except FileNotFoundError as fnf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(fnf),
        )
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT if "firewall" in str(ve).lower() else status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        )
    except Exception as e:
        logger.error("Rollback execution error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rollback execution error: {e}",
        )

    return {
        "status": res.status,
        "active_version": res.restored_model_version,
        "restored_version": res.restored_model_version,
        "source_version": res.source_model_version,
        "rollback_id": res.rollback_id,
        "reason": res.reason,
        "initiated_by": user.username,
        "role": user.role.value,
        "timestamp": res.executed_at,
    }


# ==============================================================================
# Statistical Drift Detection Endpoints (MON-01, FR-03, FR-08, TC-03, TC-04)
# ==============================================================================


@router.post(
    "/api/v1/monitoring/drift/evaluate",
    response_model=DriftReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate Statistical Feature Drift",
    description="Executes two-sample KS and PSI drift analysis against baseline reference. Requires ML_Engineer or Administrator role (MON-01, FR-03, FR-08).",
    tags=["Monitoring"],
)
async def evaluate_drift(
    request: Request,
    payload: Optional[EvaluateDriftRequest] = None,
    user: AuthenticatedUser = Depends(require_ml_engineer),
) -> DriftReportResponse:
    """Evaluate statistical drift on supplied dataset, records, or active telemetry buffer."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    current_df: Optional[pd.DataFrame] = None
    if payload:
        if payload.dataset_path:
            p = Path(payload.dataset_path)
            if not p.is_file():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Evaluation dataset file not found at: {payload.dataset_path}",
                )
            current_df = pd.read_csv(p)
        elif payload.records:
            current_df = pd.DataFrame(payload.records)

    if current_df is None:
        # Extract features from recent telemetry events
        recent = telemetry_collector.get_recent_events()
        feature_rows = [
            ev["feature_summary"]
            for ev in recent
            if ev.get("feature_summary") and isinstance(ev["feature_summary"], dict)
        ]
        current_df = pd.DataFrame(feature_rows) if feature_rows else pd.DataFrame()

    report = drift_analyzer.evaluate_drift(current_df=current_df)

    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="DRIFT_EVALUATION",
        resource="drift_engine",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={
            "overall_status": report.overall_drift_status,
            "is_drifted": report.is_drifted,
            "drifted_features": report.drifted_features,
            "sample_count": report.current_sample_count,
        },
    )

    return report


@router.get(
    "/api/v1/monitoring/drift/report",
    response_model=DriftReportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Latest Drift Report",
    description="Returns the latest computed drift report or evaluates current buffer against reference. Accessible to Viewer role (MON-01, FR-03).",
    tags=["Monitoring"],
)
async def get_drift_report(
    user: AuthenticatedUser = Depends(require_viewer),
) -> DriftReportResponse:
    """Retrieve the latest drift evaluation report."""
    latest = drift_analyzer.get_latest_report()
    if latest is not None:
        return latest

    # If no report has been generated yet, evaluate current buffer
    recent = telemetry_collector.get_recent_events()
    feature_rows = [
        ev["feature_summary"]
        for ev in recent
        if ev.get("feature_summary") and isinstance(ev["feature_summary"], dict)
    ]
    current_df = pd.DataFrame(feature_rows) if feature_rows else pd.DataFrame()
    return drift_analyzer.evaluate_drift(current_df=current_df)


@router.post(
    "/api/v1/monitoring/anomaly/evaluate",
    response_model=AnomalyBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate Anomaly Scores via Isolation Forest",
    description="Scores input observations using unsupervised Isolation Forest detector (EXP-01, FR-05). Accessible to Viewer role.",
    tags=["Monitoring"],
)
async def evaluate_anomalies(
    request: Request,
    payload: Optional[EvaluateAnomalyRequest] = None,
    user: AuthenticatedUser = Depends(require_viewer),
) -> AnomalyBatchResponse:
    """Evaluate batch anomaly scores using trained Isolation Forest detector."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    # Ensure detector is fitted or load from models directory
    if not isolation_forest_detector.is_fitted:
        joblib_path = Path(settings.model_dir) / "isolation_forest.joblib"
        if joblib_path.is_file():
            isolation_forest_detector.load(joblib_path)
        else:
            train_path = Path("data/reference/train.csv")
            if train_path.is_file():
                train_df = pd.read_csv(train_path)
                isolation_forest_detector.train(train_df)
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Anomaly detector model artifact not found and reference training data unavailable",
                )

    current_df: Optional[pd.DataFrame] = None
    if payload:
        if payload.dataset_path:
            p = Path(payload.dataset_path)
            if not p.is_file():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Evaluation dataset file not found at: {payload.dataset_path}",
                )
            current_df = pd.read_csv(p)
        elif payload.records:
            current_df = pd.DataFrame(payload.records)

    if current_df is None:
        recent = telemetry_collector.get_recent_events()
        feature_rows = [
            ev["feature_summary"]
            for ev in recent
            if ev.get("feature_summary") and isinstance(ev["feature_summary"], dict)
        ]
        current_df = pd.DataFrame(feature_rows) if feature_rows else pd.DataFrame()

    batch_res = isolation_forest_detector.evaluate_batch(current_df)

    # Log audit event for anomaly evaluation
    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="ANOMALY_EVALUATED",
        resource="anomaly_detector:isolation_forest",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={
            "total_samples": batch_res.total_samples,
            "anomaly_count": batch_res.anomaly_count,
            "anomaly_ratio": batch_res.anomaly_ratio,
            "is_anomaly_detected": batch_res.is_anomaly_detected,
        },
    )

    return batch_res


@router.post(
    "/api/v1/monitoring/sequence/evaluate",
    response_model=SequenceScoreResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate Sequence Temporal Shift via PyTorch LSTM",
    description="Scores an input observation sequence using unsupervised PyTorch LSTM Autoencoder (EXP-02, FR-06). Accessible to Viewer role.",
    tags=["Monitoring"],
)
async def evaluate_sequence(
    request: Request,
    payload: EvaluateSequenceRequest,
    user: AuthenticatedUser = Depends(require_viewer),
) -> SequenceScoreResponse:
    """Evaluate temporal shift / reconstruction loss on a sequential observation window."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    # Ensure monitor is fitted or load from models directory
    if not dl_sequence_monitor.is_fitted:
        models_dir = Path(settings.model_dir)
        if (models_dir / "dl_sequence_monitor.pt").is_file():
            dl_sequence_monitor.load(models_dir)
        else:
            train_path = Path("data/reference/train.csv")
            val_path = Path("data/reference/val.csv")
            if train_path.is_file():
                train_df = pd.read_csv(train_path)
                val_df = pd.read_csv(val_path) if val_path.is_file() else None
                dl_sequence_monitor.fit_sequence_model(train_df=train_df, val_df=val_df)
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="LSTM sequence monitor model artifact not found and reference training data unavailable",
                )

    if len(payload.sequence) < dl_sequence_monitor.window_size:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Sequence length {len(payload.sequence)} is insufficient. "
                f"Minimum required sequence length is {dl_sequence_monitor.window_size}."
            ),
        )

    records = [rec.model_dump() for rec in payload.sequence]
    try:
        score_res = dl_sequence_monitor.score_window(records)
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(ve),
        )

    # Log audit event for sequence evaluation
    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="SEQUENCE_EVALUATED",
        resource="dl_sequence_monitor:lstm",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={
            "window_size": score_res["window_size"],
            "reconstruction_error": score_res["reconstruction_error"],
            "threshold": score_res["threshold"],
            "is_shift": score_res["is_shift"],
            "anomaly_score": score_res["anomaly_score"],
        },
    )

    return SequenceScoreResponse(**score_res)


@router.post(
    "/api/v1/monitoring/sequence/batch",
    response_model=SequenceBatchResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate Multiple Temporal Windows via PyTorch LSTM",
    description="Evaluates sliding temporal windows over an observation stream (EXP-02, FR-06). Accessible to Viewer role.",
    tags=["Monitoring"],
)
async def evaluate_sequence_batch(
    request: Request,
    payload: EvaluateSequenceBatchRequest,
    user: AuthenticatedUser = Depends(require_viewer),
) -> SequenceBatchResponse:
    """Evaluate sliding temporal windows across a batch of sequential observations."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    # Ensure monitor is fitted or load from models directory
    if not dl_sequence_monitor.is_fitted:
        models_dir = Path(settings.model_dir)
        if (models_dir / "dl_sequence_monitor.pt").is_file():
            dl_sequence_monitor.load(models_dir)
        else:
            train_path = Path("data/reference/train.csv")
            val_path = Path("data/reference/val.csv")
            if train_path.is_file():
                train_df = pd.read_csv(train_path)
                val_df = pd.read_csv(val_path) if val_path.is_file() else None
                dl_sequence_monitor.fit_sequence_model(train_df=train_df, val_df=val_df)
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="LSTM sequence monitor model artifact not found and reference training data unavailable",
                )

    records = [rec.model_dump() for rec in payload.records]
    try:
        eval_res = dl_sequence_monitor.evaluate(
            dataset=records,
            window_size=payload.window_size,
            step_size=payload.step_size,
        )
    except ValueError as ve:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(ve),
        )

    # Log audit event for sequence batch evaluation
    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="SEQUENCE_BATCH_EVALUATED",
        resource="dl_sequence_monitor:lstm",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={
            "total_records": eval_res["total_records"],
            "total_windows": eval_res["total_windows"],
            "shift_windows_count": eval_res["shift_windows_count"],
            "shift_ratio": eval_res["shift_ratio"],
        },
    )

    return SequenceBatchResponse(**eval_res)


@router.post(
    "/api/v1/monitoring/qml/benchmark",
    response_model=QMLBenchmarkResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Experimental QML Benchmark (EXP-03, FR-07)",
    description=(
        "Executes an isolated Variational Quantum Classifier (VQC) benchmark using PennyLane "
        "on the local default.qubit simulator. Strictly isolated by Architectural Firewall (TC-19)."
    ),
    tags=["Monitoring"],
)
async def execute_qml_benchmark(
    request: Request,
    payload: Optional[QMLBenchmarkRequest] = None,
    user: AuthenticatedUser = Depends(require_ml_engineer),
) -> QMLBenchmarkResponse:
    """Run simulated QML circuit benchmark and classical baseline comparison."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    train_path = Path("data/reference/train.csv")
    val_path = Path("data/reference/val.csv")
    if not train_path.is_file() or not val_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reference datasets (train.csv / val.csv) are unavailable for QML benchmarking",
        )

    req = payload or QMLBenchmarkRequest()
    train_df = pd.read_csv(train_path)
    val_df = pd.read_csv(val_path)

    try:
        benchmark_res = qml_benchmark.run_benchmark(
            train_df=train_df,
            eval_df=val_df,
            n_train=req.n_train_samples,
            n_eval=req.n_eval_samples,
            epochs=req.epochs,
            lr=req.learning_rate,
        )
    except Exception as e:
        logger.exception("QML benchmark execution failure: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"QML simulation failed: {str(e)}",
        )

    # Log audit event
    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="QML_BENCHMARK_EXECUTED",
        resource="qml_benchmark:vqc",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={
            "n_train": req.n_train_samples,
            "n_eval": req.n_eval_samples,
            "epochs": req.epochs,
            "qml_accuracy": benchmark_res["quantum_metrics"]["accuracy"],
            "benchmark_only": True,
            "firewall_enforced": True,
        },
    )

    return QMLBenchmarkResponse(**benchmark_res)


# ==============================================================================
# Multi-Metric Alerting & Threshold Endpoints (ALERT-01, FR-08, NFR-07)
# ==============================================================================


@router.get(
    "/api/v1/alerts",
    response_model=List[AlertResponse],
    status_code=status.HTTP_200_OK,
    summary="List Operational Alerts",
    description="Returns active and historical alerts with optional status, severity, and metric filtering (FR-08, SAD Table 4). Accessible to Viewer.",
    tags=["Alerts"],
)
async def list_alerts(
    status: Optional[str] = Query(default=None, description="Filter by status (ACTIVE, ACKNOWLEDGED, RESOLVED)"),
    severity: Optional[str] = Query(default=None, description="Filter by severity (LOW, MEDIUM, HIGH)"),
    metric: Optional[str] = Query(default=None, description="Filter by metric name"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    user: AuthenticatedUser = Depends(require_viewer),
) -> List[AlertResponse]:
    """Retrieve filtered list of alerts."""
    return alert_manager.list_alerts(
        status=status,
        severity=severity,
        metric=metric,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/api/v1/alerts/summary",
    response_model=AlertSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Alert Aggregation Summary",
    description="Returns aggregate counts of alerts partitioned by status, severity, and metric (FR-13, NFR-08).",
    tags=["Alerts"],
)
async def get_alerts_summary(
    user: AuthenticatedUser = Depends(require_viewer),
) -> AlertSummaryResponse:
    """Retrieve aggregate counts across alert states and severities."""
    return alert_manager.get_summary()


@router.get(
    "/api/v1/alerts/{alert_id}",
    response_model=AlertResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Alert Details",
    description="Retrieves a single alert record by identifier (FR-08). Accessible to Viewer.",
    tags=["Alerts"],
)
async def get_alert(
    alert_id: str,
    user: AuthenticatedUser = Depends(require_viewer),
) -> AlertResponse:
    """Retrieve full details for an individual alert."""
    alert = alert_manager.get_alert(alert_id)
    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert '{alert_id}' not found",
        )
    return alert


@router.post(
    "/api/v1/alerts/{alert_id}/acknowledge",
    response_model=AlertResponse,
    status_code=status.HTTP_200_OK,
    summary="Acknowledge Active Alert",
    description="Transitions an active alert to ACKNOWLEDGED state. Requires ML_Engineer or Administrator role (FR-08, SEC-02).",
    tags=["Alerts"],
)
async def acknowledge_alert(
    request: Request,
    alert_id: str,
    payload: Optional[AlertAcknowledgeRequest] = None,
    user: AuthenticatedUser = Depends(require_ml_engineer),
) -> AlertResponse:
    """Acknowledge an active alert and record audit trail."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
    comment = payload.comment if payload else None

    updated = alert_manager.acknowledge_alert(alert_id=alert_id, actor=user.username, comment=comment)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert '{alert_id}' not found",
        )

    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="ALERT_ACKNOWLEDGE",
        resource=f"alert:{alert_id}",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={"comment": comment, "metric": updated.metric_name, "severity": updated.severity},
    )

    return updated


@router.post(
    "/api/v1/alerts/{alert_id}/resolve",
    response_model=AlertResponse,
    status_code=status.HTTP_200_OK,
    summary="Resolve Alert",
    description="Transitions an alert to RESOLVED state. Requires ML_Engineer or Administrator role (FR-08, SEC-02).",
    tags=["Alerts"],
)
async def resolve_alert(
    request: Request,
    alert_id: str,
    payload: Optional[AlertResolveRequest] = None,
    user: AuthenticatedUser = Depends(require_ml_engineer),
) -> AlertResponse:
    """Mark an alert as resolved and record audit event."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
    comment = payload.comment if payload else None

    updated = alert_manager.resolve_alert(alert_id=alert_id, actor=user.username, comment=comment)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert '{alert_id}' not found",
        )

    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="ALERT_RESOLVE",
        resource=f"alert:{alert_id}",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={"comment": comment, "metric": updated.metric_name, "severity": updated.severity},
    )

    return updated


@router.post(
    "/api/v1/monitoring/alerts/evaluate",
    response_model=EvaluateAlertsResponse,
    status_code=status.HTTP_200_OK,
    summary="Trigger Alert Evaluation",
    description="Evaluates latest drift report and operational telemetry against thresholds, generating or deduplicating alerts (ALERT-01, FR-08). Requires ML_Engineer.",
    tags=["Alerts"],
)
async def trigger_alert_evaluation(
    request: Request,
    user: AuthenticatedUser = Depends(require_ml_engineer),
) -> EvaluateAlertsResponse:
    """Evaluate active metrics against thresholds and trigger alerts."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")

    drift_report = drift_analyzer.get_latest_report()
    metrics = telemetry_collector.get_metrics_summary()

    eval_results = alert_manager.evaluate_all(drift_report=drift_report, metrics=metrics)
    new_alerts = [alert for alert, is_dup in eval_results if not is_dup]
    dedup_alerts = [alert for alert, is_dup in eval_results if is_dup]
    all_evaluated = [alert for alert, _ in eval_results]

    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="ALERT_EVALUATION",
        resource="alert_manager",
        outcome="SUCCESS",
        correlation_id=corr_id,
        metadata={
            "total_evaluated": len(eval_results),
            "new_alerts_count": len(new_alerts),
            "deduplicated_count": len(dedup_alerts),
        },
    )

    summary = alert_manager.get_summary()

    return EvaluateAlertsResponse(
        alerts_evaluated=len(eval_results),
        new_alerts_count=len(new_alerts),
        deduplicated_alerts_count=len(dedup_alerts),
        active_alerts_count=summary.active_alerts,
        alerts=all_evaluated,
    )


# ==============================================================================
# Audit Log & Cryptographic Verification Endpoints (SEC-02, FR-11, SR-05, SEC-05)
# ==============================================================================


@router.get(
    "/api/v1/audit/events",
    response_model=List[AuditEventResponse],
    status_code=status.HTTP_200_OK,
    summary="List Audit Log Events",
    description="Returns structured audit entries. Accessible to ML_Engineer and Administrator roles (FR-11, SR-05).",
    tags=["Audit"],
)
async def list_audit_events(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    action: Optional[str] = Query(default=None),
    actor: Optional[str] = Query(default=None),
    user: AuthenticatedUser = Depends(require_ml_engineer),
) -> List[AuditEventResponse]:
    """Retrieve audit events."""
    return audit_logger.get_events(limit=limit, offset=offset, action=action, actor=actor)


@router.get(
    "/api/v1/audit/verify",
    response_model=AuditIntegrityResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify Audit Hash Chain Integrity",
    description="Cryptographically verifies the SHA-256 hash chain across all audit records. Restricted to Administrator (SEC-05, SR-05).",
    tags=["Audit"],
)
async def verify_audit_integrity(
    request: Request,
    user: AuthenticatedUser = Depends(require_admin),
) -> AuditIntegrityResponse:
    """Execute cryptographic verification of audit records hash chain."""
    corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
    integrity_result = audit_logger.verify_integrity()

    audit_logger.log_event(
        actor=user.username,
        role=user.role.value,
        action="AUDIT_VERIFY",
        resource="audit_log",
        outcome="SUCCESS" if integrity_result.is_valid else "FAILURE",
        correlation_id=corr_id,
        metadata={
            "total_records": integrity_result.total_records,
            "is_valid": integrity_result.is_valid,
            "issues_count": len(integrity_result.issues),
        },
    )

    return integrity_result
