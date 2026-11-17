"""Isolation Forest Anomaly Monitoring Experiment Training & Tracking Script (EXP-01, FR-05).

Trains the unsupervised Isolation Forest detector on train.csv, evaluates on val.csv,
logs parameters and artifacts to MLflow under experiment 'classical-ml-monitor',
and saves local serialized joblib model artifacts.
"""

import json
from pathlib import Path
import sys
import pandas as pd

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.models.ml_monitor import IsolationForestDetector
from app.registry.mlflow_adapter import MLflowAdapter


def train_and_track_isolation_forest():
    """Train unsupervised Isolation Forest model, evaluate, and track in MLflow."""
    train_path = Path("data/reference/train.csv")
    val_path = Path("data/reference/val.csv")
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] Loading reference training data from {train_path}...")
    train_df = pd.read_csv(train_path)
    val_df = pd.read_csv(val_path)

    print(f"[*] Training IsolationForestDetector (contamination=0.05, seed={settings.model_random_seed})...")
    detector = IsolationForestDetector(
        n_estimators=100,
        contamination=0.05,
        random_state=settings.model_random_seed,
        model_version="iso-forest-v1",
    )

    train_res = detector.train(train_df)
    print(f"    Baseline Anomaly Ratio: {train_res['baseline_anomaly_ratio']}")
    print(f"    Offset Threshold:       {train_res['offset_threshold']}")
    print(f"    Mean Decision Score:    {train_res['baseline_mean_decision_score']}")

    print(f"[*] Evaluating against validation dataset ({len(val_df)} samples)...")
    val_batch = detector.evaluate_batch(val_df)
    print(f"    Val Anomaly Count:      {val_batch.anomaly_count} / {val_batch.total_samples}")
    print(f"    Val Anomaly Ratio:      {val_batch.anomaly_ratio}")
    print(f"    Val Mean Decision Score: {val_batch.mean_decision_score}")
    print(f"    Val Mean Anomaly Score:  {val_batch.mean_anomaly_score}")

    # Save to local models directory
    joblib_path = model_dir / "isolation_forest.joblib"
    detector.save(joblib_path)
    print(f"[*] Serialized detector saved to {joblib_path}")

    # Track in MLflow under experiment 'classical-ml-monitor' (FR-05, TC-05)
    print("[*] Tracking experiment run in MLflow (Experiment: classical-ml-monitor)...")
    adapter = MLflowAdapter(experiment_name="classical-ml-monitor")

    params = {
        "algorithm": "IsolationForest",
        "n_estimators": detector.n_estimators,
        "contamination": detector.contamination,
        "max_samples": str(detector.max_samples),
        "random_state": detector.random_state,
        "model_version": detector.model_version,
        "dataset_split": "train",
        "features": "StandardScaler(NUMERICAL) + OneHotEncoder(CATEGORICAL)",
    }

    metrics = {
        "train_samples": float(len(train_df)),
        "offset_threshold": float(train_res["offset_threshold"]),
        "baseline_anomaly_ratio": float(train_res["baseline_anomaly_ratio"]),
        "baseline_mean_score": float(train_res["baseline_mean_decision_score"]),
        "val_samples": float(val_batch.total_samples),
        "val_anomaly_count": float(val_batch.anomaly_count),
        "val_anomaly_ratio": float(val_batch.anomaly_ratio),
        "val_mean_decision_score": float(val_batch.mean_decision_score),
        "val_mean_anomaly_score": float(val_batch.mean_anomaly_score),
    }

    tags = {
        "experiment_name": "classical-ml-monitor",
        "model_type": "unsupervised_anomaly_detector",
        "framework": "scikit-learn",
        "issue": "EXP-01",
        "requirement": "FR-05",
        "test_case": "TC-05",
    }

    input_example = val_df.head(3)
    run_id = adapter.log_monitoring_experiment_run(
        experiment_name="classical-ml-monitor",
        run_name=f"run-{detector.model_version}",
        model_pipeline=detector.pipeline,
        input_example=input_example,
        params=params,
        metrics=metrics,
        tags=tags,
    )
    print(f"    MLflow Run ID: {run_id}")

    # Save scorecard
    scorecard = {
        "model_version": detector.model_version,
        "algorithm": "IsolationForest",
        "mlflow_run_id": run_id,
        "experiment_name": "classical-ml-monitor",
        "training_summary": train_res,
        "validation_metrics": {
            "total_samples": val_batch.total_samples,
            "anomaly_count": val_batch.anomaly_count,
            "anomaly_ratio": val_batch.anomaly_ratio,
            "mean_decision_score": val_batch.mean_decision_score,
            "mean_anomaly_score": val_batch.mean_anomaly_score,
            "is_anomaly_detected": val_batch.is_anomaly_detected,
        },
    }

    scorecard_path = model_dir / "isolation_forest_scorecard.json"
    with open(scorecard_path, "w", encoding="utf-8") as f:
        json.dump(scorecard, f, indent=2)
    print(f"[*] Scorecard saved to {scorecard_path}")

    print("[SUCCESS] Isolation Forest experiment completed and tracked successfully.")
    return scorecard


if __name__ == "__main__":
    train_and_track_isolation_forest()
