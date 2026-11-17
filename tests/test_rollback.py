"""Comprehensive Test Suite for Post-Promotion Health Guard and Rollback Orchestration (REC-02).

Fulfills:
- FR-12: Post-promotion canary monitoring and rollback to last known stable model.
- NFR-02: Worker fault isolation and reliability.
- TC-08: Rollback after failed health check restoring prior stable model.
- TC-20: End-to-end automated post-promotion rollback on error/latency breach.
- TC-19: QML Architectural Firewall enforcement.
"""

from datetime import datetime, timezone
import sqlite3
import threading
from fastapi.testclient import TestClient
import pytest

from app.models.baseline import baseline_classifier
from app.recovery.health_guard import PostPromotionHealthGuard, post_promotion_health_guard
from app.recovery.orchestrator import recovery_orchestrator
from app.recovery.registry import candidate_registry
from app.registry.mlflow_adapter import mlflow_adapter
from app.schemas.recovery import HealthStatus
from app.security.audit import audit_logger
from app.security.auth import Role, create_access_token


@pytest.fixture(autouse=True)
def clean_recovery_orchestrator_state():
    """Reset candidate registry, health evaluations, rollback history, and active model state."""
    def _reset_to_baseline():
        post_promotion_health_guard.clear()
        recovery_orchestrator.clear()
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
                        previous_stable_version = NULL,
                        previous_stable_run_id = NULL,
                        state = 'PROMOTED';
                """)
                conn.commit()
            baseline_classifier.load("models/baseline_model.joblib")
            baseline_classifier.model_version = "baseline-v1"
        except Exception:
            pass

    _reset_to_baseline()
    yield
    _reset_to_baseline()


@pytest.fixture
def engineer_token() -> str:
    return create_access_token(subject="alice_engineer", role=Role.ML_ENGINEER)


@pytest.fixture
def viewer_token() -> str:
    return create_access_token(subject="bob_viewer", role=Role.VIEWER)


@pytest.fixture
def admin_token() -> str:
    return create_access_token(subject="charlie_admin", role=Role.ADMINISTRATOR)


def _promote_test_candidate(candidate_id: str = "cand-v2"):
    """Helper to register and promote a test candidate in active_model_state."""
    now_iso = datetime.now(timezone.utc).isoformat()
    candidate_registry.create_candidate(
        candidate_id=candidate_id,
        model_version=candidate_id,
        status="PENDING_APPROVAL",
    )
    with sqlite3.connect(mlflow_adapter.db_path) as conn:
        conn.execute("""
            UPDATE active_model_state
            SET active_version = ?,
                active_run_id = ?,
                promoted_at = ?,
                previous_stable_version = 'baseline-v1',
                previous_stable_run_id = 'baseline-run-001',
                state = 'PROMOTED'
            WHERE id = 1;
        """, (candidate_id, f"run-{candidate_id}", now_iso))
        conn.commit()
    candidate_registry.update_decision(
        candidate_id=candidate_id,
        status="PROMOTED",
        decided_by="alice_engineer",
        comment="Test promotion",
    )
    baseline_classifier.model_version = candidate_id


# ==============================================================================
# 1. Post-Promotion Health Guard Tests (FR-12, NFR-02)
# ==============================================================================


def test_post_promotion_health_guard_healthy():
    """Health guard determines HEALTHY status when error rate and latency are within SLA."""
    _promote_test_candidate("cand-healthy-01")
    report = post_promotion_health_guard.evaluate_health(
        simulate_error_rate=0.02,  # 2% error rate <= 10% threshold
        simulate_latency_p95_ms=180.0,  # 180ms p95 latency <= 500ms SLA
        simulate_drift=False,
    )

    assert report.model_version == "cand-healthy-01"
    assert report.health_status == HealthStatus.HEALTHY
    assert report.recommended_action == "NONE"
    assert report.error_rate == 0.02
    assert report.latency_p95_ms == 180.0
    assert report.is_canary_active is True
    assert report.canary_remaining_seconds > 0.0


def test_post_promotion_health_guard_degraded():
    """Health guard determines DEGRADED status when warning thresholds are approached."""
    _promote_test_candidate("cand-degraded-01")
    report = post_promotion_health_guard.evaluate_health(
        simulate_error_rate=0.07,  # > 5% warning limit, but <= 10% failure limit
        simulate_latency_p95_ms=350.0,  # > 300ms warning limit, but <= 500ms SLA
        simulate_drift=False,
    )

    assert report.health_status == HealthStatus.DEGRADED
    assert report.recommended_action == "OBSERVE"
    assert any("warning" in r.lower() for r in report.reasons)


def test_post_promotion_health_guard_unhealthy_error_rate_breach():
    """Health guard identifies UNHEALTHY condition when error rate exceeds 10% failure threshold."""
    _promote_test_candidate("cand-error-spike")
    report = post_promotion_health_guard.evaluate_health(
        simulate_error_rate=0.15,  # 15% error rate > 10% threshold
        simulate_latency_p95_ms=120.0,
        simulate_drift=False,
    )

    assert report.health_status == HealthStatus.UNHEALTHY
    assert report.recommended_action == "ROLLBACK"
    assert any("error rate breach" in r.lower() for r in report.reasons)


def test_post_promotion_health_guard_unhealthy_latency_breach():
    """Health guard identifies UNHEALTHY condition when p95 latency exceeds 500ms."""
    _promote_test_candidate("cand-latency-spike")
    report = post_promotion_health_guard.evaluate_health(
        simulate_error_rate=0.01,
        simulate_latency_p95_ms=620.0,  # 620ms > 500ms SLA
        simulate_drift=False,
    )

    assert report.health_status == HealthStatus.UNHEALTHY
    assert report.recommended_action == "ROLLBACK"
    assert any("latency spike breach" in r.lower() for r in report.reasons)


def test_post_promotion_health_guard_unhealthy_drift_breach():
    """Health guard identifies UNHEALTHY condition when severe drift is detected during canary."""
    _promote_test_candidate("cand-drift-breach")
    report = post_promotion_health_guard.evaluate_health(
        simulate_error_rate=0.02,
        simulate_latency_p95_ms=150.0,
        simulate_drift=True,
    )

    assert report.health_status == HealthStatus.UNHEALTHY
    assert report.recommended_action == "ROLLBACK"
    assert report.drift_detected is True


def test_post_promotion_health_guard_insufficient_telemetry():
    """Health guard reports INSUFFICIENT_DATA and OBSERVE when live samples are below min_samples."""
    guard = PostPromotionHealthGuard(min_samples=1000)
    report = guard.evaluate_health()

    assert report.health_status == HealthStatus.INSUFFICIENT_DATA
    assert report.recommended_action == "OBSERVE"
    assert any("insufficient" in r.lower() for r in report.reasons)


# ==============================================================================
# 2. Rollback Orchestrator Tests (FR-12, TC-08, TC-20)
# ==============================================================================


def test_tc08_manual_rollback_to_fallback():
    """TC-08: Manual rollback restores prior stable model, updates active state, and logs audit record."""
    cand_id = "cand-prod-v2"
    _promote_test_candidate(cand_id)

    # Initial state verification
    state_before = mlflow_adapter.get_active_model_state()
    assert state_before["active_version"] == cand_id
    assert state_before["previous_stable_version"] == "baseline-v1"

    # Execute rollback
    res = recovery_orchestrator.execute_rollback(
        reason="Manual operator rollback due to metric degradation",
        actor="alice_engineer",
        trigger_type="MANUAL",
    )

    assert res.status == "ROLLED_BACK"
    assert res.source_model_version == cand_id
    assert res.restored_model_version == "baseline-v1"
    assert res.trigger_type == "MANUAL"

    # Verify SQLite active_model_state restored
    state_after = mlflow_adapter.get_active_model_state()
    assert state_after["active_version"] == "baseline-v1"
    assert state_after["state"] == "ROLLED_BACK"

    # Verify failed candidate transitioned to ROLLED_BACK in candidate_records
    cand_rec = candidate_registry.get_candidate(cand_id)
    assert cand_rec["status"] == "ROLLED_BACK"

    # Verify live production classifier reloaded
    assert baseline_classifier.model_version == "baseline-v1"

    # Verify rollback recorded in rollback_history table
    history = recovery_orchestrator.list_rollbacks()
    assert len(history) == 1
    assert history[0]["source_model_version"] == cand_id
    assert history[0]["restored_model_version"] == "baseline-v1"
    assert history[0]["actor"] == "alice_engineer"


def test_tc20_end_to_end_automated_post_promotion_rollback(client: TestClient, engineer_token: str):
    """TC-20: Post-promotion canary fault injection autonomously reverts active model to prior stable version."""
    cand_id = "cand-fault-injection"
    _promote_test_candidate(cand_id)

    # Inject failure traffic (18% error rate spike > 10% threshold)
    resp = client.post(
        "/api/v1/recovery/health/evaluate",
        headers={"Authorization": f"Bearer {engineer_token}"},
        json={
            "simulate_error_rate": 0.18,
            "simulate_latency_p95_ms": 150.0,
            "auto_rollback_on_failure": True,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["health_report"]["health_status"] == "UNHEALTHY"
    assert data["rollback_triggered"] is True
    assert data["rollback_result"]["status"] == "ROLLED_BACK"
    assert data["rollback_result"]["restored_model_version"] == "baseline-v1"
    assert data["rollback_result"]["trigger_type"] == "AUTOMATED"

    # Verify active state reverted autonomously
    state = mlflow_adapter.get_active_model_state()
    assert state["active_version"] == "baseline-v1"
    assert state["state"] == "ROLLED_BACK"

    # Verify candidate marked ROLLED_BACK
    cand = candidate_registry.get_candidate(cand_id)
    assert cand["status"] == "ROLLED_BACK"

    # Verify audit log captures automated rollback
    events = audit_logger.get_events(limit=10)
    rollback_events = [e for e in events if e.action == "MODEL_ROLLBACK"]
    assert len(rollback_events) >= 1
    assert rollback_events[0].resource == "model:baseline-v1"


def test_rollback_missing_fallback_model_fails_safely():
    """Orchestrator rejects rollback safely when no fallback version is recorded."""
    # Active model state without previous_stable_version
    with sqlite3.connect(mlflow_adapter.db_path) as conn:
        conn.execute("""
            UPDATE active_model_state
            SET previous_stable_version = NULL,
                previous_stable_run_id = NULL
            WHERE id = 1;
        """)
        conn.commit()

    with pytest.raises(ValueError, match="No previous stable fallback model exists"):
        recovery_orchestrator.execute_rollback(target_version=None)


def test_rollback_missing_fallback_artifact_fails_safely():
    """Orchestrator refuses to rollback if the target fallback artifact is missing on disk."""
    _promote_test_candidate("cand-corrupt-target")

    with pytest.raises(FileNotFoundError, match="Fallback model artifact"):
        recovery_orchestrator.execute_rollback(target_version="non_existent_ghost_model")


def test_rollback_idempotent_when_already_rolled_back():
    """Rollback execution is idempotent: repeated triggers on already rolled back model return gracefully."""
    _promote_test_candidate("cand-idempotent")

    # First rollback
    res1 = recovery_orchestrator.execute_rollback(reason="First rollback")
    assert res1.status == "ROLLED_BACK"

    # Second rollback targeting same version
    res2 = recovery_orchestrator.execute_rollback(
        target_version="baseline-v1",
        reason="Repeated trigger",
    )
    assert res2.status == "SKIPPED_IDEMPOTENT"
    assert "Idempotent" in res2.reason


def test_rollback_concurrent_protection():
    """Multiple concurrent rollback attempts acquire thread lock and complete safely without corruption."""
    _promote_test_candidate("cand-concurrent")
    results = []
    errors = []

    def _worker(worker_id: int):
        try:
            res = recovery_orchestrator.execute_rollback(
                target_version="baseline-v1",
                reason=f"Concurrent worker {worker_id}",
            )
            results.append(res)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 5
    # One should be ROLLED_BACK and the subsequent 4 should be SKIPPED_IDEMPOTENT
    statuses = [r.status for r in results]
    assert "ROLLED_BACK" in statuses
    assert statuses.count("SKIPPED_IDEMPOTENT") == 4


def test_tc19_rollback_blocks_qml_models():
    """TC-19: Architectural firewall strictly rejects rollback targeting a QML benchmark model."""
    _promote_test_candidate("cand-test-qml")

    with pytest.raises(ValueError, match="Architectural Firewall: QML"):
        recovery_orchestrator.execute_rollback(target_version="qml-vqc-model")


# ==============================================================================
# 3. Security, RBAC, and Audit Logging Tests (SEC-01, SEC-02)
# ==============================================================================


def test_rollback_api_rbac_and_audit(client: TestClient, engineer_token: str, viewer_token: str):
    """Rollback endpoints strictly enforce RBAC and record tamper-evident audit logs."""
    _promote_test_candidate("cand-rbac-test")

    # 1. Unauthenticated request -> 401
    resp_unauth = client.post(
        "/api/v1/models/rollback",
        json={"target_version": "baseline-v1", "reason": "Unauth attempt"},
    )
    assert resp_unauth.status_code == 401

    # 2. Viewer role -> 403 Forbidden
    resp_viewer = client.post(
        "/api/v1/models/rollback",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"target_version": "baseline-v1", "reason": "Viewer attempt"},
    )
    assert resp_viewer.status_code == 403

    # 3. ML Engineer -> 200 OK
    resp_eng = client.post(
        "/api/v1/models/rollback",
        headers={"Authorization": f"Bearer {engineer_token}"},
        json={"target_version": "baseline-v1", "reason": "Authorized engineer rollback"},
    )
    assert resp_eng.status_code == 200
    assert resp_eng.json()["status"] == "ROLLED_BACK"
    assert resp_eng.json()["active_version"] == "baseline-v1"

    # 4. Verify SHA-256 hash chaining remains 100% intact
    integrity = audit_logger.verify_integrity()
    assert integrity.is_valid is True
    assert integrity.issues == []


def test_recovery_health_and_query_endpoints_viewer_access(client: TestClient, viewer_token: str, engineer_token: str):
    """Viewers have read-only access to health, active model state, and rollback history."""
    _promote_test_candidate("cand-viewer-read")

    # 1. Active model state
    active_resp = client.get(
        "/api/v1/models/active",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert active_resp.status_code == 200
    assert active_resp.json()["active_version"] == "cand-viewer-read"
    assert active_resp.json()["previous_stable_version"] == "baseline-v1"

    # 2. Post-promotion health
    health_resp = client.get(
        "/api/v1/recovery/health",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert health_resp.status_code == 200
    assert health_resp.json()["model_version"] == "cand-viewer-read"

    # 3. Rollback history
    rb_list = client.get(
        "/api/v1/recovery/rollbacks",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert rb_list.status_code == 200
    assert "rollbacks" in rb_list.json()

    # 4. Viewer cannot trigger evaluation with auto_rollback (403)
    eval_403 = client.post(
        "/api/v1/recovery/health/evaluate",
        headers={"Authorization": f"Bearer {viewer_token}"},
        json={"simulate_error_rate": 0.20},
    )
    assert eval_403.status_code == 403


def test_prediction_serving_uses_restored_model_after_rollback(client: TestClient, engineer_token: str, viewer_token: str):
    """Live /predict endpoint serves requests without downtime and accurately uses restored model after rollback."""
    _promote_test_candidate("cand-live-serving")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    sample_payload = {
        "age": 35.0,
        "income": 65000.0,
        "loan_amount": 12000.0,
        "credit_score": 720.0,
        "debt_to_income": 0.22,
        "employment_length": 5.0,
        "loan_intent": "PERSONAL",
        "home_ownership": "RENT",
        "previous_default": "N",
    }

    # 1. Predict before rollback (serving cand-live-serving)
    pred1 = client.post("/api/v1/models/predict", headers=headers, json=sample_payload)
    assert pred1.status_code == 200
    assert pred1.json()["model_version"] == "cand-live-serving"

    # 2. Rollback to baseline-v1
    rb = client.post(
        "/api/v1/models/rollback",
        headers={"Authorization": f"Bearer {engineer_token}"},
        json={"target_version": "baseline-v1", "reason": "Restoring baseline model for live test"},
    )
    assert rb.status_code == 200
    assert rb.json()["active_version"] == "baseline-v1"

    # 3. Predict immediately after rollback (serving baseline-v1 seamlessly)
    pred2 = client.post("/api/v1/models/predict", headers=headers, json=sample_payload)
    assert pred2.status_code == 200
    assert pred2.json()["model_version"] == "baseline-v1"
    assert pred2.json()["prediction"] in (0, 1)
