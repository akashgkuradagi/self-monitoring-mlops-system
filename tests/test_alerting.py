"""Unit and Integration Tests for Multi-Metric Alert Manager (ALERT-01).

Validates:
- FR-08: Multi-metric threshold alerting (drift p-value, PSI, error rate, p95 latency).
- NFR-07: Operational telemetry integration and threshold evaluation.
- NFR-09: Scalable, thread-safe alert deduplication preventing alert storms.
- TC-04 / TC-16: Alert creation, deduplication within rolling windows, recurrence after resolution, and RBAC.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd
import pytest
from starlette.testclient import TestClient

from app.api.routes import DEMO_USERS
from app.config import settings
from app.main import app
from app.monitoring.alerting import AlertManager, alert_manager
from app.monitoring.collector import telemetry_collector
from app.monitoring.drift import drift_analyzer
from app.schemas.alert import AlertSeverity, AlertStatus
from app.schemas.metrics import MetricsSummaryResponse
from app.security.audit import audit_logger
from app.security.auth import create_access_token


@pytest.fixture(autouse=True)
def clean_state():
    """Reset alert database and telemetry collector for clean isolation."""
    alert_manager.reset()
    telemetry_collector.reset()
    yield
    alert_manager.reset()
    telemetry_collector.reset()


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Auth headers for different RBAC roles."""
    headers = {}
    for user, (_, role) in DEMO_USERS.items():
        token = create_access_token(subject=user, role=role)
        headers[role.value] = {"Authorization": f"Bearer {token}"}
    return headers


def test_threshold_evaluation_drift_metrics():
    """FR-08 / TC-04: Verify drift report triggers structured alerts for drifted features."""
    num_drift_path = Path("data/fixtures/numerical_drift_fixture.csv")
    assert num_drift_path.is_file()
    df = pd.read_csv(num_drift_path)

    drift_report = drift_analyzer.evaluate_drift(current_df=df)
    assert drift_report.overall_drift_status == "DRIFT_DETECTED"

    eval_results = alert_manager.evaluate_drift_report(drift_report)
    assert len(eval_results) > 0

    # Ensure all returned alerts are structured properly
    for alert, is_dup in eval_results:
        assert alert.alert_id.startswith("alt-")
        assert alert.severity in [AlertSeverity.HIGH.value, AlertSeverity.MEDIUM.value]
        assert alert.status == AlertStatus.ACTIVE.value
        assert is_dup is False

    # Check that overall dataset drift alert was created
    overall_alerts = [a for a, _ in eval_results if a.metric_name == "OVERALL_DATA_DRIFT"]
    assert len(overall_alerts) == 1
    assert overall_alerts[0].severity == AlertSeverity.HIGH.value
    assert overall_alerts[0].observed_value >= 1.0


def test_threshold_evaluation_telemetry_metrics():
    """FR-08 / NFR-07: Verify operational telemetry error rate and latency breach alerts."""
    # Breached metrics: error rate 15% (> 5%), p95 latency 650ms (> 500ms)
    breached_metrics = MetricsSummaryResponse(
        count=100,
        error_count=15,
        error_rate=0.15,
        latency_p50_ms=120.0,
        latency_p95_ms=650.0,
        latency_p99_ms=800.0,
        latency_mean_ms=150.0,
        model_version="baseline-v1",
        drift_status="STABLE",
        window_size=1000,
        timestamp="2026-11-17T12:00:00Z",
    )

    results = alert_manager.evaluate_telemetry_metrics(breached_metrics)
    assert len(results) == 2

    metrics_triggered = {a.metric_name: a for a, _ in results}
    assert "PREDICTION_ERROR_RATE" in metrics_triggered
    assert "PREDICTION_LATENCY_P95" in metrics_triggered

    err_alert = metrics_triggered["PREDICTION_ERROR_RATE"]
    assert err_alert.severity == AlertSeverity.HIGH.value
    assert err_alert.observed_value == 0.15
    assert err_alert.threshold == settings.alert_error_rate_threshold

    lat_alert = metrics_triggered["PREDICTION_LATENCY_P95"]
    assert lat_alert.severity == AlertSeverity.HIGH.value
    assert lat_alert.observed_value == 650.0
    assert lat_alert.threshold == settings.alert_latency_p95_threshold_ms


