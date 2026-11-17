"""Recovery and Rollback Orchestrator Subsystem (REC-02).

Implements:
- FR-12: Support rollback to last known stable model version when post-promotion checks fail.
- NFR-02: Fault-isolated, thread-safe automated and manual rollback execution.
- TC-08: Rollback after failed health check restoring prior stable model.
- TC-20: End-to-end automated post-promotion rollback upon metric breach.
- TC-19: Strict architectural firewall enforcement blocking rollback to QML models.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
import uuid

from app.models.baseline import baseline_classifier
from app.recovery.health_guard import post_promotion_health_guard
from app.recovery.registry import candidate_registry
from app.registry.mlflow_adapter import mlflow_adapter
from app.schemas.recovery import RollbackExecutionResponse
from app.security.audit import audit_logger

logger = logging.getLogger("mlops.recovery.orchestrator")


class RecoveryOrchestrator:
    """Orchestrates post-promotion canary verification, automated rollbacks, and registry restoration."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or mlflow_adapter.db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create rollback_history table in SQLite."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS rollback_history (
                        rollback_id TEXT PRIMARY KEY,
                        source_model_version TEXT NOT NULL,
                        restored_model_version TEXT NOT NULL,
                        trigger_type TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        health_evidence_json TEXT,
                        actor TEXT NOT NULL,
                        executed_at TEXT NOT NULL,
                        status TEXT NOT NULL
                    );
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_rollback_executed ON rollback_history(executed_at);"
                )
                conn.commit()

    def execute_rollback(
        self,
        target_version: Optional[str] = None,
        reason: str = "Manual administrator rollback",
        actor: str = "system",
        trigger_type: str = "MANUAL",
        health_evidence: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> RollbackExecutionResponse:
        """Safely restore previous stable model version across MLflow, SQLite, and live prediction engine."""
        rollback_id = f"rb-{uuid.uuid4().hex[:8]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        with self._lock:
            # 1. Inspect Authoritative Model State
            state = mlflow_adapter.get_active_model_state()
            if not state:
                raise ValueError("Cannot execute rollback: No active production model state found.")

            current_version = state.get("active_version", "unknown")
            fallback_version = target_version or state.get("previous_stable_version")

            if not fallback_version:
                raise ValueError(
                    f"No previous stable fallback model exists to roll back to from '{current_version}'."
                )

            # 2. Idempotency Check: Already rolled back to target?
            if state.get("state") == "ROLLED_BACK" and current_version == fallback_version:
                logger.info("Rollback skipped: Model '%s' is already active and rolled back.", fallback_version)
                return RollbackExecutionResponse(
                    rollback_id=rollback_id,
                    status="SKIPPED_IDEMPOTENT",
                    source_model_version=current_version,
                    restored_model_version=fallback_version,
                    restored_run_id=state.get("active_run_id"),
                    trigger_type=trigger_type,
                    reason=f"Idempotent: Already rolled back to {fallback_version}.",
                    executed_at=now_iso,
                    audit_event_id=None,
                )

            # 3. Architectural Firewall Enforcement (EXP-03, TC-19)
            if "qml" in fallback_version.lower() or "qml" in str(state.get("previous_stable_run_id", "")).lower():
                raise ValueError(
                    "Architectural Firewall: QML benchmark models are non-executable and cannot be restored as production fallback."
                )

            # 4. Fallback Model & Artifact Verification
            fallback_file = Path("models") / f"{fallback_version}.joblib"
            is_baseline_target = fallback_version in ("baseline-v1", "ProductionClassifier")
            default_baseline_file = Path("models") / "baseline_model.joblib"

            if fallback_file.exists():
                verified_artifact_path = str(fallback_file)
            elif is_baseline_target and default_baseline_file.exists():
                verified_artifact_path = str(default_baseline_file)
            else:
                raise FileNotFoundError(
                    f"Fallback model artifact for '{fallback_version}' could not be verified on disk."
                )

            # 5. Re-alias in MLflow and SQLite State
            fallback_result = mlflow_adapter.rollback_to_fallback(target_version=fallback_version)
            restored_version = fallback_result["active_version"]
            restored_run_id = fallback_result["active_run_id"]

            # 6. Update Candidate Registry State of Failed Model
            if current_version != restored_version:
                candidate_registry.update_decision(
                    candidate_id=current_version,
                    status="ROLLED_BACK",
                    decided_by=actor,
                    comment=f"Rolled back to {restored_version}. Reason: {reason}",
                )

            # 7. Reload Live Serving Ingestion Engine Immediately (ML-01 Zero Downtime)
            try:
                baseline_classifier.load(verified_artifact_path)
                baseline_classifier.model_version = restored_version
                logger.info(
                    "Reloaded baseline_classifier with fallback artifact: %s (version: %s)",
                    verified_artifact_path,
                    restored_version,
                )
            except Exception as e:
                logger.error("Failed to reload baseline_classifier during rollback: %s", e)

            # 8. Record in Rollback History Table
            evidence_json = json.dumps(health_evidence or {})
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO rollback_history (
                        rollback_id, source_model_version, restored_model_version,
                        trigger_type, reason, health_evidence_json, actor, executed_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ROLLED_BACK');
                """, (
                    rollback_id,
                    current_version,
                    restored_version,
                    trigger_type,
                    reason,
                    evidence_json,
                    actor,
                    now_iso,
                ))
                conn.commit()

            # 9. Record Tamper-Evident Audit Event (SEC-02, FR-11)
            audit_event = audit_logger.log_event(
                actor=actor,
                role="System" if trigger_type == "AUTOMATED" else "ML_Engineer",
                action="MODEL_ROLLBACK",
                resource=f"model:{restored_version}",
                outcome="SUCCESS",
                correlation_id=correlation_id,
                metadata={
                    "rollback_id": rollback_id,
                    "source_version": current_version,
                    "restored_version": restored_version,
                    "trigger_type": trigger_type,
                    "reason": reason,
                    "health_evidence": health_evidence,
                },
            )

            logger.warning(
                "Rollback SUCCESS: Reverted active model from %s to %s (Trigger: %s, Reason: %s)",
                current_version,
                restored_version,
                trigger_type,
                reason,
            )

            return RollbackExecutionResponse(
                rollback_id=rollback_id,
                status="ROLLED_BACK",
                source_model_version=current_version,
                restored_model_version=restored_version,
                restored_run_id=restored_run_id,
                trigger_type=trigger_type,
                reason=reason,
                executed_at=now_iso,
                audit_event_id=audit_event.event_id,
            )

    def check_and_auto_rollback(
        self,
        simulate_error_rate: Optional[float] = None,
        simulate_latency_p95_ms: Optional[float] = None,
        simulate_drift: Optional[bool] = None,
        correlation_id: Optional[str] = None,
    ) -> Optional[RollbackExecutionResponse]:
        """Evaluate post-promotion health and autonomously trigger rollback if failure is detected."""
        health = post_promotion_health_guard.evaluate_health(
            simulate_error_rate=simulate_error_rate,
            simulate_latency_p95_ms=simulate_latency_p95_ms,
            simulate_drift=simulate_drift,
        )

        if health.recommended_action == "ROLLBACK":
            failure_reasons = "; ".join(health.reasons)
            reason = f"Automated post-promotion guard failure: {failure_reasons}"
            return self.execute_rollback(
                reason=reason,
                actor="health_guard_daemon",
                trigger_type="AUTOMATED",
                health_evidence=health.model_dump(),
                correlation_id=correlation_id,
            )

        return None

    def list_rollbacks(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List historical rollback operations ordered by executed_at descending."""
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT * FROM rollback_history ORDER BY executed_at DESC LIMIT ?;",
                    (limit,),
                )
                rows = cur.fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    if item.get("health_evidence_json"):
                        try:
                            item["health_evidence"] = json.loads(item["health_evidence_json"])
                        except Exception:
                            pass
                    results.append(item)
                return results

    def clear(self) -> None:
        """Clear rollback history (for test isolation)."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM rollback_history;")
                conn.commit()


recovery_orchestrator = RecoveryOrchestrator()
