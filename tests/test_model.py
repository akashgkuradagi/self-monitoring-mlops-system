"""Baseline Model, MLflow Tracking, and Inference Test Suite (ML-01).

Maps to Requirements: FR-02, FR-04, NFR-04
Maps to Test Cases: TC-05, TC-06, TC-08
"""

import numpy as np
import pytest
from starlette.testclient import TestClient

from app.data.generator import generate_credit_risk_dataset, split_dataset
from app.data.schema import TARGET_COLUMN
from app.models.baseline import BaselineClassifier, baseline_classifier
from app.monitoring.collector import telemetry_collector
from app.registry.mlflow_adapter import MLflowAdapter
from app.security.auth import Role, create_access_token


@pytest.fixture
def clean_datasets():
    """Generate fresh, deterministic train and validation datasets for model testing."""
    df = generate_credit_risk_dataset(n_samples=600, seed=42)
    train_df, val_df, _ = split_dataset(df, train_ratio=0.70, val_ratio=0.30, test_ratio=0.0, seed=42)
    return train_df, val_df


# ==============================================================================
# Model Training, Validation & Preprocessing Tests (NFR-04, ML-01)
# ==============================================================================


def test_dataset_validation_before_training(clean_datasets):
    """Dataset is validated before fitting; corrupted data raises ValueError."""
    train_df, _ = clean_datasets
    clf = BaselineClassifier(random_state=42)

    # 1. Invalid dataset with missing required column
    bad_train_df = train_df.drop(columns=["income"])
    with pytest.raises(ValueError) as exc:
        clf.train(bad_train_df)
    assert "Missing required column" in str(exc.value)

    # 2. Valid dataset trains cleanly
    train_metrics = clf.train(train_df)
    assert clf.is_fitted is True
    assert train_metrics["train_accuracy"] > 0.60
    assert train_metrics["train_roc_auc"] > 0.65


def test_reproducible_model_training(clean_datasets):
    """NFR-04: Fixed random seed produces bitwise identical evaluation scores and predictions."""
    train_df, val_df = clean_datasets

    clf1 = BaselineClassifier(n_estimators=50, random_state=42)
    clf1.train(train_df)
    preds1, probs1 = clf1.predict(val_df)
    metrics1 = clf1.evaluate(val_df, has_labels=True)

    clf2 = BaselineClassifier(n_estimators=50, random_state=42)
    clf2.train(train_df)
    preds2, probs2 = clf2.predict(val_df)
    metrics2 = clf2.evaluate(val_df, has_labels=True)

    np.testing.assert_array_equal(preds1, preds2)
    np.testing.assert_allclose(probs1, probs2, rtol=1e-5)
    assert metrics1["accuracy"] == metrics2["accuracy"]
    assert metrics1["f1"] == metrics2["f1"]
    assert metrics1["roc_auc"] == metrics2["roc_auc"]


def test_preprocessing_handles_unseen_categories(clean_datasets):
    """OneHotEncoder ignores unseen categories in inference without crashing."""
    train_df, val_df = clean_datasets
    clf = BaselineClassifier(n_estimators=30, random_state=42)
    clf.train(train_df)

    test_sample = val_df.head(2).copy()
    test_sample["loan_intent"] = "UNKNOWN_INTENT_99"
    test_sample["home_ownership"] = "UNSEEN_HOME_TYPE"

    preds, probs = clf.predict(test_sample)
    assert len(preds) == 2
    assert len(probs) == 2


# ==============================================================================
# Delayed Ground-Truth Evaluation Logic (FR-04)
# ==============================================================================


def test_evaluation_metrics_with_and_without_ground_truth(clean_datasets):
    """FR-04: Evaluation calculates metrics when labels present, returns None when absent."""
    train_df, val_df = clean_datasets
    clf = BaselineClassifier(n_estimators=30, random_state=42)
    clf.train(train_df)

    # 1. Labels present: returns complete metrics
    eval_with_labels = clf.evaluate(val_df, has_labels=True)
    assert eval_with_labels["status"] == "evaluated"
    assert isinstance(eval_with_labels["accuracy"], float)
    assert isinstance(eval_with_labels["precision"], float)
    assert isinstance(eval_with_labels["recall"], float)
    assert isinstance(eval_with_labels["f1"], float)
    assert isinstance(eval_with_labels["roc_auc"], float)
    assert isinstance(eval_with_labels["confusion_matrix"], list)

    # 2. Labels absent: explicitly returns None (FR-04 delayed ground truth)
    val_unlabeled = val_df.drop(columns=[TARGET_COLUMN])
    eval_without_labels = clf.evaluate(val_unlabeled, has_labels=False)
    assert eval_without_labels["status"] == "labels_unavailable"
    assert eval_without_labels["accuracy"] is None
    assert eval_without_labels["precision"] is None
    assert eval_without_labels["f1"] is None
    assert eval_without_labels["roc_auc"] is None
    assert "delayed or absent" in eval_without_labels["note"].lower()


# ==============================================================================
# MLflow Tracking & Active Model Registry State Tests (ML-01, TC-05, B.6)
# ==============================================================================


