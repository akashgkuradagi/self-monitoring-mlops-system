"""Targeted Branch Coverage Optimization Test Suite.

Provides thorough edge-case testing for remaining uncovered decision branches across:
- app.data.validation
- app.models.baseline
- app.models.ml_monitor
- app.models.qml_benchmark
- app.monitoring.drift
- app.monitoring.alerting
- app.registry.mlflow_adapter
- app.recovery.validator
- app.recovery.health_guard
- app.recovery.orchestrator
- app.recovery.policy
- app.recovery.registry
- app.api.routes
"""

from pathlib import Path
import sqlite3
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.data.generator import generate_credit_risk_dataset
from app.data.schema import FEATURE_COLUMNS, TARGET_COLUMN
from app.data.validation import validate_dataset
from app.models.baseline import BaselineClassifier, baseline_classifier
from app.models.ml_monitor import IsolationForestDetector
from app.models.qml_benchmark import QMLBenchmarkClassifier
from app.monitoring.alerting import (
    AlertManager,
    AlertSeverity,
    InMemoryNotificationChannel,
    NotificationChannel,
    NotificationDispatcher,
)
from app.monitoring.drift import DriftAnalyzer
from app.recovery.health_guard import PostPromotionHealthGuard
from app.recovery.orchestrator import RecoveryOrchestrator, recovery_orchestrator
from app.recovery.policy import RecoveryPolicyEngine
from app.recovery.registry import CandidateRegistry, candidate_registry
from app.recovery.validator import GateStatus, ModelValidator
from app.registry.mlflow_adapter import MLflowAdapter
from app.schemas.drift import CategoricalDriftResult, DriftReportResponse
from app.schemas.metrics import MetricsSummaryResponse
from app.security.auth import Role, create_access_token


@pytest.fixture(autouse=True)
def cleanup_mlflow_run():
    """Ensure no dangling active MLflow runs leak between tests."""
    import mlflow
    while mlflow.active_run():
        mlflow.end_run()
    yield
    while mlflow.active_run():
        mlflow.end_run()


@pytest.fixture
def admin_headers():
    token = create_access_token(subject="admin_cov", role=Role.ADMINISTRATOR)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def operator_headers():
    token = create_access_token(subject="operator_cov", role=Role.ML_ENGINEER)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def viewer_headers():
    token = create_access_token(subject="viewer_cov", role=Role.VIEWER)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def valid_sample_df():
    """Return a deterministic schema-compliant 50-row credit dataframe."""
    return generate_credit_risk_dataset(n_samples=50, seed=42)


# ============================================================================
# 1. DATA VALIDATION BRANCH TESTS
# ============================================================================

def test_validation_null_and_type_checks(valid_sample_df):
    """Test validation.py branches for None, wrong type, and empty df."""
    rep_none = validate_dataset(None)
    assert not rep_none.is_valid
    assert "None" in rep_none.issues[0]

    rep_type = validate_dataset("not a dataframe")
    assert not rep_type.is_valid
    assert "pandas DataFrame" in rep_type.issues[0]

    rep_empty = validate_dataset(pd.DataFrame())
    assert not rep_empty.is_valid
    assert "empty" in rep_empty.issues[0]


def test_validation_allow_missing_and_duplicates(valid_sample_df):
    """Test allow_missing=True and allow_duplicates=True branches."""
    df_with_nan = valid_sample_df.copy()
    df_with_nan.loc[0, "employment_length"] = np.nan
    rep_missing_allowed = validate_dataset(df_with_nan, allow_missing=True)
    assert not any("null values" in iss for iss in rep_missing_allowed.issues)

    df_dups = pd.concat([valid_sample_df, valid_sample_df.iloc[[0]]], ignore_index=True)
    rep_dups_allowed = validate_dataset(df_dups, allow_duplicates=True)
    assert not any("duplicate row" in iss for iss in rep_dups_allowed.issues)


def test_validation_non_numeric_and_partial_categories(valid_sample_df):
    """Test non-numeric numerical columns and dropped categorical columns."""
    df_bad_type = valid_sample_df.copy()
    df_bad_type["age"] = "not_a_number"
    rep = validate_dataset(df_bad_type)
    assert not rep.is_valid
    assert any("must be numeric" in iss for iss in rep.issues)

    # Drop a categorical column to test the 'if col in df.columns' False branch
    df_missing_cat = valid_sample_df.drop(columns=["home_ownership"])
    rep_missing_cat = validate_dataset(df_missing_cat, require_target=False)
    assert not rep_missing_cat.is_valid


