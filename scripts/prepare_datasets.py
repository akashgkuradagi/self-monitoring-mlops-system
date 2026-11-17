"""Dataset Preparation Pipeline Script (DATA-01).

Generates baseline reference dataset splits and synthetic drift fixtures,
validates their schema and data integrity, and records metadata manifests.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data.generator import (
    generate_categorical_drift_fixture,
    generate_credit_risk_dataset,
    generate_gradual_drift_fixture,
    generate_numerical_drift_fixture,
    generate_stable_fixture,
    generate_sudden_drift_fixture,
    split_dataset,
)
from app.data.schema import CATEGORICAL_FEATURES, NUMERICAL_FEATURES, TARGET_COLUMN
from app.data.validation import validate_dataset


def file_sha256(file_path: Path) -> str:
    """Calculate SHA-256 hash of a file on disk."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def prepare_all_datasets():
    """Generate, validate, and write all reference and fixture datasets to disk."""
    base_dir = Path("data")
    ref_dir = base_dir / "reference"
    fixture_dir = base_dir / "fixtures"

    ref_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir.mkdir(parents=True, exist_ok=True)

    print("[*] Generating reference credit risk dataset (5,000 samples, seed=42)...")
    full_df = generate_credit_risk_dataset(n_samples=5000, seed=42)

    # Validate full dataset
    val_report = validate_dataset(full_df)
    assert val_report.is_valid, f"Validation failed on reference: {val_report.issues}"

    # Partition dataset into train (70%), validation (15%), test/reference (15%)
    train_df, val_df, test_df = split_dataset(full_df, train_ratio=0.70, val_ratio=0.15, test_ratio=0.15, seed=42)

    # Save reference files
    train_path = ref_dir / "train.csv"
    val_path = ref_dir / "val.csv"
    test_path = ref_dir / "reference.csv"

    train_df.to_csv(train_path, index=False)
    val_df.to_csv(val_path, index=False)
    test_df.to_csv(test_path, index=False)

    print(f"    Train: {len(train_df)} rows -> {train_path}")
    print(f"    Validation: {len(val_df)} rows -> {val_path}")
    print(f"    Reference Test: {len(test_df)} rows -> {test_path}")

    # Generate Drift Fixtures
    print("[*] Generating synthetic drift fixtures (1,000 samples each)...")
    fixtures = {
        "stable_fixture.csv": generate_stable_fixture(n_samples=1000, seed=101),
        "numerical_drift_fixture.csv": generate_numerical_drift_fixture(n_samples=1000, seed=202),
        "categorical_drift_fixture.csv": generate_categorical_drift_fixture(n_samples=1000, seed=303),
        "sudden_drift_fixture.csv": generate_sudden_drift_fixture(n_samples=1000, changepoint=500, seed=404),
        "gradual_drift_fixture.csv": generate_gradual_drift_fixture(n_samples=1000, n_steps=5, seed=505),
    }

    fixture_hashes = {}
    for filename, f_df in fixtures.items():
        f_path = fixture_dir / filename
        f_df.to_csv(f_path, index=False)
        f_val = validate_dataset(f_df, require_target=True)
        assert f_val.is_valid, f"Validation failed for fixture {filename}: {f_val.issues}"
        fixture_hashes[filename] = {
            "rows": len(f_df),
            "sha256": file_sha256(f_path),
        }
        print(f"    Fixture {filename}: {len(f_df)} rows -> {f_path}")

    # Create metadata manifest
    metadata = {
        "dataset_name": "Credit Risk Tabular Monitoring Dataset",
        "version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "license": "CC-BY-4.0 (Synthetic for Educational Evaluation)",
        "features": {
            "numerical": NUMERICAL_FEATURES,
            "categorical": CATEGORICAL_FEATURES,
            "target": TARGET_COLUMN,
        },
        "partitions": {
            "train": {"rows": len(train_df), "sha256": file_sha256(train_path)},
            "validation": {"rows": len(val_df), "sha256": file_sha256(val_path)},
            "reference": {"rows": len(test_df), "sha256": file_sha256(test_path)},
        },
        "fixtures": fixture_hashes,
    }

    meta_path = base_dir / "metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    print(f"[*] Manifest written to {meta_path}")
    print("[SUCCESS] All datasets generated and validated successfully.")


if __name__ == "__main__":
    prepare_all_datasets()