def test_mlflow_tracking_and_champion_promotion(tmp_path, clean_datasets):
    """TC-05: Experiment runs log metrics and artifacts; aliases manage champion state."""
    train_df, val_df = clean_datasets
    clf = BaselineClassifier(n_estimators=25, random_state=42)
    clf.train(train_df)
    val_metrics = clf.evaluate(val_df, has_labels=True)

    db_path = str(tmp_path / "test_mlflow_state.db")
    adapter = MLflowAdapter(
        tracking_uri=f"sqlite:///{tmp_path}/test_mlflow.db",
        experiment_name="test-experiment",
        db_path=db_path,
    )

    params = {"n_estimators": 25, "random_state": 42}
    run_id = adapter.log_training_run(
        model_pipeline=clf.pipeline,
        train_df=train_df,
        eval_metrics=val_metrics,
        params=params,
        model_version="baseline-v1",
        model_name="TestClassifier",
    )
    assert run_id is not None
    assert len(run_id) == 32

    # Promote to champion
    promo = adapter.promote_to_champion(run_id=run_id, model_version="baseline-v1", model_name="TestClassifier")
    assert promo["status"] == "PROMOTED"
    assert promo["active_version"] == "baseline-v1"
    assert promo["alias"] == "champion"

    # Verify SQLite active state singleton
    state = adapter.get_active_model_state()
    assert state["active_version"] == "baseline-v1"
    assert state["active_run_id"] == run_id
    assert state["state"] == "PROMOTED"

    # Promote candidate v2 -> previous becomes baseline-v1
    promo_v2 = adapter.promote_to_champion(run_id="run-v2-abc", model_version="candidate-v2", model_name="TestClassifier")
    assert promo_v2["active_version"] == "candidate-v2"
    assert promo_v2["previous_stable_version"] == "baseline-v1"

    # Roll back to previous stable
    rollback_res = adapter.rollback_to_fallback()
    assert rollback_res["status"] == "ROLLED_BACK"
    assert rollback_res["active_version"] == "baseline-v1"


# ==============================================================================
# Model Inference Endpoint & Telemetry Integration (FR-02, ML-01)
# ==============================================================================


def test_model_inference_api_success_and_telemetry(client: TestClient, clean_datasets):
    """FR-02, ML-01: POST /api/v1/models/predict returns inference and logs telemetry event."""
    train_df, _ = clean_datasets
    # Ensure baseline singleton is trained
    baseline_classifier.train(train_df)

    viewer_token = create_access_token(subject="viewer_bob", role=Role.VIEWER)
    headers = {"Authorization": f"Bearer {viewer_token}", "X-Correlation-ID": "test-req-001"}

    payload = {
        "age": 35.0,
        "income": 65000.0,
        "loan_amount": 12000.0,
        "credit_score": 720.0,
        "debt_to_income": 0.22,
        "employment_length": 5.0,
        "loan_intent": "PERSONAL",
        "home_ownership": "RENT",
        "previous_default": "N",
        "request_id": "test-req-001",
    }

    resp = client.post("/api/v1/models/predict", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    assert data["request_id"] == "test-req-001"
    assert data["prediction"] in (0, 1)
    assert 0.0 <= data["default_probability"] <= 1.0
    assert data["risk_label"] in ("LOW_RISK", "HIGH_RISK")
    assert data["model_version"] == baseline_classifier.model_version
    assert data["latency_ms"] >= 0.0

    # Verify telemetry collector recorded the event (FR-02)
    summary = telemetry_collector.get_metrics_summary()
    assert summary.count >= 1


def test_model_inference_api_authentication(client: TestClient):
    """Unauthenticated calls to /api/v1/models/predict are rejected with HTTP 401."""
    payload = {
        "age": 35.0,
        "income": 65000.0,
        "loan_amount": 12000.0,
        "credit_score": 720.0,
        "debt_to_income": 0.22,
        "employment_length": 5.0,
        "loan_intent": "PERSONAL",
        "home_ownership": "RENT",
        "previous_default": "N",
    }
    resp = client.post("/api/v1/models/predict", json=payload)
    assert resp.status_code == 401


def test_model_inference_api_input_validation(client: TestClient):
    """Malformed or out-of-range input features return HTTP 422 safely."""
    viewer_token = create_access_token(subject="viewer_bob", role=Role.VIEWER)
    headers = {"Authorization": f"Bearer {viewer_token}"}

    # Out of range credit score (999 > 850)
    bad_payload = {
        "age": 35.0,
        "income": 65000.0,
        "loan_amount": 12000.0,
        "credit_score": 999.0,  # Invalid
        "debt_to_income": 0.22,
        "employment_length": 5.0,
        "loan_intent": "PERSONAL",
        "home_ownership": "RENT",
        "previous_default": "N",
    }
    resp = client.post("/api/v1/models/predict", json=bad_payload, headers=headers)
    assert resp.status_code == 422


def test_active_model_metadata_endpoint(client: TestClient):
    """GET /api/v1/models/active returns champion metadata."""
    viewer_token = create_access_token(subject="viewer_bob", role=Role.VIEWER)
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = client.get("/api/v1/models/active", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "active_version" in data
    assert "state" in data
    assert "model_name" in data
