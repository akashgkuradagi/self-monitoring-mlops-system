"""CLI Runner for TC-18 Continuous Availability Soak Test (NFR-03).

Executes the continuous availability probe for 30 minutes (1800 seconds)
checking the service health endpoint every 5 seconds.

Usage:
    # Full official 30-minute availability test:
    python scripts/run_tc18_soak.py --duration 1800 --interval 5.0 --output reports/tc18_availability_30min.json

    # Fast 10-second smoke test:
    python scripts/run_tc18_soak.py --duration 10 --interval 0.5 --output reports/tc18_availability_smoke.json

    # Against live running URL:
    python scripts/run_tc18_soak.py --url http://127.0.0.1:8000/health --duration 1800
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List
import numpy as np

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def execute_soak_probe(
    endpoint_url: str = None,
    duration_seconds: float = 1800.0,
    interval_seconds: float = 5.0,
    output_path: str = "reports/tc18_availability_soak_report.json",
) -> Dict[str, Any]:
    """Execute availability probe against remote URL or in-process FastAPI TestClient."""
    if endpoint_url and endpoint_url.startswith("http"):
        import requests
        session = requests.Session()
        client_type = f"Live HTTP ({endpoint_url})"

        def getter():
            return session.get(endpoint_url, timeout=5.0)
    else:
        from starlette.testclient import TestClient
        from app.main import app
        tc = TestClient(app)
        client_type = "In-Process TestClient (/health)"

        def getter():
            return tc.get("/health")

    is_official = (duration_seconds >= 1800.0)
    mode_label = "OFFICIAL_30MIN_SOAK" if is_official else f"CUSTOM_{int(duration_seconds)}S"

    print("=" * 70)
    print("TC-18: Continuous Service Availability Probe (NFR-03)")
    print(f"Target:               {client_type}")
    print(f"Configured Duration:  {duration_seconds} seconds ({duration_seconds/60:.1f} minutes)")
    print(f"Sampling Interval:    {interval_seconds} seconds")
    print("Pass Criterion:       Availability >= 99.0% (NFR-03)")
    print(f"Execution Mode:       {mode_label}")
    print("=" * 70)

    start_time = time.perf_counter()
    end_time = start_time + duration_seconds
    latencies_ms: List[float] = []

    success_count = 0
    failure_count = 0
    iteration = 0

    try:
        while time.perf_counter() < end_time:
            iteration += 1
            probe_start = time.perf_counter()
            try:
                resp = getter()
                elapsed_ms = (time.perf_counter() - probe_start) * 1000.0
                latencies_ms.append(elapsed_ms)

                if resp.status_code == 200:
                    success_count += 1
                    status_str = "OK 200"
                else:
                    failure_count += 1
                    status_str = f"ERR {resp.status_code}"
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - probe_start) * 1000.0
                latencies_ms.append(elapsed_ms)
                failure_count += 1
                status_str = f"FAIL {exc}"

            elapsed_total = time.perf_counter() - start_time
            remaining = max(0.0, duration_seconds - elapsed_total)
            current_pct = (success_count / (success_count + failure_count)) * 100.0

            if iteration % 10 == 0 or iteration == 1 or failure_count > 0:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"Probe #{iteration:04d} | Status: {status_str:<10} | "
                    f"Latency: {elapsed_ms:6.2f} ms | "
                    f"Availability: {current_pct:6.2f}% | "
                    f"Remaining: {remaining:6.1f}s"
                )

            time.sleep(interval_seconds)

    except KeyboardInterrupt:
        print("\nProbe interrupted by operator! Calculating partial results...")

    total_requests = success_count + failure_count
    availability_pct = (success_count / total_requests * 100.0) if total_requests > 0 else 0.0

    p50 = float(np.percentile(latencies_ms, 50)) if latencies_ms else 0.0
    p95 = float(np.percentile(latencies_ms, 95)) if latencies_ms else 0.0
    p99 = float(np.percentile(latencies_ms, 99)) if latencies_ms else 0.0
    mean_lat = float(np.mean(latencies_ms)) if latencies_ms else 0.0
    max_lat = float(np.max(latencies_ms)) if latencies_ms else 0.0

    passed = (availability_pct >= 99.0) and (total_requests > 0)

    report = {
        "test_case": "TC-18",
        "requirement": "NFR-03",
        "test_mode": mode_label,
        "is_official_30min_execution": is_official,
        "endpoint_target": client_type,
        "configured_duration_seconds": duration_seconds,
        "actual_duration_seconds": round(time.perf_counter() - start_time, 2),
        "sampling_interval_seconds": interval_seconds,
        "total_requests": total_requests,
        "successful_requests": success_count,
        "failed_requests": failure_count,
        "availability_percentage": round(availability_pct, 4),
        "required_availability_threshold": 99.0,
        "passed": passed,
        "latency_stats_ms": {
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "p99": round(p99, 2),
            "mean": round(mean_lat, 2),
            "max": round(max_lat, 2),
        },
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print("TC-18 AVAILABILITY PROBE SUMMARY")
    print(f"Total Requests:       {total_requests}")
    print(f"Successful Requests:  {success_count}")
    print(f"Failed Requests:      {failure_count}")
    print(f"Availability:         {availability_pct:.4f}% (Threshold >= 99.0%)")
    print(f"Latency p50 / p95:    {p50:.2f} ms / {p95:.2f} ms")
    print(f"Result:               {'PASSED' if passed else 'FAILED'}")
    print(f"Saved Report:         {out_file.resolve()}")
    print("=" * 70)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TC-18 Continuous Availability Soak Probe (NFR-03)")
    parser.add_argument("--url", default=None, help="Live HTTP URL (e.g. http://localhost:8000/health)")
    parser.add_argument("--duration", type=float, default=1800.0, help="Duration in seconds (default: 1800 for 30 mins)")
    parser.add_argument("--interval", type=float, default=5.0, help="Sampling interval in seconds (default: 5.0)")
    parser.add_argument("--output", default="reports/tc18_availability_soak_report.json", help="Report output path")
    args = parser.parse_args()

    rep = execute_soak_probe(
        endpoint_url=args.url,
        duration_seconds=args.duration,
        interval_seconds=args.interval,
        output_path=args.output,
    )
    if not rep["passed"]:
        sys.exit(1)
