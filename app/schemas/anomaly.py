"""Anomaly Detection Request and Response Schemas (EXP-01, FR-05)."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AnomalyPredictionRecord(BaseModel):
    """Detailed anomaly scoring for an individual input observation."""

    index: int
    is_anomaly: bool = Field(..., description="True if observation is classified as an anomaly")
    decision_score: float = Field(..., description="Raw Isolation Forest decision function value (negative = outlier)")
    anomaly_score: float = Field(..., description="Normalized anomaly score in [0, 1] (higher = more anomalous)")


class EvaluateAnomalyRequest(BaseModel):
    """Payload to trigger unsupervised anomaly evaluation on a batch of records or file path."""

    records: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Batch of current observation records with feature values",
    )
    dataset_path: Optional[str] = Field(
        default=None,
        description="Optional local file path to CSV containing observation records",
    )


class AnomalyBatchResponse(BaseModel):
    """Structured result of evaluating an inference batch with Isolation Forest (FR-05)."""

    evaluation_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of anomaly evaluation",
    )
    model_version: str = Field(..., description="Model version of the Isolation Forest detector")
    total_samples: int = Field(..., ge=0)
    anomaly_count: int = Field(..., ge=0)
    anomaly_ratio: float = Field(..., ge=0.0, le=1.0, description="Ratio of anomalous samples in batch")
    mean_decision_score: float = Field(..., description="Mean decision function score across batch")
    mean_anomaly_score: float = Field(..., description="Mean normalized anomaly score across batch")
    is_anomaly_detected: bool = Field(..., description="True if anomaly ratio exceeds threshold")
    contamination_threshold: float = Field(..., description="Configured contamination parameter threshold")
    predictions: List[AnomalyPredictionRecord] = Field(
        default_factory=list,
        description="Individual anomaly prediction records",
    )
