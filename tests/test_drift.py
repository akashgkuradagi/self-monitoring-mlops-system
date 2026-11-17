"""Unit and Integration Tests for Statistical Drift Detection Engine (MON-01).

Validates:
- FR-03: Comparing active input distributions against baseline reference.
- FR-08: KS-test (p-value < alpha) and PSI (PSI >= threshold) drift decisions.
- NFR-09: Scalable, deterministic statistical comparison without memory leaks.
- TC-03: Baseline reference and stable fixture comparison producing STABLE status.
- TC-04: Numerical and categorical drift fixture detection producing DRIFT_DETECTED.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from starlette.testclient import TestClient

from app.api.routes import DEMO_USERS
from app.config import settings
from app.main import app
from app.monitoring.collector import telemetry_collector
from app.monitoring.drift import DriftAnalyzer, drift_analyzer
from app.security.auth import create_access_token


@pytest.fixture(autouse=True)
def reset_telemetry():
    """Reset telemetry buffer between tests for clean isolation."""
    telemetry_collector.reset()
    yield
    telemetry_collector.reset()


@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Auth headers for different RBAC roles."""
    headers = {}
    for user, (_, role) in DEMO_USERS.items():
        token = create_access_token(subject=user, role=role)
        headers[role.value] = {"Authorization": f"Bearer {token}"}
    return headers


def test_reference_dataset_loaded_properly():
    """Verify reference dataset is loaded and contains expected schema columns."""
    ref_df = drift_analyzer.reference_df
    assert len(ref_df) == 750
    assert "income" in ref_df.columns
    assert "loan_intent" in ref_df.columns
    assert "loan_status" in ref_df.columns


def test_stable_fixture_produces_no_drift():
    """TC-03: Test that stable fixture compared against reference exhibits no statistical drift."""
    stable_path = Path("data/fixtures/stable_fixture.csv")
    assert stable_path.is_file(), "stable_fixture.csv must exist"
    stable_df = pd.read_csv(stable_path)

    report = drift_analyzer.evaluate_drift(current_df=stable_df)

    assert report.overall_drift_status == "STABLE"
    assert report.is_drifted is False
    assert len(report.drifted_features) == 0
    assert report.current_sample_count == len(stable_df)

    # Check that numerical KS test p-values are all >= 0.05 and PSI < 0.10
    for feat_name, num_res in report.numerical_drift.items():
        assert num_res.ks_drift is False, f"Feature {feat_name} unexpectedly drifted on KS test (p={num_res.ks_p_value})"
        assert num_res.psi_drift is False, f"Feature {feat_name} unexpectedly drifted on PSI (psi={num_res.psi_value})"
        assert num_res.psi_value < settings.drift_psi_moderate_threshold

    # Check categorical PSI values
    for feat_name, cat_res in report.categorical_drift.items():
        assert cat_res.psi_drift is False, f"Feature {feat_name} unexpectedly drifted on categorical PSI"
        assert cat_res.psi_value < settings.drift_psi_moderate_threshold


def test_numerical_drift_detection():
    """TC-04: Test that numerical drift fixture is detected with KS test and numerical PSI."""
    num_drift_path = Path("data/fixtures/numerical_drift_fixture.csv")
    assert num_drift_path.is_file(), "numerical_drift_fixture.csv must exist"
    num_df = pd.read_csv(num_drift_path)

    report = drift_analyzer.evaluate_drift(current_df=num_df)

    assert report.overall_drift_status == "DRIFT_DETECTED"
    assert report.is_drifted is True
    assert len(report.drifted_features) > 0

    # Specifically shifted numerical features: income, credit_score, debt_to_income
    assert "income" in report.drifted_features
    assert "credit_score" in report.drifted_features
    assert "debt_to_income" in report.drifted_features

    income_res = report.numerical_drift["income"]
    assert income_res.drift_detected is True
    assert income_res.ks_p_value < settings.drift_ks_alpha
    assert income_res.psi_value >= settings.drift_psi_threshold

    credit_res = report.numerical_drift["credit_score"]
    assert credit_res.drift_detected is True
    assert credit_res.ks_p_value < settings.drift_ks_alpha
    assert credit_res.psi_value >= settings.drift_psi_threshold


def test_categorical_drift_detection():
    """TC-04: Test that categorical drift fixture is detected via categorical PSI."""
    cat_drift_path = Path("data/fixtures/categorical_drift_fixture.csv")
    assert cat_drift_path.is_file(), "categorical_drift_fixture.csv must exist"
    cat_df = pd.read_csv(cat_drift_path)

    report = drift_analyzer.evaluate_drift(current_df=cat_df)

    assert report.overall_drift_status == "DRIFT_DETECTED"
    assert report.is_drifted is True

    # Shifted categorical features: loan_intent, home_ownership, previous_default
    assert "loan_intent" in report.drifted_features
    assert "home_ownership" in report.drifted_features
    assert "previous_default" in report.drifted_features

    intent_res = report.categorical_drift["loan_intent"]
    assert intent_res.drift_detected is True
    assert intent_res.psi_value >= settings.drift_psi_threshold
    assert intent_res.category_proportions is not None