# ============================================================================
# 2. BASELINE MODEL BRANCH TESTS
# ============================================================================

def test_baseline_model_branches(valid_sample_df, tmp_path):
    """Test unfitted errors, bad input, and custom seed branches in BaselineClassifier."""
    model = BaselineClassifier()

    # Unfitted predict / evaluate / save
    with pytest.raises(RuntimeError, match="not fitted"):
        model.predict(valid_sample_df)

    with pytest.raises(RuntimeError, match="not fitted"):
        model.evaluate(valid_sample_df)

    with pytest.raises(RuntimeError, match="Cannot save unfitted"):
        model.save(tmp_path / "model.joblib")

    # Load non-existent file
    with pytest.raises(FileNotFoundError):
        model.load(tmp_path / "does_not_exist.joblib")

    # Train with custom seed and verify serialization
    model.train(valid_sample_df, seed=123)
    assert model.random_state == 123
    assert model.is_fitted

    # Save and load fitted model
    save_path = tmp_path / "fitted_model.joblib"
    model.save(save_path)
    assert save_path.exists()

    loaded_model = BaselineClassifier()
    loaded_model.load(save_path)
    assert loaded_model.is_fitted
    preds_loaded, _ = loaded_model.predict(valid_sample_df)
    assert len(preds_loaded) == len(valid_sample_df)

    # Predict with missing features
    bad_df = valid_sample_df.drop(columns=["age"])
    with pytest.raises(ValueError, match="Features missing"):
        model.predict(bad_df)

    # Evaluate with invalid eval_df
    bad_eval = valid_sample_df.copy()
    bad_eval.loc[0, "age"] = -5
    with pytest.raises(ValueError, match="failed validation"):
        model.evaluate(bad_eval)

    # Train with invalid dataset
    with pytest.raises(ValueError, match="failed validation"):
        model.train(bad_eval)


# ============================================================================
# 3. ML MONITOR (ISOLATION FOREST) BRANCH TESTS
# ============================================================================

def test_ml_monitor_branches(valid_sample_df):
    """Test IsolationForestDetector edge branches: empty input, missing cols, seed."""
    det = IsolationForestDetector()

    # Fit with invalid train data
    bad_train = valid_sample_df.copy()
    bad_train.loc[0, "age"] = 999
    with pytest.raises(ValueError, match="failed validation"):
        det.train(bad_train)

    # Train with seed
    det.train(valid_sample_df, seed=42)
    assert det.is_fitted

    # Empty predict_records
    preds, scores, probs = det.predict_records(pd.DataFrame(columns=FEATURE_COLUMNS))
    assert len(preds) == 0

    # Empty evaluate_batch
    resp_empty = det.evaluate_batch([])
    assert resp_empty.total_samples == 0

    # Missing columns in predict_records
    with pytest.raises(ValueError, match="missing required feature columns"):
        det.predict_records(pd.DataFrame([{"income": 50000.0}]))

    # Valid predict_records
    df_valid = valid_sample_df.drop(columns=[TARGET_COLUMN]).head(5)
    preds_val, scores_val, probs_val = det.predict_records(df_valid)
    assert len(preds_val) == 5


# ============================================================================
# 4. QML BENCHMARK BRANCH TESTS
# ============================================================================

