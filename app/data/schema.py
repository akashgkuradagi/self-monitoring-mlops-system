"""Dataset Schema Definitions and Constraints (DATA-01).

Defines standard tabular credit risk schema, column classifications,
allowable ranges, and categories for validation and modeling.
"""

from typing import Dict, List, Set

TARGET_COLUMN: str = "loan_status"
VALID_TARGET_VALUES: Set[int] = {0, 1}

NUMERICAL_FEATURES: List[str] = [
    "age",
    "income",
    "loan_amount",
    "credit_score",
    "debt_to_income",
    "employment_length",
]

CATEGORICAL_FEATURES: List[str] = [
    "loan_intent",
    "home_ownership",
    "previous_default",
]

FEATURE_COLUMNS: List[str] = NUMERICAL_FEATURES + CATEGORICAL_FEATURES
ALL_DATASET_COLUMNS: List[str] = FEATURE_COLUMNS + [TARGET_COLUMN]

# Allowable numerical bounds for validation
NUMERICAL_BOUNDS: Dict[str, tuple[float, float]] = {
    "age": (18.0, 100.0),
    "income": (1000.0, 2_000_000.0),
    "loan_amount": (500.0, 500_000.0),
    "credit_score": (300.0, 850.0),
    "debt_to_income": (0.0, 2.0),
    "employment_length": (0.0, 60.0),
}

# Allowable categorical sets for validation
CATEGORICAL_CATEGORIES: Dict[str, Set[str]] = {
    "loan_intent": {
        "PERSONAL",
        "EDUCATION",
        "MEDICAL",
        "VENTURE",
        "HOMEIMPROVEMENT",
        "DEBTCONSOLIDATION",
    },
    "home_ownership": {"RENT", "MORTGAGE", "OWN", "OTHER"},
    "previous_default": {"Y", "N"},
}
