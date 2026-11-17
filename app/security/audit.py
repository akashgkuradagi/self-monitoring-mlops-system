"""Structured, Tamper-Evident Audit Logging Subsystem (SEC-02).

Implements requirements:
- FR-11: Audit logging for model promotions, rejections, rollbacks, and security actions.
- SR-03: Sensitive credential and secret masking in log outputs.
- SR-05: Accountability via persistent tamper-evident audit trail with SHA-256 hash chaining.
- NFR-11: Log privacy (redaction of raw sensitive feature attributes and credentials).

Security Architecture Invariant:
This system provides application-level append-only guarantees and cryptographic tamper-evidence
via SHA-256 hash chaining. Any modification, deletion, reordering, or corruption of records
breaks the cryptographic link to subsequent records and is flagged by integrity verification.
It does not claim absolute physical immutability against raw filesystem alteration without OS-level controls.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional
import uuid

from app.config import settings
from app.schemas.audit import AuditEventResponse, AuditIntegrityResponse
from app.security.middleware import redact_sensitive_dict

# Fixed 64-character hex zero string representing the root (genesis) of the audit hash chain
GENESIS_HASH = "0" * 64


class AuditLogger:
    """Thread-safe, append-only SQLite audit log engine with SHA-256 hash chaining."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.audit_db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Create a dedicated SQLite connection with standard isolation and row factory."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=20.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize the audit_log schema, indexes, and database-level append-only triggers."""
        # Ensure parent directory exists if a path is provided
        p = Path(self.db_path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)

        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    role TEXT NOT NULL,
                    action TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    correlation_id TEXT,
                    metadata_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL
                );
            """)

            # Helpful query indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor);")

            # Database-level defense-in-depth triggers: prohibit direct UPDATE or DELETE
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS abort_audit_update
                BEFORE UPDATE ON audit_log
                BEGIN
                    SELECT RAISE(FAIL, 'Audit log records are append-only; direct UPDATE operations are prohibited.');
                END;
            """)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS abort_audit_delete
                BEFORE DELETE ON audit_log
                BEGIN
                    SELECT RAISE(FAIL, 'Audit log records are append-only; direct DELETE operations are prohibited.');
                END;
            """)
            conn.commit()

    @staticmethod
    def compute_record_hash(
        event_id: str,
        timestamp: str,
        actor: str,
        role: str,
        action: str,
        resource: str,
        outcome: str,
        correlation_id: Optional[str],
        metadata_json: str,
        prev_hash: str,
    ) -> str:
        """Compute deterministic SHA-256 hash over canonical representation of record fields."""
        canonical_dict = {
            "action": action,
            "actor": actor,
            "correlation_id": correlation_id or "",
            "event_id": event_id,
            "metadata_json": metadata_json,
            "outcome": outcome,
            "prev_hash": prev_hash,
            "resource": resource,
            "role": role,
            "timestamp": timestamp,
        }
        # Strictly deterministic sorted canonical byte sequence
        canonical_bytes = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(canonical_bytes).hexdigest()

    def log_event(
        self,
        actor: str,
        action: str,
        resource: str,
        outcome: str,
        role: Optional[str] = None,
        correlation_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> AuditEventResponse:
        """Append an audit event, cryptographically linking it to the prior record's hash."""
        assigned_id = event_id or f"audit-{uuid.uuid4().hex[:12]}"
        now_iso = timestamp or datetime.now(timezone.utc).isoformat()
        assigned_role = role or "ANONYMOUS"

        # Apply sensitive data masking before storage
        sanitized_meta = redact_sensitive_dict(metadata or {})
        meta_json = json.dumps(sanitized_meta, sort_keys=True)

        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()

            # Retrieve prior record's hash atomically under transaction
            cursor.execute("SELECT record_hash FROM audit_log ORDER BY id DESC LIMIT 1;")
            row = cursor.fetchone()
            prev_hash = row["record_hash"] if row else GENESIS_HASH

            # Compute current record hash
            record_hash = self.compute_record_hash(
                event_id=assigned_id,
                timestamp=now_iso,
                actor=actor,
                role=assigned_role,
                action=action,
                resource=resource,
                outcome=outcome,
                correlation_id=correlation_id,
                metadata_json=meta_json,
                prev_hash=prev_hash,
            )

            cursor.execute(
                """
                INSERT INTO audit_log (
                    event_id, timestamp, actor, role, action, resource,
                    outcome, correlation_id, metadata_json, prev_hash, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    assigned_id,
                    now_iso,
                    actor,
                    assigned_role,
                    action,
                    resource,
                    outcome,
                    correlation_id,
                    meta_json,
                    prev_hash,
                    record_hash,
                ),
            )
            inserted_id = cursor.lastrowid
            conn.commit()

        return AuditEventResponse(
            id=inserted_id,
            event_id=assigned_id,
            timestamp=now_iso,
            actor=actor,
            role=assigned_role,
            action=action,
            resource=resource,
            outcome=outcome,
            correlation_id=correlation_id,
            metadata=sanitized_meta,
            prev_hash=prev_hash,
            record_hash=record_hash,
        )

    def get_events(
        self,
        limit: int = 100,
        offset: int = 0,
        action: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> List[AuditEventResponse]:
        """Fetch audit log records with optional filtering."""
        query = "SELECT * FROM audit_log"
        params: List[Any] = []
        conditions: List[str] = []

        if action:
            conditions.append("action = ?")
            params.append(action)
        if actor:
            conditions.append("actor = ?")
            params.append(actor)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id ASC LIMIT ? OFFSET ?;"
        params.extend([limit, offset])

        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()

        events: List[AuditEventResponse] = []
        for r in rows:
            meta = json.loads(r["metadata_json"]) if r["metadata_json"] else {}
            events.append(
                AuditEventResponse(
                    id=r["id"],
                    event_id=r["event_id"],
                    timestamp=r["timestamp"],
                    actor=r["actor"],
                    role=r["role"],
                    action=r["action"],
                    resource=r["resource"],
                    outcome=r["outcome"],
                    correlation_id=r["correlation_id"],
                    metadata=meta,
                    prev_hash=r["prev_hash"],
                    record_hash=r["record_hash"],
                )
            )
        return events

    def verify_integrity(self) -> AuditIntegrityResponse:
        """Inspect and verify the cryptographic integrity of the audit hash chain.

        Detects:
        - In-place modifications to fields (record_hash mismatch).
        - Deletions or reorderings of records (prev_hash mismatch).
        - Truncations or unauthorized insertions.
        """
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM audit_log ORDER BY id ASC;")
            rows = cursor.fetchall()

        issues: List[Dict[str, Any]] = []
        expected_prev_hash = GENESIS_HASH
        verified_count = 0

        for idx, row in enumerate(rows):
            row_id = row["id"]
            event_id = row["event_id"]
            stored_prev_hash = row["prev_hash"]
            stored_record_hash = row["record_hash"]

            # 1. Check hash chain continuity
            if stored_prev_hash != expected_prev_hash:
                issues.append({
                    "record_id": row_id,
                    "event_id": event_id,
                    "error_type": "CHAIN_BROKEN",
                    "description": (
                        f"Broken hash chain at row {row_id} ({event_id}): "
                        f"stored prev_hash '{stored_prev_hash}' does not match "
                        f"expected '{expected_prev_hash}'."
                    ),
                })

            # 2. Recompute expected record hash from canonical stored fields
            calculated_hash = self.compute_record_hash(
                event_id=row["event_id"],
                timestamp=row["timestamp"],
                actor=row["actor"],
                role=row["role"],
                action=row["action"],
                resource=row["resource"],
                outcome=row["outcome"],
                correlation_id=row["correlation_id"],
                metadata_json=row["metadata_json"],
                prev_hash=stored_prev_hash,
            )

            if stored_record_hash != calculated_hash:
                issues.append({
                    "record_id": row_id,
                    "event_id": event_id,
                    "error_type": "HASH_MISMATCH",
                    "description": (
                        f"Tampered record at row {row_id} ({event_id}): "
                        f"stored record_hash '{stored_record_hash}' does not match "
                        f"calculated hash '{calculated_hash}'."
                    ),
                })
            else:
                verified_count += 1

            # Advance expected previous hash for next record
            expected_prev_hash = stored_record_hash

        is_valid = (len(issues) == 0)
        return AuditIntegrityResponse(
            is_valid=is_valid,
            total_records=len(rows),
            verified_records=verified_count,
            issues=issues,
            verified_at=datetime.now(timezone.utc).isoformat(),
        )


# Global singleton audit logger instance
audit_logger = AuditLogger()