def test_qml_benchmark_branches(valid_sample_df, tmp_path):
    """Test QMLBenchmarkClassifier edge branches and error cases."""
    qml = QMLBenchmarkClassifier()

    # Unfitted predict_proba
    with pytest.raises(RuntimeError, match="not fitted"):
        qml.predict_proba(valid_sample_df)

    # Save comparison with no results
    with pytest.raises(RuntimeError, match="No benchmark results to save"):
        qml.save_comparison(tmp_path / "scorecard.json")

    # Evaluate missing target column
    no_target = valid_sample_df.drop(columns=[TARGET_COLUMN])
    with pytest.raises(ValueError, match=f"missing target column '{TARGET_COLUMN}'"):
        qml.evaluate(no_target)

    # Mock fitted model and test predict_proba with missing cols and raw numpy
    qml.is_fitted = True
    mock_out = MagicMock()
    mock_out.numpy.return_value = np.zeros(2)
    qml.torch_model = MagicMock(return_value=mock_out)
    qml.scaler = MagicMock()
    qml.scaler.transform.return_value = np.zeros((2, len(qml.feature_subset)), dtype=np.float32)

    # Missing features in dataframe
    with pytest.raises(ValueError, match="Input missing features"):
        qml.predict_proba(pd.DataFrame({"wrong_col": [1, 2]}))

    # Raw numpy array
    raw_x = np.zeros((2, len(qml.feature_subset)), dtype=np.float32)
    preds = qml.predict_proba(raw_x)
    assert len(preds) == 2


# ============================================================================
# 5. DATA DRIFT DETECTOR BRANCH TESTS
# ============================================================================

def test_drift_detector_branches(valid_sample_df, tmp_path):
    """Test drift detector branch edge-cases: empty inputs, constant values, invalid ref."""
    detector = DriftAnalyzer(reference_path=str(tmp_path / "nonexistent.csv"))

    # Missing reference file
    with pytest.raises(FileNotFoundError):
        _ = detector.reference_df()

    # Set invalid reference dataframe
    bad_df = valid_sample_df.copy()
    bad_df.loc[0, "age"] = 500
    with pytest.raises(ValueError, match="failed validation"):
        detector.set_reference_dataframe(bad_df)

    # Numerical PSI with empty series
    psi_empty = detector.calculate_numerical_psi(pd.Series([], dtype=float), pd.Series([1.0, 2.0]))
    assert psi_empty == 0.0

    # Numerical PSI with constant values (bin_edges < 2)
    psi_const = detector.calculate_numerical_psi(pd.Series([5.0] * 20), pd.Series([5.0] * 20))
    assert psi_const == 0.0

    # Categorical PSI with empty series
    psi_cat_empty, _, _, _ = detector.calculate_categorical_psi(pd.Series([], dtype=str), pd.Series(["A", "B"]))
    assert psi_cat_empty == 0.0

    # Categorical PSI with valid counts
    ref_cat = pd.Series(["RENT", "OWN", "MORTGAGE"] * 10)
    cur_cat = pd.Series(["RENT", "RENT", "OWN"] * 10)
    psi_cat, props, _, _ = detector.calculate_categorical_psi(ref_cat, cur_cat)
    assert psi_cat >= 0.0
    assert props is not None

    # Evaluate drift with curr_len < min_sample_size and update_telemetry_status=False
    detector.set_reference_dataframe(valid_sample_df)
    tiny_df = valid_sample_df.iloc[:2]
    rep_tiny = detector.evaluate_drift(tiny_df, update_telemetry_status=False)
    assert rep_tiny.overall_drift_status == "INSUFFICIENT_DATA"

    # Evaluate drift with update_telemetry_status=False on sufficient data
    rep_full = detector.evaluate_drift(valid_sample_df, update_telemetry_status=False)
    assert rep_full.overall_drift_status in ("STABLE", "DRIFT_DETECTED")


# ============================================================================
# 6. ALERTING SYSTEM BRANCH TESTS
# ============================================================================

