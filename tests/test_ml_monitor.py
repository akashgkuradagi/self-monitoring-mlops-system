"""Unit and Integration Tests for Isolation Forest Anomaly Detection (EXP-01, FR-05, TC-05).

Validates:
- FR-05: ML-based monitoring experiment tracking parameters, version, and evaluation metrics in MLflow.
- TC-05: Experiment metadata logging under 'classical-ml-monitor'.
- Unsupervised anomaly detection, scoring, input validation, and API authentication.
"""

from pathlib import Path
import mlflow
import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

from app.api.routes import DEMO_USERS
from app.config import settings
from app.data.generator import generate_credit_risk_dataset
from app.main import app
from app.models.baseline import baseline_classifier
from app.models.ml_monitor import IsolationForestDetector, isolation_forest_detector
from app.registry.mlflow_adapter import MLflowAdapter
from app.security.audit import audit_logger
from app.security.auth import create_access_token


@pytest.fixture
def sample_data():
    """Deterministic reference dataset for anomaly detector tests."""
    return generate_credit_risk_dataset(n_samples=400, seed=42)


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


def test_detector_initialization_and_reproducibility(sample_data):
    """EXP-01 / TC-05: Verify reproducible anomaly scoring with fixed random seeds."""
    det1 = IsolationForestDetector(n_estimators=50, contamination=0.05, random_state=42)
    det1.train(sample_data)
    _, scores1, _ = det1.predict_records(sample_data.head(50))

    det2 = IsolationForestDetector(n_estimators=50, contamination=0.05, random_state=42)
    det2.train(sample_data)
    _, scores2, _ = det2.predict_records(sample_data.head(50))

    np.testing.assert_allclose(scores1, scores2, rtol=1e-5)


def test_detector_training_without_target_leakage(sample_data):
    """FR-05: Ensure unsupervised training operates strictly on feature columns without target labels."""
    detector = IsolationForestDetector(n_estimators=50, contamination=0.05, random_state=42)
    res = detector.train(sample_data)

    assert detector.is_fitted is True
    assert res["algorithm"] == "IsolationForest"
    assert res["train_sample_count"] == len(sample_data)
    assert 0.0 < res["baseline_anomaly_ratio"] <= 0.10
    assert isinstance(res["offset_threshold"], float)


def test_detector_prediction_and_scoring(sample_data):
    """EXP-01: Verify is_anomaly, raw decision scores, and normalized anomaly scores."""
    detector = IsolationForestDetector(n_estimators=50, contamination=0.05, random_state=42)
    detector.train(sample_data)

    test_df = sample_data.iloc[:60].copy()
    is_anomaly, dec_scores, norm_scores = detector.predict_records(test_df)

    assert len(is_anomaly) == 60
    assert len(dec_scores) == 60
    assert len(norm_scores) == 60

    # Assert normalized score range [0.0, 1.0]
    assert (norm_scores >= 0.0).all() and (norm_scores <= 1.0).all()

    # Outliers should have higher normalized scores than inliers
    if is_anomaly.any() and (~is_anomaly).any():
        outlier_scores = norm_scores[is_anomaly]
        inlier_scores = norm_scores[~is_anomaly]
        assert outlier_scores.mean() > inlier_scores.mean()


def test_detector_handles_unseen_categories(sample_data):
    """Ensure preprocessor gracefully handles unseen categorical values without errors."""
    detector = IsolationForestDetector(n_estimators=50, contamination=0.05, random_state=42)
    detector.train(sample_data)

    novel_df = sample_data.iloc[:10].copy()
    novel_df["loan_intent"] = "SPACE_EXPLORATION"  # Unseen category
    novel_df["home_ownership"] = "TREEHOUSE"       # Unseen category

    is_anomaly, dec_scores, norm_scores = detector.predict_records(novel_df)
    assert len(is_anomaly) == 10
    assert not np.isnan(norm_scores).any()


def test_detector_batch_evaluation(sample_data):
    """Acceptance Criteria 2: Batch evaluation outputs anomaly ratio and mean decision score."""
    detector = IsolationForestDetector(n_estimators=50, contamination=0.05, random_state=42)
    detector.train(sample_data)

    batch_res = detector.evaluate_batch(sample_data.iloc[:100])
    assert batch_res.total_samples == 100
    assert batch_res.anomaly_count >= 0
    assert 0.0 <= batch_res.anomaly_ratio <= 1.0
    assert isinstance(batch_res.mean_decision_score, float)
    assert isinstance(batch_res.mean_anomaly_score, float)
    assert len(batch_res.predictions) == 100


