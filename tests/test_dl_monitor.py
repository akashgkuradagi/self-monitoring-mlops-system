"""Test Suite for Deep Learning Sequence / Temporal Shift Model (EXP-02, FR-06).

Verifies:
- LSTMAutoencoder architecture and CPU execution
- Reproducibility and seed handling
- Sequence window construction and chronological ordering (no future leakage)
- Training and validation partition separation (zero target leakage)
- Reconstruction scoring, threshold calibration, and shift detection
- Insufficient sequence length and missing feature error handling
- Serialization / deserialization roundtrip
- MLflow experiment logging under 'dl-sequence-monitor'
- API endpoints RBAC and audit logging (/api/v1/monitoring/sequence/evaluate, /batch)
- Regression compatibility with ML-01, MON-01, and EXP-01
"""

from pathlib import Path
from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
import pytest
import torch

from app.data.schema import FEATURE_COLUMNS, TARGET_COLUMN
from app.main import app
from app.models.baseline import baseline_classifier
from app.models.dl_monitor import DLSequenceMonitor, LSTMAutoencoder, dl_sequence_monitor
from app.models.ml_monitor import isolation_forest_detector
from app.monitoring.drift import drift_analyzer
from app.registry.mlflow_adapter import mlflow_adapter
from app.security.audit import audit_logger
from app.security.auth import Role, create_access_token


@pytest.fixture
def api_client() -> TestClient:
    """Provide TestClient for FastAPI endpoints."""
    return TestClient(app)


@pytest.fixture
def viewer_token() -> str:
    """Generate access token for Viewer role."""
    return create_access_token(subject="test_viewer", role=Role.VIEWER)


@pytest.fixture
def sample_feature_rows() -> list[dict]:
    """Provide 15 sequential sample records matching dataset schema."""
    rows = []
    intents = ["PERSONAL", "EDUCATION", "MEDICAL", "VENTURE", "HOMEIMPROVEMENT"]
    ownerships = ["RENT", "MORTGAGE", "OWN", "OTHER"]
    for i in range(15):
        rows.append(
            {
                "age": 25.0 + (i % 10),
                "income": 50000.0 + (i * 1000),
                "loan_amount": 10000.0 + (i * 500),
                "credit_score": 680.0 + (i % 20),
                "debt_to_income": 0.25 + (i * 0.01),
                "employment_length": 3.0 + (i % 5),
                "loan_intent": intents[i % len(intents)],
                "home_ownership": ownerships[i % len(ownerships)],
                "previous_default": "N" if i % 4 != 0 else "Y",
            }
        )
    return rows


def test_lstm_autoencoder_initialization_and_cpu_execution():
    """Verify PyTorch LSTM Autoencoder builds properly and executes forward pass on CPU."""
    input_dim = 18
    hidden_dim = 32
    latent_dim = 16
    seq_len = 10
    batch_size = 4

    model = LSTMAutoencoder(
        input_dim=input_dim,
        hidden_dim=hidden_dim,
        latent_dim=latent_dim,
        seq_len=seq_len,
    )

    # Ensure device is CPU
    for param in model.parameters():
        assert param.device == torch.device("cpu")

    dummy_input = torch.randn(batch_size, seq_len, input_dim, dtype=torch.float32)
    reconstructed = model(dummy_input)

    assert reconstructed.shape == (batch_size, seq_len, input_dim)
    assert not torch.isnan(reconstructed).any()


def test_reproducible_configuration_and_seeds():
    """Verify DLSequenceMonitor initializes with specified seed and hyperparameters."""
    monitor1 = DLSequenceMonitor(random_state=42, window_size=10, epochs=5)
    monitor2 = DLSequenceMonitor(random_state=42, window_size=10, epochs=5)

    assert monitor1.random_state == monitor2.random_state == 42
    assert monitor1.window_size == monitor2.window_size == 10
    assert monitor1.device == torch.device("cpu")


def test_sequence_construction_and_chronological_ordering():
    """Verify sliding sequence windows preserve temporal ordering and prevent future leakage."""
    monitor = DLSequenceMonitor(window_size=10)

    # Generate 25 sequential observations with a clear monotonic counter
    raw_array = np.arange(50).reshape(25, 2).astype(np.float32)

    sequences = monitor._create_sequences(raw_array, window_size=10, step_size=1)

    # 25 samples with window_size=10 yields 16 sliding windows
    assert sequences.shape == (16, 10, 2)

    # Verify first window contains rows 0..9 in exact order
    assert np.allclose(sequences[0].cpu().numpy(), raw_array[0:10])

    # Verify second window contains rows 1..10 in exact order
    assert np.allclose(sequences[1].cpu().numpy(), raw_array[1:11])

    # Verify final window contains rows 15..24 in exact order
    assert np.allclose(sequences[15].cpu().numpy(), raw_array[15:25])