def test_alerting_branches(tmp_path):
    """Test notification channels, dispatch exceptions, and alert manager queries."""
    # Base NotificationChannel
    base_channel = NotificationChannel()
    with pytest.raises(NotImplementedError):
        base_channel.send(MagicMock())

    # Dispatcher error handling
    failing_channel = MagicMock()
    failing_channel.send.side_effect = RuntimeError("Channel delivery failed")
    dispatcher = NotificationDispatcher()
    dispatcher.register_channel(failing_channel)
    dispatcher.dispatch(MagicMock())  # Caught and logged without raising

    # InMemory channel lazy creation
    dispatcher_empty = NotificationDispatcher()
    in_mem = dispatcher_empty.in_memory_channel
    assert isinstance(in_mem, InMemoryNotificationChannel)

    # AlertManager queries
    test_db = str(tmp_path / "test_alerts.db")
    mgr = AlertManager(db_path=test_db, dedup_window_seconds=1)

    # get_alert for non-existent alert
    assert mgr.get_alert("missing-id") is None

    # acknowledge and resolve for non-existent alert
    assert mgr.acknowledge_alert("missing-id", actor="tester") is None
    assert mgr.resolve_alert("missing-id", actor="tester") is None

    # Create an alert and test deduplication
    alert1, is_dup1 = mgr.record_or_deduplicate(
        metric_name="TEST_METRIC",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.95,
        threshold=0.50,
        message="High metric observed",
    )
    assert not is_dup1

    # Immediate second call within dedup window -> duplicate
    alert2, is_dup2 = mgr.record_or_deduplicate(
        metric_name="TEST_METRIC",
        severity=AlertSeverity.HIGH.value,
        observed_value=0.96,
        threshold=0.50,
        message="High metric observed again",
    )
    assert is_dup2

    # Query with status and metric filter
    alerts_filtered = mgr.list_alerts(status="ACTIVE", metric="TEST_METRIC")
    assert len(alerts_filtered) >= 1

    # Drift report evaluation with INSUFFICIENT_DATA
    dummy_insufficient = DriftReportResponse(
        evaluation_timestamp="2026-01-01T00:00:00Z",
        overall_drift_status="INSUFFICIENT_DATA",
        is_drifted=False,
        reference_sample_count=100,
        current_sample_count=5,
        drifted_features=[],
        numerical_drift={},
        categorical_drift={},
        thresholds={},
        reference_dataset_path="data/reference/train.csv",
    )
    assert mgr.evaluate_drift_report(dummy_insufficient) == []

    # Drift report with DRIFT_DETECTED and categorical moderate threshold
    dummy_drifted = DriftReportResponse(
        evaluation_timestamp="2026-01-01T00:00:00Z",
        overall_drift_status="DRIFT_DETECTED",
        is_drifted=True,
        reference_sample_count=100,
        current_sample_count=100,
        drifted_features=["loan_intent"],
        numerical_drift={},
        categorical_drift={
            "loan_intent": CategoricalDriftResult(
                feature_name="loan_intent",
                drift_detected=False,
                psi_drift=False,
                psi_value=0.15,
                category_proportions={},
            ),
        },
        thresholds={},
        reference_dataset_path="data/reference/train.csv",
    )
    res_drift = mgr.evaluate_drift_report(dummy_drifted)
    assert len(res_drift) >= 1

    # Telemetry metrics warning-level latency
    metrics_warning = MetricsSummaryResponse(
        count=100,
        error_count=0,
        error_rate=0.0,
        latency_p50_ms=50.0,
        latency_p95_ms=350.0,
        latency_p99_ms=400.0,
        latency_mean_ms=60.0,
        model_version="v1",
        drift_status="STABLE",
        window_size=100,
        timestamp="2026-01-01T00:00:00Z",
    )
    res_metrics = mgr.evaluate_telemetry_metrics(metrics_warning)
    assert any(a.metric_name == "PREDICTION_LATENCY_P95" and a.severity == "MEDIUM" for a, _ in res_metrics)

    # evaluate_all with partial None arguments
    assert mgr.evaluate_all(drift_report=dummy_insufficient, metrics=None) == []
    assert len(mgr.evaluate_all(drift_report=None, metrics=metrics_warning)) >= 1

    # Test corrupted context JSON parsing fallback
    with sqlite3.connect(test_db) as conn:
        conn.execute(
            "UPDATE alerts SET supporting_context = 'invalid-json' WHERE alert_id = ?",
            (alert1.alert_id,),
        )
    corrupt_alert = mgr.get_alert(alert1.alert_id)
    assert corrupt_alert is not None
    assert corrupt_alert.supporting_context == {}


# ============================================================================
# 7. MLFLOW ADAPTER BRANCH TESTS
# ============================================================================