def test_severity_assignment_warning_vs_critical():
    """FR-08: Verify correct assignment of MEDIUM vs HIGH severity levels."""
    # Latency between warning (300ms) and critical (500ms) -> MEDIUM
    warning_metrics = MetricsSummaryResponse(
        count=50,
        error_count=0,
        error_rate=0.0,
        latency_p50_ms=100.0,
        latency_p95_ms=350.0,
        latency_p99_ms=400.0,
        latency_mean_ms=120.0,
        model_version="baseline-v1",
        drift_status="STABLE",
        window_size=1000,
        timestamp="2026-11-17T12:00:00Z",
    )
    res_warn = alert_manager.evaluate_telemetry_metrics(warning_metrics)
    assert len(res_warn) == 1
    assert res_warn[0][0].severity == AlertSeverity.MEDIUM.value
    assert res_warn[0][0].threshold == settings.alert_latency_p95_warning_ms

    # Latency above critical (500ms) -> HIGH
    critical_metrics = MetricsSummaryResponse(
        count=50,
        error_count=0,
        error_rate=0.0,
        latency_p50_ms=100.0,
        latency_p95_ms=550.0,
        latency_p99_ms=600.0,
        latency_mean_ms=120.0,
        model_version="baseline-v1",
        drift_status="STABLE",
        window_size=1000,
        timestamp="2026-11-17T12:00:00Z",
    )
    res_crit = alert_manager.evaluate_telemetry_metrics(critical_metrics)
    assert len(res_crit) == 1
    assert res_crit[0][0].severity == AlertSeverity.HIGH.value
    assert res_crit[0][0].threshold == settings.alert_latency_p95_threshold_ms


def test_alert_deduplication_within_rolling_window():
    """Acceptance Criteria 2 / TC-16: Rapid consecutive breaches within 5 minutes are aggregated."""
    # First breach
    alert1, is_dup1 = alert_manager.record_or_deduplicate(
        metric_name="PREDICTION_ERROR_RATE",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.08,
        threshold=0.05,
        message="Error rate spike 1",
    )
    assert is_dup1 is False
    assert alert1.dedup_count == 1

    # Second breach immediately after (same metric, feature, and severity)
    alert2, is_dup2 = alert_manager.record_or_deduplicate(
        metric_name="PREDICTION_ERROR_RATE",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.10,
        threshold=0.05,
        message="Error rate spike 2",
    )
    assert is_dup2 is True
    assert alert2.alert_id == alert1.alert_id
    assert alert2.dedup_count == 2
    assert alert2.observed_value == 0.10

    # Third breach
    alert3, is_dup3 = alert_manager.record_or_deduplicate(
        metric_name="PREDICTION_ERROR_RATE",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.12,
        threshold=0.05,
        message="Error rate spike 3",
    )
    assert is_dup3 is True
    assert alert3.dedup_count == 3

    # Verify only ONE active alert exists in storage
    active_alerts = alert_manager.list_alerts(status="ACTIVE")
    assert len(active_alerts) == 1
    assert active_alerts[0].dedup_count == 3


def test_alert_recurrence_after_resolution():
    """Acceptance Criteria 2 / FR-08: Permit new alert when previous is resolved and condition recurs."""
    # 1. Trigger initial alert
    alert1, is_dup1 = alert_manager.record_or_deduplicate(
        metric_name="DRIFT_KS",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.001,
        threshold=0.05,
        feature_name="income",
        message="Initial income drift",
    )
    assert is_dup1 is False

    # 2. Resolve initial alert
    resolved = alert_manager.resolve_alert(alert_id=alert1.alert_id, actor="ml_engineer")
    assert resolved is not None
    assert resolved.status == AlertStatus.RESOLVED.value
    assert resolved.resolved_by == "ml_engineer"

    # 3. Recurrence of the same breach condition
    alert2, is_dup2 = alert_manager.record_or_deduplicate(
        metric_name="DRIFT_KS",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.002,
        threshold=0.05,
        feature_name="income",
        message="Recurrent income drift after resolution",
    )
    assert is_dup2 is False, "Resolved alert must not block new active alert on recurrent condition"
    assert alert2.alert_id != alert1.alert_id
    assert alert2.status == AlertStatus.ACTIVE.value
    assert alert2.dedup_count == 1

    # Total in DB should now be 2 (1 resolved, 1 active)
    all_alerts = alert_manager.list_alerts()
    assert len(all_alerts) == 2


def test_concurrent_alert_creation():
    """NFR-09: Concurrent alert creation across worker threads is thread-safe."""
    def trigger_worker(worker_id: int):
        return alert_manager.record_or_deduplicate(
            metric_name="PREDICTION_ERROR_RATE",
            severity=AlertSeverity.HIGH.value,
            observed_value=0.06 + (worker_id * 0.001),
            threshold=0.05,
            message=f"Worker {worker_id} breach",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(trigger_worker, range(20)))

    # All 20 threads targeted the same condition -> exactly 1 should be original, 19 deduplicated
    new_creations = [r for r, is_dup in results if not is_dup]
    duplicates = [r for r, is_dup in results if is_dup]

    assert len(new_creations) == 1
    assert len(duplicates) == 19

    active = alert_manager.list_alerts(status="ACTIVE")
    assert len(active) == 1
    assert active[0].dedup_count == 20


