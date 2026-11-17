"""Inference and Model Metadata Schemas (ML-01)."""

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class PredictionRequest(BaseModel):
    """Input features payload schema for tabular credit risk inference."""

    age: float = Field(..., ge=18.0, le=100.0, description="Applicant age in years")
    income: float = Field(..., ge=1000.0, le=2_000_000.0, description="Annual income in USD")
    loan_amount: float = Field(..., ge=500.0, le=500_000.0, description="Requested loan amount in USD")
    credit_score: float = Field(..., ge=300.0, le=850.0, description="FICO/Credit bureau score")
    debt_to_income: float = Field(..., ge=0.0, le=2.0, description="Debt-to-income ratio")
    employment_length: float = Field(..., ge=0.0, le=60.0, description="Employment tenure in years")
    loan_intent: str = Field(..., description="Loan purpose: PERSONAL, EDUCATION, MEDICAL, VENTURE, HOMEIMPROVEMENT, DEBTCONSOLIDATION")
    home_ownership: str = Field(..., description="Housing tenure: RENT, MORTGAGE, OWN, OTHER")
    previous_default: str = Field(..., description="Historical default record: Y or N")
    request_id: Optional[str] = Field(default=None, description="Optional client-provided correlation ID")


class PredictionResponse(BaseModel):
    """Inference output schema with class label, calibrated default probability, and latency."""

    request_id: str = Field(..., description="Assigned request identifier")
    prediction: int = Field(..., description="Predicted class: 0 (non-default) or 1 (default)")
    default_probability: float = Field(..., ge=0.0, le=1.0, description="Estimated probability of default")
    risk_label: str = Field(..., description="'LOW_RISK' for non-default or 'HIGH_RISK' for default")
    model_version: str = Field(..., description="Active production model version")
    latency_ms: float = Field(..., ge=0.0, description="Inference execution duration in milliseconds")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC observation timestamp",
    )


class ActiveModelResponse(BaseModel):
    """Metadata response describing the active champion production model."""

    active_version: str
    active_run_id: str
    promoted_at: str
    previous_stable_version: Optional[str] = None
    state: str
    model_name: str