def test_mlflow_adapter_branches(tmp_path):
    """Test MLflowAdapter parent directory creation, experiment creation, and tags."""
    nested_db = str(tmp_path / "deep" / "nested" / "active_model.db")
    adapter = MLflowAdapter(tracking_uri=f"sqlite:///{tmp_path}/mlflow.db", db_path=nested_db)
    assert Path(nested_db).exists()

    # log_qml_experiment_run with extra tags and non-existent experiment
    with patch("mlflow.get_experiment_by_name", return_value=None), \
         patch("mlflow.create_experiment", return_value="exp-123"), \
         patch("mlflow.set_experiment"), \
         patch("mlflow.start_run") as mock_start_run:
        mock_run = MagicMock()
        mock_run.info.run_id = "test-run-999"
        mock_start_run.return_value.__enter__.return_value = mock_run

        run_id = adapter.log_qml_experiment_run(
            run_name="qml_test_cov",
            params={"n_qubits": 4},
            metrics={"acc": 0.85, "invalid": None},
            tags={"custom_tag": "cov"},
            artifact_paths=[str(tmp_path)],
        )
        assert run_id == "test-run-999"

    # promote_to_champion with benchmark_only tag raising ValueError
    with patch("mlflow.tracking.MlflowClient") as mock_client_cls:
        client = MagicMock()
        mock_client_cls.return_value = client
        run_info = MagicMock()
        run_info.data.tags = {"benchmark_only": "true"}
        client.get_run.return_value = run_info

        with pytest.raises(ValueError, match="Architectural Firewall"):
            adapter.promote_to_champion(run_id="run-qml", model_version="qml-v1")

    # rollback_to_fallback when no state exists
    with patch.object(adapter, "get_active_model_state", return_value=None):
        with pytest.raises(ValueError, match="No previous stable fallback model exists"):
            adapter.rollback_to_fallback()


# ============================================================================
# 8. RECOVERY VALIDATOR BRANCH TESTS
# ============================================================================

def test_recovery_validator_branches(valid_sample_df, tmp_path):
    """Test candidate validator edge cases: QML firewall, missing file, non-model artifact."""
    val = ModelValidator(val_data_path=str(tmp_path / "val.csv"))

    # EXP-03 QML Architectural Firewall check
    with pytest.raises(ValueError, match="Architectural Firewall: QML models are benchmark-only"):
        val.validate_candidate("qml_candidate_v1")

    # Artifact check when candidate file exists directly but lacks predict()
    cand_joblib = tmp_path / "cand_test_cov.joblib"
    try:
        import joblib
        joblib.dump({"pipeline": 12345}, cand_joblib)
        rep = val.validate_candidate("cand_test_cov", model_path=str(cand_joblib))
        assert rep.gates["artifact_check"].status == GateStatus.FAILED
        assert "predict()" in rep.gates["artifact_check"].details
    finally:
        if cand_joblib.exists():
            cand_joblib.unlink()

    # Artifact check when file is corrupted
    corrupt_joblib = tmp_path / "cand_corrupt_cov.joblib"
    try:
        with open(corrupt_joblib, "wb") as f:
            f.write(b"not a valid joblib file")
        rep = val.validate_candidate("cand_corrupt_cov", model_path=str(corrupt_joblib))
        assert rep.gates["artifact_check"].status == GateStatus.FAILED
    finally:
        if corrupt_joblib.exists():
            corrupt_joblib.unlink()

    # Regression gate evaluation with mock pipeline
    valid_sample_df.to_csv(tmp_path / "val.csv", index=False)
    mock_pipeline = MagicMock()
    mock_pipeline.predict.return_value = np.zeros(len(valid_sample_df))
    with patch.object(val, "min_accuracy", 0.99):
        rep = val.validate_candidate("baseline-test", model_pipeline=mock_pipeline)
        assert "regression_gate" in rep.gates


# ============================================================================
# 9. HEALTH GUARD & RECOVERY ORCHESTRATOR BRANCH TESTS
# ============================================================================

