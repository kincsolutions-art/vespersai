"""Protected account endpoints and the reusable authorization dependencies.

Enforcement order, all server-side:

1. Bearer access token → validated against the WorkOS JWKS  → verified `sub`.
2. `sub` → WorkOS User Management → verified email + `email_verified`.
3. verified email → database allowlist → local user / personal tenant.
4. local membership → tenant access.

Successful WorkOS authentication alone grants nothing: steps 3 and 4 are what
authorize the request, and both live in PostgreSQL. No identity fact is ever read
from a request header, query parameter or JSON body.

**Revocation policy.** `authorize_local_access` runs on *every* protected
request, not only at sign-in, and re-reads the allowlist, the user status, the
tenant status and the membership status from PostgreSQL each time. An existing
browser session therefore cannot outlive revoked application access: the next
request it makes is refused.

**Upstream email changes are a separate, slower path, on purpose.** Step 2 is
skipped on read paths, so `GET /api/account` never calls WorkOS and the allowlist
is always evaluated against the email *stored* locally for the verified subject.
A change made at WorkOS is reconciled only by `POST /api/account/provision`,
which in this application runs at sign-in and from the account page's retry
button — so it may not run for a long time. Access therefore continues under the
stored, allowlisted address until then. This does not weaken revocation: an
administrator revokes by removing the *stored* address from the allowlist (or
disabling the user, tenant or membership), and that bites on the next request.
Deriving authorization from an email the user can change upstream is exactly the
property we do not want; the verified subject is the identity.

This is application-access revocation, which is deliberately distinct from
cryptographic token validity: an issued access token stays verifiable until it
expires, and we do not implement a token denylist.
"""

import logging
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from backend.auth.errors import ProfileUnavailable, RateLimited
from backend.auth.ratelimit import FixedWindowLimiter
from backend.auth.tokens import AccessTokenValidator, AuthNotConfigured, TokenError
from backend.auth.workos import ProfileSource, VerifiedProfile
from backend.identity import (
    EmailAlreadyBound,
    ExternalSubjectConflict,
    IdentityDisabled,
    IdentityService,
    InvalidIdentityInput,
    SignupNotAllowed,
    SubjectBindingRequired,
    SubjectRequired,
    TenantMembership,
    TenantRepository,
    UserRecord,
)
from backend.storage.tenant_context import tenant_scope

logger = logging.getLogger("vespers.api")
router = APIRouter(prefix="/api", tags=["account"])


class AccountResponse(BaseModel):
    user_id: UUID
    email: str
    tenant_id: UUID
    tenant_name: str
    tenant_kind: str
    role: str
    created: bool = False


@dataclass(frozen=True, slots=True)
class AuthenticatedAccount:
    user_id: UUID
    email: str
    membership: TenantMembership


def _validator(request: Request) -> AccessTokenValidator:
    validator = getattr(request.app.state, "token_validator", None)
    if not isinstance(validator, AccessTokenValidator):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication-not-configured")
    return validator


def _directory(request: Request) -> ProfileSource:
    directory = getattr(request.app.state, "workos_directory", None)
    if not isinstance(directory, ProfileSource):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication-not-configured")
    return directory


def _limiter(request: Request) -> FixedWindowLimiter:
    limiter = request.app.state.auth_limiter
    assert isinstance(limiter, FixedWindowLimiter)
    return limiter