def test_persistence_and_retrieval(tmp_path):
    """SAD Table 6: Alerts persist in SQLite and are retrievable across manager instances."""
    custom_db = str(tmp_path / "test_alerts.db")
    mgr1 = AlertManager(db_path=custom_db)

    alert1, _ = mgr1.record_or_deduplicate(
        metric_name="DRIFT_PSI",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.35,
        threshold=0.25,
        feature_name="credit_score",
        message="Credit score high PSI shift",
    )

    # Instantiate separate manager on same database
    mgr2 = AlertManager(db_path=custom_db)
    retrieved = mgr2.get_alert(alert1.alert_id)

    assert retrieved is not None
    assert retrieved.alert_id == alert1.alert_id
    assert retrieved.metric_name == "DRIFT_PSI"
    assert retrieved.feature_name == "credit_score"
    assert retrieved.observed_value == 0.35


def test_alert_lifecycle_state_transitions():
    """Verify ACTIVE -> ACKNOWLEDGED -> RESOLVED lifecycle transitions."""
    alert, _ = alert_manager.record_or_deduplicate(
        metric_name="PREDICTION_LATENCY_P95",
        severity=AlertSeverity.HIGH.value,
        observed_value=600.0,
        threshold=500.0,
        message="Latency breach",
    )
    assert alert.status == AlertStatus.ACTIVE.value
    assert alert.acknowledged_at is None
    assert alert.resolved_at is None

    # Transition to ACKNOWLEDGED
    acked = alert_manager.acknowledge_alert(
        alert_id=alert.alert_id,
        actor="engineer_user",
        comment="Investigating network congestion",
    )
    assert acked is not None
    assert acked.status == AlertStatus.ACKNOWLEDGED.value
    assert acked.acknowledged_by == "engineer_user"
    assert acked.acknowledged_at is not None

    # Transition to RESOLVED
    resolved = alert_manager.resolve_alert(
        alert_id=alert.alert_id,
        actor="admin_user",
        comment="Autoscaled worker replicas",
    )
    assert resolved is not None
    assert resolved.status == AlertStatus.RESOLVED.value
    assert resolved.resolved_by == "admin_user"
    assert resolved.resolved_at is not None


def test_notification_dispatcher_channels():
    """Verify local in-memory notification channel receives newly generated alerts."""
    in_mem_ch = alert_manager.dispatcher.in_memory_channel
    in_mem_ch.clear()

    # Initial alert triggers notification
    alert, is_dup = alert_manager.record_or_deduplicate(
        metric_name="OVERALL_DATA_DRIFT",
        severity=AlertSeverity.HIGH.value,
        observed_value=3.0,
        threshold=1.0,
        message="3 features drifted",
    )
    assert is_dup is False
    assert len(in_mem_ch.get_notifications()) == 1
    assert in_mem_ch.get_notifications()[0].alert_id == alert.alert_id

    # Deduplicated breach does not send duplicate notifications
    _, is_dup2 = alert_manager.record_or_deduplicate(
        metric_name="OVERALL_DATA_DRIFT",
        severity=AlertSeverity.HIGH.value,
        observed_value=3.0,
        threshold=1.0,
        message="3 features drifted again",
    )
    assert is_dup2 is True
    assert len(in_mem_ch.get_notifications()) == 1  # No duplicate notification


