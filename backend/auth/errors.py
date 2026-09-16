class AuthError(Exception):
    """Base for authentication/authorization failures. Fixed labels only."""


class AccessDenied(AuthError):
    """Authenticated with WorkOS, but not authorized for this application."""


class ProfileUnavailable(AuthError):
    """WorkOS could not be reached or returned an unusable profile.

    Distinct from AccessDenied on purpose: an upstream failure must never be
    reported as a successful provisioning, nor as a policy denial.
    """


class RateLimited(AuthError):
    """Too many auth/provisioning attempts for this subject."""
