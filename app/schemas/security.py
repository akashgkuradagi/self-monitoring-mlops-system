"""Security and Lifecycle API Request and Response Schemas (SEC-01)."""

from pydantic import BaseModel, Field
from app.security.auth import Role


class LoginRequest(BaseModel):
    """Credentials schema for token acquisition."""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """Bearer access token payload schema."""

    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    role: Role
    username: str


class CandidateDecisionRequest(BaseModel):
    """Schema for candidate promotion or rejection decision (SR-01, SR-02)."""

    decision: str = Field(..., pattern="^(APPROVE|REJECT|APPROVED|REJECTED)$")
    comment: str = Field(..., min_length=1, max_length=512)


class RollbackRequest(BaseModel):
    """Schema for triggering model rollback (SR-01, SR-02)."""

    target_version: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=512)
