"""Comprehensive Test Suite for Recovery Policy, Automated Validation Gates, and Promotion Decision API (REC-01).

Fulfills:
- FR-09: Recovery policy evaluation and cooldown suppression (TC-16).
- FR-10: Automated candidate validation gates (Artifact, Schema, Regression, Latency).
- FR-14: Human-in-the-loop candidate decision API (TC-11).
- NFR-10: Automation safety (Dual-Gate promotion control, TC-07).
- TC-19: QML Architectural Firewall enforcement.
"""

import time
from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
import pytest
from sklearn.base import BaseEstimator, ClassifierMixin

from app.recovery.policy import RecoveryPolicyEngine, recovery_policy_engine
from app.recovery.registry import candidate_registry
from app.recovery.validator import ModelValidator, model_validator
from app.registry.mlflow_adapter import mlflow_adapter
from app.schemas.recovery import CandidateStatus, GateStatus
from app.security.audit import audit_logger
from app.security.auth import Role, create_access_token


class MockDegradedClassifier(BaseEstimator, ClassifierMixin):
    """Mock classifier that always predicts 0 (failing accuracy/F1 regression gate)."""

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.zeros(len(X), dtype=int)

    def predict_proba(self, X):
        probs = np.zeros((len(X), 2))
        probs[:, 0] = 1.0
        return probs


class MockHighLatencyClassifier(BaseEstimator, ClassifierMixin):
    """Mock classifier with artificial latency (failing latency gate)."""

    def __init__(self, delay_sec: float = 0.03):
        self.delay_sec = delay_sec

    def fit(self, X, y):
        return self

    def predict(self, X):
        time.sleep(self.delay_sec)
        return np.ones(len(X), dtype=int)

    def predict_proba(self, X):
        time.sleep(self.delay_sec)
        probs = np.zeros((len(X), 2))
        probs[:, 1] = 1.0
        return probs


@pytest.fixture(autouse=True)
def clean_recovery_state():
    """Reset recovery engine cooldown, candidate registry, and restore active model state before/after each test."""
    import sqlite3

    def _reset_active_state():
        recovery_policy_engine.reset_cooldown()
        candidate_registry.clear()
        try:
            with sqlite3.connect(mlflow_adapter.db_path) as conn:
                conn.execute("""
                    INSERT INTO active_model_state (
                        id, active_version, active_run_id, promoted_at,
                        previous_stable_version, previous_stable_run_id, state
                    ) VALUES (1, 'baseline-v1', 'baseline-run-001', '2026-01-01T00:00:00Z', NULL, NULL, 'PROMOTED')
                    ON CONFLICT(id) DO UPDATE SET
                        active_version = 'baseline-v1',
                        active_run_id = 'baseline-run-001',
                        state = 'PROMOTED';
                """)
                conn.commit()
        except Exception:
            pass

    _reset_active_state()
    yield
    _reset_active_state()


@pytest.fixture
def engineer_token() -> str:
    return create_access_token(subject="alice_engineer", role=Role.ML_ENGINEER)


@pytest.fixture
def viewer_token() -> str:
    return create_access_token(subject="bob_viewer", role=Role.VIEWER)


@pytest.fixture
def admin_token() -> str:
    return create_access_token(subject="charlie_admin", role=Role.ADMINISTRATOR)


# ==============================================================================
# 1. Recovery Policy Engine & Cooldown Suppression (FR-09, TC-16)
# ==============================================================================


def test_tc16_recovery_policy_cooldown_suppression():
    """TC-16: Recovery policy approves first drift trigger and suppresses subsequent triggers within cooldown."""
    engine = RecoveryPolicyEngine(cooldown_seconds=600)

    # 1. Non-drift / no severe alerts -> INELIGIBLE
    status, eligible, reason, rem = engine.evaluate_trigger(drift_detected=False, severity="LOW")
    assert status == "INELIGIBLE"
    assert not eligible

    # 2. First critical drift breach -> TRIGGER_APPROVED
    status, eligible, reason, rem = engine.evaluate_trigger(
        drift_detected=True,
        severity="HIGH",
        context={"test": "initial_breach"},
    )
    assert status == "TRIGGER_APPROVED"
    assert eligible
    assert rem == 0.0

    # 3. Second breach within cooldown (immediately after) -> SUPPRESSED_COOLDOWN
    status2, eligible2, reason2, rem2 = engine.evaluate_trigger(
        drift_detected=True,
        severity="HIGH",
        context={"test": "duplicate_breach_1"},
    )
    assert status2 == "SUPPRESSED_COOLDOWN"
    assert not eligible2
    assert rem2 > 0.0
    assert "Cooldown active" in reason2

    # 4. Third breach also suppressed
    status3, eligible3, reason3, rem3 = engine.evaluate_trigger(
        drift_detected=True,
        severity="CRITICAL",
        context={"test": "duplicate_breach_2"},
    )
    assert status3 == "SUPPRESSED_COOLDOWN"
    assert not eligible3

    # Check engine diagnostics
    diag = engine.get_status()
    assert diag["is_cooldown_active"] is True
    assert diag["total_triggers"] == 1
    assert diag["total_suppressions"] == 2