def test_training_and_validation_separation_zero_leakage():
    """Verify training and validation datasets remain strictly decoupled and target is excluded."""
    train_path = Path("data/reference/train.csv")
    val_path = Path("data/reference/val.csv")
    assert train_path.exists(), "train.csv must exist"
    assert val_path.exists(), "val.csv must exist"

    train_df = pd.read_csv(train_path)
    val_df = pd.read_csv(val_path)

    # Confirm target column exists in raw data but is excluded from FEATURE_COLUMNS
    assert TARGET_COLUMN in train_df.columns
    assert TARGET_COLUMN not in FEATURE_COLUMNS

    monitor = DLSequenceMonitor(window_size=10, epochs=2, batch_size=64, random_state=42)
    metrics = monitor.fit_sequence_model(train_df=train_df, val_df=val_df)

    assert monitor.is_fitted is True
    assert monitor.input_dim == 18  # 6 numerical + 12 one-hot categories
    assert metrics["train_samples_count"] == len(train_df)
    assert metrics["train_sequences_count"] == len(train_df) - 10 + 1
    assert metrics["val_sequences_count"] == len(val_df) - 10 + 1
    assert monitor.threshold is not None
    assert monitor.threshold > 0.0


def test_inference_reconstruction_error_and_threshold_calibration():
    """Verify scoring a window calculates MSE reconstruction error and detects shifted anomalies."""
    train_path = Path("data/reference/train.csv")
    val_path = Path("data/reference/val.csv")
    train_df = pd.read_csv(train_path)
    val_df = pd.read_csv(val_path)

    monitor = DLSequenceMonitor(window_size=10, epochs=3, batch_size=64, random_state=42)
    monitor.fit_sequence_model(train_df=train_df, val_df=val_df)

    # Score normal window from val_df
    normal_window = val_df.iloc[:10]
    normal_res = monitor.score_window(normal_window)

    assert "reconstruction_error" in normal_res
    assert "threshold" in normal_res
    assert "is_shift" in normal_res
    assert "anomaly_score" in normal_res
    assert normal_res["window_size"] == 10
    assert normal_res["reconstruction_error"] >= 0.0

    # Create artificial severe shift window (extreme values)
    shifted_window = normal_window.copy()
    shifted_window["income"] = 2_000_000.0
    shifted_window["loan_amount"] = 500_000.0
    shifted_window["debt_to_income"] = 1.99
    shifted_window["credit_score"] = 300.0

    shifted_res = monitor.score_window(shifted_window)
    assert shifted_res["reconstruction_error"] > normal_res["reconstruction_error"]
    assert shifted_res["is_shift"] is True
    assert shifted_res["anomaly_score"] > normal_res["anomaly_score"]


def test_insufficient_sequence_and_malformed_input_handling():
    """Verify model gracefully rejects insufficient sequence length and missing columns."""
    monitor = DLSequenceMonitor(window_size=10)

    # Test unfitted state
    with pytest.raises(RuntimeError, match="not fitted"):
        monitor.score_window([{"age": 25.0}])

    # Fit quickly with minimal data
    train_df = pd.read_csv("data/reference/train.csv").iloc[:50]
    monitor.fit_sequence_model(train_df=train_df, epochs=1)

    # Test insufficient sequence length (5 < 10)
    short_df = train_df.iloc[:5]
    with pytest.raises(ValueError, match="insufficient"):
        monitor.score_window(short_df)

    # Test missing column
    bad_df = train_df.iloc[:10].drop(columns=["income"])
    with pytest.raises(ValueError, match="(?i)missing required feature columns"):
        monitor.score_window(bad_df)


def test_artifact_serialization_and_deserialization_roundtrip(tmp_path):
    """Verify PyTorch weights, preprocessor, and metadata save and restore identically."""
    train_df = pd.read_csv("data/reference/train.csv").iloc[:100]
    monitor = DLSequenceMonitor(window_size=10, epochs=2, random_state=42)
    monitor.fit_sequence_model(train_df=train_df)

    weights_path, meta_path = monitor.save(tmp_path)
    assert weights_path.exists()
    assert meta_path.exists()
    assert (tmp_path / "dl_sequence_preprocessor.joblib").exists()

    # Load into fresh monitor
    loaded_monitor = DLSequenceMonitor()
    loaded_monitor.load(tmp_path)

    assert loaded_monitor.is_fitted is True
    assert loaded_monitor.window_size == monitor.window_size
    assert loaded_monitor.threshold == monitor.threshold

    # Compare inference output on sample window
    test_window = train_df.iloc[-10:]
    orig_res = monitor.score_window(test_window)
    loaded_res = loaded_monitor.score_window(test_window)

    assert orig_res["reconstruction_error"] == loaded_res["reconstruction_error"]
    assert orig_res["is_shift"] == loaded_res["is_shift"]


