"""Audit Logging and Tamper-Evidence Test Suite (SEC-02).

Maps to Requirements: FR-11, SR-03, SR-05, NFR-11
Maps to Test Cases: TC-12, TC-14, SEC-03, SEC-05
"""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from starlette.testclient import TestClient

from app.security.audit import GENESIS_HASH, audit_logger
from app.security.auth import Role, create_access_token


# ==============================================================================
# Persistent Storage & Hash-Chain Generation Tests (FR-11, SR-05, TC-12)
# ==============================================================================


def test_audit_event_creation_and_persistence():
    """TC-12: Audit event captures all mandatory fields and persists into SQLite."""
    event = audit_logger.log_event(
        actor="engineer_bob",
        role="ML_Engineer",
        action="CANDIDATE_VALIDATE",
        resource="candidate:cand-001",
        outcome="SUCCESS",
        correlation_id="req-corr-123",
        metadata={"metric_auc": 0.94, "gate_status": "PASSED"},
    )

    assert event.event_id.startswith("audit-")
    assert event.actor == "engineer_bob"
    assert event.role == "ML_Engineer"
    assert event.action == "CANDIDATE_VALIDATE"
    assert event.resource == "candidate:cand-001"
    assert event.outcome == "SUCCESS"
    assert event.correlation_id == "req-corr-123"
    assert event.metadata["metric_auc"] == 0.94
    assert event.prev_hash == GENESIS_HASH
    assert len(event.record_hash) == 64

    # Verify persistent retrieval from SQLite
    events = audit_logger.get_events()
    assert len(events) == 1
    assert events[0].event_id == event.event_id
    assert events[0].record_hash == event.record_hash


def test_hash_chain_genesis_and_continuity():
    """SEC-05: Hash chain begins at genesis hash and sequentially links subsequent records."""
    evt1 = audit_logger.log_event(actor="alice", action="LOGIN_SUCCESS", resource="/auth", outcome="SUCCESS")
    evt2 = audit_logger.log_event(actor="bob", action="CANDIDATE_PROMOTE", resource="candidate:cand-1", outcome="SUCCESS")
    evt3 = audit_logger.log_event(actor="carol", action="MODEL_ROLLBACK", resource="model:v1", outcome="SUCCESS")

    assert evt1.prev_hash == GENESIS_HASH
    assert evt2.prev_hash == evt1.record_hash
    assert evt3.prev_hash == evt2.record_hash

    # Verify retrieved chain
    events = audit_logger.get_events()
    assert len(events) == 3
    assert events[0].prev_hash == GENESIS_HASH
    assert events[1].prev_hash == events[0].record_hash
    assert events[2].prev_hash == events[1].record_hash


def test_hash_chain_integrity_verification_success():
    """SEC-05: Cryptographic verification succeeds on an uncorrupted audit log."""
    for i in range(5):
        audit_logger.log_event(
            actor=f"user_{i}",
            action="ACTION_TEST",
            resource=f"resource_{i}",
            outcome="SUCCESS",
        )

    verification = audit_logger.verify_integrity()
    assert verification.is_valid is True
    assert verification.total_records == 5
    assert verification.verified_records == 5
    assert len(verification.issues) == 0


# ==============================================================================
# Tamper Detection Tests: Modifications, Deletions & Reorderings (SEC-05)
# ==============================================================================


def test_detection_of_tampered_record():
    """SEC-05: Modification of an audit record field is detected as a HASH_MISMATCH."""
    audit_logger.log_event(actor="admin", action="ACTION_1", resource="res1", outcome="SUCCESS")
    audit_logger.log_event(actor="admin", action="ACTION_2", resource="res2", outcome="SUCCESS")
    audit_logger.log_event(actor="admin", action="ACTION_3", resource="res3", outcome="SUCCESS")

    # Bypass trigger to simulate out-of-band direct database tampering
    with audit_logger._get_connection() as conn:
        conn.execute("DROP TRIGGER IF EXISTS abort_audit_update;")
        conn.execute("UPDATE audit_log SET actor = 'malicious_impostor' WHERE id = 2;")
        conn.commit()

    verification = audit_logger.verify_integrity()
    assert verification.is_valid is False
    assert len(verification.issues) >= 1

    error_types = [issue["error_type"] for issue in verification.issues]
    assert "HASH_MISMATCH" in error_types


