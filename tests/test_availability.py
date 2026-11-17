"""Test Suite for Continuous Service Availability (TC-18, NFR-03, FR-01).

Maps to Requirements:
- NFR-03: Availability (During a 30-minute controlled demo, the health endpoint
  shall achieve at least 99% successful responses, excluding planned restarts).
- FR-01: Health Endpoint verification.
- TC-18: Continuous Availability Probe.

Execution Design:
- Fast smoke probe (test_tc18_availability_smoke_probe) runs as part of the default
  test suite in < 2 seconds, asserting probe mechanics, statistical calculations,
  and failure accounting.
- Full 30-minute probe (test_tc18_30min_continuous_availability_soak) is registered
  under pytest marker `@pytest.mark.soak` and skipped during standard runs.
- Full soak probe execution command:
  `pytest -m soak` or `python scripts/run_tc18_soak.py --duration 1800`
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Dict, List
import numpy as np
import pytest
from starlette.testclient import TestClient


def run_availability_probe(
    client: Any,
    endpoint: str = "/health",
    duration_seconds: float = 5.0,
    interval_seconds: float = 0.05,
    output_report_path: str = "reports/tc18_availability_report.json",
    test_mode_label: str = "SMOKE",
) -> Dict[str, Any]:
    """Execute continuous availability probe recording timestamp, status, and latency.

    Args:
        client: HTTP test client or session with .get() method.
        endpoint: Endpoint URL path to probe.
        duration_seconds: Total probe duration in seconds.
        interval_seconds: Sleep interval between consecutive probes.
        output_report_path: Destination JSON report file.
        test_mode_label: Identifier label (e.g., 'OFFICIAL_30MIN_SOAK' or 'SMOKE').

    Returns:
        Structured dictionary of probe metrics and pass/fail assessment.
    """
    start_time = time.perf_counter()
    end_time = start_time + duration_seconds
    records: List[Dict[str, Any]] = []
    latencies_ms: List[float] = []

    success_count = 0
    failure_count = 0

    while time.perf_counter() < end_time:
        probe_start = time.perf_counter()
        probe_ts = datetime.now(timezone.utc).isoformat()
        try:
            resp = client.get(endpoint)
            elapsed_ms = (time.perf_counter() - probe_start) * 1000.0
            latencies_ms.append(elapsed_ms)

            if resp.status_code == 200:
                success_count += 1
                records.append({
                    "timestamp": probe_ts,
                    "status_code": resp.status_code,
                    "latency_ms": round(elapsed_ms, 3),
                    "success": True,
                })
            else:
                failure_count += 1
                records.append({
                    "timestamp": probe_ts,
                    "status_code": resp.status_code,
                    "latency_ms": round(elapsed_ms, 3),
                    "success": False,
                    "error": f"HTTP_{resp.status_code}",
                })
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - probe_start) * 1000.0
            latencies_ms.append(elapsed_ms)
            failure_count += 1
            records.append({
                "timestamp": probe_ts,
                "status_code": 0,
                "latency_ms": round(elapsed_ms, 3),
                "success": False,
                "error": str(exc),
            })

        time.sleep(interval_seconds)

    total_requests = success_count + failure_count
    availability_pct = (success_count / total_requests * 100.0) if total_requests > 0 else 0.0

    p50 = float(np.percentile(latencies_ms, 50)) if latencies_ms else 0.0
    p95 = float(np.percentile(latencies_ms, 95)) if latencies_ms else 0.0
    p99 = float(np.percentile(latencies_ms, 99)) if latencies_ms else 0.0
    mean_lat = float(np.mean(latencies_ms)) if latencies_ms else 0.0
    max_lat = float(np.max(latencies_ms)) if latencies_ms else 0.0

    # NFR-03 Acceptance Criteria: Availability >= 99.0%
    is_passed = (availability_pct >= 99.0) and (total_requests > 0)

    report: Dict[str, Any] = {
        "test_case": "TC-18",
        "requirement": "NFR-03",
        "test_mode": test_mode_label,
        "endpoint": endpoint,
        "configured_duration_seconds": duration_seconds,
        "actual_duration_seconds": round(time.perf_counter() - start_time, 2),
        "sampling_interval_seconds": interval_seconds,
        "total_requests": total_requests,
        "successful_requests": success_count,
        "failed_requests": failure_count,
        "availability_percentage": round(availability_pct, 4),
        "required_availability_threshold": 99.0,
        "passed": is_passed,
        "latency_stats_ms": {
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "mean": round(mean_lat, 2),
            "max": round(max_lat, 2),
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    # Save to report destination
    out_path = Path(output_report_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


def test_tc18_availability_smoke_probe(client: TestClient):
    """TC-18 Smoke: Fast verification of continuous availability probing mechanics.

    Executes a high-frequency short-duration probe (2 seconds) to verify
    probe execution, latency percentiles, request accounting, and report writing.
    """
    report = run_availability_probe(
        client=client,
        endpoint="/health",
        duration_seconds=2.0,
        interval_seconds=0.04,
        output_report_path="reports/tc18_smoke_probe_report.json",
        test_mode_label="DEVELOPMENT_SMOKE",
    )

    assert report["total_requests"] >= 15, "Smoke probe must execute at least 15 requests"
    assert report["failed_requests"] == 0, "Healthy service should yield zero failures"
    assert report["availability_percentage"] == 100.0
    assert report["passed"] is True
    assert report["latency_stats_ms"]["p95"] < 100.0, "p95 latency in test client should be under 100ms"
    assert Path("reports/tc18_smoke_probe_report.json").exists()


def test_tc18_probe_detects_server_unavailability_and_errors():
    """Verify that the availability probe detects failures and calculates availability degradation."""
    class MockFlakyClient:
        def __init__(self):
            self.calls = 0

        def get(self, url: str):
            self.calls += 1
            # Simulate 10% server failure rate
            if self.calls % 5 == 0:
                class MockErrResp:
                    status_code = 503
                return MockErrResp()

            class MockOkResp:
                status_code = 200
            return MockOkResp()

    flaky_client = MockFlakyClient()
    report = run_availability_probe(
        client=flaky_client,
        endpoint="/health",
        duration_seconds=0.5,
        interval_seconds=0.01,
        output_report_path="reports/tc18_simulated_failure_test.json",
        test_mode_label="SIMULATED_FAILURE_TEST",
    )

    assert report["failed_requests"] > 0, "Probe must detect simulated 503 server errors"
    # Flaky client has 20% failure rate (every 5th call fails), so availability is ~80%
    assert report["availability_percentage"] < 95.0, "Availability must reflect degraded state"
    assert report["passed"] is False, "Flaky service below 99% must fail TC-18"


@pytest.mark.soak
def test_tc18_30min_continuous_availability_soak(client: TestClient):
    """TC-18 Official: 30-Minute Continuous Availability Probe (NFR-03).

    Official execution targets 30 minutes (1800 seconds) with 5-second sampling intervals
    producing 360 requests. Pass criteria requires availability >= 99.0% (>356/360 successes).

    To run official test:
        pytest -m soak -s
    Or configure custom duration via env:
        SOAK_DURATION_SECONDS=1800 pytest -m soak
    """
    duration = float(os.environ.get("SOAK_DURATION_SECONDS", "1800"))
    interval = float(os.environ.get("SOAK_INTERVAL_SECONDS", "5.0"))

    report = run_availability_probe(
        client=client,
        endpoint="/health",
        duration_seconds=duration,
        interval_seconds=interval,
        output_report_path="reports/tc18_availability_soak_report.json",
        test_mode_label="OFFICIAL_30MIN_SOAK" if duration >= 1800 else f"SOAK_{int(duration)}S",
    )

    assert report["passed"] is True, (
        f"TC-18 failed: availability was {report['availability_percentage']}% "
        f"(minimum required: {report['required_availability_threshold']}%)"
    )
    assert report["availability_percentage"] >= 99.0