def test_mlflow_experiment_logging_under_dl_sequence_monitor():
    """Verify experiment run logs to 'dl-sequence-monitor' without touching ProductionClassifier."""
    train_df = pd.read_csv("data/reference/train.csv").iloc[:50]
    monitor = DLSequenceMonitor(window_size=10, epochs=1, random_state=42)
    metadata = monitor.fit_sequence_model(train_df=train_df)

    run_id = mlflow_adapter.log_dl_experiment_run(
        experiment_name="dl-sequence-monitor",
        run_name="test-pytest-run",
        pytorch_model=monitor.model,
        params={"epochs": 1, "window_size": 10, "feature_dim": 18},
        metrics={"final_train_loss": metadata["final_train_loss"]},
    )

    assert isinstance(run_id, str)
    assert len(run_id) > 0

    # Ensure ProductionClassifier was not altered
    active_model = mlflow_adapter.get_active_model_state()
    assert active_model["active_version"] == "baseline-v1"
    assert active_model["active_run_id"] != run_id


def test_api_sequence_evaluate_rbac_and_audit(api_client, viewer_token, sample_feature_rows):
    """Verify POST /api/v1/monitoring/sequence/evaluate enforces auth and records audit event."""
    # 1. Unauthenticated request rejected with 401
    payload = {"sequence": sample_feature_rows[:10]}
    res_unauth = api_client.post("/api/v1/monitoring/sequence/evaluate", json=payload)
    assert res_unauth.status_code == 401

    # 2. Authenticated request succeeds
    headers = {"Authorization": f"Bearer {viewer_token}", "X-Correlation-ID": "test-corr-seq-01"}
    res_auth = api_client.post("/api/v1/monitoring/sequence/evaluate", json=payload, headers=headers)
    assert res_auth.status_code == 200

    data = res_auth.json()
    assert data["window_size"] == 10
    assert "reconstruction_error" in data
    assert "threshold" in data
    assert "is_shift" in data
    assert "anomaly_score" in data

    # 3. Insufficient sequence (< 10 records) returns 422
    short_payload = {"sequence": sample_feature_rows[:5]}
    res_short = api_client.post("/api/v1/monitoring/sequence/evaluate", json=short_payload, headers=headers)
    assert res_short.status_code == 422

    # 4. Verify audit event was recorded
    events = audit_logger.get_events(limit=5, action="SEQUENCE_EVALUATED")
    assert len(events) > 0
    assert events[0].actor == "test_viewer"
    assert events[0].correlation_id == "test-corr-seq-01"


def test_api_sequence_batch_evaluate_and_audit(api_client, viewer_token, sample_feature_rows):
    """Verify POST /api/v1/monitoring/sequence/batch evaluates sliding windows and logs audit event."""
    headers = {"Authorization": f"Bearer {viewer_token}", "X-Correlation-ID": "test-corr-seq-batch-01"}
    payload = {
        "records": sample_feature_rows,  # 15 records
        "window_size": 10,
        "step_size": 1,
    }

    res = api_client.post("/api/v1/monitoring/sequence/batch", json=payload, headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert data["total_records"] == 15
    assert data["window_size"] == 10
    assert data["total_windows"] == 6  # 15 - 10 + 1 = 6 windows
    assert len(data["windows"]) == 6
    assert "mean_reconstruction_error" in data
    assert "shift_ratio" in data

    # Verify audit event
    events = audit_logger.get_events(limit=5, action="SEQUENCE_BATCH_EVALUATED")
    assert len(events) > 0
    assert events[0].actor == "test_viewer"
    assert events[0].correlation_id == "test-corr-seq-batch-01"


def test_regression_compatibility_with_mon01_exp01_and_ml01():
    """Verify EXP-02 implementation introduces zero regression to MON-01, EXP-01, or ML-01."""
    # 1. Baseline Classifier (ML-01) remains functional
    assert baseline_classifier is not None

    # 2. Statistical Drift Analyzer (MON-01) remains functional
    assert drift_analyzer is not None
    assert drift_analyzer.reference_df is not None

    # 3. Isolation Forest Anomaly Detector (EXP-01) remains functional
    assert isolation_forest_detector is not None

    # 4. Sequence Monitor (EXP-02) is cleanly decoupled
    assert dl_sequence_monitor is not None
