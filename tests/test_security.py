"""Security and RBAC Test Suite (SEC-01).

Maps to Requirements: SR-01, SR-02, SR-04, SR-06, NFR-12
Maps to Test Cases: SEC-01, SEC-02, SEC-04, TC-11, TC-13
"""

from datetime import timedelta
from starlette.testclient import TestClient

from app.security.auth import Role, create_access_token
from app.security.middleware import redact_sensitive_dict


# ==============================================================================
# Token Authentication & Validation Tests (SR-01, SEC-01)
# ==============================================================================


def test_login_successful_jwt_generation(client: TestClient):
    """Obtaining access token with valid credentials succeeds (SR-01)."""
    resp = client.post(
        "/api/v1/auth/token",
        json={"username": "engineer", "password": "engineer123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["role"] == "ML_Engineer"
    assert data["username"] == "engineer"
    assert data["expires_in_seconds"] > 0


def test_login_invalid_credentials_returns_401(client: TestClient):
    """Invalid credentials return HTTP 401 Unauthorized (SR-01)."""
    resp = client.post(
        "/api/v1/auth/token",
        json={"username": "engineer", "password": "wrongpassword"},
    )
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers


def test_missing_authentication_returns_401(client: TestClient):
    """SEC-01: Accessing protected candidate and rollback endpoints without credentials returns 401 (SR-01)."""
    # Candidate validate
    resp_val = client.post("/api/v1/candidates/cand-001/validate")
    assert resp_val.status_code == 401
    assert "WWW-Authenticate" in resp_val.headers

    # Candidate decision
    resp_dec = client.post(
        "/api/v1/candidates/cand-001/decision",
        json={"decision": "APPROVE", "comment": "Approved for testing"},
    )
    assert resp_dec.status_code == 401

    # Rollback
    resp_rb = client.post(
        "/api/v1/models/rollback",
        json={"target_version": "baseline-v1", "reason": "Testing rollback"},
    )
    assert resp_rb.status_code == 401


def test_expired_token_rejected_with_401(client: TestClient):
    """Expired JWT token returns HTTP 401 with descriptive error (SR-01)."""
    # Create an intentionally expired token
    expired_token = create_access_token(
        subject="test-engineer",
        role=Role.ML_ENGINEER,
        expires_delta=timedelta(seconds=-10),
    )
    resp = client.post(
        "/api/v1/candidates/cand-001/validate",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_forged_or_malformed_token_rejected_with_401(client: TestClient):
    """Tokens with invalid signature or corrupted string return HTTP 401 (SR-01)."""
    resp = client.post(
        "/api/v1/candidates/cand-001/validate",
        headers={"Authorization": "Bearer invalid.jwt.signature.here"},
    )
    assert resp.status_code == 401


# ==============================================================================
# Role-Based Access Control (RBAC) Tests (SR-02, SEC-02, TC-11)
# ==============================================================================


def test_viewer_role_denied_with_403_on_management_endpoints(client: TestClient):
    """SEC-02: Authenticated Viewer role attempting model lifecycle actions is denied with 403 Forbidden (SR-02, TC-11)."""
    viewer_token = create_access_token(subject="alice_viewer", role=Role.VIEWER)
    headers = {"Authorization": f"Bearer {viewer_token}"}

    # Viewer attempts candidate validation
    val_resp = client.post("/api/v1/candidates/cand-001/validate", headers=headers)
    assert val_resp.status_code == 403
    assert "not authorized" in val_resp.json()["detail"].lower()

    # Viewer attempts promotion decision
    dec_resp = client.post(
        "/api/v1/candidates/cand-001/decision",
        headers=headers,
        json={"decision": "APPROVE", "comment": "Unauthorized viewer attempt"},
    )
    assert dec_resp.status_code == 403

    # Viewer attempts model rollback
    rb_resp = client.post(
        "/api/v1/models/rollback",
        headers=headers,
        json={"target_version": "baseline-v1", "reason": "Unauthorized rollback attempt"},
    )
    assert rb_resp.status_code == 403


def test_ml_engineer_role_authorized_for_lifecycle(client: TestClient):
    """TC-11: Authenticated ML_Engineer can validate, approve/reject, and trigger rollback (SR-02)."""
    engineer_token = create_access_token(subject="bob_engineer", role=Role.ML_ENGINEER)
    headers = {"Authorization": f"Bearer {engineer_token}"}

    # Validate
    val_resp = client.post("/api/v1/candidates/cand-001/validate", headers=headers)
    assert val_resp.status_code == 200
    assert val_resp.json()["status"] == "VALIDATED"
    assert val_resp.json()["validated_by"] == "bob_engineer"

    # Decision
    dec_resp = client.post(
        "/api/v1/candidates/cand-001/decision",
        headers=headers,
        json={"decision": "APPROVE", "comment": "Quality gates passed"},
    )
    assert dec_resp.status_code == 200
    assert dec_resp.json()["state"] == "PROMOTED"

    # Rollback
    rb_resp = client.post(
        "/api/v1/models/rollback",
        headers=headers,
        json={"target_version": "baseline-v1", "reason": "Performance regression in v2"},
    )
    assert rb_resp.status_code == 200
    assert rb_resp.json()["status"] == "ROLLED_BACK"


def test_admin_role_authorized_for_lifecycle(client: TestClient):
    """Administrator role has full access to model lifecycle operations (SR-02)."""
    admin_token = create_access_token(subject="carol_admin", role=Role.ADMINISTRATOR)
    headers = {"Authorization": f"Bearer {admin_token}"}

    val_resp = client.post("/api/v1/candidates/cand-002/validate", headers=headers)
    assert val_resp.status_code == 200

    dec_resp = client.post(
        "/api/v1/candidates/cand-002/decision",
        headers=headers,
        json={"decision": "REJECT", "comment": "Failed security checks"},
    )
    assert dec_resp.status_code == 200
    assert dec_resp.json()["state"] == "REJECTED"


def test_api_key_authentication(client: TestClient):
    """Preconfigured API key in Authorization header authorizes requests (SR-01, SR-02)."""
    # Use preconfigured engineer key: test-engineer-key
    resp = client.post(
        "/api/v1/candidates/cand-003/validate",
        headers={"Authorization": "Bearer test-engineer-key"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "VALIDATED"

    # Use preconfigured viewer key attempting candidate validation -> 403 Forbidden
    resp_viewer = client.post(
        "/api/v1/candidates/cand-003/validate",
        headers={"Authorization": "Bearer test-viewer-key"},
    )
    assert resp_viewer.status_code == 403


# ==============================================================================
# Security Boundaries & Payload Validation (SR-04, SEC-04, TC-13)
# ==============================================================================


def test_oversized_payload_rejected_with_413(client: TestClient):
    """SEC-04, TC-13: Payload exceeding 1 MB is rejected safely with HTTP 413 (SR-04)."""
    oversized_headers = {
        "Content-Length": str(1_048_576 + 1024),  # > 1 MB
        "Content-Type": "application/json",
    }
    resp = client.post(
        "/api/v1/events/prediction",
        headers=oversized_headers,
        content=b"{}",
    )
    assert resp.status_code == 413
    assert "exceeds the maximum allowed limit" in resp.json()["message"]


def test_malformed_decision_payload_rejected_with_422(client: TestClient):
    """SEC-04: Decision not matching allowed pattern is rejected with HTTP 422 safely (SR-04)."""
    admin_token = create_access_token(subject="admin", role=Role.ADMINISTRATOR)
    headers = {"Authorization": f"Bearer {admin_token}"}

    resp = client.post(
        "/api/v1/candidates/cand-001/decision",
        headers=headers,
        json={"decision": "INVALID_DECISION_STRING", "comment": "test"},
    )
    assert resp.status_code == 422


def test_security_headers_present_on_responses(client: TestClient):
    """OWASP defensive security headers are attached to all HTTP responses."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-XSS-Protection"] == "1; mode=block"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_sensitive_data_redaction_helper():
    """SR-03: Sensitive credentials and tokens are redacted from dictionaries."""
    sample_data = {
        "request_id": "req-123",
        "authorization": "Bearer secret-token-value",
        "api_key": "raw-key-12345",
        "nested": {
            "password": "super-secret-password",
            "feature_mean": 0.42,
        },
    }
    redacted = redact_sensitive_dict(sample_data)
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["feature_mean"] == 0.42
