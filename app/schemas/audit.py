"""Structured Audit Logging Schemas (SEC-02, FR-11, SR-05)."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AuditEventResponse(BaseModel):
    """Schema for individual tamper-evident audit record."""

    id: Optional[int] = Field(default=None, description="Sequential row index")
    event_id: str = Field(..., description="Unique audit event UUID")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp of event")
    actor: str = Field(..., description="Authenticated user identity or system principal")
    role: str = Field(..., description="Associated RBAC role at execution time")
    action: str = Field(..., description="Audited operational or security action")
    resource: str = Field(..., description="Target system resource or version")
    outcome: str = Field(..., description="Result of action: SUCCESS, DENIED, or FAILURE")
    correlation_id: Optional[str] = Field(default=None, description="Request or correlation identifier")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Redacted contextual event metadata")
    prev_hash: str = Field(..., description="SHA-256 hash of preceding record (or genesis)")
    record_hash: str = Field(..., description="Cryptographic SHA-256 hash of this record")


class AuditIntegrityResponse(BaseModel):
    """Schema for audit log hash-chain verification report."""

    is_valid: bool = Field(..., description="True if entire hash chain is unbroken and valid")
    total_records: int = Field(..., ge=0, description="Total audit records inspected")
    verified_records: int = Field(..., ge=0, description="Count of successfully validated records")
    issues: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of detected anomalies (modifications, deletions, broken links)",
    )
    verified_at: str = Field(..., description="ISO 8601 UTC timestamp of verification")
