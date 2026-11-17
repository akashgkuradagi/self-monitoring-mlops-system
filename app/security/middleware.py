"""Security Boundary Middleware Subsystem (SEC-01).

Implements:
- Payload size bounding (<= 1 MB) per SR-04, SR-06, and NFR-12.
- OWASP security headers injection.
- Sensitive credential and data redaction helpers for application logging (SR-03).
"""

from typing import Any, Callable, Dict, Set
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.config import settings

# Sensitive dictionary keys to redact in logs and debugging outputs
REDACTED_KEYS: Set[str] = {
    "authorization",
    "bearer",
    "token",
    "jwt",
    "secret",
    "password",
    "api_key",
    "apikey",
    "jwt_secret_key",
}


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Enforces upper bounds on incoming request body payloads (SR-04, TC-13).

    Rejects oversized requests exceeding MAX_REQUEST_SIZE_BYTES (1 MB) with HTTP 413.
    """

    def __init__(self, app: ASGIApp, max_size_bytes: int = settings.max_request_size_bytes):
        super().__init__(app)
        self.max_size_bytes = max_size_bytes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
                if length > self.max_size_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": "PayloadTooLarge",
                            "message": (
                                f"Request payload size ({length} bytes) exceeds the maximum allowed "
                                f"limit of {self.max_size_bytes} bytes (1 MB)."
                            ),
                        },
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={"error": "BadRequest", "message": "Invalid Content-Length header value."},
                )

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Applies standard OWASP defensive headers to all HTTP responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        return response


def is_sensitive_key(key: str) -> bool:
    """Check if key matches or contains any known sensitive token substring."""
    key_lower = key.lower()
    return any(term in key_lower for term in REDACTED_KEYS)


def redact_sensitive_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively mask sensitive credentials and secrets in telemetry or log dictionaries."""
    sanitized: Dict[str, Any] = {}
    for key, value in data.items():
        if is_sensitive_key(key):
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, dict):
            sanitized[key] = redact_sensitive_dict(value)
        elif isinstance(value, list):
            sanitized[key] = [
                redact_sensitive_dict(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized
