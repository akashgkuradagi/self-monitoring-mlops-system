"""Standalone Script to Train, Evaluate, and Register the PyTorch LSTM Sequence Monitor (EXP-02, FR-06).

Executes:
1. Validates and loads reference datasets (train.csv and val.csv) with zero target leakage.
2. Trains unsupervised PyTorch LSTMAutoencoder on rolling sequential windows (W=10).
3. Evaluates reconstruction error and determines the 95th percentile threshold on val.csv.
4. Benchmarks performance on synthetic drift fixtures (stable, numerical, categorical, sudden, gradual).
5. Persists model weights, preprocessor, and metadata to models/ directory.
6. Logs architecture, hyperparameters, loss metrics, and artifacts to MLflow under 'dl-sequence-monitor'.
"""

import json
import logging
from pathlib import Path
import sys
import time

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from app.models.dl_monitor import DLSequenceMonitor  # noqa: E402
from app.registry.mlflow_adapter import mlflow_adapter  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("scripts.train_dl_monitor")


def main() -> None:
    """Execute end-to-end training, fixture evaluation, and MLflow logging for EXP-02."""
    logger.info("Starting PyTorch LSTM Sequence Monitor training workflow (EXP-02)...")

    train_path = Path("data/reference/train.csv")
    val_path = Path("data/reference/val.csv")

    if not train_path.exists() or not val_path.exists():
        logger.error("Reference datasets not found. Ensure DATA-01 datasets are generated.")
        sys.exit(1)

    train_df = pd.read_csv(train_path)
    val_df = pd.read_csv(val_path)

    logger.info("Loaded train.csv (%d rows) and val.csv (%d rows)", len(train_df), len(val_df))

    # 1. Initialize and Fit Sequence Monitor
    monitor = DLSequenceMonitor(
        window_size=10,
        hidden_dim=32,
        latent_dim=16,
        num_layers=1,
        lr=0.003,
        batch_size=64,
        epochs=15,
        random_state=42,
        device="cpu",
    )

    t0 = time.perf_counter()
    train_metadata = monitor.fit_sequence_model(train_df=train_df, val_df=val_df)
    train_duration = time.perf_counter() - t0

    logger.info("Model training complete in %.2f seconds.", train_duration)
    logger.info(
        "Final Train Loss: %.4f | Val Mean Recon Error: %.4f | Threshold (95th pct): %.4f",
        train_metadata["final_train_loss"],
        train_metadata["val_mean_reconstruction_error"],
        train_metadata["reconstruction_threshold"],
    )

    # 2. Evaluate on Synthetic Drift Fixtures
    fixtures = {
        "stable": Path("data/fixtures/stable_fixture.csv"),
        "numerical_drift": Path("data/fixtures/numerical_drift_fixture.csv"),
        "categorical_drift": Path("data/fixtures/categorical_drift_fixture.csv"),
        "sudden_drift": Path("data/fixtures/sudden_drift_fixture.csv"),
        "gradual_drift": Path("data/fixtures/gradual_drift_fixture.csv"),
    }

    fixture_results = {}
    for name, path in fixtures.items():
        if path.exists():
            df_fix = pd.read_csv(path)
            res = monitor.evaluate(df_fix, window_size=10, step_size=1)
            fixture_results[name] = {
                "total_windows": res["total_windows"],
                "mean_reconstruction_error": res["mean_reconstruction_error"],
                "shift_windows_count": res["shift_windows_count"],
                "shift_ratio": res["shift_ratio"],
            }
            logger.info(
                "Fixture '%s': Mean Error=%.4f, Shift Ratio=%.3f (%d/%d windows shifted)",
                name,
                res["mean_reconstruction_error"],
                res["shift_ratio"],
                res["shift_windows_count"],
                res["total_windows"],
            )

    # 3. Serialize Artifacts to models/
    models_dir = Path("models")
    weights_path, metadata_path = monitor.save(models_dir)

    scorecard_path = models_dir / "dl_sequence_scorecard.json"
    full_scorecard = {
        "model_type": "LSTMAutoencoder",
        "framework": "PyTorch",
        "architecture": {
            "encoder": "nn.LSTM(18, 32) -> nn.Linear(32, 16)",
            "decoder": "nn.Linear(16, 32) -> nn.LSTM(32, 32) -> nn.Linear(32, 18)",
        },
        "training_metadata": train_metadata,
        "fixture_evaluations": fixture_results,
    }
    with open(scorecard_path, "w", encoding="utf-8") as f:
        json.dump(full_scorecard, f, indent=2)
    logger.info("Saved evaluation scorecard to %s", scorecard_path)

    # 4. Log to MLflow under 'dl-sequence-monitor'
    mlflow_params = {
        "model_type": "LSTMAutoencoder",
        "window_size": 10,
        "hidden_dim": 32,
        "latent_dim": 16,
        "num_layers": 1,
        "learning_rate": 0.003,
        "batch_size": 64,
        "epochs": 15,
        "random_state": 42,
        "optimizer": "Adam",
        "loss_fn": "MSELoss",
        "device": "cpu",
        "feature_dim": 18,
    }
    mlflow_metrics = {
        "final_train_loss": train_metadata["final_train_loss"],
        "val_mean_reconstruction_error": train_metadata["val_mean_reconstruction_error"],
        "reconstruction_threshold": train_metadata["reconstruction_threshold"],
        "val_anomaly_ratio": train_metadata["val_anomaly_ratio"],
        "training_duration_seconds": train_duration,
        "stable_fixture_shift_ratio": fixture_results.get("stable", {}).get("shift_ratio", 0.0),
        "numerical_drift_shift_ratio": fixture_results.get("numerical_drift", {}).get("shift_ratio", 0.0),
        "sudden_drift_shift_ratio": fixture_results.get("sudden_drift", {}).get("shift_ratio", 0.0),
        "gradual_drift_shift_ratio": fixture_results.get("gradual_drift", {}).get("shift_ratio", 0.0),
    }

    run_id = mlflow_adapter.log_dl_experiment_run(
        experiment_name="dl-sequence-monitor",
        run_name="lstm-autoencoder-baseline",
        pytorch_model=monitor.model,
        params=mlflow_params,
        metrics=mlflow_metrics,
        tags={
            "stage": "sprint-2-stage-2",
            "issue": "#9",
            "requirement": "FR-06",
            "model_family": "temporal_shift_monitor",
        },
        artifact_paths=[
            str(weights_path),
            str(metadata_path),
            str(scorecard_path),
        ],
    )

    logger.info("Logged deep learning sequence monitor to MLflow run: %s", run_id)
    print("\n" + "=" * 60)
    print("EXP-02 PYTORCH LSTM SEQUENCE MONITOR SCORECARD")
    print("=" * 60)
    print(f"MLflow Run ID:          {run_id}")
    print(f"Final Train Loss:        {train_metadata['final_train_loss']:.4f}")
    print(f"Val Mean Recon Error:    {train_metadata['val_mean_reconstruction_error']:.4f}")
    print(f"Shift Threshold:         {train_metadata['reconstruction_threshold']:.4f}")
    print(f"Val False Positive Rate: {train_metadata['val_anomaly_ratio']:.3f}")
    print("-" * 60)
    print("Fixture Drift Detection Ratios:")
    for fix_name, fix_stat in fixture_results.items():
        print(f"  - {fix_name:<20}: Mean Loss={fix_stat['mean_reconstruction_error']:.4f}, Shift Ratio={fix_stat['shift_ratio']:.3f}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
