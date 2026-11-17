"""Test Case TC-09: API Performance, Load & Concurrency (NFR-01, NFR-09, CORE-01).

Validates that:
1. 1,000 synthetic prediction telemetry events are ingested without loss.
2. 10 concurrent client workers can submit events simultaneously.
3. p95 latency remains well under the 500 ms threshold for operational and health endpoints.
"""

from concurrent.futures import ThreadPoolExecutor
import time
from starlette.testclient import TestClient


def test_concurrent_load_and_lossless_ingestion(client: TestClient):
    """TC-09: Ingest 1,000 synthetic events via 10 concurrent threads without loss; verify p95 latency."""
    total_events = 1000
    num_threads = 10

    def send_event(idx: int) -> float:
        start_time = time.perf_counter()
        resp = client.post(
            "/api/v1/events/prediction",
            json={
                "request_id": f"load-req-{idx:05d}",
                "model_version": "baseline-v1",
                "latency_ms": 15.0 + float(idx % 50),
                "success": (idx % 20 != 0),  # 5% synthetic failure rate
                "feature_summary": {
                    "feature_0_mean": 0.5,
                    "feature_1_mean": 0.25,
                },
            },
        )
        assert resp.status_code == 202
        return (time.perf_counter() - start_time) * 1000.0  # ms

    # Execute concurrent ingestion across 10 worker threads
    start_all = time.perf_counter()
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        latencies = list(executor.map(send_event, range(total_events)))
    total_wall_time = time.perf_counter() - start_all
    assert total_wall_time > 0, "Ingestion benchmark must record non-zero elapsed duration"

    # Verify that all 1,000 events are accounted for
    summary_resp = client.get("/api/v1/metrics/summary")
    assert summary_resp.status_code == 200
    summary = summary_resp.json()

    assert summary["count"] == total_events, f"Expected {total_events} events, got {summary['count']}"
    assert summary["error_count"] == 50  # 1000 / 20 = 50 errors
    assert summary["error_rate"] == 0.05

    # Check observed request round-trip p95 latency from client perspective
    sorted_latencies = sorted(latencies)
    client_p95_ms = sorted_latencies[int(0.95 * total_events)]
    assert client_p95_ms < 500.0, f"Observed client p95 latency {client_p95_ms}ms exceeded 500ms limit"

    # Verify health check endpoint p95 latency remains under 500ms during/after load
    health_latencies = []
    for _ in range(50):
        t0 = time.perf_counter()
        h_resp = client.get("/health")
        assert h_resp.status_code == 200
        health_latencies.append((time.perf_counter() - t0) * 1000.0)

    health_p95 = sorted(health_latencies)[int(0.95 * len(health_latencies))]
    assert health_p95 < 500.0, f"Health endpoint p95 latency {health_p95}ms exceeded 500ms limit"
