"""WorkOS AuthKit access enforcement for the FastAPI backend."""

from backend.auth.errors import (
    AccessDenied,
    AuthError,
    ProfileUnavailable,
    RateLimited,
)
from backend.auth.ratelimit import FixedWindowLimiter
from backend.auth.tokens import (
    AccessTokenValidator,
    AuthNotConfigured,
    TokenError,
    VerifiedSubject,
)
from backend.auth.workos import ProfileSource, VerifiedProfile, WorkOSDirectory

__all__ = [
    "AccessDenied",
    "AccessTokenValidator",
    "AuthError",
    "AuthNotConfigured",
    "FixedWindowLimiter",
    "ProfileSource",
    "ProfileUnavailable",
    "RateLimited",
    "TokenError",
    "VerifiedProfile",
    "VerifiedSubject",
    "WorkOSDirectory",
]