def test_detection_of_deleted_record():
    """SEC-05: Deletion of an intermediate audit record breaks the chain and is detected."""
    audit_logger.log_event(actor="admin", action="ACTION_1", resource="res1", outcome="SUCCESS")
    audit_logger.log_event(actor="admin", action="ACTION_2", resource="res2", outcome="SUCCESS")
    audit_logger.log_event(actor="admin", action="ACTION_3", resource="res3", outcome="SUCCESS")

    # Bypass trigger to simulate out-of-band deletion of record #2
    with audit_logger._get_connection() as conn:
        conn.execute("DROP TRIGGER IF EXISTS abort_audit_delete;")
        conn.execute("DELETE FROM audit_log WHERE id = 2;")
        conn.commit()

    verification = audit_logger.verify_integrity()
    assert verification.is_valid is False

    error_types = [issue["error_type"] for issue in verification.issues]
    assert "CHAIN_BROKEN" in error_types


def test_detection_of_reordered_records():
    """SEC-05: Reordering audit records breaks the sequential prev_hash links."""
    audit_logger.log_event(actor="user_a", action="ACTION_A", resource="res_a", outcome="SUCCESS")
    audit_logger.log_event(actor="user_b", action="ACTION_B", resource="res_b", outcome="SUCCESS")

    # Swap rows 1 and 2 IDs
    with audit_logger._get_connection() as conn:
        conn.execute("DROP TRIGGER IF EXISTS abort_audit_update;")
        conn.execute("UPDATE audit_log SET id = 999 WHERE id = 1;")
        conn.execute("UPDATE audit_log SET id = 1 WHERE id = 2;")
        conn.execute("UPDATE audit_log SET id = 2 WHERE id = 999;")
        conn.commit()

    verification = audit_logger.verify_integrity()
    assert verification.is_valid is False
    assert any(i["error_type"] == "CHAIN_BROKEN" for i in verification.issues)


def test_database_triggers_prevent_update_and_delete():
    """SEC-05, SR-05: Database triggers abort any direct UPDATE or DELETE operations."""
    audit_logger.log_event(actor="admin", action="IMMUTABLE_TEST", resource="res", outcome="SUCCESS")

    with audit_logger._get_connection() as conn:
        # Direct UPDATE attempt
        with pytest.raises(sqlite3.DatabaseError) as exc_update:
            conn.execute("UPDATE audit_log SET actor = 'hacker' WHERE id = 1;")
        assert "append-only" in str(exc_update.value).lower()

        # Direct DELETE attempt
        with pytest.raises(sqlite3.DatabaseError) as exc_delete:
            conn.execute("DELETE FROM audit_log WHERE id = 1;")
        assert "append-only" in str(exc_delete.value).lower()


# ==============================================================================
# Concurrency & Redaction Tests (SR-03, SR-05, NFR-11)
# ==============================================================================


def test_concurrent_audit_writes():
    """SR-05: 50 concurrent writes across 5 worker threads maintain hash chain integrity."""
    total_events = 50

    def write_event(idx: int):
        return audit_logger.log_event(
            actor=f"worker_{idx % 5}",
            action="CONCURRENT_ACTION",
            resource=f"resource_{idx}",
            outcome="SUCCESS",
            metadata={"worker_index": idx},
        )

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(write_event, range(total_events)))

    assert len(results) == total_events

    # Verify all records stored and unbroken
    verification = audit_logger.verify_integrity()
    assert verification.is_valid is True
    assert verification.total_records == total_events
    assert verification.verified_records == total_events
    assert len(verification.issues) == 0


def test_sensitive_data_redacted_in_audit_metadata():
    """SR-03, NFR-11, SEC-03: Sensitive tokens, passwords, and API keys are redacted before persistence."""
    audit_logger.log_event(
        actor="user_alice",
        action="LOGIN_ATTEMPT",
        resource="/auth",
        outcome="FAILURE",
        metadata={
            "submitted_password": "super_secret_password_123",
            "api_key": "raw_secret_api_key",
            "bearer_token": "Bearer eyJhbGciOi...",
            "safe_feature_count": 42,
        },
    )

    events = audit_logger.get_events()
    assert len(events) == 1
    meta = events[0].metadata

    # Sensitive values masked
    assert meta["submitted_password"] == "[REDACTED]"
    assert meta["api_key"] == "[REDACTED]"
    assert meta["bearer_token"] == "[REDACTED]"
    assert meta["safe_feature_count"] == 42


