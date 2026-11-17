"""Security Package (SEC-01, SEC-02).

Provides authentication, RBAC authorization, token management,
tamper-evident audit logging, and security boundary middleware.
"""

from app.security.audit import AuditLogger, audit_logger
from app.security.auth import (
    AuthenticatedUser,
    Role,
    create_access_token,
    decode_access_token,
    get_current_user,
    require_admin,
    require_ml_engineer,
    require_role,
    require_viewer,
)

__all__ = [
    "Role",
    "AuthenticatedUser",
    "create_access_token",
    "decode_access_token",
    "get_current_user",
    "require_role",
    "require_viewer",
    "require_ml_engineer",
    "require_admin",
    "AuditLogger",
    "audit_logger",
]
