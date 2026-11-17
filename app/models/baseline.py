"""Baseline Tabular Production Model Subsystem (ML-01).

Implements:
- FR-04: Baseline tabular classification pipeline and delayed ground-truth accuracy logic.
- NFR-04: Reproducible training with fixed random seeds and persistent preprocessing.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.config import settings
from app.data.schema import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERICAL_FEATURES,
    TARGET_COLUMN,
)
from app.data.validation import validate_dataset


class BaselineClassifier:
    """Production tabular classifier with integrated preprocessor and delayed ground-truth evaluation."""

    def __init__(
        self,
        n_estimators: int = 100,
        max_depth: int = 8,
        min_samples_split: int = 5,
        random_state: Optional[int] = None,
        model_version: str = "baseline-v1",
    ):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.random_state = random_state or settings.model_random_seed
        self.model_version = model_version
        self.pipeline: Optional[Pipeline] = None
        self._is_fitted: bool = False

    def _build_pipeline(self) -> Pipeline:
        """Construct scikit-learn Pipeline with ColumnTransformer preprocessing."""
        preprocessor = ColumnTransformer(
            transformers=[
                ("num", StandardScaler(), NUMERICAL_FEATURES),
                ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
            ],
            remainder="drop",
        )

        classifier = RandomForestClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            min_samples_split=self.min_samples_split,
            random_state=self.random_state,
            class_weight="balanced",
        )

        return Pipeline(steps=[
            ("preprocessor", preprocessor),
            ("classifier", classifier),
        ])

    def train(self, train_df: pd.DataFrame, seed: Optional[int] = None) -> Dict[str, Any]:
        """Validate input training data and fit baseline model pipeline (NFR-04)."""
        # Validate dataset schema and integrity before training
        report = validate_dataset(train_df, require_target=True)
        if not report.is_valid:
            raise ValueError(f"Training dataset failed validation: {report.issues}")

        if seed is not None:
            self.random_state = seed

        self.pipeline = self._build_pipeline()

        X_train = train_df[FEATURE_COLUMNS]
        y_train = train_df[TARGET_COLUMN].astype(int)

        self.pipeline.fit(X_train, y_train)
        self._is_fitted = True

        train_preds = self.pipeline.predict(X_train)
        train_probs = self.pipeline.predict_proba(X_train)[:, 1]

        return {
            "model_version": self.model_version,
            "train_samples": len(train_df),
            "train_accuracy": round(float(accuracy_score(y_train, train_preds)), 4),
            "train_roc_auc": round(float(roc_auc_score(y_train, train_probs)), 4),
            "train_f1": round(float(f1_score(y_train, train_preds, zero_division=0)), 4),
            "random_state": self.random_state,
        }

    def evaluate(self, eval_df: pd.DataFrame, has_labels: bool = True) -> Dict[str, Any]:
        """Evaluate model against validation dataset with explicit delayed ground-truth handling (FR-04).

        When ground-truth labels are absent:
        Returns accuracy: null / 'unavailable' to avoid synthetic or fabricated metrics.
        """
        if not self._is_fitted or self.pipeline is None:
            raise RuntimeError("Model is not fitted. Train or load weights before evaluation.")

        # Check if ground-truth labels are truly present in the dataframe
        labels_present = has_labels and (TARGET_COLUMN in eval_df.columns)

        if not labels_present:
            # Explicit delayed ground truth evaluation (FR-04)
            return {
                "model_version": self.model_version,
                "status": "labels_unavailable",
                "accuracy": None,
                "precision": None,
                "recall": None,
                "f1": None,
                "roc_auc": None,
                "confusion_matrix": None,
                "sample_count": len(eval_df),
                "note": "Ground-truth labels are delayed or absent; accuracy figures are reported as unavailable.",
            }

        report = validate_dataset(eval_df, require_target=True)
        if not report.is_valid:
            raise ValueError(f"Evaluation dataset failed validation: {report.issues}")

        X_eval = eval_df[FEATURE_COLUMNS]
        y_true = eval_df[TARGET_COLUMN].astype(int)

        y_pred = self.pipeline.predict(X_eval)
        y_prob = self.pipeline.predict_proba(X_eval)[:, 1]

        acc = float(accuracy_score(y_true, y_pred))
        prec = float(precision_score(y_true, y_pred, zero_division=0))
        rec = float(recall_score(y_true, y_pred, zero_division=0))
        f1 = float(f1_score(y_true, y_pred, zero_division=0))
        auc = float(roc_auc_score(y_true, y_prob))
        cm = confusion_matrix(y_true, y_pred).tolist()

        return {
            "model_version": self.model_version,
            "status": "evaluated",
            "accuracy": round(acc, 4),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "roc_auc": round(auc, 4),
            "confusion_matrix": cm,
            "sample_count": len(eval_df),
            "note": "Evaluated on validation dataset with true labels.",
        }

    def predict(self, features_df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """Perform batch inference returning class predictions and default probability scores."""
        if not self._is_fitted or self.pipeline is None:
            raise RuntimeError("Model is not fitted. Train or load weights before inference.")

        # Validate feature presence
        missing_features = [f for f in FEATURE_COLUMNS if f not in features_df.columns]
        if missing_features:
            raise ValueError(f"Features missing from input: {missing_features}")

        X = features_df[FEATURE_COLUMNS]
        predictions = self.pipeline.predict(X)
        probabilities = self.pipeline.predict_proba(X)[:, 1]

        return predictions, probabilities

    def save(self, file_path: Union[str, Path]) -> None:
        """Serialize fitted pipeline and metadata to disk."""
        if not self._is_fitted or self.pipeline is None:
            raise RuntimeError("Cannot save unfitted model.")

        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "pipeline": self.pipeline,
            "model_version": self.model_version,
            "random_state": self.random_state,
            "hyperparameters": {
                "n_estimators": self.n_estimators,
                "max_depth": self.max_depth,
                "min_samples_split": self.min_samples_split,
            },
        }
        joblib.dump(payload, path)

    def load(self, file_path: Union[str, Path]) -> None:
        """Load serialized pipeline from disk."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Model file not found: {path}")

        payload = joblib.load(path)
        self.pipeline = payload["pipeline"]
        self.model_version = payload.get("model_version", self.model_version)
        self.random_state = payload.get("random_state", self.random_state)
        self._is_fitted = True

    @property
    def is_fitted(self) -> bool:
        """Return True if model pipeline is trained and ready for inference."""
        return self._is_fitted


# Global singleton instance
baseline_classifier = BaselineClassifier()
