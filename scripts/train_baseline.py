"""Baseline Tabular Model Training and MLflow Registration Script (ML-01).

Trains the production tabular classifier on train.csv, evaluates against val.csv,
logs parameters and artifacts to MLflow, and registers the baseline champion model.
"""

import json
from pathlib import Path
import sys
import pandas as pd

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.models.baseline import BaselineClassifier
from app.registry.mlflow_adapter import MLflowAdapter


def train_and_register_baseline():
    """Train baseline Random Forest model, evaluate, and track in MLflow."""
    train_path = Path("data/reference/train.csv")
    val_path = Path("data/reference/val.csv")
    model_dir = Path(settings.model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] Loading training data from {train_path}...")
    train_df = pd.read_csv(train_path)
    val_df = pd.read_csv(val_path)

    print(f"[*] Training BaselineClassifier (seed={settings.model_random_seed})...")
    clf = BaselineClassifier(
        n_estimators=100,
        max_depth=8,
        min_samples_split=5,
        random_state=settings.model_random_seed,
        model_version=settings.default_model_version,
    )

    train_res = clf.train(train_df)
    print(f"    Train Accuracy: {train_res['train_accuracy']}, Train ROC-AUC: {train_res['train_roc_auc']}")

    print(f"[*] Evaluating against validation dataset ({len(val_df)} samples)...")
    val_metrics = clf.evaluate(val_df, has_labels=True)
    print(f"    Val Accuracy:  {val_metrics['accuracy']}")
    print(f"    Val Precision: {val_metrics['precision']}")
    print(f"    Val Recall:    {val_metrics['recall']}")
    print(f"    Val F1-Score:  {val_metrics['f1']}")
    print(f"    Val ROC-AUC:   {val_metrics['roc_auc']}")

    # Save to local model directory
    joblib_path = model_dir / "baseline_model.joblib"
    clf.save(joblib_path)
    print(f"[*] Serialized model pipeline saved to {joblib_path}")

    # Log to MLflow
    print(f"[*] Logging run and artifacts to MLflow (URI: {settings.mlflow_tracking_uri})...")
    adapter = MLflowAdapter()

    params = {
        "n_estimators": clf.n_estimators,
        "max_depth": clf.max_depth,
        "min_samples_split": clf.min_samples_split,
        "random_state": clf.random_state,
        "algorithm": "RandomForestClassifier",
        "preprocessor": "ColumnTransformer(StandardScaler, OneHotEncoder)",
    }

    run_id = adapter.log_training_run(
        model_pipeline=clf.pipeline,
        train_df=train_df,
        eval_metrics=val_metrics,
        params=params,
        dataset_version="1.0.0",
        model_version=clf.model_version,
    )
    print(f"    MLflow Run ID: {run_id}")

    # Register as initial champion
    promo = adapter.promote_to_champion(run_id=run_id, model_version=clf.model_version)
    print(f"[*] Registered model '{settings.mlflow_model_name}' as '{promo['alias']}' in active_model_state")

    # Record evaluation scorecard artifact
    scorecard_path = model_dir / "baseline_scorecard.json"
    with open(scorecard_path, "w", encoding="utf-8") as f:
        json.dump(val_metrics, f, indent=2)
    print(f"[*] Scorecard saved to {scorecard_path}")

    print("[SUCCESS] Baseline model trained, evaluated, and registered successfully.")
    return val_metrics


if __name__ == "__main__":
    train_and_register_baseline()
