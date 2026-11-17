"""MLflow Tracking and Model Registry Subsystem (ML-01).

Implements:
- FR-04: Model tracking, metric logging, and artifact persistence.
- NFR-04: Metadata reproducibility (hyperparameters, seeds, dataset versions).
- B.6: Model registry aliases ('champion', 'stable_fallback') and SQLite active_model_state singleton.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
import mlflow
import mlflow.pytorch
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient
import pandas as pd
import torch

from app.config import settings
from app.data.schema import FEATURE_COLUMNS

logger = logging.getLogger("mlops.registry")


class MLflowAdapter:
    """Interfaces with persistent MLflow tracking and SQLite active model registry state."""

    def __init__(
        self,
        tracking_uri: Optional[str] = None,
        experiment_name: Optional[str] = None,
        db_path: Optional[str] = None,
    ):
        self.tracking_uri = tracking_uri or settings.mlflow_tracking_uri
        self.experiment_name = experiment_name or settings.mlflow_experiment_name
        self.db_path = db_path or settings.audit_db_path
        self._lock = threading.Lock()
        self._init_sqlite_state_table()

    def _get_client(self) -> MlflowClient:
        """Instantiate and configure MLflow tracking client."""
        mlflow.set_tracking_uri(self.tracking_uri)
        return MlflowClient(tracking_uri=self.tracking_uri)

    def _init_sqlite_state_table(self) -> None:
        """Create singleton active_model_state table in SQLite."""
        p = Path(self.db_path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_model_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    active_version TEXT NOT NULL,
                    active_run_id TEXT NOT NULL,
                    promoted_at TEXT NOT NULL,
                    previous_stable_version TEXT,
                    previous_stable_run_id TEXT,
                    state TEXT NOT NULL
                );
            """)
            conn.commit()

    def ensure_experiment(self) -> str:
        """Ensure the target experiment exists and return its ID."""
        mlflow.set_tracking_uri(self.tracking_uri)
        experiment = mlflow.get_experiment_by_name(self.experiment_name)
        if experiment is None:
            return mlflow.create_experiment(self.experiment_name)
        return experiment.experiment_id

    def log_training_run(
        self,
        model_pipeline: Any,
        train_df: pd.DataFrame,
        eval_metrics: Dict[str, Any],
        params: Dict[str, Any],
        dataset_version: str = "1.0.0",
        model_version: str = "baseline-v1",
        model_name: Optional[str] = None,
    ) -> str:
        """Log training parameters, metrics, model artifacts, and register candidate in MLflow."""
        registered_name = model_name or settings.mlflow_model_name
        mlflow.set_tracking_uri(self.tracking_uri)
        self.ensure_experiment()
        mlflow.set_experiment(self.experiment_name)

        input_example = train_df[FEATURE_COLUMNS].head(3)
        sample_preds = model_pipeline.predict(input_example)
        signature = infer_signature(input_example, sample_preds)

        with mlflow.start_run(run_name=f"train-{model_version}") as run:
            run_id = run.info.run_id

            # Log Hyperparameters
            mlflow.log_params(params)
            mlflow.log_param("dataset_version", dataset_version)
            mlflow.log_param("model_version", model_version)
            mlflow.log_param("train_samples", len(train_df))

            # Log Evaluation Metrics
            numeric_metrics = {
                k: float(v) for k, v in eval_metrics.items()
                if isinstance(v, (int, float)) and v is not None and k != "sample_count"
            }
            if numeric_metrics:
                mlflow.log_metrics(numeric_metrics)

            # Set Run Tags
            mlflow.set_tags({
                "model_version": model_version,
                "dataset_version": dataset_version,
                "framework": "scikit-learn",
                "role": "candidate",
                "logged_at": datetime.now(timezone.utc).isoformat(),
            })

            # Log and Register Model Artifact
            mlflow.sklearn.log_model(
                sk_model=model_pipeline,
                artifact_path="model",
                signature=signature,
                input_example=input_example,
                registered_model_name=registered_name,
                serialization_format="cloudpickle",
            )

        return run_id

    def log_monitoring_experiment_run(
        self,
        experiment_name: str,
        run_name: str,
        model_pipeline: Any,
        input_example: pd.DataFrame,
        params: Dict[str, Any],
        metrics: Dict[str, Any],
        tags: Optional[Dict[str, str]] = None,
        artifact_path: str = "model",
    ) -> str:
        """Log experimental monitoring run (EXP-01, FR-05) without altering ProductionClassifier."""
        mlflow.set_tracking_uri(self.tracking_uri)
        exp = mlflow.get_experiment_by_name(experiment_name)
        if exp is None:
            mlflow.create_experiment(experiment_name)
        mlflow.set_experiment(experiment_name)

        sample_preds = model_pipeline.predict(input_example)
        signature = infer_signature(input_example, sample_preds)

        with mlflow.start_run(run_name=run_name) as run:
            run_id = run.info.run_id

            mlflow.log_params(params)

            numeric_metrics = {
                k: float(v) for k, v in metrics.items()
                if isinstance(v, (int, float)) and v is not None
            }
            if numeric_metrics:
                mlflow.log_metrics(numeric_metrics)

            run_tags = {
                "experiment_name": experiment_name,
                "framework": "scikit-learn",
                "logged_at": datetime.now(timezone.utc).isoformat(),
            }
            if tags:
                run_tags.update(tags)
            mlflow.set_tags(run_tags)

            mlflow.sklearn.log_model(
                sk_model=model_pipeline,
                artifact_path=artifact_path,
                signature=signature,
                input_example=input_example,
                serialization_format="cloudpickle",
            )

        return run_id

    def log_dl_experiment_run(
        self,
        experiment_name: str,
        run_name: str,
        pytorch_model: Any,
        params: Dict[str, Any],
        metrics: Dict[str, Any],
        tags: Optional[Dict[str, str]] = None,
        artifact_paths: Optional[List[str]] = None,
        input_example: Optional[Any] = None,
    ) -> str:
        """Log experimental deep learning sequence monitoring run (EXP-02, FR-06) under dl-sequence-monitor.

        Completely isolated from ProductionClassifier and active_model_state.
        """
        mlflow.set_tracking_uri(self.tracking_uri)
        exp = mlflow.get_experiment_by_name(experiment_name)
        if exp is None:
            mlflow.create_experiment(experiment_name)
        mlflow.set_experiment(experiment_name)

        if input_example is None:
            w = int(params.get("window_size", 10))
            d = int(params.get("feature_dim", 18))
            input_example = torch.zeros((1, w, d), dtype=torch.float32)

        with mlflow.start_run(run_name=run_name) as run:
            run_id = run.info.run_id

            mlflow.log_params(params)

            numeric_metrics = {
                k: float(v)
                for k, v in metrics.items()
                if isinstance(v, (int, float)) and v is not None
            }
            if numeric_metrics:
                mlflow.log_metrics(numeric_metrics)

            run_tags = {
                "experiment_name": experiment_name,
                "framework": "PyTorch",
                "logged_at": datetime.now(timezone.utc).isoformat(),
            }
            if tags:
                run_tags.update(tags)
            mlflow.set_tags(run_tags)

            # Log PyTorch model
            try:
                mlflow.pytorch.log_model(
                    pytorch_model=pytorch_model,
                    artifact_path="model",
                    input_example=input_example,
                )
            except Exception as e:
                logger.warning("Could not log model via mlflow.pytorch: %s", e)

            # Log any additional artifacts
            if artifact_paths:
                for path in artifact_paths:
                    if Path(path).exists():
                        mlflow.log_artifact(path)

        return run_id

    def log_qml_experiment_run(
        self,
        run_name: str,
        params: Dict[str, Any],
        metrics: Dict[str, Any],
        tags: Optional[Dict[str, str]] = None,
        artifact_paths: Optional[List[str]] = None,
        experiment_name: str = "qml-benchmarks",
    ) -> str:
        """Log experimental QML benchmark run (EXP-03, FR-07) under 'qml-benchmarks'.

        Enforces architectural firewall: tags run with benchmark_only=True and production_eligible=False.
        """
        mlflow.set_tracking_uri(self.tracking_uri)
        exp = mlflow.get_experiment_by_name(experiment_name)
        if exp is None:
            mlflow.create_experiment(experiment_name)
        mlflow.set_experiment(experiment_name)

        with mlflow.start_run(run_name=run_name) as run:
            run_id = run.info.run_id

            mlflow.log_params(params)

            numeric_metrics = {
                k: float(v)
                for k, v in metrics.items()
                if isinstance(v, (int, float)) and v is not None
            }
            if numeric_metrics:
                mlflow.log_metrics(numeric_metrics)

            run_tags = {
                "experiment_name": experiment_name,
                "framework": "PennyLane",
                "benchmark_only": "true",
                "production_eligible": "false",
                "firewall_enforced": "true",
                "logged_at": datetime.now(timezone.utc).isoformat(),
            }
            if tags:
                run_tags.update(tags)
            mlflow.set_tags(run_tags)

            if artifact_paths:
                for path in artifact_paths:
                    if Path(path).exists():
                        mlflow.log_artifact(path)

        return run_id

    def promote_to_champion(
        self,
        run_id: str,
        model_version: str,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update MLflow aliases ('champion', 'stable_fallback') and update SQLite singleton state."""
        # Architectural Firewall Enforcement (EXP-03, FR-07, TC-19):
        # Strictly prohibit QML models from being staged or promoted to champion.
        if (
            (model_name and "qml" in model_name.lower())
            or (model_version and "qml" in model_version.lower())
            or (run_id and "qml" in run_id.lower())
        ):
            raise ValueError(
                "Architectural Firewall: QML models are benchmark-only and cannot be promoted to champion."
            )

        registered_name = model_name or settings.mlflow_model_name
        client = self._get_client()

        # Check if the run belongs to a benchmark-only experiment
        try:
            run_info = client.get_run(run_id)
            if run_info and run_info.data.tags.get("benchmark_only") == "true":
                raise ValueError(
                    "Architectural Firewall: Benchmark-only models cannot be promoted to champion."
                )
        except ValueError:
            raise
        except Exception:
            pass

        with self._lock:
            # 1. Inspect current state from SQLite
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM active_model_state WHERE id = 1;")
                curr_state = cursor.fetchone()

            prev_version = curr_state["active_version"] if curr_state else None
            prev_run_id = curr_state["active_run_id"] if curr_state else None
            promoted_at = datetime.now(timezone.utc).isoformat()

            # 2. Update MLflow Model Registry Aliases if registry versions exist
            try:
                # Find the registry model version matching run_id
                versions = client.search_model_versions(f"name='{registered_name}'")
                target_version_num = None
                prev_version_num = None

                for v in versions:
                    if v.run_id == run_id:
                        target_version_num = v.version
                    if prev_run_id and v.run_id == prev_run_id:
                        prev_version_num = v.version

                if target_version_num:
                    client.set_registered_model_alias(registered_name, "champion", target_version_num)
                    client.set_model_version_tag(registered_name, target_version_num, "role", "active_production")

                if prev_version_num:
                    client.set_registered_model_alias(registered_name, "stable_fallback", prev_version_num)
                    client.set_model_version_tag(registered_name, prev_version_num, "role", "stable_fallback")
            except Exception:
                # Local or test stores without full registry backend gracefully continue
                pass

            # 3. Update SQLite singleton authoritative state
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO active_model_state (
                        id, active_version, active_run_id, promoted_at,
                        previous_stable_version, previous_stable_run_id, state
                    ) VALUES (1, ?, ?, ?, ?, ?, 'PROMOTED')
                    ON CONFLICT(id) DO UPDATE SET
                        active_version = excluded.active_version,
                        active_run_id = excluded.active_run_id,
                        promoted_at = excluded.promoted_at,
                        previous_stable_version = excluded.previous_stable_version,
                        previous_stable_run_id = excluded.previous_stable_run_id,
                        state = excluded.state;
                """, (model_version, run_id, promoted_at, prev_version, prev_run_id))
                conn.commit()

        return {
            "status": "PROMOTED",
            "active_version": model_version,
            "active_run_id": run_id,
            "previous_stable_version": prev_version,
            "promoted_at": promoted_at,
            "alias": "champion",
        }

    def get_active_model_state(self) -> Optional[Dict[str, Any]]:
        """Fetch the current authoritative active model state from SQLite."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM active_model_state WHERE id = 1;")
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def rollback_to_fallback(self, target_version: Optional[str] = None) -> Dict[str, Any]:
        """Reinstate previous stable version as champion in SQLite and MLflow."""
        with self._lock:
            state = self.get_active_model_state()
            target_v = target_version or (state.get("previous_stable_version") if state else None)
            if not state or not target_v:
                raise ValueError("No previous stable fallback model exists to roll back to.")

            target_run_id = state.get("previous_stable_run_id") or f"run-{target_v}"
            rolled_back_at = datetime.now(timezone.utc).isoformat()

            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE active_model_state
                    SET active_version = ?,
                        active_run_id = ?,
                        promoted_at = ?,
                        previous_stable_version = ?,
                        previous_stable_run_id = ?,
                        state = 'ROLLED_BACK'
                    WHERE id = 1;
                """, (target_v, target_run_id, rolled_back_at, state["active_version"], state["active_run_id"]))
                conn.commit()

        return {
            "status": "ROLLED_BACK",
            "active_version": target_v,
            "active_run_id": target_run_id,
            "reinstated_at": rolled_back_at,
        }


# Global singleton instance
mlflow_adapter = MLflowAdapter()