def test_health_guard_branches():
    """Test health guard canary status edge branches and filter logic."""
    guard = PostPromotionHealthGuard()

    # State is None
    with patch("app.registry.mlflow_adapter.mlflow_adapter.get_active_model_state", return_value=None):
        canary = guard.get_canary_status()
        assert not canary["is_canary_active"]
        assert canary["active_version"] == "unknown"

    # State is not PROMOTED
    with patch("app.registry.mlflow_adapter.mlflow_adapter.get_active_model_state", return_value={"state": "ROLLED_BACK"}):
        canary = guard.get_canary_status()
        assert not canary["is_canary_active"]

    # State with invalid promoted_at ISO string
    with patch("app.registry.mlflow_adapter.mlflow_adapter.get_active_model_state", return_value={"state": "PROMOTED", "promoted_at": "invalid_date"}):
        canary = guard.get_canary_status()
        assert not canary["is_canary_active"]

    # Recent events matching active version and latency calculation
    with patch("app.registry.mlflow_adapter.mlflow_adapter.get_active_model_state", return_value={"active_version": "v1", "state": "PROMOTED", "promoted_at": "2026-01-01T00:00:00Z"}), \
         patch("app.monitoring.collector.telemetry_collector.get_recent_events", return_value=[
             {"model_version": "v1", "latency_ms": 120.0},
             {"model_version": "v1", "latency_ms": 140.0},
         ]):
        eval_res = guard.evaluate_health()
        assert eval_res.latency_p95_ms > 0

    # Query latest evaluation with no model version (None branch)
    guard.clear()
    assert guard.get_latest_evaluation(model_version=None) is None

    # Insert record with corrupt details_json
    with guard._lock:
        with guard._get_connection() as conn:
            conn.execute(
                "INSERT INTO health_guard_evaluations (model_version, health_status, error_rate, latency_p95_ms, sample_count, details_json, evaluated_at) VALUES ('v1', 'HEALTHY', 0.0, 100.0, 10, 'corrupt-json', '2026-01-01');"
            )
    latest_corrupt = guard.get_latest_evaluation(model_version=None)
    assert latest_corrupt is not None
    assert "details" not in latest_corrupt


def test_recovery_orchestrator_branches(tmp_path):
    """Test orchestrator error conditions, corrupt history json, and fallback execution."""
    orch = RecoveryOrchestrator(db_path=str(tmp_path / "test_orch.db"))

    # No active model state found
    with patch("app.registry.mlflow_adapter.mlflow_adapter.get_active_model_state", return_value=None):
        with pytest.raises(ValueError, match="No active production model state found"):
            orch.execute_rollback(reason="Test failure")

    # Target version model file does not exist
    with patch("app.registry.mlflow_adapter.mlflow_adapter.get_active_model_state", return_value={
        "active_version": "v2",
        "previous_stable_version": "missing_v1",
    }):
        with pytest.raises(FileNotFoundError, match="Fallback model artifact"):
            orch.execute_rollback(reason="Test missing file")

    # Corrupt health evidence json in rollback history
    with sqlite3.connect(orch.db_path) as conn:
        conn.execute(
            "INSERT INTO rollback_history VALUES ('rb-1', 'v2', 'v1', 'reason', 'operator', '2026-01-01', 'bad-json', 'MANUAL', 'corr-1')"
        )
    hist = orch.list_rollbacks()
    assert len(hist) >= 1
    assert "health_evidence" not in hist[0] or hist[0].get("health_evidence") is None


# ============================================================================
# 10. RECOVERY POLICY & REGISTRY BRANCH TESTS
# ============================================================================

def test_recovery_policy_and_registry_branches(tmp_path):
    """Test RecoveryPolicyEngine and CandidateRegistry edge branches."""
    # Policy: init with corrupt evaluated_at
    db_policy = str(tmp_path / "test_policy.db")
    pol = RecoveryPolicyEngine(db_path=db_policy)

    # Evaluate with CRITICAL alerts
    status_str, eligible, reason, cooldown = pol.evaluate_trigger(
        drift_detected=False,
        active_alerts=[{"severity": "CRITICAL", "metric_name": "TEST"}],
    )
    assert eligible

    # Registry: get_candidate with corrupt validation_report JSON
    db_reg = str(tmp_path / "test_reg.db")
    reg = CandidateRegistry(db_path=db_reg)
    with sqlite3.connect(db_reg) as conn:
        conn.execute("INSERT INTO candidate_records VALUES ('c1', 'v1', 'test_model', 'run-1', 'VALIDATED', 'test', 'bad-json', '2026-01-01', NULL, NULL, NULL, NULL);")

    cand = reg.get_candidate("c1")
    assert cand is not None
    assert cand["validation_report"] == "bad-json"

    # List candidates with status filter vs without
    assert len(reg.list_candidates(status="VALIDATED")) == 1
    assert len(reg.list_candidates(status="PROMOTED")) == 0


