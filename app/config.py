"""Application Configuration Module."""

from typing import Dict, List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """System settings and environment configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Self-Monitoring MLOps System"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False
    host: str = "0.0.0.0"  # nosec B104
    port: int = 8000

    # API Routing
    api_v1_prefix: str = "/api/v1"

    # Telemetry and Buffer Limits
    rolling_window_size: int = 1000
    max_request_size_bytes: int = 1_048_576  # 1 MB (SR-04, SR-06, NFR-12)

    # Active Baseline Model Identification
    default_model_version: str = "baseline-v1"

    # Security & Authentication (SEC-01, SR-01, SR-02)
    jwt_secret_key: str = "change-this-insecure-default-key-to-a-32-byte-hex-string"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    # Bootstrap API Keys (format: key1:Role1,key2:Role2)
    api_keys: str = "test-viewer-key:Viewer,test-engineer-key:ML_Engineer,test-admin-key:Administrator"

    # CORS Allowed Origins (comma-separated)
    cors_allowed_origins: str = "http://localhost:3000,http://localhost:8000"

    # Audit Logging Storage (SEC-02, FR-11, SR-05)
    audit_db_path: str = "telemetry.db"

    # MLflow Tracking & Registry Configuration (ML-01, FR-04, NFR-04)
    mlflow_tracking_uri: str = "sqlite:///mlflow.db"
    mlflow_experiment_name: str = "production-tabular-monitoring"
    mlflow_model_name: str = "ProductionClassifier"
    model_random_seed: int = 42
    model_dir: str = "models"

    # Statistical Drift Monitoring Configuration (MON-01, FR-03, FR-08)
    drift_ks_alpha: float = 0.05
    drift_psi_threshold: float = 0.25
    drift_psi_moderate_threshold: float = 0.10
    drift_min_sample_size: int = 50
    drift_reference_path: str = "data/reference/reference.csv"
    drift_num_bins: int = 10

    # Multi-Metric Alerting Configuration (ALERT-01, FR-08, NFR-07)
    alerts_db_path: str = "telemetry.db"
    alert_dedup_window_seconds: int = 300  # 5-minute rolling window (Acceptance Criteria 2)
    alert_error_rate_threshold: float = 0.05  # 5% error rate limit
    alert_latency_p95_threshold_ms: float = 500.0  # 500ms p95 latency limit (NFR-01)
    alert_latency_p95_warning_ms: float = 300.0  # 300ms p95 warning threshold

    # Recovery Policy & Validation Gates Configuration (REC-01, FR-09, FR-10, NFR-10, TC-16)
    recovery_cooldown_seconds: int = 600  # 10-minute suppression window (TC-16)
    candidate_latency_threshold_ms: float = 500.0  # 500ms p95 validation gate limit
    candidate_min_accuracy: float = 0.70  # Minimum accuracy gate threshold
    candidate_f1_margin: float = 0.02  # Maximum acceptable F1 degradation below champion

    # Post-Promotion Health Guard & Rollback Configuration (REC-02, FR-12, NFR-02, TC-08, TC-20)
    canary_window_seconds: int = 300  # 5-minute post-promotion canary monitoring window
    canary_min_samples: int = 10  # Minimum samples required for confident canary health evaluation
    post_promotion_error_threshold: float = 0.10  # 10% error rate breach triggers auto-rollback (TC-20)
    post_promotion_latency_threshold_ms: float = 500.0  # 500ms p95 latency breach triggers auto-rollback
    post_promotion_warning_error_rate: float = 0.05  # 5% error rate warning threshold
    post_promotion_warning_latency_ms: float = 300.0  # 300ms p95 latency warning threshold

    @property
    def parsed_api_keys(self) -> Dict[str, str]:
        """Parse raw api_keys string into key -> role mapping."""
        mapping = {}
        if not self.api_keys:
            return mapping
        for pair in self.api_keys.split(","):
            pair = pair.strip()
            if ":" in pair:
                k, r = pair.split(":", 1)
                mapping[k.strip()] = r.strip()
        return mapping

    @property
    def parsed_cors_origins(self) -> List[str]:
        """Parse comma-separated origins string into list of origins."""
        if not self.cors_allowed_origins:
            return ["*"]
        origins = [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
        return origins if origins else ["*"]


settings = Settings()
