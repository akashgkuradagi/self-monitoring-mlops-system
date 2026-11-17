"""Deterministic Dataset and Drift Fixture Generator (DATA-01).

Generates:
1. Reference baseline tabular credit risk dataset (deterministic, documented distributions).
2. Train, validation, and reference test partitions (zero target leakage).
3. Synthetic drift fixtures:
   - Stable distribution (TC-03: No drift)
   - Numerical feature drift (TC-04: KS-test p < 0.05)
   - Categorical feature drift (TC-04: Chi-square p < 0.05)
   - Sudden distribution shift (abrupt changepoint)
   - Gradual distribution shift (progressive step-wise drift)
"""

from typing import Tuple
import numpy as np
import pandas as pd


def generate_credit_risk_dataset(n_samples: int = 5000, seed: int = 42) -> pd.DataFrame:
    """Generate a reproducible tabular credit risk reference dataset.

    Distributions:
    - age: Normal(38, 11), bounded [18, 85]
    - income: LogNormal(10.8, 0.5), bounded [15k, 300k]
    - loan_amount: Normal(15000, 7000), bounded [1k, 45k]
    - credit_score: Normal(675, 50), bounded [350, 850]
    - debt_to_income: Normal ratio (0.28, 0.10), bounded [0.02, 0.95]
    - employment_length: Exponential(scale=6), bounded [0, 40]
    - loan_intent: Categorical multinomial
    - home_ownership: Categorical multinomial
    - previous_default: Categorical binary ('Y', 'N')
    - loan_status (Target): Logistic probability modeled from credit score, DTI, and default history.
    """
    rng = np.random.default_rng(seed)

    age = np.clip(np.round(rng.normal(38.0, 11.0, n_samples)), 18.0, 85.0)
    income = np.clip(np.round(rng.lognormal(10.8, 0.5, n_samples), 2), 15000.0, 300000.0)
    loan_amount = np.clip(np.round(rng.normal(15000.0, 7000.0, n_samples), 2), 1000.0, 45000.0)
    credit_score = np.clip(np.round(rng.normal(675.0, 50.0, n_samples)), 350.0, 850.0)
    debt_to_income = np.clip(np.round(rng.normal(0.28, 0.10, n_samples), 4), 0.02, 0.95)
    employment_length = np.clip(np.round(rng.exponential(scale=6.0, size=n_samples), 1), 0.0, 40.0)

    loan_intents = ["PERSONAL", "EDUCATION", "MEDICAL", "VENTURE", "HOMEIMPROVEMENT", "DEBTCONSOLIDATION"]
    intent_probs = [0.22, 0.20, 0.18, 0.15, 0.15, 0.10]
    loan_intent = rng.choice(loan_intents, size=n_samples, p=intent_probs)

    home_types = ["RENT", "MORTGAGE", "OWN", "OTHER"]
    home_probs = [0.48, 0.40, 0.10, 0.02]
    home_ownership = rng.choice(home_types, size=n_samples, p=home_probs)

    prev_defaults = ["N", "Y"]
    default_probs = [0.82, 0.18]
    previous_default = rng.choice(prev_defaults, size=n_samples, p=default_probs)

    # Compute realistic latent default probability (Log-Odds)
    z = (
        -1.5
        - 0.012 * (credit_score - 675.0)
        + 3.5 * (debt_to_income - 0.28)
        - 0.000008 * (income - 50000.0)
        + 1.2 * (previous_default == "Y").astype(float)
        + 0.4 * (home_ownership == "RENT").astype(float)
        + 0.5 * (loan_intent == "DEBTCONSOLIDATION").astype(float)
    )
    p_default = 1.0 / (1.0 + np.exp(-z))
    loan_status = (rng.uniform(size=n_samples) < p_default).astype(int)

    df = pd.DataFrame({
        "age": age,
        "income": income,
        "loan_amount": loan_amount,
        "credit_score": credit_score,
        "debt_to_income": debt_to_income,
        "employment_length": employment_length,
        "loan_intent": loan_intent,
        "home_ownership": home_ownership,
        "previous_default": previous_default,
        "loan_status": loan_status,
    })

    # Ensure uniqueness (no duplicates)
    df = df.drop_duplicates().reset_index(drop=True)
    return df


