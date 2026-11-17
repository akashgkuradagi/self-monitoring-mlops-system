"""Recovery Policy Engine with Cooldown Suppression (REC-01).

Implements:
- FR-09: Recovery policy evaluation and cooldown suppression window (TC-16).
- NFR-10: Automated recovery gating preventing redundant training loops.
"""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger("mlops.recovery.policy")


class RecoveryPolicyEngine:
    """Evaluates system drift and alerts to safely authorize or suppress model retraining."""

    def __init__(
        self,
        cooldown_seconds: Optional[int] = None,
        db_path: Optional[str] = None,
    ):
        self.cooldown_seconds = (
            cooldown_seconds
            if cooldown_seconds is not None
            else settings.recovery_cooldown_seconds
        )
        self.db_path = db_path or settings.audit_db_path
        self._last_trigger_time: Optional[datetime] = None
        self._total_triggers: int = 0
        self._total_suppressions: int = 0
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize recovery policy event history table in SQLite."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS recovery_policy_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        status TEXT NOT NULL,
                        candidate_eligible INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        cooldown_remaining_seconds REAL NOT NULL,
                        evaluated_at TEXT NOT NULL,
                        context_json TEXT
                    );
                """)
                conn.commit()

                # Restore last trigger timestamp from database if exists
                cursor = conn.execute("""
                    SELECT evaluated_at FROM recovery_policy_events
                    WHERE status = 'TRIGGER_APPROVED'
                    ORDER BY event_id DESC LIMIT 1;
                """)
                row = cursor.fetchone()
                if row:
                    try:
                        self._last_trigger_time = datetime.fromisoformat(row["evaluated_at"])
                    except Exception:
                        pass

    def evaluate_trigger(
        self,
        drift_detected: bool = False,
        active_alerts: Optional[List[Dict[str, Any]]] = None,
        severity: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, bool, str, float]:
        """Evaluate whether incoming drift/alert event warrants candidate retraining.

        Returns:
            Tuple[status, candidate_eligible, reason, cooldown_remaining_seconds]
        """
        now = datetime.now(timezone.utc)

        with self._lock:
            # 1. Determine eligibility based on drift or alert severity
            has_critical_alert = False
            if active_alerts:
                for alert in active_alerts:
                    sev = str(alert.get("severity", "")).upper()
                    if sev in ("HIGH", "CRITICAL"):
                        has_critical_alert = True
                        break

            if severity and severity.upper() in ("HIGH", "CRITICAL"):
                has_critical_alert = True

            is_condition_met = drift_detected or has_critical_alert

            if not is_condition_met:
                remaining = self._get_cooldown_remaining(now)
                reason = "No severe drift or critical alert detected. Retraining is ineligible."
                self._record_event("INELIGIBLE", False, reason, remaining, now, context)
                return "INELIGIBLE", False, reason, remaining

            # 2. Check Cooldown Suppression Window (TC-16, FR-09)
            remaining = self._get_cooldown_remaining(now)
            if remaining > 0.0:
                self._total_suppressions += 1
                reason = (
                    f"Cooldown active ({remaining:.1f}s remaining of {self.cooldown_seconds}s). "
                    "Duplicate candidate retraining trigger suppressed."
                )
                logger.info("Recovery policy suppressed trigger: %s", reason)
                self._record_event("SUPPRESSED_COOLDOWN", False, reason, remaining, now, context)
                return "SUPPRESSED_COOLDOWN", False, reason, remaining

            # 3. Retraining Approved
            self._last_trigger_time = now
            self._total_triggers += 1
            reason = (
                "Recovery policy approved candidate retraining: severe drift or critical alert detected "
                "outside active cooldown."
            )
            logger.info("Recovery policy approved trigger: %s", reason)
            self._record_event("TRIGGER_APPROVED", True, reason, 0.0, now, context)
            return "TRIGGER_APPROVED", True, reason, 0.0

    def _get_cooldown_remaining(self, now: datetime) -> float:
        """Calculate seconds remaining in active cooldown window."""
        if self._last_trigger_time is None:
            return 0.0
        elapsed = (now - self._last_trigger_time).total_seconds()
        remaining = self.cooldown_seconds - elapsed
        return max(0.0, remaining)

    def _record_event(
        self,
        status: str,
        candidate_eligible: bool,
        reason: str,
        remaining_seconds: float,
        timestamp: datetime,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record evaluation history in SQLite."""
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO recovery_policy_events (
                        status, candidate_eligible, reason,
                        cooldown_remaining_seconds, evaluated_at, context_json
                    ) VALUES (?, ?, ?, ?, ?, ?);
                """, (
                    status,
                    1 if candidate_eligible else 0,
                    reason,
                    remaining_seconds,
                    timestamp.isoformat(),
                    json.dumps(context or {}),
                ))
                conn.commit()
        except Exception as e:
            logger.warning("Failed to record recovery policy event: %s", e)

    def get_status(self) -> Dict[str, Any]:
        """Return current policy engine status."""
        now = datetime.now(timezone.utc)
        with self._lock:
            remaining = self._get_cooldown_remaining(now)
            return {
                "cooldown_seconds": self.cooldown_seconds,
                "is_cooldown_active": remaining > 0.0,
                "cooldown_remaining_seconds": round(remaining, 2),
                "last_trigger_time": self._last_trigger_time.isoformat() if self._last_trigger_time else None,
                "total_triggers": self._total_triggers,
                "total_suppressions": self._total_suppressions,
            }

    def reset_cooldown(self) -> None:
        """Reset cooldown timestamp and event history in DB (for test isolation)."""
        with self._lock:
            self._last_trigger_time = None
            self._total_triggers = 0
            self._total_suppressions = 0
            try:
                with self._get_connection() as conn:
                    conn.execute("DELETE FROM recovery_policy_events;")
                    conn.commit()
            except Exception:
                pass


recovery_policy_engine = RecoveryPolicyEngine()
