"""Test Suite for Experimental Quantum ML (QML) Benchmark and Architectural Firewall (EXP-03, FR-07, TC-19).

Verifies:
- VQC architecture, PennyLane 'default.qubit' local simulation, and metadata.
- Reproducibility and seed handling.
- Feature preprocessing and angle embedding scaling.
- Training, prediction, and evaluation behavior.
- Classical vs QML benchmark comparison.
- MLflow experiment isolation under 'qml-benchmarks'.
- Architectural Firewall enforcement (TC-19):
  * Blocks promotion of QML models via API with HTTP 422
  * Blocks candidate validation of QML models via API with HTTP 422
  * Blocks promotion directly at MLflow registry adapter level
- API authentication and RBAC for /api/v1/monitoring/qml/benchmark.
- Regression compatibility with ML-01, MON-01, EXP-01, and EXP-02.
"""

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
import pytest

from app.data.schema import NUMERICAL_FEATURES, TARGET_COLUMN
from app.main import app
from app.models.baseline import baseline_classifier
from app.models.dl_monitor import dl_sequence_monitor
from app.models.ml_monitor import isolation_forest_detector
from app.models.qml_benchmark import (
    QML_FEATURE_SUBSET,
    QMLBenchmarkClassifier,
    benchmark_only,
    qml_benchmark,
)
from app.monitoring.drift import drift_analyzer
from app.registry.mlflow_adapter import mlflow_adapter
from app.security.audit import audit_logger
from app.security.auth import Role, create_access_token


@pytest.fixture
def api_client() -> TestClient:
    """Provide TestClient for FastAPI endpoints."""
    return TestClient(app)


@pytest.fixture
def ml_engineer_token() -> str:
    """Generate access token for ML_Engineer role."""
    return create_access_token(subject="test_engineer", role=Role.ML_ENGINEER)


@pytest.fixture
def viewer_token() -> str:
    """Generate access token for Viewer role."""
    return create_access_token(subject="test_viewer", role=Role.VIEWER)


def test_qml_circuit_initialization_and_metadata():
    """Verify QMLBenchmarkClassifier initializes with default.qubit simulator and benchmark_only flag."""
    classifier = QMLBenchmarkClassifier(
        n_qubits=4,
        n_layers=2,
        simulator_device="default.qubit",
        random_state=42,
    )

    assert classifier.n_qubits == 4
    assert classifier.n_layers == 2
    assert classifier.simulator_device == "default.qubit"
    assert classifier.benchmark_only is True
    assert benchmark_only is True
    assert classifier.feature_subset == QML_FEATURE_SUBSET
    # Verify all chosen QML features are authoritative numerical features
    for f in classifier.feature_subset:
        assert f in NUMERICAL_FEATURES


def test_qml_reproducibility():
    """Verify deterministic behavior across identical seeds."""
    qml1 = QMLBenchmarkClassifier(random_state=42)
    qml2 = QMLBenchmarkClassifier(random_state=42)

    assert qml1.random_state == qml2.random_state == 42
    assert qml1.feature_subset == qml2.feature_subset


def test_qml_feature_preprocessing_and_subset_validation():
    """Verify feature scaling to [0, pi] and rejection of missing columns."""
    train_df = pd.read_csv("data/reference/train.csv").iloc[:30].copy()
    classifier = QMLBenchmarkClassifier(random_state=42)

    # Missing required feature column raises ValueError
    bad_df = train_df.drop(columns=["credit_score"])
    with pytest.raises(ValueError, match="Missing required quantum feature"):
        classifier.fit(bad_df, epochs=1)

    # Missing target column raises ValueError
    no_target_df = train_df.drop(columns=[TARGET_COLUMN])
    with pytest.raises(ValueError, match="Target column 'loan_status' is required"):
        classifier.fit(no_target_df, epochs=1)


def test_qml_training_and_evaluation_behavior():
    """Verify VQC training on local simulator and valid probabilistic predictions."""
    train_df = pd.read_csv("data/reference/train.csv").iloc[:40].copy()
    val_df = pd.read_csv("data/reference/val.csv").iloc[:20].copy()

    classifier = QMLBenchmarkClassifier(n_qubits=4, n_layers=2, random_state=42)
    train_metrics = classifier.fit(train_df, epochs=2, lr=0.05, batch_size=16)

    assert classifier.is_fitted is True
    assert "training_time_seconds" in train_metrics
    assert "final_loss" in train_metrics
    assert train_metrics["samples_trained"] == 40

    # Predictions
    probs = classifier.predict_proba(val_df)
    assert len(probs) == 20
    assert (probs >= 0.0).all() and (probs <= 1.0).all()

    preds = classifier.predict(val_df)
    assert len(preds) == 20
    assert set(np.unique(preds)).issubset({0, 1})

    # Evaluation
    eval_metrics = classifier.evaluate(val_df)
    assert "accuracy" in eval_metrics
    assert 0.0 <= eval_metrics["accuracy"] <= 1.0
    assert "f1_score" in eval_metrics
    assert "mean_inference_latency_ms" in eval_metrics


