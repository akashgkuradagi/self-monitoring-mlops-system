"""Candidate Registry Persistence Subsystem (REC-01).

Implements:
- FR-10 / FR-14: Candidate model lifecycle tracking in SQLite.
- B.8: Candidate state transitions:
  PENDING_VALIDATION -> VALIDATION_FAILED / PENDING_APPROVAL -> PROMOTED / REJECTED / ROLLED_BACK.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger("mlops.recovery.registry")


class CandidateRegistry:
    """Manages candidate model lifecycle records in SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.audit_db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create candidate_records table and indexes."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS candidate_records (
                        candidate_id TEXT PRIMARY KEY,
                        model_version TEXT NOT NULL,
                        model_name TEXT NOT NULL,
                        run_id TEXT,
                        status TEXT NOT NULL,
                        trigger_reason TEXT,
                        validation_report TEXT,
                        created_at TEXT NOT NULL,
                        validated_at TEXT,
                        decided_at TEXT,
                        decided_by TEXT,
                        decision_comment TEXT
                    );
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidate_records(status);"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_candidates_created_at ON candidate_records(created_at);"
                )
                conn.commit()

    def create_candidate(
        self,
        candidate_id: str,
        model_version: str,
        model_name: str = "ProductionClassifier",
        run_id: Optional[str] = None,
        trigger_reason: Optional[str] = None,
        status: str = "PENDING_VALIDATION",
    ) -> Dict[str, Any]:
        """Create a new candidate model record."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO candidate_records (
                        candidate_id, model_version, model_name, run_id,
                        status, trigger_reason, validation_report, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                """, (
                    candidate_id,
                    model_version,
                    model_name,
                    run_id,
                    status,
                    trigger_reason or "Automated retraining trigger",
                    now_iso,
                ))
                conn.commit()

        return self.get_candidate(candidate_id)  # type: ignore

    def get_candidate(self, candidate_id: str) -> Optional[Dict[str, Any]]:
        """Fetch candidate record by candidate_id."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM candidate_records WHERE candidate_id = ?;",
                    (candidate_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                if data.get("validation_report"):
                    try:
                        data["validation_report"] = json.loads(data["validation_report"])
                    except Exception:
                        pass
                return data

    def list_candidates(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List candidate records ordered by created_at descending."""
        with self._lock:
            with self._get_connection() as conn:
                if status:
                    cursor = conn.execute(
                        "SELECT * FROM candidate_records WHERE status = ? ORDER BY created_at DESC LIMIT ?;",
                        (status, limit),
                    )
                else:
                    cursor = conn.execute(
                        "SELECT * FROM candidate_records ORDER BY created_at DESC LIMIT ?;",
                        (limit,),
                    )
                rows = cursor.fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    if item.get("validation_report"):
                        try:
                            item["validation_report"] = json.loads(item["validation_report"])
                        except Exception:
                            pass
                    results.append(item)
                return results

    def update_validation(
        self,
        candidate_id: str,
        status: str,
        validation_report: Dict[str, Any],
        validated_by: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update candidate with validation outcome and transition status."""
        now_iso = datetime.now(timezone.utc).isoformat()
        report_json = json.dumps(validation_report)

        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE candidate_records
                    SET status = ?,
                        validation_report = ?,
                        validated_at = ?
                    WHERE candidate_id = ?;
                """, (status, report_json, now_iso, candidate_id))
                conn.commit()

        return self.get_candidate(candidate_id)

    def update_decision(
        self,
        candidate_id: str,
        status: str,
        decided_by: str,
        comment: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Record human approval or rejection decision."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    UPDATE candidate_records
                    SET status = ?,
                        decided_at = ?,
                        decided_by = ?,
                        decision_comment = ?
                    WHERE candidate_id = ?;
                """, (status, now_iso, decided_by, comment, candidate_id))
                conn.commit()

        return self.get_candidate(candidate_id)

    def clear(self) -> None:
        """Clear candidate records (used for test teardown)."""
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM candidate_records;")
                conn.commit()


candidate_registry = CandidateRegistry()
