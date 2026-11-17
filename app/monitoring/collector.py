"""Operational Telemetry Collector Subsystem (FR-02, NFR-07).

Collects and buffers inference events, computes rolling statistical metrics (p50, p95, error rate),
and maintains thread-safe operational observability for the FastAPI service.
"""

from collections import deque
from datetime import datetime, timezone
import threading
from typing import Deque, List, Optional
import uuid
import numpy as np

from app.config import settings
from app.schemas.metrics import MetricsSummaryResponse
from app.schemas.telemetry import PredictionEventRequest, PredictionEventResponse


class TelemetryCollector:
    """Thread-safe in-memory buffer and aggregator for operational telemetry."""

    def __init__(self, window_size: Optional[int] = None):
        self.window_size = window_size or settings.rolling_window_size
        self._lock = threading.Lock()

        # Cumulative counters
        self._total_events: int = 0
        self._error_count: int = 0

        # Rolling sample buffer for latency calculations
        self._latencies: Deque[float] = deque(maxlen=self.window_size)
        self._recent_events: Deque[dict] = deque(maxlen=self.window_size)

        # Active model tracking and drift state
        self._latest_model_version: Optional[str] = settings.default_model_version
        self._drift_status: str = "UNKNOWN"

    def record_event(self, event: PredictionEventRequest) -> PredictionEventResponse:
        """Ingest a prediction telemetry event and update counters and rolling buffers."""
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._lock:
            self._total_events += 1
            if not event.success:
                self._error_count += 1

            self._latencies.append(float(event.latency_ms))
            self._latest_model_version = event.model_version

            self._recent_events.append({
                "event_id": event_id,
                "request_id": event.request_id,
                "model_version": event.model_version,
                "latency_ms": event.latency_ms,
                "success": event.success,
                "feature_summary": event.feature_summary,
                "timestamp": event.timestamp or now_iso,
            })

        return PredictionEventResponse(
            status="accepted",
            event_id=event_id,
            message="Telemetry event accepted for ingestion",
            timestamp=now_iso,
        )

    def get_metrics_summary(self) -> MetricsSummaryResponse:
        """Compute aggregated operational metrics over cumulative and rolling window data."""
        with self._lock:
            total = self._total_events
            errors = self._error_count
            latencies_list: List[float] = list(self._latencies)
            model_ver = self._latest_model_version
            drift_stat = self._drift_status

        error_rate = float(errors / total) if total > 0 else 0.0

        if latencies_list:
            latencies_arr = np.array(latencies_list, dtype=np.float64)
            p50 = float(np.percentile(latencies_arr, 50))
            p95 = float(np.percentile(latencies_arr, 95))
            p99 = float(np.percentile(latencies_arr, 99))
            mean = float(np.mean(latencies_arr))
        else:
            p50 = 0.0
            p95 = 0.0
            p99 = 0.0
            mean = 0.0

        return MetricsSummaryResponse(
            count=total,
            error_count=errors,
            error_rate=round(error_rate, 4),
            latency_p50_ms=round(p50, 2),
            latency_p95_ms=round(p95, 2),
            latency_p99_ms=round(p99, 2),
            latency_mean_ms=round(mean, 2),
            model_version=model_ver,
            drift_status=drift_stat,
            window_size=self.window_size,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def set_drift_status(self, status: str) -> None:
        """Update the active drift status reported by the monitoring engine."""
        with self._lock:
            self._drift_status = status

    def get_recent_events(self) -> List[dict]:
        """Return a copy of recent events buffered in the rolling window."""
        with self._lock:
            return list(self._recent_events)

    def reset(self) -> None:
        """Clear all counters and rolling windows (used for test isolation)."""
        with self._lock:
            self._total_events = 0
            self._error_count = 0
            self._latencies.clear()
            self._recent_events.clear()
            self._latest_model_version = settings.default_model_version
            self._drift_status = "UNKNOWN"


# Global singleton instance
telemetry_collector = TelemetryCollector()
