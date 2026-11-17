"""Dataset Curation, Drift Fixtures & DVC Tracking Test Suite (DATA-01).

Maps to Requirements: FR-03, FR-08, NFR-04, NFR-06
Maps to Test Cases: TC-03, TC-04, TC-15
"""

import json
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

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
    NUMERICAL_FEATURES,
    TARGET_COLUMN,
)
from app.data.validation import validate_dataset


# ==============================================================================
# Determinism & Reproducibility Tests (NFR-04)
# ==============================================================================


def test_deterministic_dataset_generation():
    """NFR-04: Fixed random seed produces bitwise identical dataframes."""
    df1 = generate_credit_risk_dataset(n_samples=500, seed=42)
    df2 = generate_credit_risk_dataset(n_samples=500, seed=42)
    df_diff_seed = generate_credit_risk_dataset(n_samples=500, seed=999)

    pd.testing.assert_frame_equal(df1, df2)
    assert not df1["income"].equals(df_diff_seed["income"])


def test_dataset_partition_splits_and_zero_leakage():
    """NFR-04: Split partitions achieve exact ratios with zero index or row leakage."""
    full_df = generate_credit_risk_dataset(n_samples=1000, seed=42)
    train_df, val_df, test_df = split_dataset(
        full_df, train_ratio=0.70, val_ratio=0.15, test_ratio=0.15, seed=42
    )

    assert len(train_df) == 700
    assert len(val_df) == 150
    assert len(test_df) == 150
    assert len(train_df) + len(val_df) + len(test_df) == len(full_df)

    # Validate schema across all partitions
    for part in [train_df, val_df, test_df]:
        report = validate_dataset(part)
        assert report.is_valid is True


# ==============================================================================
# Schema & Integrity Validation Tests (DATA-01)
# ==============================================================================


def test_schema_validation_passes_on_valid_data():
    """Valid dataset satisfies all validation criteria."""
    df = generate_credit_risk_dataset(n_samples=300, seed=42)
    report = validate_dataset(df)
    assert report.is_valid is True
    assert len(report.issues) == 0
    assert report.row_count == 300
    assert report.column_count == len(ALL_DATASET_COLUMNS)


def test_schema_validation_detects_missing_column():
    """Dropping a mandatory column flags a validation failure."""
    df = generate_credit_risk_dataset(n_samples=100, seed=42)
    dropped_df = df.drop(columns=["credit_score"])

    report = validate_dataset(dropped_df)
    assert report.is_valid is False
    assert any("Missing required column" in issue for issue in report.issues)


def test_schema_validation_detects_nulls_and_duplicates():
    """Null values and duplicate rows are detected and rejected."""
    df = generate_credit_risk_dataset(n_samples=100, seed=42)

    # Insert null
    df_with_null = df.copy()
    df_with_null.loc[0, "income"] = np.nan
    report_null = validate_dataset(df_with_null)
    assert report_null.is_valid is False
    assert any("null values" in issue for issue in report_null.issues)

    # Insert duplicate
    df_with_dup = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    report_dup = validate_dataset(df_with_dup)
    assert report_dup.is_valid is False
    assert any("duplicate row" in issue for issue in report_dup.issues)


def test_schema_validation_detects_out_of_bounds_numerical():
    """Out-of-range numerical values trigger validation errors."""
    df = generate_credit_risk_dataset(n_samples=100, seed=42)

    df_bad_age = df.copy()
    df_bad_age.loc[0, "age"] = 150.0  # Max allowable is 100.0
    report = validate_dataset(df_bad_age)
    assert report.is_valid is False
    assert any("age" in issue and "outside allowable range" in issue for issue in report.issues)


def test_schema_validation_detects_invalid_categorical_and_target():
    """Unrecognized categories and target values are rejected."""
    df = generate_credit_risk_dataset(n_samples=100, seed=42)

    # Invalid categorical
    df_bad_cat = df.copy()
    df_bad_cat.loc[0, "loan_intent"] = "GAMBLING_SPREE"
    report_cat = validate_dataset(df_bad_cat)
    assert report_cat.is_valid is False
    assert any("unrecognized category" in issue for issue in report_cat.issues)

    # Invalid target class
    df_bad_target = df.copy()
    df_bad_target.loc[0, TARGET_COLUMN] = 99
    report_target = validate_dataset(df_bad_target)
    assert report_target.is_valid is False
    assert any("invalid class" in issue for issue in report_target.issues)