# ==============================================================================
# RBAC API Endpoint Tests (SR-01, SR-02, SEC-05)
# ==============================================================================


def test_audit_endpoints_rbac_permissions(client: TestClient):
    """SEC-05: Audit inspection requires ML_Engineer/Admin; verification requires Admin only."""
    viewer_token = create_access_token(subject="viewer", role=Role.VIEWER)
    engineer_token = create_access_token(subject="engineer", role=Role.ML_ENGINEER)
    admin_token = create_access_token(subject="admin", role=Role.ADMINISTRATOR)

    # 1. Unauthenticated access
    assert client.get("/api/v1/audit/events").status_code == 401
    assert client.get("/api/v1/audit/verify").status_code == 401

    # 2. Viewer access (Denied with 403)
    assert client.get("/api/v1/audit/events", headers={"Authorization": f"Bearer {viewer_token}"}).status_code == 403
    assert client.get("/api/v1/audit/verify", headers={"Authorization": f"Bearer {viewer_token}"}).status_code == 403

    # 3. ML_Engineer access (Can view events, but cannot execute admin verification)
    assert client.get("/api/v1/audit/events", headers={"Authorization": f"Bearer {engineer_token}"}).status_code == 200
    assert client.get("/api/v1/audit/verify", headers={"Authorization": f"Bearer {engineer_token}"}).status_code == 403

    # 4. Administrator access (Full access to events and verification)
    events_resp = client.get("/api/v1/audit/events", headers={"Authorization": f"Bearer {admin_token}"})
    assert events_resp.status_code == 200

    verify_resp = client.get("/api/v1/audit/verify", headers={"Authorization": f"Bearer {admin_token}"})
    assert verify_resp.status_code == 200
    assert verify_resp.json()["is_valid"] is True


# ==============================================================================
# Integration with Authentication & Lifecycle (FR-11, SR-05, TC-12)
# ==============================================================================


def test_lifecycle_and_auth_operations_record_audit_events(client: TestClient):
    """TC-12, FR-11: Authentication, candidate validation, decisions, and rollbacks generate audit events."""
    admin_token = create_access_token(subject="admin_user", role=Role.ADMINISTRATOR)
    headers = {"Authorization": f"Bearer {admin_token}", "X-Correlation-ID": "corr-test-999"}

    # 1. Candidate validation
    val_resp = client.post("/api/v1/candidates/cand-test-01/validate", headers=headers)
    assert val_resp.status_code == 200

    # 2. Candidate decision
    dec_resp = client.post(
        "/api/v1/candidates/cand-test-01/decision",
        headers=headers,
        json={"decision": "APPROVE", "comment": "Audit test promotion"},
    )
    assert dec_resp.status_code == 200

    # 3. Rollback
    rb_resp = client.post(
        "/api/v1/models/rollback",
        headers=headers,
        json={"target_version": "baseline-v1", "reason": "Audit test rollback"},
    )
    assert rb_resp.status_code == 200

    # 4. Failed login
    fail_login = client.post("/api/v1/auth/token", json={"username": "fake_user", "password": "badpassword"})
    assert fail_login.status_code == 401

    # 5. Authorization denial
    viewer_token = create_access_token(subject="viewer_user", role=Role.VIEWER)
    denied_resp = client.post(
        "/api/v1/candidates/cand-test-01/validate",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert denied_resp.status_code == 403

    # Inspect all audit records
    events = audit_logger.get_events()
    actions = [e.action for e in events]

    assert "CANDIDATE_VALIDATE" in actions
    assert "CANDIDATE_PROMOTED" in actions
    assert "MODEL_ROLLBACK" in actions
    assert "LOGIN_FAILED" in actions
    assert "AUTHORIZATION_DENIED" in actions

    # Verify cryptographic integrity of the entire generated chain
    integrity = audit_logger.verify_integrity()
    assert integrity.is_valid is True
    assert integrity.total_records == len(events)