def test_recovery_policy_reset_and_status_api(client: TestClient, engineer_token: str, viewer_token: str):
    """Recovery policy endpoint returns status to Viewer and processes evaluations for ML_Engineer."""
    # Viewer can read policy status
    resp = client.get(
        "/api/v1/recovery/policy",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cooldown_seconds"] == 600
    assert data["is_cooldown_active"] is False

    # ML Engineer evaluates recovery policy via API
    eval_resp = client.post(
        "/api/v1/recovery/evaluate",
        headers={"Authorization": f"Bearer {engineer_token}"},
        json={
            "drift_detected": True,
            "severity": "HIGH",
            "active_alert_ids": ["alert-001", "alert-002"],
            "trigger_source": "drift_daemon",
        },
    )
    assert eval_resp.status_code == 200
    res_data = eval_resp.json()
    assert res_data["status"] == "TRIGGER_APPROVED"
    assert res_data["candidate_eligible"] is True

    # Immediate second evaluation via API is suppressed
    eval_resp2 = client.post(
        "/api/v1/recovery/evaluate",
        headers={"Authorization": f"Bearer {engineer_token}"},
        json={
            "drift_detected": True,
            "severity": "HIGH",
            "active_alert_ids": ["alert-003"],
        },
    )
    assert eval_resp2.status_code == 200
    res_data2 = eval_resp2.json()
    assert res_data2["status"] == "SUPPRESSED_COOLDOWN"
    assert res_data2["candidate_eligible"] is False
    assert res_data2["cooldown_remaining_seconds"] > 0.0


# ==============================================================================
# 2. Automated Validation Gates (FR-10, NFR-10)
# ==============================================================================


def test_automated_validator_passing_candidate():
    """ModelValidator passes when candidate meets all artifact, schema, regression, and latency gates."""
    candidate_id = "cand-valid-01"
    candidate_registry.create_candidate(
        candidate_id=candidate_id,
        model_version="v2.0.0",
        model_name="ProductionClassifier",
    )

    result = model_validator.validate_candidate(candidate_id=candidate_id, validated_by="test_runner")
    assert result.all_passed is True
    assert result.status == "VALIDATED"
    assert result.gates["artifact_check"].status == GateStatus.PASSED
    assert result.gates["schema_compatibility"].status == GateStatus.PASSED
    assert result.gates["regression_gate"].status == GateStatus.PASSED
    assert result.gates["latency_gate"].status == GateStatus.PASSED

    # Verify candidate updated in SQLite registry
    rec = candidate_registry.get_candidate(candidate_id)
    assert rec is not None
    assert rec["status"] == CandidateStatus.PENDING_APPROVAL.value
    assert rec["validation_report"]["all_passed"] is True


def test_automated_validator_missing_artifact_failure():
    """ModelValidator fails artifact_check if model file cannot be found."""
    candidate_id = "cand-missing-art"
    candidate_registry.create_candidate(
        candidate_id=candidate_id,
        model_version="v-ghost",
    )

    result = model_validator.validate_candidate(
        candidate_id=candidate_id,
        model_path="models/non_existent_file.joblib",
    )
    assert result.all_passed is False
    assert result.status == "VALIDATION_FAILED"
    assert result.gates["artifact_check"].status == GateStatus.FAILED

    rec = candidate_registry.get_candidate(candidate_id)
    assert rec["status"] == CandidateStatus.VALIDATION_FAILED.value


def test_automated_validator_schema_compatibility_failure():
    """ModelValidator fails schema_compatibility if required features are absent."""
    candidate_id = "cand-bad-schema"
    candidate_registry.create_candidate(candidate_id=candidate_id, model_version="v-bad-schema")

    # Incomplete dataframe missing required columns
    bad_val_df = pd.DataFrame({"some_random_column": [1, 2, 3]})

    result = model_validator.validate_candidate(
        candidate_id=candidate_id,
        val_df=bad_val_df,
    )
    assert result.all_passed is False
    assert result.gates["schema_compatibility"].status == GateStatus.FAILED
    assert "missing required feature columns" in result.gates["schema_compatibility"].details.lower()


def test_automated_validator_regression_performance_gate_failure():
    """ModelValidator fails regression_gate if candidate accuracy or F1 degrades."""
    candidate_id = "cand-degraded-perf"
    candidate_registry.create_candidate(candidate_id=candidate_id, model_version="v-degraded")

    degraded_model = MockDegradedClassifier()

    result = model_validator.validate_candidate(
        candidate_id=candidate_id,
        model_pipeline=degraded_model,
    )
    assert result.all_passed is False
    assert result.gates["regression_gate"].status == GateStatus.FAILED
    assert "Regression check failed" in result.gates["regression_gate"].details


def test_automated_validator_latency_benchmark_gate_failure():
    """ModelValidator fails latency_gate if p95 latency exceeds threshold."""
    candidate_id = "cand-high-latency"
    candidate_registry.create_candidate(candidate_id=candidate_id, model_version="v-slow")

    # Custom validator with very low latency threshold to trigger failure deterministically
    strict_validator = ModelValidator(max_latency_p95_ms=0.001)

    result = strict_validator.validate_candidate(candidate_id=candidate_id)
    assert result.all_passed is False
    assert result.gates["latency_gate"].status == GateStatus.FAILED
    assert "exceeds threshold" in result.gates["latency_gate"].details


# ==============================================================================
# 3. Dual-Gate Human Approval & Candidate Promotion API (FR-10, FR-14, TC-07, TC-11)
# ==============================================================================


def test_tc07_promotion_blocked_without_passing_validation(client: TestClient, engineer_token: str):
    """TC-07: Attempting to promote an unvalidated or failed candidate is strictly blocked with HTTP 422."""
    headers = {"Authorization": f"Bearer {engineer_token}"}

    # 1. Unregistered candidate
    resp = client.post(
        "/api/v1/candidates/cand-unregistered/decision",
        headers=headers,
        json={"decision": "APPROVE", "comment": "Trying to bypass validation"},
    )
    assert resp.status_code == 422
    assert "cannot be promoted without passing automated validation gates" in resp.json()["detail"]

    # 2. Registered candidate that is still PENDING_VALIDATION
    candidate_registry.create_candidate(
        candidate_id="cand-pending",
        model_version="v3.0.0",
        status="PENDING_VALIDATION",
    )
    resp2 = client.post(
        "/api/v1/candidates/cand-pending/decision",
        headers=headers,
        json={"decision": "APPROVE", "comment": "Bypass attempt"},
    )
    assert resp2.status_code == 422
    assert "cannot be promoted without passing automated validation gates" in resp2.json()["detail"]

    # 3. Candidate that FAILED validation
    candidate_registry.create_candidate(
        candidate_id="cand-failed",
        model_version="v3.1.0",
        status="VALIDATION_FAILED",
    )
    resp3 = client.post(
        "/api/v1/candidates/cand-failed/decision",
        headers=headers,
        json={"decision": "APPROVE", "comment": "Bypass attempt"},
    )
    assert resp3.status_code == 422

    # Check audit log recorded block events
    logs = audit_logger.get_events(limit=20)
    blocked_actions = [e.action for e in logs if "BLOCKED" in e.action]
    assert "PROMOTION_BLOCKED_UNVALIDATED" in blocked_actions


def test_tc11_candidate_validation_and_promotion_flow(client: TestClient, engineer_token: str, viewer_token: str):
    """TC-11: Full end-to-end workflow: validation pass followed by authorized human promotion decision."""
    cand_id = "cand-approved-01"

    # 1. Viewer cannot validate (403)
    viewer_val = client.post(
        f"/api/v1/candidates/{cand_id}/validate",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert viewer_val.status_code == 403

    # 2. ML Engineer validates candidate successfully
    eng_val = client.post(
        f"/api/v1/candidates/{cand_id}/validate",
        headers={"Authorization": f"Bearer {engineer_token}"},
    )
    assert eng_val.status_code == 200
    val_data = eng_val.json()
    assert val_data["status"] == "VALIDATED"
    assert val_data["all_passed"] is True
    assert val_data["checks"]["regression_gate"] == "PASSED"

    # 3. Viewer cannot approve candidate (403)
    viewer_dec = client.post(
        f"/api/v1/candidates/{cand_id}/decision",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"decision": "APPROVE", "comment": "Unauthorized viewer attempt"},
    )
    assert viewer_dec.status_code == 403

    # 4. ML Engineer approves candidate promotion
    eng_dec = client.post(
        f"/api/v1/candidates/{cand_id}/decision",
        headers={"Authorization": f"Bearer {engineer_token}"},
        json={"decision": "APPROVE", "comment": "Scorecard reviewed and production approved"},
    )
    assert eng_dec.status_code == 200
    dec_data = eng_dec.json()
    assert dec_data["state"] == "PROMOTED"
    assert dec_data["approver"] == "alice_engineer"
    assert dec_data["role"] == "ML_Engineer"

    # 5. Verify authoritative active model state updated
    state = mlflow_adapter.get_active_model_state()
    assert state is not None
    assert state["active_version"] == cand_id
    assert state["state"] == "PROMOTED"

    # 6. Verify candidate registry record
    rec = candidate_registry.get_candidate(cand_id)
    assert rec["status"] == "PROMOTED"
    assert rec["decided_by"] == "alice_engineer"


def test_candidate_rejection_flow(client: TestClient, engineer_token: str):
    """Authorized ML Engineer can explicitly reject a candidate with comments."""
    cand_id = "cand-to-reject"

    # Validate first
    _ = client.post(
        f"/api/v1/candidates/{cand_id}/validate",
        headers={"Authorization": f"Bearer {engineer_token}"},
    )

    # Reject
    resp = client.post(
        f"/api/v1/candidates/{cand_id}/decision",
        headers={"Authorization": f"Bearer {engineer_token}"},
        json={"decision": "REJECT", "comment": "Business logic deprecation; rejected"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "REJECTED"

    rec = candidate_registry.get_candidate(cand_id)
    assert rec["status"] == "REJECTED"
    assert rec["decision_comment"] == "Business logic deprecation; rejected"


# ==============================================================================
# 4. Candidate Registration & Query Endpoints
# ==============================================================================


def test_candidate_registration_and_listing_endpoints(client: TestClient, engineer_token: str, viewer_token: str):
    """Test candidate creation, detail retrieval, and filtered listing."""
    cand_id = "cand-reg-01"

    # Register candidate
    create_resp = client.post(
        "/api/v1/candidates",
        headers={"Authorization": f"Bearer {engineer_token}"},
        json={
            "candidate_id": cand_id,
            "model_version": "v1.2.0",
            "model_name": "ProductionClassifier",
            "run_id": "run-xyz-123",
            "trigger_reason": "Scheduled retraining",
        },
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["candidate_id"] == cand_id
    assert create_resp.json()["status"] == "PENDING_VALIDATION"

    # Viewer queries specific candidate
    get_resp = client.get(
        f"/api/v1/candidates/{cand_id}",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["candidate_id"] == cand_id

    # 404 for unknown candidate
    get_404 = client.get(
        "/api/v1/candidates/cand-nonexistent",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert get_404.status_code == 404

    # List candidates
    list_resp = client.get(
        "/api/v1/candidates?limit=10",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1


# ==============================================================================
# 5. TC-19: QML Architectural Firewall Negative Enforcement
# ==============================================================================


def test_tc19_architectural_firewall_rejection(client: TestClient, engineer_token: str):
    """TC-19: QML models are strictly blocked from registration, validation, and promotion via HTTP 422."""
    headers = {"Authorization": f"Bearer {engineer_token}"}

    # 1. Block registration
    reg_res = client.post(
        "/api/v1/candidates",
        headers=headers,
        json={"candidate_id": "qml-candidate-01", "model_version": "qml-v1"},
    )
    assert reg_res.status_code == 422
    assert "QML models are benchmark-only" in reg_res.json()["detail"]

    # 2. Block validation
    val_res = client.post(
        "/api/v1/candidates/qml-vqc-model/validate",
        headers=headers,
    )
    assert val_res.status_code == 422
    assert "QML models are benchmark-only" in val_res.json()["detail"]

    # 3. Block promotion decision
    dec_res = client.post(
        "/api/v1/candidates/qml-vqc-model/decision",
        headers=headers,
        json={"decision": "APPROVE", "comment": "Attempting QML promotion"},
    )
    assert dec_res.status_code == 422
    assert "QML models are benchmark-only" in dec_res.json()["detail"]


# ==============================================================================
# 6. Audit Trail Completeness & Cryptographic Chaining
# ==============================================================================


def test_recovery_lifecycle_audit_trail_and_hash_integrity(client: TestClient, engineer_token: str):
    """Lifecycle actions generate tamper-evident audit logs maintaining verifiable hash chaining."""
    cand_id = "cand-audit-flow"
    headers = {"Authorization": f"Bearer {engineer_token}"}

    # Step 1: Policy evaluation
    client.post(
        "/api/v1/recovery/evaluate",
        headers=headers,
        json={"drift_detected": True, "severity": "HIGH"},
    )

    # Step 2: Validate
    client.post(f"/api/v1/candidates/{cand_id}/validate", headers=headers)

    # Step 3: Approve
    client.post(
        f"/api/v1/candidates/{cand_id}/decision",
        headers=headers,
        json={"decision": "APPROVE", "comment": "Final audit test promotion"},
    )

    # Verify audit events recorded
    events = audit_logger.get_events(limit=50)
    event_actions = [e.action for e in events]
    assert "RECOVERY_POLICY_EVALUATED" in event_actions
    assert "CANDIDATE_VALIDATE" in event_actions
    assert "CANDIDATE_PROMOTED" in event_actions

    # Verify SHA-256 hash chaining remains completely intact
    integrity = audit_logger.verify_integrity()
    assert integrity.is_valid is True
    assert integrity.issues == []
