"""Test Case TC-01: Health Endpoint (FR-01, CORE-01).

Validates that GET /health returns HTTP 200 with service status, application version,
and a valid ISO 8601 timestamp.
"""

from datetime import datetime
from starlette.testclient import TestClient


def test_health_endpoint_success(client: TestClient):
    """TC-01: Service Health check returns HTTP 200 with valid version and status fields."""
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["app_version"] == "0.1.0"
    assert "timestamp" in data
    assert "environment" in data

    # Verify timestamp parseable as valid ISO 8601
    parsed_time = datetime.fromisoformat(data["timestamp"])
    assert parsed_time is not None


def test_root_endpoint_metadata(client: TestClient):
    """Validates the root service metadata route."""
    response = client.get("/")
    assert response.status_code == 200

    data = response.json()
    assert data["name"] == "Self-Monitoring MLOps System"
    assert data["version"] == "0.1.0"
    assert data["health"] == "/health"
    assert data["docs"] == "/docs"
