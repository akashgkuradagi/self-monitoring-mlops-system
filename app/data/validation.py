"""Reusable Dataset Validation Engine (DATA-01).

Validates schema compliance, data types, missingness, duplicates,
value bounds, categorical domains, and target class validity.
"""

from typing import Any, Dict, List, Optional
import pandas as pd
from pydantic import BaseModel, Field

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


class DataValidationReport(BaseModel):
    """Structured report returned from comprehensive dataset validation."""

    is_valid: bool = Field(..., description="True if dataset satisfies all validation checks")
    row_count: int = Field(..., ge=0, description="Total number of evaluated rows")
    column_count: int = Field(..., ge=0, description="Total number of evaluated columns")
    issues: List[str] = Field(default_factory=list, description="List of detected validation errors")
    summary: Dict[str, Any] = Field(default_factory=dict, description="Metadata summary of validation")


def validate_dataset(
    df: Optional[pd.DataFrame],
    require_target: bool = True,
    allow_missing: bool = False,
    allow_duplicates: bool = False,
) -> DataValidationReport:
    """Execute comprehensive validation suite against candidate dataframe.

    Checks:
    1. Non-empty dataframe structure.
    2. Column presence (all expected feature columns and target if required).
    3. Absence of null / NaN values (zero-missing tolerance by default).
    4. Absence of duplicate rows.
    5. Numeric types and range bounds for numerical features.
    6. Categorical string domain compliance.
    7. Valid binary target classes (0 and 1).
    """
    issues: List[str] = []

    # 1. Null or empty check
    if df is None:
        return DataValidationReport(
            is_valid=False,
            row_count=0,
            column_count=0,
            issues=["Dataset is None."],
            summary={"status": "REJECTED_NULL"},
        )

    if not isinstance(df, pd.DataFrame):
        return DataValidationReport(
            is_valid=False,
            row_count=0,
            column_count=0,
            issues=[f"Expected pandas DataFrame, got {type(df).__name__}."],
            summary={"status": "REJECTED_TYPE"},
        )

    row_count, col_count = df.shape
    if row_count == 0:
        return DataValidationReport(
            is_valid=False,
            row_count=0,
            column_count=col_count,
            issues=["Dataset contains zero records (empty table)."],
            summary={"status": "REJECTED_EMPTY"},
        )

    # 2. Required columns check
    expected_cols = ALL_DATASET_COLUMNS if require_target else FEATURE_COLUMNS
    missing_cols = [c for c in expected_cols if c not in df.columns]
    if missing_cols:
        issues.append(f"Missing required column(s): {missing_cols}")

    # 3. Missing values check
    if not allow_missing:
        null_counts = df[expected_cols].isnull().sum() if set(expected_cols).issubset(df.columns) else df.isnull().sum()
        cols_with_nulls = null_counts[null_counts > 0]
        if not cols_with_nulls.empty:
            issues.append(f"Dataset contains null values: {cols_with_nulls.to_dict()}")

    # 4. Duplicate records check
    if not allow_duplicates:
        duplicate_count = int(df.duplicated().sum())
        if duplicate_count > 0:
            issues.append(f"Dataset contains {duplicate_count} duplicate row(s).")

    # 5. Numerical features: type and range bounds
    for col in NUMERICAL_FEATURES:
        if col in df.columns:
            if not pd.api.types.is_numeric_dtype(df[col]):
                issues.append(f"Column '{col}' must be numeric, got {df[col].dtype}.")
            else:
                min_bound, max_bound = NUMERICAL_BOUNDS[col]
                out_of_bounds = df[(df[col] < min_bound) | (df[col] > max_bound)]
                if not out_of_bounds.empty:
                    issues.append(
                        f"Column '{col}' has {len(out_of_bounds)} values outside allowable range [{min_bound}, {max_bound}]."
                    )

    # 6. Categorical features: domain membership
    for col in CATEGORICAL_FEATURES:
        if col in df.columns:
            allowed_categories = CATEGORICAL_CATEGORIES[col]
            observed_values = set(df[col].dropna().unique())
            invalid_values = observed_values - allowed_categories
            if invalid_values:
                issues.append(
                    f"Column '{col}' contains unrecognized category value(s): {invalid_values}. "
                    f"Allowed: {allowed_categories}."
                )

    # 7. Target column validation
    if require_target and TARGET_COLUMN in df.columns:
        observed_targets = set(df[TARGET_COLUMN].dropna().unique())
        invalid_targets = observed_targets - VALID_TARGET_VALUES
        if invalid_targets:
            issues.append(
                f"Target column '{TARGET_COLUMN}' contains invalid class(es): {invalid_targets}. "
                f"Expected binary classes in {VALID_TARGET_VALUES}."
            )

    is_valid = len(issues) == 0
    return DataValidationReport(
        is_valid=is_valid,
        row_count=row_count,
        column_count=col_count,
        issues=issues,
        summary={
            "status": "VALIDATED" if is_valid else "INVALID",
            "require_target": require_target,
            "issues_count": len(issues),
        },
    )
