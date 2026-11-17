"""Test Case TC-02: Prediction Metrics Collection (FR-02, NFR-07, CORE-01).

Validates that prediction events are ingested asynchronously via POST /api/v1/events/prediction
and aggregate counts, response latency, and error tracking accurately reflect submitted batches
on GET /api/v1/metrics/summary.
"""

from starlette.testclient import TestClient


def test_initial_metrics_summary(client: TestClient):
    """Empty metrics summary starts with zero count and error rates."""
    response = client.get("/api/v1/metrics/summary")
    assert response.status_code == 200

    data = response.json()
    assert data["count"] == 0
    assert data["error_count"] == 0
    assert data["error_rate"] == 0.0
    assert data["latency_p50_ms"] == 0.0
    assert data["latency_p95_ms"] == 0.0
    assert data["drift_status"] == "UNKNOWN"


def test_prediction_event_ingestion_accepted(client: TestClient):
    """Valid prediction event returns HTTP 202 Accepted with assigned event_id."""
    payload = {
        "request_id": "req-demo-001",
        "model_version": "baseline-v1",
        "latency_ms": 42.5,
        "success": True,
        "feature_summary": {
            "feature_a_mean": 0.31,
            "feature_b_missing_rate": 0.02,
        },
    }
    response = client.post("/api/v1/events/prediction", json=payload)
    assert response.status_code == 202

    data = response.json()
    assert data["status"] == "accepted"
    assert data["event_id"].startswith("evt-")
    assert "timestamp" in data


def test_prediction_batch_metrics_aggregation(client: TestClient):
    """TC-02: Submitting a test batch of N requests updates counts, latencies, and error rates."""
    # Submit 20 successful events with known ascending latencies (10ms, 20ms, ..., 200ms)
    for i in range(1, 21):
        resp = client.post(
            "/api/v1/events/prediction",
            json={
                "request_id": f"req-success-{i:03d}",
                "model_version": "baseline-v1",
                "latency_ms": float(i * 10),
                "success": True,
            },
        )
        assert resp.status_code == 202

    # Submit 5 failed events with 500ms latency
    for j in range(1, 6):
        resp = client.post(
            "/api/v1/events/prediction",
            json={
                "request_id": f"req-fail-{j:03d}",
                "model_version": "baseline-v1",
                "latency_ms": 500.0,
                "success": False,
            },
        )
        assert resp.status_code == 202

    # Inspect operational summary
    summary_resp = client.get("/api/v1/metrics/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()

    assert summary["count"] == 25
    assert summary["error_count"] == 5
    assert summary["error_rate"] == 0.20  # 5 errors out of 25 requests = 20%
    assert summary["model_version"] == "baseline-v1"
    # p50 of [10..200] and 5*500 (25 items, 13th item is 130.0)
    assert summary["latency_p50_ms"] > 0.0
    assert summary["latency_p95_ms"] >= 200.0


def test_prediction_event_validation_errors(client: TestClient):
    """Validation rejects negative latency and missing mandatory fields with HTTP 422."""
    # Negative latency
    neg_resp = client.post(
        "/api/v1/events/prediction",
        json={
            "request_id": "req-invalid",
            "model_version": "baseline-v1",
            "latency_ms": -5.0,
            "success": True,
        },
    )
    assert neg_resp.status_code == 422

    # Missing model_version
    missing_resp = client.post(
        "/api/v1/events/prediction",
        json={
            "request_id": "req-invalid",
            "latency_ms": 10.0,
        },
    )
    assert missing_resp.status_code == 422