def split_dataset(
    df: pd.DataFrame,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Deterministically partition dataset into train, validation, and test splits (zero leakage)."""
    assert round(train_ratio + val_ratio + test_ratio, 5) == 1.0, "Split ratios must sum to 1.0"

    n = len(df)
    rng = np.random.default_rng(seed)
    shuffled_indices = rng.permutation(n)

    train_end = int(n * train_ratio)
    val_end = train_end + int(n * val_ratio)

    train_idx = shuffled_indices[:train_end]
    val_idx = shuffled_indices[train_end:val_end]
    test_idx = shuffled_indices[val_end:]

    train_df = df.iloc[train_idx].reset_index(drop=True)
    val_df = df.iloc[val_idx].reset_index(drop=True)
    test_df = df.iloc[test_idx].reset_index(drop=True)

    return train_df, val_df, test_df


# ==============================================================================
# Synthetic Drift Fixture Generators (TC-03, TC-04, FR-03, FR-08)
# ==============================================================================


def generate_stable_fixture(n_samples: int = 1000, seed: int = 101) -> pd.DataFrame:
    """TC-03: Generate stable test window matching baseline reference distribution (No Drift)."""
    df = generate_credit_risk_dataset(n_samples=n_samples, seed=seed)
    return df


def generate_numerical_drift_fixture(n_samples: int = 1000, seed: int = 202) -> pd.DataFrame:
    """TC-04: Generate covariate shifted window with significant numerical drift (p < 0.05).

    Shifts:
    - income: mean drops by 30% (economic recession)
    - credit_score: shifts down by 45 points
    - debt_to_income: shifts up by 35%
    """
    df = generate_credit_risk_dataset(n_samples=n_samples, seed=seed)

    # Inject covariate shift
    df["income"] = np.clip(np.round(df["income"] * 0.70, 2), 15000.0, 300000.0)
    df["credit_score"] = np.clip(np.round(df["credit_score"] - 45.0), 350.0, 850.0)
    df["debt_to_income"] = np.clip(np.round(df["debt_to_income"] * 1.35, 4), 0.02, 0.95)

    return df


def generate_categorical_drift_fixture(n_samples: int = 1000, seed: int = 303) -> pd.DataFrame:
    """TC-04: Generate dataset with categorical concept drift (p < 0.05 on Chi-square).

    Shifts:
    - loan_intent: 60% becomes 'DEBTCONSOLIDATION', 25% 'MEDICAL'
    - home_ownership: 80% becomes 'RENT'
    - previous_default: default rate rises from 18% to 50%
    """
    df = generate_credit_risk_dataset(n_samples=n_samples, seed=seed)
    rng = np.random.default_rng(seed)

    intents = ["DEBTCONSOLIDATION", "MEDICAL", "PERSONAL", "EDUCATION", "VENTURE", "HOMEIMPROVEMENT"]
    intent_p = [0.60, 0.25, 0.05, 0.04, 0.03, 0.03]
    df["loan_intent"] = rng.choice(intents, size=len(df), p=intent_p)

    homes = ["RENT", "MORTGAGE", "OWN", "OTHER"]
    home_p = [0.80, 0.15, 0.04, 0.01]
    df["home_ownership"] = rng.choice(homes, size=len(df), p=home_p)

    defaults = ["Y", "N"]
    default_p = [0.50, 0.50]
    df["previous_default"] = rng.choice(defaults, size=len(df), p=default_p)

    return df


def generate_sudden_drift_fixture(
    n_samples: int = 1000,
    changepoint: int = 500,
    seed: int = 404,
) -> pd.DataFrame:
    """Generate time-series stream with an abrupt changepoint at row `changepoint`."""
    stable_half = generate_stable_fixture(n_samples=changepoint, seed=seed)
    shifted_half = generate_numerical_drift_fixture(n_samples=n_samples - changepoint, seed=seed + 1)

    combined = pd.concat([stable_half, shifted_half], ignore_index=True)
    return combined


def generate_gradual_drift_fixture(
    n_samples: int = 1000,
    n_steps: int = 5,
    seed: int = 505,
) -> pd.DataFrame:
    """Generate dataset with progressive step-wise shift across consecutive batches."""
    step_size = n_samples // n_steps
    batches = []

    for step in range(n_steps):
        drift_factor = step / (n_steps - 1)  # 0.0 -> 1.0
        batch = generate_credit_risk_dataset(n_samples=step_size, seed=seed + step)

        # Incrementally scale shift
        batch["income"] = np.clip(np.round(batch["income"] * (1.0 - 0.30 * drift_factor), 2), 15000.0, 300000.0)
        batch["credit_score"] = np.clip(np.round(batch["credit_score"] - (45.0 * drift_factor)), 350.0, 850.0)
        batch["debt_to_income"] = np.clip(np.round(batch["debt_to_income"] * (1.0 + 0.35 * drift_factor), 4), 0.02, 0.95)

        batches.append(batch)

    return pd.concat(batches, ignore_index=True)
