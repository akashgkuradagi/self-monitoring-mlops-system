"""Data Management, Schema, and Synthetic Fixtures Package (DATA-01)."""

from app.data.generator import (
    generate_categorical_drift_fixture,
    generate_credit_risk_dataset,
    generate_gradual_drift_fixture,
    generate_numerical_drift_fixture,
    generate_stable_fixture,
    generate_sudden_drift_fixture,
    split_dataset,
)
from app.data.schema import (
    ALL_DATASET_COLUMNS,
    CATEGORICAL_CATEGORIES,
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERICAL_BOUNDS,
    NUMERICAL_FEATURES,
    TARGET_COLUMN,
    VALID_TARGET_VALUES,
)
from app.data.validation import DataValidationReport, validate_dataset

__all__ = [
    "TARGET_COLUMN",
    "VALID_TARGET_VALUES",
    "NUMERICAL_FEATURES",
    "CATEGORICAL_FEATURES",
    "FEATURE_COLUMNS",
    "ALL_DATASET_COLUMNS",
    "NUMERICAL_BOUNDS",
    "CATEGORICAL_CATEGORIES",
    "validate_dataset",
    "DataValidationReport",
    "generate_credit_risk_dataset",
    "split_dataset",
    "generate_stable_fixture",
    "generate_numerical_drift_fixture",
    "generate_categorical_drift_fixture",
    "generate_sudden_drift_fixture",
    "generate_gradual_drift_fixture",
]
