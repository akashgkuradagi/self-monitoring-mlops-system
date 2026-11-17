"""Structured Drift Analysis and Evaluation Schemas (MON-01, FR-03, FR-08)."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class NumericalDriftResult(BaseModel):
    """Statistical drift metrics for a single continuous feature."""

    feature_name: str
    ks_statistic: float = Field(..., description="Two-sample Kolmogorov-Smirnov test statistic D")
    ks_p_value: float = Field(..., description="Asymptotic p-value for two-sample KS test")
    ks_drift: bool = Field(..., description="True if KS p-value < significance alpha")
    psi_value: float = Field(..., description="Population Stability Index across reference quantile bins")
    psi_drift: bool = Field(..., description="True if PSI >= configured psi_threshold")
    drift_detected: bool = Field(..., description="True if either KS or PSI identifies significant shift")
    reference_mean: float
    current_mean: float
    reference_std: float
    current_std: float


class CategoricalDriftResult(BaseModel):
    """Statistical drift metrics for a single categorical feature."""

    feature_name: str
    psi_value: float = Field(..., description="Population Stability Index across discrete category bins")
    psi_drift: bool = Field(..., description="True if PSI >= configured psi_threshold")
    chi2_statistic: Optional[float] = Field(default=None, description="Chi-Square goodness-of-fit statistic")
    chi2_p_value: Optional[float] = Field(default=None, description="Chi-Square test p-value")
    drift_detected: bool = Field(..., description="True if PSI or Chi2 detects significant categorical drift")
    category_proportions: Dict[str, Dict[str, float]] = Field(
        default_factory=dict,
        description="Observed category proportions in reference vs current window",
    )


class DriftReportResponse(BaseModel):
    """Comprehensive structured drift evaluation report (FR-03, FR-08, TC-03, TC-04)."""

    evaluation_timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of drift evaluation",
    )
    overall_drift_status: str = Field(
        ...,
        description="'STABLE', 'DRIFT_DETECTED', or 'INSUFFICIENT_DATA'",
    )
    is_drifted: bool = Field(..., description="True if one or more features exceed drift thresholds")
    reference_sample_count: int = Field(..., ge=0)
    current_sample_count: int = Field(..., ge=0)
    drifted_features: List[str] = Field(default_factory=list, description="List of feature names exhibiting drift")
    numerical_drift: Dict[str, NumericalDriftResult] = Field(default_factory=dict)
    categorical_drift: Dict[str, CategoricalDriftResult] = Field(default_factory=dict)
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    reference_dataset_path: str


class EvaluateDriftRequest(BaseModel):
    """Request payload to trigger drift evaluation against baseline reference."""

    records: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Batch of current observation records with feature values",
    )
    dataset_path: Optional[str] = Field(
        default=None,
        description="Optional local file path to CSV containing current observation records",
    )
