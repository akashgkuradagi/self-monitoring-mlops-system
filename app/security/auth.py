"""Authentication and Role-Based Access Control (RBAC) Subsystem (SEC-01).

Enforces SR-01, SR-02, SR-06, and NFR-12 across the FastAPI service.
Supports JWT bearer tokens and configured API keys for service authentication.
"""

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Optional, Sequence
import jwt
from pydantic import BaseModel, Field
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.security.audit import audit_logger

# HTTP Bearer scheme with automatic 401 handling when configured
security_bearer = HTTPBearer(auto_error=False)


class Role(str, Enum):
    """Approved system RBAC roles (SR-02, SAD Section 4.4).

    Strictly limited to the three roles specified in the approved SRS/SAD:
    - Viewer: Read-only access to metrics, telemetry, and system status.
    - ML_Engineer: Authorized for candidate validation, promotion decisions, and rollback.
    - Administrator: Full operational, administrative, and configuration authority.
    """

    VIEWER = "Viewer"
    ML_ENGINEER = "ML_Engineer"
    ADMINISTRATOR = "Administrator"


class AuthenticatedUser(BaseModel):
    """Authenticated user context attached to request state."""

    username: str = Field(..., description="Unique subject or identity")
    role: Role = Field(..., description="Authorized role")


class TokenResponse(BaseModel):
    """JWT Access Token response payload."""

    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    role: Role


def create_access_token(
    subject: str,
    role: Role,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict] = None,
) -> str:
    """Generate a signed JWT token containing subject identity and RBAC role."""
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)

    payload = {
        "sub": subject,
        "role": role.value,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)

    encoded_jwt = jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return encoded_jwt


def decode_access_token(token: str) -> dict:
    """Validate JWT signature, expiration, and payload integrity."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Please re-authenticate.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token signature or claims.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> AuthenticatedUser:
    """Extract and validate credentials from the HTTP Authorization header (SR-01).

    Supports:
    1. JWT Bearer token: validated cryptographically against configured secret key.
    2. Bootstrap API Key: matched against configured service keys.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = credentials.credentials.strip()

    # 1. Check if token matches a configured API key
    api_keys = settings.parsed_api_keys
    if raw_token in api_keys:
        role_str = api_keys[raw_token]
        try:
            role = Role(role_str)
            return AuthenticatedUser(username=f"service-{role_str.lower()}", role=role)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Misconfigured role for API key: {role_str}",
            )

    # 2. Decode as signed JWT Bearer Token
    payload = decode_access_token(raw_token)
    username: Optional[str] = payload.get("sub")
    role_str: Optional[str] = payload.get("role")

    if not username or not role_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token claims missing subject ('sub') or role ('role').",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        role = Role(role_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token specifies an unrecognized role: '{role_str}'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthenticatedUser(username=username, role=role)


def require_role(allowed_roles: Sequence[Role]):
    """FastAPI Dependency factory enforcing server-side RBAC (SR-02, SEC-02, TC-11)."""

    def role_dependency(
        request: Request,
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if user.role not in allowed_roles:
            corr_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID")
            # Audit log authorization denial (SR-05, SEC-02)
            audit_logger.log_event(
                actor=user.username,
                role=user.role.value,
                action="AUTHORIZATION_DENIED",
                resource=request.url.path,
                outcome="DENIED",
                correlation_id=corr_id,
                metadata={"required_roles": [r.value for r in allowed_roles]},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Access denied: User '{user.username}' with role '{user.role.value}' "
                    f"is not authorized for this operation. Required role(s): {[r.value for r in allowed_roles]}."
                ),
            )
        return user

    return role_dependency


# Common reusable authorization dependencies
require_viewer = require_role([Role.VIEWER, Role.ML_ENGINEER, Role.ADMINISTRATOR])
require_ml_engineer = require_role([Role.ML_ENGINEER, Role.ADMINISTRATOR])
require_admin = require_role([Role.ADMINISTRATOR])
