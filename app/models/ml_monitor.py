"""Unsupervised Classical ML Anomaly Detector using Isolation Forest (EXP-01, FR-05).

Implements:
- FR-05: ML-based monitoring experiment logging model version, parameters, and metrics to MLflow.
- TC-05: Experiment metadata tracking under 'classical-ml-monitor'.
- Multivariate outlier scoring independent of ground-truth labels.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.config import settings
from app.data.schema import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERICAL_FEATURES,
)
from app.data.validation import validate_dataset
from app.schemas.anomaly import AnomalyBatchResponse, AnomalyPredictionRecord


class IsolationForestDetector:
    """Unsupervised multivariate anomaly detector using scikit-learn IsolationForest."""

    def __init__(
        self,
        n_estimators: int = 100,
        contamination: float = 0.05,
        max_samples: Union[str, int, float] = "auto",
        random_state: Optional[int] = None,
        model_version: str = "iso-forest-v1",
    ):
        self.n_estimators = n_estimators
        self.contamination = contamination
        self.max_samples = max_samples
        self.random_state = random_state or settings.model_random_seed
        self.model_version = model_version
        self.pipeline: Optional[Pipeline] = None
        self._is_fitted: bool = False
        self._offset: float = -0.5

    @property
    def is_fitted(self) -> bool:
        """Return whether detector pipeline is fitted."""
        return self._is_fitted

    def _build_pipeline(self) -> Pipeline:
        """Construct scikit-learn Pipeline with ColumnTransformer preprocessing."""
        preprocessor = ColumnTransformer(
            transformers=[
                ("num", StandardScaler(), NUMERICAL_FEATURES),
                ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL_FEATURES),
            ],
            remainder="drop",
        )

        detector = IsolationForest(
            n_estimators=self.n_estimators,
            contamination=self.contamination,
            max_samples=self.max_samples,
            random_state=self.random_state,
        )

        return Pipeline(steps=[
            ("preprocessor", preprocessor),
            ("detector", detector),
        ])

    def train(self, train_df: pd.DataFrame, seed: Optional[int] = None) -> Dict[str, Any]:
        """Fit unsupervised Isolation Forest pipeline on reference feature distributions (FR-05)."""
        report = validate_dataset(train_df, require_target=False)
        if not report.is_valid:
            raise ValueError(f"Training dataset failed validation: {report.issues}")

        if seed is not None:
            self.random_state = seed

        self.pipeline = self._build_pipeline()

        X_train = train_df[FEATURE_COLUMNS]
        self.pipeline.fit(X_train)
        self._is_fitted = True

        raw_detector = self.pipeline.named_steps["detector"]
        self._offset = float(raw_detector.offset_)

        # Evaluate on training distribution
        raw_scores = self.pipeline.decision_function(X_train)
        raw_preds = self.pipeline.predict(X_train)
        anomalies = (raw_preds == -1)
        anomaly_ratio = float(anomalies.mean())
        mean_score = float(raw_scores.mean())

        return {
            "model_version": self.model_version,
            "algorithm": "IsolationForest",
            "n_estimators": self.n_estimators,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "offset_threshold": round(self._offset, 4),
            "train_sample_count": len(train_df),
            "baseline_anomaly_ratio": round(anomaly_ratio, 4),
            "baseline_mean_decision_score": round(mean_score, 4),
        }

    def predict_records(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Produce anomaly predictions, decision function values, and normalized scores.

        Returns:
            Tuple[np.ndarray, np.ndarray, np.ndarray]:
                - is_anomaly (bool array, True for outliers)
                - decision_scores (raw scikit-learn decision function, negative = anomaly)
                - anomaly_scores (normalized score in [0, 1], higher = more anomalous)
        """
        if not self._is_fitted or self.pipeline is None:
            raise RuntimeError("IsolationForestDetector must be trained before predicting")

        if len(df) == 0:
            return np.array([], dtype=bool), np.array([], dtype=float), np.array([], dtype=float)

        missing_cols = [c for c in FEATURE_COLUMNS if c not in df.columns]
        if missing_cols:
            raise ValueError(f"Input dataframe missing required feature columns: {missing_cols}")

        X = df[FEATURE_COLUMNS]
        raw_preds = self.pipeline.predict(X)
        decision_scores = self.pipeline.decision_function(X)

        is_anomaly = (raw_preds == -1)

        # Normalize score into [0.0, 1.0] where 1.0 represents extreme isolation/anomaly
        # In scikit-learn, decision_function is roughly in [-0.5, 0.5]
        norm_scores = np.clip(0.5 - (decision_scores / 1.0), 0.0, 1.0)

        return is_anomaly, decision_scores, norm_scores

    def evaluate_batch(self, df: pd.DataFrame) -> AnomalyBatchResponse:
        """Evaluate an inference batch, returning structured anomaly summary and per-row records."""
        now_iso = datetime.now(timezone.utc).isoformat()

        if len(df) == 0:
            return AnomalyBatchResponse(
                evaluation_timestamp=now_iso,
                model_version=self.model_version,
                total_samples=0,
                anomaly_count=0,
                anomaly_ratio=0.0,
                mean_decision_score=0.0,
                mean_anomaly_score=0.0,
                is_anomaly_detected=False,
                contamination_threshold=self.contamination,
                predictions=[],
            )

        is_anomaly, decision_scores, norm_scores = self.predict_records(df)

        total = len(df)
        anomaly_count = int(is_anomaly.sum())
        anomaly_ratio = float(anomaly_count / total) if total > 0 else 0.0
        mean_dec = float(decision_scores.mean()) if total > 0 else 0.0
        mean_norm = float(norm_scores.mean()) if total > 0 else 0.0

        # Anomaly alarm threshold: ratio exceeds 1.5x expected contamination or 10%
        alert_threshold = max(self.contamination * 1.5, 0.10)
        is_detected = bool(anomaly_ratio >= alert_threshold)

        records: List[AnomalyPredictionRecord] = []
        for idx in range(total):
            records.append(
                AnomalyPredictionRecord(
                    index=idx,
                    is_anomaly=bool(is_anomaly[idx]),
                    decision_score=round(float(decision_scores[idx]), 4),
                    anomaly_score=round(float(norm_scores[idx]), 4),
                )
            )

        return AnomalyBatchResponse(
            evaluation_timestamp=now_iso,
            model_version=self.model_version,
            total_samples=total,
            anomaly_count=anomaly_count,
            anomaly_ratio=round(anomaly_ratio, 4),
            mean_decision_score=round(mean_dec, 4),
            mean_anomaly_score=round(mean_norm, 4),
            is_anomaly_detected=is_detected,
            contamination_threshold=self.contamination,
            predictions=records,
        )

    def evaluate(
        self,
        eval_df: pd.DataFrame,
        ground_truth_labels: Optional[pd.Series] = None,
    ) -> Dict[str, Any]:
        """Compute evaluation metrics, optionally against ground-truth labels if provided."""
        batch_res = self.evaluate_batch(eval_df)

        metrics: Dict[str, Any] = {
            "sample_count": batch_res.total_samples,
            "anomaly_count": batch_res.anomaly_count,
            "anomaly_ratio": batch_res.anomaly_ratio,
            "mean_decision_score": batch_res.mean_decision_score,
            "mean_anomaly_score": batch_res.mean_anomaly_score,
            "offset_threshold": round(self._offset, 4),
        }

        # If labelled ground truth is provided (e.g. synthetic anomaly labels or default correlation)
        if ground_truth_labels is not None and len(ground_truth_labels) == len(eval_df):
            y_true = ground_truth_labels.to_numpy(dtype=int)
            y_pred = np.array([1 if p.is_anomaly else 0 for p in batch_res.predictions])

            metrics["precision"] = round(float(precision_score(y_true, y_pred, zero_division=0)), 4)
            metrics["recall"] = round(float(recall_score(y_true, y_pred, zero_division=0)), 4)
            metrics["f1"] = round(float(f1_score(y_true, y_pred, zero_division=0)), 4)
            try:
                scores = np.array([p.anomaly_score for p in batch_res.predictions])
                metrics["roc_auc"] = round(float(roc_auc_score(y_true, scores)), 4)
            except Exception:
                metrics["roc_auc"] = None
        else:
            # Delayed ground-truth semantics
            metrics["precision"] = None
            metrics["recall"] = None
            metrics["f1"] = None
            metrics["roc_auc"] = None

        return metrics

    def save(self, path: Union[str, Path]) -> None:
        """Persist fitted detector pipeline to disk."""
        if not self._is_fitted or self.pipeline is None:
            raise RuntimeError("Cannot save unfitted IsolationForestDetector")

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "pipeline": self.pipeline,
            "offset": self._offset,
            "n_estimators": self.n_estimators,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "model_version": self.model_version,
        }
        joblib.dump(state, p)

    def load(self, path: Union[str, Path]) -> None:
        """Restore detector pipeline from disk."""
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Detector artifact not found at: {p}")

        state = joblib.load(p)
        self.pipeline = state["pipeline"]
        self._offset = state["offset"]
        self.n_estimators = state["n_estimators"]
        self.contamination = state["contamination"]
        self.random_state = state["random_state"]
        self.model_version = state.get("model_version", self.model_version)
        self._is_fitted = True


# Global singleton instance
isolation_forest_detector = IsolationForestDetector()
