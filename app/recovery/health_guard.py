"""Post-Promotion Health Guard Subsystem (REC-02).

Implements:
- FR-12: Post-promotion canary monitoring for promoted model versions.
- NFR-02: Reliability and continuous health evaluation.
- TC-08 / TC-20: Error-rate spike and latency breach detection triggering automated rollback.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional

import numpy as np

from app.config import settings
from app.monitoring.collector import telemetry_collector
from app.registry.mlflow_adapter import mlflow_adapter
from app.schemas.recovery import HealthStatus, PostPromotionHealthResponse

logger = logging.getLogger("mlops.recovery.health_guard")


class PostPromotionHealthGuard:
    """Monitors live post-promotion canary metrics on newly promoted production models."""

    def __init__(
        self,
        canary_window_seconds: Optional[int] = None,
        error_threshold: Optional[float] = None,
        latency_p95_threshold_ms: Optional[float] = None,
        min_samples: Optional[int] = None,
        db_path: Optional[str] = None,
    ):
        self.canary_window_seconds = (
            canary_window_seconds
            if canary_window_seconds is not None
            else settings.canary_window_seconds
        )
        self.error_threshold = (
            error_threshold
            if error_threshold is not None
            else settings.post_promotion_error_threshold
        )
        self.latency_p95_threshold_ms = (
            latency_p95_threshold_ms
            if latency_p95_threshold_ms is not None
            else settings.post_promotion_latency_threshold_ms
        )
        self.min_samples = (
            min_samples
            if min_samples is not None
            else settings.canary_min_samples
        )
        self.db_path = db_path or settings.audit_db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create health_guard_evaluations table in SQLite."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS health_guard_evaluations (
                        eval_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        model_version TEXT NOT NULL,
                        health_status TEXT NOT NULL,
                        error_rate REAL NOT NULL,
                        latency_p95_ms REAL NOT NULL,
                        sample_count INTEGER NOT NULL,
                        details_json TEXT NOT NULL,
                        evaluated_at TEXT NOT NULL
                    );
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_health_eval_version ON health_guard_evaluations(model_version);"
                )
                conn.commit()

    def get_canary_status(self) -> Dict[str, Any]:
        """Check whether the active model is within its post-promotion canary window."""
        state = mlflow_adapter.get_active_model_state()
        if not state:
            return {
                "active_version": "unknown",
                "is_canary_active": False,
                "canary_remaining_seconds": 0.0,
                "promoted_at": None,
            }

        active_version = state.get("active_version", "unknown")
        promoted_at_str = state.get("promoted_at")
        if not promoted_at_str or state.get("state") != "PROMOTED":
            return {
                "active_version": active_version,
                "is_canary_active": False,
                "canary_remaining_seconds": 0.0,
                "promoted_at": promoted_at_str,
            }

        try:
            promoted_at = datetime.fromisoformat(promoted_at_str)
            now = datetime.now(timezone.utc)
            if promoted_at.tzinfo is None:
                promoted_at = promoted_at.replace(tzinfo=timezone.utc)
            elapsed = (now - promoted_at).total_seconds()
            remaining = max(0.0, self.canary_window_seconds - elapsed)
            return {
                "active_version": active_version,
                "is_canary_active": remaining > 0.0,
                "canary_remaining_seconds": round(remaining, 2),
                "promoted_at": promoted_at_str,
            }
        except Exception:
            return {
                "active_version": active_version,
                "is_canary_active": False,
                "canary_remaining_seconds": 0.0,
                "promoted_at": promoted_at_str,
            }

    def evaluate_health(
        self,
        simulate_error_rate: Optional[float] = None,
        simulate_latency_p95_ms: Optional[float] = None,
        simulate_drift: Optional[bool] = None,
    ) -> PostPromotionHealthResponse:
        """Evaluate post-promotion health for the currently active production model.

        Fulfills: FR-12, NFR-02, TC-08, TC-20.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        canary_info = self.get_canary_status()
        active_version = canary_info["active_version"]
        is_canary_active = canary_info["is_canary_active"]
        canary_remaining = canary_info["canary_remaining_seconds"]
        promoted_at_str = canary_info["promoted_at"]

        # Collect recent telemetry from rolling buffer
        summary = telemetry_collector.get_metrics_summary()
        recent_events = telemetry_collector.get_recent_events()

        # Filter events for the active model version if available
        matching_events = [e for e in recent_events if isinstance(e, dict) and e.get("model_version") == active_version]
        if not matching_events:
            matching_events = recent_events

        sample_count = len(matching_events)
        latencies = [float(e["latency_ms"]) for e in matching_events if isinstance(e, dict) and "latency_ms" in e and e["latency_ms"] is not None]

        if latencies:
            actual_p95_latency = float(np.percentile(latencies, 95))
        else:
            actual_p95_latency = summary.latency_p95_ms

        actual_error_rate = summary.error_rate

        # Support fault injection / simulation for TC-20 verification
        error_rate = simulate_error_rate if simulate_error_rate is not None else actual_error_rate
        latency_p95_ms = simulate_latency_p95_ms if simulate_latency_p95_ms is not None else actual_p95_latency
        drift_detected = bool(simulate_drift) if simulate_drift is not None else False

        reasons: List[str] = []
        is_simulated = (
            simulate_error_rate is not None
            or simulate_latency_p95_ms is not None
            or simulate_drift is not None
        )

        # Health Evaluation Logic
        if sample_count < self.min_samples and not is_simulated:
            status = HealthStatus.INSUFFICIENT_DATA
            action = "OBSERVE"
            reasons.append(
                f"Insufficient telemetry samples ({sample_count}/{self.min_samples}) for conclusive health evaluation."
            )
        else:
            has_error_breach = error_rate > self.error_threshold
            has_latency_breach = latency_p95_ms > self.latency_p95_threshold_ms

            if has_error_breach:
                reasons.append(
                    f"Error rate breach: {error_rate:.2%} exceeds post-promotion threshold {self.error_threshold:.2%}."
                )
            if has_latency_breach:
                reasons.append(
                    f"Latency spike breach: p95={latency_p95_ms:.2f}ms exceeds threshold {self.latency_p95_threshold_ms}ms."
                )
            if drift_detected:
                reasons.append("Severe data/prediction drift detected during post-promotion canary.")

            if has_error_breach or has_latency_breach or drift_detected:
                status = HealthStatus.UNHEALTHY
                action = "ROLLBACK"
            elif error_rate > settings.post_promotion_warning_error_rate or latency_p95_ms > settings.post_promotion_warning_latency_ms:
                status = HealthStatus.DEGRADED
                action = "OBSERVE"
                reasons.append("Warning thresholds approached; model is under close observation.")
            else:
                status = HealthStatus.HEALTHY
                action = "NONE"
                reasons.append("All post-promotion health signals within SLA limits.")

        # Persist health evaluation to SQLite
        details = {
            "reasons": reasons,
            "action": action,
            "is_canary_active": is_canary_active,
            "is_simulated": is_simulated,
        }
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO health_guard_evaluations (
                        model_version, health_status, error_rate,
                        latency_p95_ms, sample_count, details_json, evaluated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (
                    active_version,
                    status.value,
                    float(error_rate),
                    float(latency_p95_ms),
                    int(sample_count),
                    json.dumps(details),
                    now_iso,
                ))
                conn.commit()

        logger.info(
            "Post-promotion health evaluation for %s: status=%s, error_rate=%.4f, p95=%.2fms, action=%s",
            active_version,
            status.value,
            error_rate,
            latency_p95_ms,
            action,
        )

        return PostPromotionHealthResponse(
            model_version=active_version,
            health_status=status,
            is_canary_active=is_canary_active,
            promoted_at=promoted_at_str,
            canary_remaining_seconds=canary_remaining,
            sample_count=sample_count,
            error_rate=round(float(error_rate), 4),
            latency_p95_ms=round(float(latency_p95_ms), 2),
            drift_detected=drift_detected,
            reasons=reasons,
            recommended_action=action,
            evaluated_at=now_iso,
        )

    def get_latest_evaluation(self, model_version: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Fetch the most recent health evaluation from SQLite."""
        with self._lock:
            with self._get_connection() as conn:
                if model_version:
                    cur = conn.execute(
                        "SELECT * FROM health_guard_evaluations WHERE model_version = ? ORDER BY eval_id DESC LIMIT 1;",
                        (model_version,),
                    )
                else:
                    cur = conn.execute(
                        "SELECT * FROM health_guard_evaluations ORDER BY eval_id DESC LIMIT 1;"
                    )
                row = cur.fetchone()
                if row:
                    res = dict(row)
                    try:
                        res["details"] = json.loads(res["details_json"])
                    except Exception:
                        pass
                    return res
        return None

    def clear(self) -> None:
        """Clear evaluation history (test isolation)."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM health_guard_evaluations;")
                conn.commit()


post_promotion_health_guard = PostPromotionHealthGuard()
