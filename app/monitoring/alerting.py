"""Multi-Metric Alert Manager and Deduplication Subsystem (ALERT-01).

Implements:
- FR-08: Configurable threshold alerting (drift p-values, numerical/categorical PSI, error rate, p95 latency).
- NFR-07: Operational observability alert integration.
- NFR-09: Scalable, thread-safe deduplication preventing alert storms within rolling windows.
- TC-04 / TC-16: Alert creation, deduplication, lifecycle transitions, and recovery policy trigger integration.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple
import uuid

from app.config import settings
from app.schemas.alert import (
    AlertResponse,
    AlertSeverity,
    AlertStatus,
    AlertSummaryResponse,
)
from app.schemas.drift import DriftReportResponse
from app.schemas.metrics import MetricsSummaryResponse

logger = logging.getLogger("app.monitoring.alerting")


class NotificationChannel:
    """Abstract base class for alert notification channels."""

    def send(self, alert: AlertResponse) -> None:
        """Deliver alert record to channel."""
        raise NotImplementedError


class LoggingNotificationChannel(NotificationChannel):
    """Local logging notification channel."""

    def send(self, alert: AlertResponse) -> None:
        log_msg = (
            f"ALERT [{alert.severity}] {alert.metric_name} "
            f"(Feature: {alert.feature_name or 'N/A'}) - "
            f"Observed: {alert.observed_value} vs Threshold: {alert.threshold}. "
            f"Message: {alert.message} (Alert ID: {alert.alert_id})"
        )
        if alert.severity == AlertSeverity.HIGH.value:
            logger.error(log_msg)
        elif alert.severity == AlertSeverity.MEDIUM.value:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)


class InMemoryNotificationChannel(NotificationChannel):
    """In-memory channel storing delivered alerts for programmatic inspection and testing."""

    def __init__(self):
        self._lock = threading.Lock()
        self._notifications: List[AlertResponse] = []

    def send(self, alert: AlertResponse) -> None:
        with self._lock:
            self._notifications.append(alert)

    def get_notifications(self) -> List[AlertResponse]:
        with self._lock:
            return list(self._notifications)

    def clear(self) -> None:
        with self._lock:
            self._notifications.clear()


class NotificationDispatcher:
    """Dispatches alerts across registered notification channels safely."""

    def __init__(self):
        self._channels: List[NotificationChannel] = [
            LoggingNotificationChannel(),
            InMemoryNotificationChannel(),
        ]
        self._lock = threading.Lock()

    def register_channel(self, channel: NotificationChannel) -> None:
        with self._lock:
            self._channels.append(channel)

    @property
    def in_memory_channel(self) -> InMemoryNotificationChannel:
        for ch in self._channels:
            if isinstance(ch, InMemoryNotificationChannel):
                return ch
        new_ch = InMemoryNotificationChannel()
        self.register_channel(new_ch)
        return new_ch

    def dispatch(self, alert: AlertResponse) -> None:
        """Broadcast alert to all registered channels without raising uncaught exceptions."""
        with self._lock:
            channels = list(self._channels)

        for channel in channels:
            try:
                channel.send(alert)
            except Exception as e:
                logger.warning(f"Failed to dispatch alert to {channel.__class__.__name__}: {e}")


class AlertManager:
    """Persistent, thread-safe Multi-Metric Alert Manager with rolling window deduplication."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        dedup_window_seconds: Optional[int] = None,
    ):
        self.db_path = db_path or settings.alerts_db_path
        self.dedup_window_seconds = (
            dedup_window_seconds
            if dedup_window_seconds is not None
            else settings.alert_dedup_window_seconds
        )
        self.dispatcher = NotificationDispatcher()
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize alerts SQLite schema and indices."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS alerts (
                        alert_id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL,
                        metric_name TEXT NOT NULL,
                        feature_name TEXT,
                        severity TEXT NOT NULL,
                        observed_value REAL NOT NULL,
                        threshold REAL NOT NULL,
                        status TEXT NOT NULL,
                        message TEXT NOT NULL,
                        supporting_context TEXT NOT NULL,
                        dedup_count INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        acknowledged_at TEXT,
                        acknowledged_by TEXT,
                        resolved_at TEXT,
                        resolved_by TEXT
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_fingerprint ON alerts(fingerprint);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_created_at ON alerts(created_at);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity);")
                conn.commit()

    @staticmethod
    def generate_fingerprint(metric_name: str, feature_name: Optional[str], severity: str) -> str:
        """Construct a deterministic fingerprint for alert deduplication."""
        feat_str = (feature_name or "system").strip().lower()
        return f"{metric_name.upper()}:{feat_str}:{severity.upper()}"

    def record_or_deduplicate(
        self,
        metric_name: str,
        severity: str,
        observed_value: float,
        threshold: float,
        message: str,
        feature_name: Optional[str] = None,
        supporting_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[AlertResponse, bool]:
        """Record a new alert or aggregate with existing active alert within deduplication window.

        Returns:
            Tuple[AlertResponse, bool]: (alert_record, is_duplicate)
        """
        fingerprint = self.generate_fingerprint(metric_name, feature_name, severity)
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        context_json = json.dumps(supporting_context or {})

        with self._lock:
            with self._get_connection() as conn:
                # Find active or acknowledged alert with same fingerprint
                cursor = conn.execute(
                    """
                    SELECT * FROM alerts
                    WHERE fingerprint = ? AND status IN ('ACTIVE', 'ACKNOWLEDGED')
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (fingerprint,),
                )
                existing = cursor.fetchone()

                if existing:
                    # Check deduplication window
                    last_seen_dt = datetime.fromisoformat(existing["last_seen_at"])
                    elapsed_seconds = (now_dt - last_seen_dt).total_seconds()

                    if elapsed_seconds <= self.dedup_window_seconds:
                        # Rapid consecutive breach -> deduplicate & aggregate
                        new_count = existing["dedup_count"] + 1
                        conn.execute(
                            """
                            UPDATE alerts
                            SET dedup_count = ?,
                                last_seen_at = ?,
                                observed_value = ?,
                                message = ?,
                                supporting_context = ?
                            WHERE alert_id = ?
                            """,
                            (
                                new_count,
                                now_iso,
                                float(observed_value),
                                message,
                                context_json,
                                existing["alert_id"],
                            ),
                        )
                        conn.commit()

                        updated_alert = AlertResponse(
                            alert_id=existing["alert_id"],
                            fingerprint=fingerprint,
                            metric_name=metric_name,
                            feature_name=feature_name,
                            severity=severity,
                            observed_value=float(observed_value),
                            threshold=float(threshold),
                            status=existing["status"],
                            message=message,
                            supporting_context=supporting_context or {},
                            dedup_count=new_count,
                            created_at=existing["created_at"],
                            last_seen_at=now_iso,
                            acknowledged_at=existing["acknowledged_at"],
                            acknowledged_by=existing["acknowledged_by"],
                            resolved_at=existing["resolved_at"],
                            resolved_by=existing["resolved_by"],
                        )
                        return updated_alert, True

                # Genuine new alert or recurring after resolution/window expiry
                alert_id = f"alt-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO alerts (
                        alert_id, fingerprint, metric_name, feature_name, severity,
                        observed_value, threshold, status, message, supporting_context,
                        dedup_count, created_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alert_id,
                        fingerprint,
                        metric_name,
                        feature_name,
                        severity,
                        float(observed_value),
                        float(threshold),
                        AlertStatus.ACTIVE.value,
                        message,
                        context_json,
                        1,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()

                new_alert = AlertResponse(
                    alert_id=alert_id,
                    fingerprint=fingerprint,
                    metric_name=metric_name,
                    feature_name=feature_name,
                    severity=severity,
                    observed_value=float(observed_value),
                    threshold=float(threshold),
                    status=AlertStatus.ACTIVE.value,
                    message=message,
                    supporting_context=supporting_context or {},
                    dedup_count=1,
                    created_at=now_iso,
                    last_seen_at=now_iso,
                )

        # Broadcast notification on genuine new alert
        self.dispatcher.dispatch(new_alert)
        return new_alert, False

    def get_alert(self, alert_id: str) -> Optional[AlertResponse]:
        """Retrieve single alert by identifier."""
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return self._row_to_model(row)

    def list_alerts(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        metric: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[AlertResponse]:
        """Query alert history with optional status, severity, and metric filtering."""
        query = "SELECT * FROM alerts WHERE 1=1"
        params: List[Any] = []

        if status:
            statuses = [s.strip().upper() for s in status.split(",") if s.strip()]
            if len(statuses) == 1:
                query += " AND status = ?"
                params.append(statuses[0])
            elif len(statuses) > 1:
                placeholders = ",".join("?" for _ in statuses)
                query += f" AND status IN ({placeholders})"
                params.extend(statuses)

        if severity:
            query += " AND severity = ?"
            params.append(severity.upper())

        if metric:
            query += " AND metric_name = ?"
            params.append(metric)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(query, params)
                return [self._row_to_model(r) for r in cur.fetchall()]

    def acknowledge_alert(
        self,
        alert_id: str,
        actor: str,
        comment: Optional[str] = None,
    ) -> Optional[AlertResponse]:
        """Acknowledge an active alert, updating status and actor details."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,))
                existing = cur.fetchone()
                if not existing:
                    return None

                conn.execute(
                    """
                    UPDATE alerts
                    SET status = ?, acknowledged_at = ?, acknowledged_by = ?
                    WHERE alert_id = ?
                    """,
                    (AlertStatus.ACKNOWLEDGED.value, now_iso, actor, alert_id),
                )
                conn.commit()

                cur = conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,))
                return self._row_to_model(cur.fetchone())

    def resolve_alert(
        self,
        alert_id: str,
        actor: str,
        comment: Optional[str] = None,
    ) -> Optional[AlertResponse]:
        """Mark an alert as resolved."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,))
                existing = cur.fetchone()
                if not existing:
                    return None

                conn.execute(
                    """
                    UPDATE alerts
                    SET status = ?, resolved_at = ?, resolved_by = ?
                    WHERE alert_id = ?
                    """,
                    (AlertStatus.RESOLVED.value, now_iso, actor, alert_id),
                )
                conn.commit()

                cur = conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,))
                return self._row_to_model(cur.fetchone())

    def get_summary(self) -> AlertSummaryResponse:
        """Compute aggregate alert counts by status, severity, and metric."""
        with self._lock:
            with self._get_connection() as conn:
                total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
                active = conn.execute(
                    "SELECT COUNT(*) FROM alerts WHERE status = ?",
                    (AlertStatus.ACTIVE.value,),
                ).fetchone()[0]
                ack = conn.execute(
                    "SELECT COUNT(*) FROM alerts WHERE status = ?",
                    (AlertStatus.ACKNOWLEDGED.value,),
                ).fetchone()[0]
                resolved = conn.execute(
                    "SELECT COUNT(*) FROM alerts WHERE status = ?",
                    (AlertStatus.RESOLVED.value,),
                ).fetchone()[0]

                by_sev: Dict[str, int] = {}
                for r in conn.execute("SELECT severity, COUNT(*) FROM alerts GROUP BY severity"):
                    by_sev[r[0]] = r[1]

                by_met: Dict[str, int] = {}
                for r in conn.execute("SELECT metric_name, COUNT(*) FROM alerts GROUP BY metric_name"):
                    by_met[r[0]] = r[1]

        return AlertSummaryResponse(
            total_alerts=total,
            active_alerts=active,
            acknowledged_alerts=ack,
            resolved_alerts=resolved,
            by_severity=by_sev,
            by_metric=by_met,
        )

    def evaluate_drift_report(self, drift_report: DriftReportResponse) -> List[Tuple[AlertResponse, bool]]:
        """Evaluate drift report metrics against thresholds, returning generated/updated alerts."""
        results: List[Tuple[AlertResponse, bool]] = []

        if drift_report.overall_drift_status == "INSUFFICIENT_DATA":
            return results

        # 1. Numerical feature drift checks (KS & PSI)
        for feat, num_res in drift_report.numerical_drift.items():
            if num_res.ks_drift:
                severity = AlertSeverity.HIGH.value if num_res.ks_p_value < 0.01 else AlertSeverity.MEDIUM.value
                alert, is_dup = self.record_or_deduplicate(
                    metric_name="DRIFT_KS",
                    severity=severity,
                    observed_value=num_res.ks_p_value,
                    threshold=settings.drift_ks_alpha,
                    feature_name=feat,
                    message=f"Two-sample KS drift detected on continuous feature '{feat}' (p-value: {num_res.ks_p_value})",
                    supporting_context={
                        "ks_statistic": num_res.ks_statistic,
                        "ks_p_value": num_res.ks_p_value,
                        "reference_mean": num_res.reference_mean,
                        "current_mean": num_res.current_mean,
                    },
                )
                results.append((alert, is_dup))

            if num_res.psi_drift or num_res.psi_value >= settings.drift_psi_moderate_threshold:
                severity = (
                    AlertSeverity.HIGH.value
                    if num_res.psi_value >= settings.drift_psi_threshold
                    else AlertSeverity.MEDIUM.value
                )
                alert, is_dup = self.record_or_deduplicate(
                    metric_name="DRIFT_PSI",
                    severity=severity,
                    observed_value=num_res.psi_value,
                    threshold=settings.drift_psi_threshold,
                    feature_name=feat,
                    message=f"PSI shift detected on continuous feature '{feat}' (PSI: {num_res.psi_value})",
                    supporting_context={"psi_value": num_res.psi_value},
                )
                results.append((alert, is_dup))

        # 2. Categorical feature drift checks (PSI)
        for feat, cat_res in drift_report.categorical_drift.items():
            if cat_res.psi_drift or cat_res.psi_value >= settings.drift_psi_moderate_threshold:
                severity = (
                    AlertSeverity.HIGH.value
                    if cat_res.psi_value >= settings.drift_psi_threshold
                    else AlertSeverity.MEDIUM.value
                )
                alert, is_dup = self.record_or_deduplicate(
                    metric_name="DRIFT_PSI",
                    severity=severity,
                    observed_value=cat_res.psi_value,
                    threshold=settings.drift_psi_threshold,
                    feature_name=feat,
                    message=f"PSI categorical shift detected on feature '{feat}' (PSI: {cat_res.psi_value})",
                    supporting_context={
                        "psi_value": cat_res.psi_value,
                        "chi2_statistic": cat_res.chi2_statistic,
                        "chi2_p_value": cat_res.chi2_p_value,
                    },
                )
                results.append((alert, is_dup))

        # 3. Overall dataset drift status breach
        if drift_report.overall_drift_status == "DRIFT_DETECTED":
            alert, is_dup = self.record_or_deduplicate(
                metric_name="OVERALL_DATA_DRIFT",
                severity=AlertSeverity.HIGH.value,
                observed_value=float(len(drift_report.drifted_features)),
                threshold=1.0,
                feature_name=None,
                message=f"Overall dataset distribution drift confirmed: {len(drift_report.drifted_features)} feature(s) drifted",
                supporting_context={"drifted_features": drift_report.drifted_features},
            )
            results.append((alert, is_dup))

        return results

    def evaluate_telemetry_metrics(
        self,
        metrics: MetricsSummaryResponse,
    ) -> List[Tuple[AlertResponse, bool]]:
        """Evaluate operational telemetry (error rate, p95 latency) against configured thresholds."""
        results: List[Tuple[AlertResponse, bool]] = []

        # Error rate threshold check (FR-08)
        if metrics.count >= 10 and metrics.error_rate > settings.alert_error_rate_threshold:
            alert, is_dup = self.record_or_deduplicate(
                metric_name="PREDICTION_ERROR_RATE",
                severity=AlertSeverity.HIGH.value,
                observed_value=metrics.error_rate,
                threshold=settings.alert_error_rate_threshold,
                feature_name=None,
                message=f"Operational error rate breached threshold: {metrics.error_rate * 100:.1f}% > {settings.alert_error_rate_threshold * 100:.1f}%",
                supporting_context={
                    "total_count": metrics.count,
                    "error_count": metrics.error_count,
                    "error_rate": metrics.error_rate,
                },
            )
            results.append((alert, is_dup))

        # P95 latency threshold check (FR-08, NFR-01)
        if metrics.count >= 5:
            if metrics.latency_p95_ms > settings.alert_latency_p95_threshold_ms:
                alert, is_dup = self.record_or_deduplicate(
                    metric_name="PREDICTION_LATENCY_P95",
                    severity=AlertSeverity.HIGH.value,
                    observed_value=metrics.latency_p95_ms,
                    threshold=settings.alert_latency_p95_threshold_ms,
                    feature_name=None,
                    message=f"P95 latency breached critical threshold: {metrics.latency_p95_ms:.1f}ms > {settings.alert_latency_p95_threshold_ms:.1f}ms",
                    supporting_context={
                        "latency_p50_ms": metrics.latency_p50_ms,
                        "latency_p95_ms": metrics.latency_p95_ms,
                        "latency_p99_ms": metrics.latency_p99_ms,
                    },
                )
                results.append((alert, is_dup))
            elif metrics.latency_p95_ms > settings.alert_latency_p95_warning_ms:
                alert, is_dup = self.record_or_deduplicate(
                    metric_name="PREDICTION_LATENCY_P95",
                    severity=AlertSeverity.MEDIUM.value,
                    observed_value=metrics.latency_p95_ms,
                    threshold=settings.alert_latency_p95_warning_ms,
                    feature_name=None,
                    message=f"P95 latency breached warning threshold: {metrics.latency_p95_ms:.1f}ms > {settings.alert_latency_p95_warning_ms:.1f}ms",
                    supporting_context={"latency_p95_ms": metrics.latency_p95_ms},
                )
                results.append((alert, is_dup))

        return results

    def evaluate_all(
        self,
        drift_report: Optional[DriftReportResponse] = None,
        metrics: Optional[MetricsSummaryResponse] = None,
    ) -> List[Tuple[AlertResponse, bool]]:
        """Evaluate both drift report and operational telemetry metrics."""
        results: List[Tuple[AlertResponse, bool]] = []
        if drift_report is not None:
            results.extend(self.evaluate_drift_report(drift_report))
        if metrics is not None:
            results.extend(self.evaluate_telemetry_metrics(metrics))
        return results

    def reset(self) -> None:
        """Clear alerts table and in-memory notifications (used for test isolation)."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM alerts")
                conn.commit()
        self.dispatcher.in_memory_channel.clear()

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> AlertResponse:
        context = {}
        try:
            context = json.loads(row["supporting_context"])
        except Exception:
            pass

        return AlertResponse(
            alert_id=row["alert_id"],
            fingerprint=row["fingerprint"],
            metric_name=row["metric_name"],
            feature_name=row["feature_name"],
            severity=row["severity"],
            observed_value=float(row["observed_value"]),
            threshold=float(row["threshold"]),
            status=row["status"],
            message=row["message"],
            supporting_context=context,
            dedup_count=int(row["dedup_count"]),
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            acknowledged_at=row["acknowledged_at"],
            acknowledged_by=row["acknowledged_by"],
            resolved_at=row["resolved_at"],
            resolved_by=row["resolved_by"],
        )


# Global singleton instance
alert_manager = AlertManager()