def test_sudden_and_gradual_drift_fixtures():
    """Verify sudden step shift and gradual distribution drift fixtures trigger detection."""
    sudden_path = Path("data/fixtures/sudden_drift_fixture.csv")
    gradual_path = Path("data/fixtures/gradual_drift_fixture.csv")

    assert sudden_path.is_file()
    assert gradual_path.is_file()

    sudden_report = drift_analyzer.evaluate_drift(current_df=pd.read_csv(sudden_path))
    assert sudden_report.overall_drift_status == "DRIFT_DETECTED"
    assert sudden_report.is_drifted is True
    assert len(sudden_report.drifted_features) >= 3

    gradual_report = drift_analyzer.evaluate_drift(current_df=pd.read_csv(gradual_path))
    assert gradual_report.overall_drift_status == "DRIFT_DETECTED"
    assert gradual_report.is_drifted is True
    assert len(gradual_report.drifted_features) >= 3


def test_psi_zero_frequency_smoothing_and_unseen_categories():
    """Test Laplace smoothing and unseen category handling in PSI computation."""
    ref_series_num = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0] * 20)
    # Current series has numbers concentrated entirely in one bin and missing others
    cur_series_num = pd.Series([10.0, 10.1, 10.2] * 20)

    psi_num = DriftAnalyzer.calculate_numerical_psi(ref_series_num, cur_series_num, num_bins=5)
    assert not np.isnan(psi_num)
    assert not np.isinf(psi_num)
    assert psi_num > 0.0

    # Categorical PSI with novel unseen category
    ref_series_cat = pd.Series(["RENT", "OWN", "MORTGAGE"] * 20)
    cur_series_cat = pd.Series(["RENT", "UNSEEN_VALUE", "ANOTHER_NOVEL"] * 20)

    psi_cat, props, _, _ = DriftAnalyzer.calculate_categorical_psi(ref_series_cat, cur_series_cat)
    assert not np.isnan(psi_cat)
    assert not np.isinf(psi_cat)
    assert psi_cat > 0.0
    assert "__UNSEEN__" in props


def test_insufficient_sample_size_handling():
    """Verify that sample sizes below min_sample_size return INSUFFICIENT_DATA."""
    small_df = pd.read_csv("data/fixtures/stable_fixture.csv").iloc[:25]
    assert len(small_df) < settings.drift_min_sample_size

    report = drift_analyzer.evaluate_drift(current_df=small_df)

    assert report.overall_drift_status == "INSUFFICIENT_DATA"
    assert report.is_drifted is False
    assert report.current_sample_count == 25
    assert len(report.drifted_features) == 0

    # Ensure telemetry summary reflects INSUFFICIENT_DATA
    summary = telemetry_collector.get_metrics_summary()
    assert summary.drift_status == "INSUFFICIENT_DATA"


def test_drift_api_rbac_permissions(client, auth_headers):
    """Test RBAC enforcement on drift evaluation and reporting endpoints."""
    # 1. Unauthenticated evaluate -> 401
    resp_unauth = client.post("/api/v1/monitoring/drift/evaluate")
    assert resp_unauth.status_code == 401

    # 2. Viewer role cannot evaluate -> 403
    resp_viewer_eval = client.post(
        "/api/v1/monitoring/drift/evaluate",
        headers=auth_headers["Viewer"],
        json={"dataset_path": "data/fixtures/stable_fixture.csv"},
    )
    assert resp_viewer_eval.status_code == 403

    # 3. ML_Engineer can evaluate -> 200
    resp_engineer_eval = client.post(
        "/api/v1/monitoring/drift/evaluate",
        headers=auth_headers["ML_Engineer"],
        json={"dataset_path": "data/fixtures/stable_fixture.csv"},
    )
    assert resp_engineer_eval.status_code == 200
    data = resp_engineer_eval.json()
    assert data["overall_drift_status"] == "STABLE"

    # 4. Viewer can retrieve drift report -> 200
    resp_viewer_report = client.get(
        "/api/v1/monitoring/drift/report",
        headers=auth_headers["Viewer"],
    )
    assert resp_viewer_report.status_code == 200
    report_data = resp_viewer_report.json()
    assert report_data["overall_drift_status"] == "STABLE"


def test_drift_evaluation_updates_telemetry_summary(client, auth_headers):
    """Test that evaluating a drifted dataset dynamically updates /api/v1/metrics/summary."""
    # Evaluate numerical drift fixture
    resp = client.post(
        "/api/v1/monitoring/drift/evaluate",
        headers=auth_headers["ML_Engineer"],
        json={"dataset_path": "data/fixtures/numerical_drift_fixture.csv"},
    )
    assert resp.status_code == 200
    assert resp.json()["overall_drift_status"] == "DRIFT_DETECTED"

    # Verify /api/v1/metrics/summary reflects DRIFT_DETECTED
    summary_resp = client.get("/api/v1/metrics/summary")
    assert summary_resp.status_code == 200
    assert summary_resp.json()["drift_status"] == "DRIFT_DETECTED"


def test_drift_api_evaluate_with_records_payload(client, auth_headers):
    """Test evaluating drift directly via records batch payload."""
    stable_df = pd.read_csv("data/fixtures/stable_fixture.csv")
    records = stable_df.to_dict(orient="records")

    resp = client.post(
        "/api/v1/monitoring/drift/evaluate",
        headers=auth_headers["ML_Engineer"],
        json={"records": records},
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["current_sample_count"] == len(stable_df)
    assert res_data["overall_drift_status"] == "STABLE"
    assert len(res_data["drifted_features"]) == 0


def test_drift_api_evaluate_missing_file_404(client, auth_headers):
    """Test 404 response when non-existent dataset file is supplied."""
    resp = client.post(
        "/api/v1/monitoring/drift/evaluate",
        headers=auth_headers["ML_Engineer"],
        json={"dataset_path": "data/fixtures/non_existent.csv"},
    )
    assert resp.status_code == 404
