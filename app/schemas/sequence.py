"""Pydantic Schemas for Deep Learning Sequence / Temporal Shift Monitoring (EXP-02, FR-06).

Defines schemas for sequential inference observation records, window evaluation
requests, and structured reconstruction loss responses.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class SequenceRecord(BaseModel):
    """Single observation record matching the authoritative dataset schema (DATA-01)."""

    age: float = Field(..., ge=18.0, le=100.0, description="Applicant age in years.")
    income: float = Field(..., ge=0.0, description="Annual applicant income.")
    loan_amount: float = Field(..., ge=0.0, description="Requested loan principal amount.")
    credit_score: float = Field(..., ge=300.0, le=850.0, description="Applicant credit score.")
    debt_to_income: float = Field(..., ge=0.0, description="Debt-to-income ratio.")
    employment_length: float = Field(..., ge=0.0, description="Years of employment.")
    loan_intent: str = Field(..., description="Categorical loan intent category.")
    home_ownership: str = Field(..., description="Categorical home ownership status.")
    previous_default: str = Field(..., description="Categorical historical default indicator ('Y'/'N').")


class EvaluateSequenceRequest(BaseModel):
    """Payload for evaluating a single temporal observation sequence."""

    sequence: List[SequenceRecord] = Field(
        ...,
        min_length=1,
        description="Chronologically ordered sequence of observation records.",
    )


class SequenceScoreResponse(BaseModel):
    """Reconstruction loss and temporal anomaly result for a sequential window."""

    window_size: int = Field(..., description="Length of observation sequence evaluated.")
    reconstruction_error: float = Field(..., description="Mean squared reconstruction error across sequence features.")
    threshold: float = Field(..., description="Baseline anomaly/shift decision threshold.")
    is_shift: bool = Field(..., description="Boolean flag indicating whether reconstruction error exceeds threshold.")
    anomaly_score: float = Field(
        ...,
        ge=0.0,
        description="Normalized anomaly score relative to baseline threshold.",
    )
    details: Optional[Dict[str, Any]] = Field(default=None, description="Supplemental sequence evaluation metrics.")


class EvaluateSequenceBatchRequest(BaseModel):
    """Payload for evaluating multiple sliding temporal windows over an observation stream."""

    records: List[SequenceRecord] = Field(
        ...,
        min_length=10,
        description="Stream of chronologically ordered observation records.",
    )
    window_size: int = Field(default=10, ge=2, le=100, description="Sliding window sequence length.")
    step_size: int = Field(default=1, ge=1, le=50, description="Step stride between consecutive sliding windows.")


class SequenceBatchResponse(BaseModel):
    """Batch evaluation response across sliding temporal sequence windows."""

    total_records: int = Field(..., description="Total input records evaluated.")
    window_size: int = Field(..., description="Sequence length of each temporal window.")
    step_size: int = Field(..., description="Stride between consecutive temporal windows.")
    total_windows: int = Field(..., description="Number of sequential windows constructed.")
    threshold: float = Field(..., description="Baseline anomaly/shift decision threshold.")
    mean_reconstruction_error: float = Field(..., description="Mean reconstruction error across all sequence windows.")
    shift_windows_count: int = Field(..., description="Count of sequence windows exceeding the shift threshold.")
    shift_ratio: float = Field(..., ge=0.0, le=1.0, description="Proportion of sequence windows exhibiting shift.")
    windows: List[SequenceScoreResponse] = Field(..., description="Per-window reconstruction scores.")