# ============================================================================
# 11. API ROUTE BRANCH TESTS
# ============================================================================

def test_api_routes_alert_404s(client, viewer_headers, operator_headers):
    """Test alert 404 branches for GET, acknowledge, and resolve."""
    res_get = client.get("/api/v1/alerts/nonexistent_alert_id", headers=viewer_headers)
    assert res_get.status_code == 404

    res_ack = client.post("/api/v1/alerts/nonexistent_alert_id/acknowledge", headers=operator_headers)
    assert res_ack.status_code == 404

    res_res = client.post("/api/v1/alerts/nonexistent_alert_id/resolve", headers=operator_headers)
    assert res_res.status_code == 404


def test_api_routes_model_active_no_state(client, viewer_headers):
    """Test /api/v1/models/active and /api/v1/recovery/health when no state exists."""
    with patch("app.registry.mlflow_adapter.mlflow_adapter.get_active_model_state", return_value=None):
        res = client.get("/api/v1/models/active", headers=viewer_headers)
        assert res.status_code == 200
        assert res.json()["active_run_id"] == "local-baseline"

        res_rec = client.get("/api/v1/recovery/health", headers=viewer_headers)
        assert res_rec.status_code == 200
        assert res_rec.json()["health_status"] in ("HEALTHY", "DEGRADED", "UNHEALTHY", "INSUFFICIENT_DATA")


def test_api_routes_candidate_reject(client, admin_headers):
    """Test /api/v1/candidates/{candidate_id}/decision with decision REJECT."""
    import uuid
    unique_cand_id = f"cand_rej_{uuid.uuid4().hex[:8]}"
    candidate_registry.create_candidate(
        candidate_id=unique_cand_id,
        model_version="v_reject",
        model_name="ProductionClassifier",
        trigger_reason="Test reject branch",
    )
    res = client.post(
        f"/api/v1/candidates/{unique_cand_id}/decision",
        headers=admin_headers,
        json={
            "decision": "REJECT",
            "comment": "Rejected in branch test",
        },
    )
    assert res.status_code == 200
    assert res.json()["state"] == "REJECTED"


def test_api_routes_drift_and_anomaly_telemetry_fallbacks(client, operator_headers):
    """Test drift evaluate and anomaly evaluate falling back to telemetry buffer or failing on bad path."""
    # Drift evaluate with no payload (falls back to telemetry buffer)
    res_drift_trigger = client.post(
        "/api/v1/monitoring/drift/evaluate",
        headers=operator_headers,
        json=None,
    )
    assert res_drift_trigger.status_code == 200

    # Drift evaluate with invalid dataset_path -> 404
    res_bad_path = client.post(
        "/api/v1/monitoring/drift/evaluate",
        headers=operator_headers,
        json={"dataset_path": "nonexistent/file.csv"},
    )
    assert res_bad_path.status_code == 404

    # Anomaly evaluate with invalid dataset_path -> 404
    res_bad_anomaly = client.post(
        "/api/v1/monitoring/anomaly/evaluate",
        headers=operator_headers,
        json={"dataset_path": "nonexistent/file.csv"},
    )
    assert res_bad_anomaly.status_code == 404

    # Anomaly evaluate with no payload (falls back to telemetry buffer)
    res_anomaly_buf = client.post(
        "/api/v1/monitoring/anomaly/evaluate",
        headers=operator_headers,
        json=None,
    )
    assert res_anomaly_buf.status_code == 200