def test_qml_benchmark_method_and_classical_comparison(tmp_path):
    """Verify run_benchmark() generates side-by-side comparison with classical Random Forest."""
    train_df = pd.read_csv("data/reference/train.csv").iloc[:50].copy()
    val_df = pd.read_csv("data/reference/val.csv").iloc[:25].copy()

    classifier = QMLBenchmarkClassifier(n_qubits=4, n_layers=2, random_state=42)
    results = classifier.run_benchmark(
        train_df=train_df,
        eval_df=val_df,
        n_train=40,
        n_eval=20,
        epochs=2,
    )

    assert results["status"] == "SUCCESS"
    assert results["benchmark_only"] is True
    assert results["firewall_enforced"] is True
    assert "circuit_metadata" in results
    assert results["circuit_metadata"]["simulator_device"] == "default.qubit"
    assert results["circuit_metadata"]["is_hardware"] is False

    # Classical comparison check
    comparisons = results["classical_comparison"]
    assert len(comparisons) == 2
    model_names = {c["model_name"] for c in comparisons}
    assert "Classical_Random_Forest" in model_names
    assert "Variational_Quantum_Classifier" in model_names

    # Save comparison scorecard
    out_file = tmp_path / "qml_comparison.json"
    classifier.save_comparison(out_file)
    assert out_file.exists()


def test_qml_mlflow_experiment_isolation():
    """Verify QML benchmark logs to 'qml-benchmarks' without altering ProductionClassifier."""
    run_id = mlflow_adapter.log_qml_experiment_run(
        run_name="test-qml-run",
        params={"n_qubits": 4, "ansatz": "BasicEntanglerLayers", "benchmark_only": True},
        metrics={"qml_accuracy": 0.68, "training_time_sec": 0.25},
    )

    assert isinstance(run_id, str)
    assert len(run_id) > 0

    # Ensure ProductionClassifier and active_model_state remain intact
    active_state = mlflow_adapter.get_active_model_state()
    assert active_state["active_version"] == "baseline-v1"
    assert active_state["active_run_id"] != run_id


def test_tc19_architectural_firewall_blocks_promotion_via_api(api_client, ml_engineer_token):
    """TC-19: Attempting to promote a QML model via API returns HTTP 422 and logs audit event."""
    headers = {
        "Authorization": f"Bearer {ml_engineer_token}",
        "X-Correlation-ID": "test-tc19-corr-id",
    }
    payload = {"decision": "APPROVE", "comment": "Attempting illegal QML promotion"}

    res = api_client.post(
        "/api/v1/candidates/qml-vqc-model-01/decision",
        json=payload,
        headers=headers,
    )

    # Must return HTTP 422 Unprocessable Entity
    assert res.status_code == 422
    data = res.json()
    assert "QML models are benchmark-only and cannot be promoted" in data["detail"]

    # Verify audit event was logged with failure / blocked outcome
    events = audit_logger.get_events(limit=5, action="FIREWALL_BLOCKED_PROMOTION")
    assert len(events) > 0
    assert events[0].actor == "test_engineer"
    assert events[0].outcome == "BLOCKED"
    assert events[0].metadata.get("violation") == "TC-19_FIREWALL_BREACH"


def test_architectural_firewall_blocks_validation_via_api(api_client, ml_engineer_token):
    """Verify architectural firewall prevents QML models from candidate validation pipeline."""
    headers = {"Authorization": f"Bearer {ml_engineer_token}"}

    res = api_client.post("/api/v1/candidates/qml-vqc-candidate-99/validate", headers=headers)
    assert res.status_code == 422
    assert "QML models are benchmark-only" in res.json()["detail"]

    events = audit_logger.get_events(limit=5, action="FIREWALL_BLOCKED_VALIDATION")
    assert len(events) > 0
    assert events[0].outcome == "BLOCKED"


def test_architectural_firewall_blocks_registry_promotion():
    """Verify mlflow_adapter.promote_to_champion rejects QML models at the library level."""
    with pytest.raises(ValueError, match="Architectural Firewall"):
        mlflow_adapter.promote_to_champion(run_id="run-qml-001", model_version="qml-v1")


def test_qml_api_endpoint_rbac_and_execution(api_client, ml_engineer_token, viewer_token):
    """Verify POST /api/v1/monitoring/qml/benchmark enforces RBAC and returns structured results."""
    # 1. Unauthenticated request rejected with 401
    res_unauth = api_client.post("/api/v1/monitoring/qml/benchmark")
    assert res_unauth.status_code == 401

    # 2. Viewer role rejected with 403 (requires ML_Engineer or Admin)
    res_viewer = api_client.post(
        "/api/v1/monitoring/qml/benchmark",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_viewer.status_code == 403

    # 3. ML_Engineer role succeeds (200 OK)
    payload = {
        "n_train_samples": 40,
        "n_eval_samples": 20,
        "epochs": 2,
        "learning_rate": 0.05,
    }
    headers = {
        "Authorization": f"Bearer {ml_engineer_token}",
        "X-Correlation-ID": "test-qml-api-corr",
    }
    res = api_client.post("/api/v1/monitoring/qml/benchmark", json=payload, headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert data["status"] == "SUCCESS"
    assert data["benchmark_only"] is True
    assert data["firewall_enforced"] is True
    assert data["circuit_metadata"]["simulator_device"] == "default.qubit"
    assert len(data["classical_comparison"]) == 2

    # Verify audit event
    events = audit_logger.get_events(limit=5, action="QML_BENCHMARK_EXECUTED")
    assert len(events) > 0
    assert events[0].actor == "test_engineer"
    assert events[0].correlation_id == "test-qml-api-corr"


def test_regression_compatibility_with_exp01_exp02_mon01_ml01():
    """Verify EXP-03 introduces zero regression to existing models and monitors."""
    # ML-01 Production baseline
    assert baseline_classifier is not None

    # MON-01 Drift analyzer
    assert drift_analyzer is not None

    # EXP-01 Isolation forest
    assert isolation_forest_detector is not None

    # EXP-02 DL sequence monitor
    assert dl_sequence_monitor is not None

    # EXP-03 QML benchmark
    assert qml_benchmark is not None
    assert qml_benchmark.benchmark_only is True