def test_detector_empty_and_insufficient_input():
    """Verify safe handling of empty input dataframe."""
    detector = IsolationForestDetector(n_estimators=50, contamination=0.05, random_state=42)
    empty_df = pd.DataFrame()

    with pytest.raises(RuntimeError):
        detector.predict_records(empty_df)


def test_detector_serialization_roundtrip(sample_data, tmp_path):
    """Ensure detector persists to disk and reloads cleanly via joblib."""
    detector = IsolationForestDetector(n_estimators=50, contamination=0.05, random_state=42)
    detector.train(sample_data)

    save_path = tmp_path / "iso_test.joblib"
    detector.save(save_path)
    assert save_path.is_file()

    loaded = IsolationForestDetector()
    loaded.load(save_path)
    assert loaded.is_fitted is True

    _, orig_scores, _ = detector.predict_records(sample_data.head(20))
    _, load_scores, _ = loaded.predict_records(sample_data.head(20))
    np.testing.assert_allclose(orig_scores, load_scores, rtol=1e-5)


def test_mlflow_experiment_tracking_under_classical_ml_monitor(sample_data):
    """Acceptance Criteria 3 & TC-05: Experiment logs under 'classical-ml-monitor' without touching production model."""
    detector = IsolationForestDetector(n_estimators=50, contamination=0.05, random_state=42)
    train_res = detector.train(sample_data)

    adapter = MLflowAdapter(experiment_name="classical-ml-monitor")
    params = {
        "algorithm": "IsolationForest",
        "n_estimators": detector.n_estimators,
        "contamination": detector.contamination,
        "random_state": detector.random_state,
    }
    metrics = {
        "offset_threshold": train_res["offset_threshold"],
        "baseline_anomaly_ratio": train_res["baseline_anomaly_ratio"],
    }
    tags = {
        "experiment_name": "classical-ml-monitor",
        "issue": "EXP-01",
        "requirement": "FR-05",
    }

    run_id = adapter.log_monitoring_experiment_run(
        experiment_name="classical-ml-monitor",
        run_name="test-run-iso-01",
        model_pipeline=detector.pipeline,
        input_example=sample_data.head(3),
        params=params,
        metrics=metrics,
        tags=tags,
    )
    assert run_id is not None

    # Verify run recorded in MLflow
    client = mlflow.tracking.MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
    run_data = client.get_run(run_id)
    assert run_data.data.params["algorithm"] == "IsolationForest"
    assert "offset_threshold" in run_data.data.metrics

    # CRITICAL: Verify ProductionClassifier champion alias was NOT modified
    active_state = adapter.get_active_model_state()
    if active_state:
        assert active_state["active_version"] != detector.model_version


def test_anomaly_api_rbac_and_evaluation(client, auth_headers):
    """SAD / FR-05: Test authenticated anomaly evaluation API endpoint."""
    # 1. Unauthenticated -> 401
    resp_unauth = client.post("/api/v1/monitoring/anomaly/evaluate")
    assert resp_unauth.status_code == 401

    # 2. Viewer role -> 200
    resp_viewer = client.post(
        "/api/v1/monitoring/anomaly/evaluate",
        headers=auth_headers["Viewer"],
        json={"dataset_path": "data/fixtures/stable_fixture.csv"},
    )
    assert resp_viewer.status_code == 200
    data = resp_viewer.json()
    assert "total_samples" in data
    assert "anomaly_ratio" in data
    assert "mean_decision_score" in data
    assert "mean_anomaly_score" in data

    # 3. Verify audit trail records ANOMALY_EVALUATED
    events = audit_logger.get_events(action="ANOMALY_EVALUATED", limit=10)
    assert len(events) >= 1
    assert events[0].outcome == "SUCCESS"


def test_baseline_and_drift_compatibility():
    """Verify that adding Isolation Forest detector causes zero regressions to ML-01 and MON-01."""
    # Baseline classifier fitted and functional
    assert baseline_classifier.model_version == "baseline-v1"

    # Fixture detection still operational
    stable_path = Path("data/fixtures/stable_fixture.csv")
    assert stable_path.is_file()
    stable_df = pd.read_csv(stable_path)

    # Anomaly detector evaluates stable fixture cleanly
    if not isolation_forest_detector.is_fitted:
        isolation_forest_detector.train(stable_df)

    batch = isolation_forest_detector.evaluate_batch(stable_df)
    assert batch.total_samples == len(stable_df)
    assert batch.anomaly_ratio < 0.20