def test_alert_api_rbac_permissions(client, auth_headers):
    """Test RBAC authorization on all alert endpoints (SAD Table 4)."""
    # 1. Unauthenticated list alerts -> 401
    resp_unauth = client.get("/api/v1/alerts")
    assert resp_unauth.status_code == 401

    # 2. Viewer can view alerts -> 200
    resp_viewer = client.get("/api/v1/alerts", headers=auth_headers["Viewer"])
    assert resp_viewer.status_code == 200

    # Create an alert to test lifecycle transitions
    alert, _ = alert_manager.record_or_deduplicate(
        metric_name="DRIFT_KS",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.005,
        threshold=0.05,
        feature_name="debt_to_income",
        message="Debt-to-income KS breach",
    )

    # 3. Viewer cannot acknowledge -> 403
    resp_viewer_ack = client.post(
        f"/api/v1/alerts/{alert.alert_id}/acknowledge",
        headers=auth_headers["Viewer"],
        json={"comment": "Trying to ack"},
    )
    assert resp_viewer_ack.status_code == 403

    # 4. ML_Engineer can acknowledge -> 200
    resp_eng_ack = client.post(
        f"/api/v1/alerts/{alert.alert_id}/acknowledge",
        headers=auth_headers["ML_Engineer"],
        json={"comment": "Triaging issue"},
    )
    assert resp_eng_ack.status_code == 200
    assert resp_eng_ack.json()["status"] == "ACKNOWLEDGED"

    # 5. Viewer cannot resolve -> 403
    resp_viewer_res = client.post(
        f"/api/v1/alerts/{alert.alert_id}/resolve",
        headers=auth_headers["Viewer"],
    )
    assert resp_viewer_res.status_code == 403

    # 6. ML_Engineer can resolve -> 200
    resp_eng_res = client.post(
        f"/api/v1/alerts/{alert.alert_id}/resolve",
        headers=auth_headers["ML_Engineer"],
        json={"comment": "Resolved by retrain"},
    )
    assert resp_eng_res.status_code == 200
    assert resp_eng_res.json()["status"] == "RESOLVED"

    # 7. Viewer cannot trigger alert evaluation -> 403
    resp_viewer_eval = client.post(
        "/api/v1/monitoring/alerts/evaluate",
        headers=auth_headers["Viewer"],
    )
    assert resp_viewer_eval.status_code == 403

    # 8. ML_Engineer can trigger alert evaluation -> 200
    resp_eng_eval = client.post(
        "/api/v1/monitoring/alerts/evaluate",
        headers=auth_headers["ML_Engineer"],
    )
    assert resp_eng_eval.status_code == 200
    eval_data = resp_eng_eval.json()
    assert "alerts_evaluated" in eval_data


def test_alert_filtering_and_summary_endpoint(client, auth_headers):
    """Test query filtering and aggregate summary endpoint."""
    # Seed alerts
    alert_manager.record_or_deduplicate(
        metric_name="DRIFT_KS",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.001,
        threshold=0.05,
        feature_name="income",
        message="Income drift",
    )
    alert_manager.record_or_deduplicate(
        metric_name="PREDICTION_LATENCY_P95",
        severity=AlertSeverity.MEDIUM.value,
        observed_value=320.0,
        threshold=300.0,
        message="Latency warning",
    )

    # Test summary endpoint
    sum_resp = client.get("/api/v1/alerts/summary", headers=auth_headers["Viewer"])
    assert sum_resp.status_code == 200
    sum_data = sum_resp.json()
    assert sum_data["total_alerts"] == 2
    assert sum_data["active_alerts"] == 2
    assert sum_data["by_severity"]["HIGH"] == 1
    assert sum_data["by_severity"]["MEDIUM"] == 1

    # Test filtering by severity=HIGH
    resp_filtered = client.get(
        "/api/v1/alerts",
        headers=auth_headers["Viewer"],
        params={"severity": "HIGH"},
    )
    assert resp_filtered.status_code == 200
    items = resp_filtered.json()
    assert len(items) == 1
    assert items[0]["severity"] == "HIGH"
    assert items[0]["metric_name"] == "DRIFT_KS"


def test_audit_logging_integration(client, auth_headers):
    """Verify security-sensitive alert actions record tamper-evident audit events."""
    alert, _ = alert_manager.record_or_deduplicate(
        metric_name="DRIFT_PSI",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.30,
        threshold=0.25,
        feature_name="age",
        message="Age PSI breach",
    )

    # Acknowledge via API
    client.post(
        f"/api/v1/alerts/{alert.alert_id}/acknowledge",
        headers=auth_headers["ML_Engineer"],
        json={"comment": "Acknowledged in audit test"},
    )

    # Resolve via API
    client.post(
        f"/api/v1/alerts/{alert.alert_id}/resolve",
        headers=auth_headers["ML_Engineer"],
        json={"comment": "Resolved in audit test"},
    )

    # Verify audit events exist
    events = audit_logger.get_events(limit=50)
    ack_events = [e for e in events if e.action == "ALERT_ACKNOWLEDGE"]
    res_events = [e for e in events if e.action == "ALERT_RESOLVE"]

    assert len(ack_events) >= 1
    assert ack_events[0].resource == f"alert:{alert.alert_id}"
    assert ack_events[0].outcome == "SUCCESS"

    assert len(res_events) >= 1
    assert res_events[0].resource == f"alert:{alert.alert_id}"
    assert res_events[0].outcome == "SUCCESS"