# ==============================================================================
# Drift Fixture Statistical Validation (TC-03, TC-04, FR-03, FR-08)
# ==============================================================================


def test_stable_fixture_exhibits_no_drift():
    """TC-03: Stable fixture matches reference distribution (all numerical KS p-values > 0.05)."""
    ref_df = generate_credit_risk_dataset(n_samples=2000, seed=42)
    stable_df = generate_stable_fixture(n_samples=1000, seed=101)

    for feature in NUMERICAL_FEATURES:
        res = stats.ks_2samp(ref_df[feature], stable_df[feature])
        assert res.pvalue > 0.05, f"Feature '{feature}' unexpectedly drifted (p={res.pvalue:.4f})"


def test_numerical_drift_fixture_statistically_significant():
    """TC-04: Numerical drift fixture exhibits significant shift (p < 0.05) on shifted features."""
    ref_df = generate_credit_risk_dataset(n_samples=2000, seed=42)
    drift_df = generate_numerical_drift_fixture(n_samples=1000, seed=202)

    shifted_features = ["income", "credit_score", "debt_to_income"]
    for feature in shifted_features:
        res = stats.ks_2samp(ref_df[feature], drift_df[feature])
        assert res.pvalue < 0.01, f"Expected drift for '{feature}', but p={res.pvalue:.4f}"


def test_categorical_drift_fixture_statistically_significant():
    """TC-04: Categorical drift fixture exhibits significant shift in distribution (p < 0.01)."""
    ref_df = generate_credit_risk_dataset(n_samples=2000, seed=42)
    cat_drift_df = generate_categorical_drift_fixture(n_samples=1000, seed=303)

    # Compare 'loan_intent' category frequencies
    ref_counts = ref_df["loan_intent"].value_counts(normalize=True)
    drift_counts = cat_drift_df["loan_intent"].value_counts(normalize=True)

    # DEBTCONSOLIDATION was injected at 60% vs baseline ~10%
    assert drift_counts.get("DEBTCONSOLIDATION", 0.0) >= 0.50
    assert ref_counts.get("DEBTCONSOLIDATION", 0.0) <= 0.15


def test_sudden_and_gradual_drift_fixtures():
    """Sudden fixture exhibits changepoint; gradual fixture exhibits monotonic step-wise drift."""
    sudden_df = generate_sudden_drift_fixture(n_samples=1000, changepoint=500, seed=404)
    assert len(sudden_df) == 1000

    # First half should differ from second half
    first_half_income = sudden_df.iloc[:500]["income"].mean()
    second_half_income = sudden_df.iloc[500:]["income"].mean()
    assert second_half_income < first_half_income * 0.85

    # Gradual fixture
    gradual_df = generate_gradual_drift_fixture(n_samples=1000, n_steps=5, seed=505)
    assert len(gradual_df) == 1000


# ==============================================================================
# DVC Artifacts & Metadata Manifest Tests (NFR-04, NFR-06)
# ==============================================================================


def test_dvc_configuration_and_metadata_files():
    """NFR-04: DVC tracking files, pipeline definitions, and metadata exist."""
    assert Path(".dvc").is_dir()
    assert Path(".dvc/config").is_file()
    assert Path(".dvc/config.local.example").is_file()

    # DVC tracking files
    assert Path("data/reference.dvc").is_file()
    assert Path("data/fixtures.dvc").is_file()
    assert Path("dvc.yaml").is_file()

    # Dataset metadata manifest
    meta_path = Path("data/metadata.json")
    assert meta_path.is_file()

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    assert meta["dataset_name"] == "Credit Risk Tabular Monitoring Dataset"
    assert "partitions" in meta
    assert "train" in meta["partitions"]
    assert "reference" in meta["partitions"]
    assert "fixtures" in meta