async def require_subject(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Verified WorkOS subject, or 401. Never trusts a browser-supplied identity."""
    validator = _validator(request)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing-bearer-token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization[7:].strip()
    try:
        # PyJWKClient fetches the key set with blocking urllib on a cache miss.
        # Running it inline would stall the event loop for every other request
        # for the duration of a WorkOS round trip.
        verified = await run_in_threadpool(validator.verify, token)
    except AuthNotConfigured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "authentication-not-configured"
        ) from None
    except TokenError as error:
        # Fixed label only: never the token, never provider detail.
        logger.info("auth_token_rejected", extra={"reason": str(error)})
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, str(error), headers={"WWW-Authenticate": "Bearer"}
        ) from None

    if not _limiter(request).check(f"subject:{verified.subject}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate-limited")
    return verified.subject


async def _verified_profile(request: Request, subject: str) -> VerifiedProfile:
    try:
        profile = await _directory(request).fetch_profile(subject)
    except ProfileUnavailable as error:
        # An upstream failure is an error, never a denial and never a success.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from None
    if not profile.email_verified:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "email-not-verified")
    return profile


async def authorize_local_access(
    service: IdentityService, subject: str
) -> tuple[UserRecord, list[TenantMembership]]:
    """Re-check every locally revocable condition. Raises 403 on any failure.

    Deliberately re-run per request rather than cached in the session, so
    removing an allowlist entry or disabling a user, tenant or membership takes
    effect on the very next call an already-signed-in browser makes.
    """
    user = await service.find_user(external_id=subject)
    if user is None:
        # Authenticated with WorkOS, unknown to this application.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not-provisioned")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "identity-disabled")
    # The allowlist is authorization, not a signup gate: an address removed after
    # provisioning must lose access without waiting for the token to expire.
    if not await service.is_signup_allowed(user.email):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not-allowlisted")
    # app.list_memberships returns rows only where the membership, the user and
    # the tenant are all active, so tenant and membership disablement land here.
    memberships = await service.list_memberships(user.id)
    if not memberships:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "no-active-membership")
    return user, memberships


async def require_account(
    request: Request,
    subject: Annotated[str, Depends(require_subject)],
    tenant_id: Annotated[UUID | None, Query()] = None,
) -> AuthenticatedAccount:
    """Resolve an already-provisioned local account. Does not provision."""
    sessions = request.app.state.sessions
    async with sessions() as session, session.begin():
        service = IdentityService(session)
        user, memberships = await authorize_local_access(service, subject)

        if tenant_id is None:
            membership = memberships[0]
        else:
            # A requested tenant is honoured only when the backend finds a real
            # active membership for it. A forged id is refused, never silently
            # redirected to a tenant the caller does happen to belong to.
            matched = [m for m in memberships if m.tenant_id == tenant_id]
            if not matched:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant-not-permitted")
            membership = matched[0]
        return AuthenticatedAccount(user_id=user.id, email=user.email, membership=membership)


@router.get("/account", response_model=AccountResponse)
async def read_account(
    request: Request,
    account: Annotated[AuthenticatedAccount, Depends(require_account)],
) -> AccountResponse:
    """Minimal protected resource, read inside a real tenant scope."""
    sessions = request.app.state.sessions
    async with sessions() as session, session.begin():
        # Proves the tenant context path works end to end: the repository reads
        # under row-level security rather than trusting the resolution above.
        async with tenant_scope(session, account.membership.tenant_id):
            tenant = await TenantRepository(session).get_tenant()
            if tenant is None:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "tenant-not-visible")
    return AccountResponse(
        user_id=account.user_id,
        email=account.email,
        tenant_id=tenant.id,
        tenant_name=tenant.name,
        tenant_kind=tenant.kind,
        role=account.membership.role,
    )


@router.post("/account/provision", response_model=AccountResponse)
async def provision_account(
    request: Request,
    subject: Annotated[str, Depends(require_subject)],
) -> AccountResponse:
    """Idempotent first-login provisioning.

    Safe to retry: duplicate callbacks converge on one user, tenant and
    membership. The allowlist is enforced here in the database, so a direct API
    call cannot bypass whatever the dashboard shows.
    """
    profile = await _verified_profile(request, subject)
    sessions = request.app.state.sessions
    try:
        async with sessions() as session, session.begin():
            provisioned = await IdentityService(session).provision_personal_tenant(
                email=profile.email, external_id=profile.subject
            )
    except SignupNotAllowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not-allowlisted") from None
    except IdentityDisabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "identity-disabled") from None
    except ExternalSubjectConflict:
        raise HTTPException(status.HTTP_409_CONFLICT, "external-subject-conflict") from None
    except EmailAlreadyBound:
        raise HTTPException(status.HTTP_409_CONFLICT, "email-already-bound") from None
    except SubjectBindingRequired:
        raise HTTPException(status.HTTP_409_CONFLICT, "subject-binding-required") from None
    except SubjectRequired:
        # Unreachable through this route: `subject` is the verified token claim.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing-claim:sub") from None
    except InvalidIdentityInput:
        # SQLSTATE 22023 from the bootstrap surface: the identity values were
        # refused. That is a bad request, not an outage, and reporting it as
        # "provisioning-unavailable" would send an operator hunting a database
        # they have no reason to suspect.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid-identity-input") from None
    except (SQLAlchemyError, RateLimited):
        # An *unexpected* storage failure, kept distinct from every decision
        # above, and never reported as successful provisioning.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "provisioning-unavailable") from (
            None
        )

    # Re-read through the same boundary a plain request uses, so provisioning can
    # never report access that a subsequent GET would refuse.
    async with sessions() as session, session.begin():
        _, memberships = await authorize_local_access(IdentityService(session), subject)
    membership = next(
        (m for m in memberships if m.tenant_id == provisioned.tenant_id), memberships[0]
    )
    return AccountResponse(
        user_id=provisioned.user_id,
        email=profile.email,
        tenant_id=membership.tenant_id,
        tenant_name=membership.tenant_name,
        tenant_kind=membership.tenant_kind,
        role=membership.role,
        created=provisioned.created,
    )