def test_api_routes_dl_and_qml_error_branches(client, viewer_headers, operator_headers):
    """Test DL sequence score 422, DL batch evaluate 422, and QML 503 branches."""
    # DL score ValueError -> 422
    with patch(
        "app.models.dl_monitor.dl_sequence_monitor.score_window",
        side_effect=ValueError("Invalid sequence input"),
    ):
        res_dl = client.post(
            "/api/v1/monitoring/sequence/evaluate",
            headers=viewer_headers,
            json={"sequence": [{"age": 25.0, "loan_amount": 5000.0} for _ in range(10)]},
        )
        assert res_dl.status_code == 422

    # DL evaluate ValueError -> 422
    with patch(
        "app.models.dl_monitor.dl_sequence_monitor.evaluate",
        side_effect=ValueError("Insufficient window data"),
    ):
        res_dl_batch = client.post(
            "/api/v1/monitoring/sequence/batch",
            headers=viewer_headers,
            json={"records": [{"age": 25.0, "loan_amount": 5000.0} for _ in range(10)], "window_size": 5, "step_size": 1},
        )
        assert res_dl_batch.status_code == 422

    # QML benchmark with missing reference datasets -> 503
    with patch("pathlib.Path.is_file", return_value=False):
        res_qml = client.post(
            "/api/v1/monitoring/qml/benchmark",
            headers=operator_headers,
            json={"epochs": 1, "learning_rate": 0.01},
        )
        assert res_qml.status_code == 503


def test_api_routes_predict_error_handling(client, viewer_headers, valid_sample_df):
    """Test /api/v1/models/predict when model.predict raises an unexpected Exception -> 400."""
    with patch.object(baseline_classifier, "predict", side_effect=ValueError("Simulated prediction matrix mismatch")):
        payload = valid_sample_df.drop(columns=[TARGET_COLUMN]).iloc[0].to_dict()
        res = client.post("/api/v1/models/predict", headers=viewer_headers, json=payload)
        assert res.status_code == 400
        assert "Inference failed" in res.json()["detail"]


def test_api_routes_health_evaluate_auto_rollback(client, operator_headers):
    """Test /api/v1/recovery/health/evaluate auto-rollback success and failure branches."""
    # 1. Auto-rollback succeeds
    mock_rollback_res = MagicMock()
    mock_rollback_res.model_dump.return_value = {"status": "SUCCESS"}
    with patch.object(recovery_orchestrator, "execute_rollback", return_value=mock_rollback_res) as mock_rb:
        res = client.post(
            "/api/v1/recovery/health/evaluate",
            headers=operator_headers,
            json={
                "auto_rollback_on_failure": True,
                "simulate_error_rate": 0.50,
            },
        )
        assert res.status_code == 200
        assert res.json()["rollback_triggered"] is True
        assert mock_rb.called

    # 2. Auto-rollback encounters exception (caught and logged)
    with patch.object(recovery_orchestrator, "execute_rollback", side_effect=RuntimeError("Storage failure during rollback")):
        res_err = client.post(
            "/api/v1/recovery/health/evaluate",
            headers=operator_headers,
            json={
                "auto_rollback_on_failure": True,
                "simulate_error_rate": 0.50,
            },
        )
        assert res_err.status_code == 200
        assert res_err.json()["rollback_triggered"] is False


def test_api_routes_rollback_error_branches(client, operator_headers):
    """Test /api/v1/models/rollback FileNotFoundError (404), ValueError (422/400), and Exception (500)."""
    # FileNotFoundError -> 404
    with patch.object(recovery_orchestrator, "execute_rollback", side_effect=FileNotFoundError("Model file missing")):
        res = client.post(
            "/api/v1/models/rollback",
            headers=operator_headers,
            json={"target_version": "v1", "reason": "Test missing file"},
        )
        assert res.status_code == 404

    # ValueError with 'firewall' -> 422
    with patch.object(recovery_orchestrator, "execute_rollback", side_effect=ValueError("Architectural Firewall breach")):
        res = client.post(
            "/api/v1/models/rollback",
            headers=operator_headers,
            json={"target_version": "qml_model", "reason": "Test firewall"},
        )
        assert res.status_code == 422

    # ValueError general -> 400
    with patch.object(recovery_orchestrator, "execute_rollback", side_effect=ValueError("Invalid version string")):
        res = client.post(
            "/api/v1/models/rollback",
            headers=operator_headers,
            json={"target_version": "invalid_v", "reason": "Test bad version"},
        )
        assert res.status_code == 400

    # Unexpected Exception -> 500
    with patch.object(recovery_orchestrator, "execute_rollback", side_effect=RuntimeError("Database corruption")):
        res = client.post(
            "/api/v1/models/rollback",
            headers=operator_headers,
            json={"target_version": "v1", "reason": "Test 500 crash"},
        )
        assert res.status_code == 500
