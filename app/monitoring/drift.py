"""Statistical Drift Detection Engine (MON-01).

Implements:
- FR-03: Comparing active input distributions against reference baseline.
- FR-08: Drift threshold evaluation (KS-test p-value < alpha, PSI >= threshold).
- NFR-09: Scalable, thread-safe distribution comparison using scipy.stats and numpy.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from scipy import stats

from app.config import settings
from app.data.schema import (
    CATEGORICAL_FEATURES,
    NUMERICAL_FEATURES,
)
from app.data.validation import validate_dataset
from app.monitoring.collector import telemetry_collector
from app.schemas.drift import (
    CategoricalDriftResult,
    DriftReportResponse,
    NumericalDriftResult,
)


class DriftAnalyzer:
    """Core statistical drift engine performing Kolmogorov-Smirnov and PSI analyses."""

    def __init__(
        self,
        reference_path: Optional[str] = None,
        ks_alpha: Optional[float] = None,
        psi_threshold: Optional[float] = None,
        psi_moderate_threshold: Optional[float] = None,
        min_sample_size: Optional[int] = None,
        num_bins: Optional[int] = None,
    ):
        self.reference_path = reference_path or settings.drift_reference_path
        self.ks_alpha = ks_alpha if ks_alpha is not None else settings.drift_ks_alpha
        self.psi_threshold = psi_threshold if psi_threshold is not None else settings.drift_psi_threshold
        self.psi_moderate_threshold = psi_moderate_threshold if psi_moderate_threshold is not None else settings.drift_psi_moderate_threshold
        self.min_sample_size = min_sample_size if min_sample_size is not None else settings.drift_min_sample_size
        self.num_bins = num_bins if num_bins is not None else settings.drift_num_bins

        self._reference_df: Optional[pd.DataFrame] = None
        self._latest_report: Optional[DriftReportResponse] = None
        self._load_reference_dataset()

    def _load_reference_dataset(self) -> None:
        """Load and validate the baseline reference dataset from disk."""
        path = Path(self.reference_path)
        if path.is_file():
            df = pd.read_csv(path)
            report = validate_dataset(df, require_target=False)
            if report.is_valid:
                self._reference_df = df

    @property
    def reference_df(self) -> pd.DataFrame:
        """Return the validated reference dataset, loading from disk if necessary."""
        if self._reference_df is None:
            self._load_reference_dataset()
            if self._reference_df is None:
                raise FileNotFoundError(f"Reference dataset not found at: {self.reference_path}")
        return self._reference_df

    def set_reference_dataframe(self, df: pd.DataFrame) -> None:
        """Inject an explicit reference dataframe (used for test isolation)."""
        report = validate_dataset(df, require_target=False)
        if not report.is_valid:
            raise ValueError(f"Supplied reference dataframe failed validation: {report.issues}")
        self._reference_df = df.copy()

    @staticmethod
    def calculate_numerical_psi(
        ref_series: pd.Series,
        cur_series: pd.Series,
        num_bins: int = 10,
        epsilon: float = 1e-4,
    ) -> float:
        """Compute Population Stability Index (PSI) for continuous features across reference quantile bins."""
        ref_clean = ref_series.dropna().to_numpy(dtype=float)
        cur_clean = cur_series.dropna().to_numpy(dtype=float)

        if len(ref_clean) == 0 or len(cur_clean) == 0:
            return 0.0

        quantiles = np.linspace(0, 100, num_bins + 1)
        bin_edges = np.percentile(ref_clean, quantiles)
        bin_edges = np.unique(bin_edges)

        # Ensure outer edges cover full support
        if len(bin_edges) < 2:
            return 0.0
        bin_edges[0] = -np.inf
        bin_edges[-1] = np.inf

        ref_counts, _ = np.histogram(ref_clean, bins=bin_edges)
        cur_counts, _ = np.histogram(cur_clean, bins=bin_edges)

        ref_prop = ref_counts / len(ref_clean)
        cur_prop = cur_counts / len(cur_clean)

        # Zero-frequency smoothing
        ref_prop = np.where(ref_prop == 0, epsilon, ref_prop)
        cur_prop = np.where(cur_prop == 0, epsilon, cur_prop)

        # Re-normalize
        ref_prop /= ref_prop.sum()
        cur_prop /= cur_prop.sum()

        psi = np.sum((cur_prop - ref_prop) * np.log(cur_prop / ref_prop))
        return float(np.round(max(0.0, psi), 4))

    @staticmethod
    def calculate_categorical_psi(
        ref_series: pd.Series,
        cur_series: pd.Series,
        epsilon: float = 1e-4,
    ) -> Tuple[float, Dict[str, Dict[str, float]], Optional[float], Optional[float]]:
        """Compute PSI and Chi-Square goodness-of-fit for categorical features."""
        ref_clean = ref_series.dropna().astype(str)
        cur_clean = cur_series.dropna().astype(str)

        all_categories = sorted(list(set(ref_clean.unique())))
        if not all_categories or len(cur_clean) == 0:
            return 0.0, {}, None, None

        ref_n = len(ref_clean)
        cur_n = len(cur_clean)

        ref_counts = [int((ref_clean == c).sum()) for c in all_categories]
        cur_counts = [int((cur_clean == c).sum()) for c in all_categories]

        # Account for potential unseen categories in current window
        unseen_cur = int((~cur_clean.isin(all_categories)).sum())
        ref_counts.append(0)
        cur_counts.append(unseen_cur)

        ref_prop = np.array(ref_counts, dtype=float) / ref_n
        cur_prop = np.array(cur_counts, dtype=float) / cur_n

        # Proportions record for reporting
        cat_props: Dict[str, Dict[str, float]] = {}
        for idx, cat in enumerate(all_categories):
            cat_props[cat] = {
                "reference": round(float(ref_prop[idx]), 4),
                "current": round(float(cur_prop[idx]), 4),
            }
        if unseen_cur > 0:
            cat_props["__UNSEEN__"] = {
                "reference": 0.0,
                "current": round(float(cur_prop[-1]), 4),
            }

        # Zero-frequency smoothing
        ref_prop = np.where(ref_prop == 0, epsilon, ref_prop)
        cur_prop = np.where(cur_prop == 0, epsilon, cur_prop)
        ref_prop /= ref_prop.sum()
        cur_prop /= cur_prop.sum()

        psi = np.sum((cur_prop - ref_prop) * np.log(cur_prop / ref_prop))

        # Chi-Square test over known categories
        chi2_stat = None
        chi2_p = None
        try:
            expected_freq = ref_prop[:len(all_categories)] * cur_n
            observed_freq = np.array(cur_counts[:len(all_categories)], dtype=float)
            if expected_freq.sum() > 0 and (expected_freq > 0).all():
                res = stats.chisquare(f_obs=observed_freq, f_exp=expected_freq)
                chi2_stat = round(float(res.statistic), 4)
                chi2_p = round(float(res.pvalue), 6)
        except Exception:
            pass

        return float(np.round(max(0.0, psi), 4)), cat_props, chi2_stat, chi2_p

    def evaluate_drift(
        self,
        current_df: pd.DataFrame,
        update_telemetry_status: bool = True,
    ) -> DriftReportResponse:
        """Execute full statistical drift analysis comparing current window against baseline reference."""
        # 1. Validation and sample size checks
        curr_len = len(current_df) if current_df is not None else 0
        ref_df = self.reference_df
        ref_len = len(ref_df)

        now_iso = datetime.now(timezone.utc).isoformat()
        thresholds = {
            "ks_alpha": self.ks_alpha,
            "psi_threshold": self.psi_threshold,
            "psi_moderate_threshold": self.psi_moderate_threshold,
            "min_sample_size": self.min_sample_size,
        }

        if curr_len < self.min_sample_size:
            if update_telemetry_status:
                telemetry_collector.set_drift_status("INSUFFICIENT_DATA")

            return DriftReportResponse(
                evaluation_timestamp=now_iso,
                overall_drift_status="INSUFFICIENT_DATA",
                is_drifted=False,
                reference_sample_count=ref_len,
                current_sample_count=curr_len,
                drifted_features=[],
                numerical_drift={},
                categorical_drift={},
                thresholds=thresholds,
                reference_dataset_path=str(self.reference_path),
            )

        numerical_results: Dict[str, NumericalDriftResult] = {}
        categorical_results: Dict[str, CategoricalDriftResult] = {}
        drifted_features: List[str] = []

        # 2. Evaluate Continuous Features with KS Test & Numerical PSI
        for col in NUMERICAL_FEATURES:
            if col in current_df.columns and col in ref_df.columns:
                ref_series = ref_df[col].dropna()
                cur_series = current_df[col].dropna()

                # Two-sample Kolmogorov-Smirnov test
                ks_res = stats.ks_2samp(ref_series, cur_series)
                ks_stat = round(float(ks_res.statistic), 4)
                ks_p = round(float(ks_res.pvalue), 6)
                ks_drift = bool(ks_p < self.ks_alpha)

                # Numerical Population Stability Index
                psi_val = self.calculate_numerical_psi(ref_series, cur_series, num_bins=self.num_bins)
                psi_drift = bool(psi_val >= self.psi_threshold)

                drift_detected = ks_drift or psi_drift
                if drift_detected:
                    drifted_features.append(col)

                numerical_results[col] = NumericalDriftResult(
                    feature_name=col,
                    ks_statistic=ks_stat,
                    ks_p_value=ks_p,
                    ks_drift=ks_drift,
                    psi_value=psi_val,
                    psi_drift=psi_drift,
                    drift_detected=drift_detected,
                    reference_mean=round(float(ref_series.mean()), 2),
                    current_mean=round(float(cur_series.mean()), 2),
                    reference_std=round(float(ref_series.std()), 2),
                    current_std=round(float(cur_series.std()), 2),
                )

        # 3. Evaluate Categorical Features with Categorical PSI & Chi-Square
        for col in CATEGORICAL_FEATURES:
            if col in current_df.columns and col in ref_df.columns:
                ref_series = ref_df[col].dropna().astype(str)
                cur_series = current_df[col].dropna().astype(str)

                psi_val, cat_props, chi2_stat, chi2_p = self.calculate_categorical_psi(ref_series, cur_series)
                psi_drift = bool(psi_val >= self.psi_threshold)
                chi2_drift = bool(chi2_p is not None and chi2_p < self.ks_alpha)

                drift_detected = psi_drift or chi2_drift
                if drift_detected:
                    drifted_features.append(col)

                categorical_results[col] = CategoricalDriftResult(
                    feature_name=col,
                    psi_value=psi_val,
                    psi_drift=psi_drift,
                    chi2_statistic=chi2_stat,
                    chi2_p_value=chi2_p,
                    drift_detected=drift_detected,
                    category_proportions=cat_props,
                )

        # 4. Overall Drift Determination (Acceptance Criteria 3 & 4)
        is_drifted = len(drifted_features) > 0
        overall_status = "DRIFT_DETECTED" if is_drifted else "STABLE"

        # Update global telemetry collector state
        if update_telemetry_status:
            telemetry_collector.set_drift_status(overall_status)

        report = DriftReportResponse(
            evaluation_timestamp=now_iso,
            overall_drift_status=overall_status,
            is_drifted=is_drifted,
            reference_sample_count=ref_len,
            current_sample_count=curr_len,
            drifted_features=sorted(list(set(drifted_features))),
            numerical_drift=numerical_results,
            categorical_drift=categorical_results,
            thresholds=thresholds,
            reference_dataset_path=str(self.reference_path),
        )
        self._latest_report = report
        return report

    def get_latest_report(self) -> Optional[DriftReportResponse]:
        """Return the most recently computed drift report."""
        return self._latest_report


# Global singleton instance
drift_analyzer = DriftAnalyzer()
