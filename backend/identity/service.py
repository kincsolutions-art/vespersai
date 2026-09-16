"""Bootstrap identity operations that run *before* a tenant context exists.

Identity lookup, eligibility checks and first-tenant creation cannot be
authorized by a tenant-scoped policy, because no tenant is selected yet. They go
through the narrowly scoped SECURITY DEFINER functions created in migration
0002 rather than through a general privileged repository.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.identity.errors import (
    EmailAlreadyBound,
    ExternalSubjectConflict,
    IdentityDisabled,
    InvalidIdentityInput,
    SignupNotAllowed,
    SubjectBindingRequired,
    SubjectRequired,
)
from backend.storage.models import ACTIVE


# Mirrors the SQL expression in migration 0002 and the email_normalized generated
# column: case and surrounding whitespace only. No provider-specific dot or
# plus-tag handling, which would wrongly merge distinct addresses.
def normalize_email(email: str) -> str:
    return email.strip().lower()


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: UUID
    email: str
    external_id: str | None
    status: str

    @property
    def is_active(self) -> bool:
        return self.status == ACTIVE


@dataclass(frozen=True, slots=True)
class ProvisionedIdentity:
    user_id: UUID
    tenant_id: UUID
    membership_id: UUID
    created: bool


@dataclass(frozen=True, slots=True)
class MembershipRecord:
    membership_id: UUID
    user_id: UUID
    tenant_id: UUID
    role: str


@dataclass(frozen=True, slots=True)
class TenantMembership:
    membership_id: UUID
    tenant_id: UUID
    tenant_name: str
    tenant_kind: str
    role: str


_SIGNUP_ALLOWED = text("SELECT app.signup_allowed(:email)")
_FIND_USER = text("SELECT id, email, external_id, status FROM app.find_user(:email, :external_id)")
_PROVISION = text(
    "SELECT out_user_id, out_tenant_id, out_membership_id, out_created"
    " FROM app.provision_personal_identity(:email, :external_id, :tenant_name)"
)
_RESOLVE = text(
    "SELECT membership_id, user_id, tenant_id, role"
    " FROM app.resolve_membership(:user_id, :tenant_id)"
)
_LIST_MEMBERSHIPS = text(
    "SELECT out_membership_id, out_tenant_id, out_tenant_name, out_tenant_kind, out_role"
    " FROM app.list_memberships(:user_id)"
)

# SQLSTATEs raised deliberately by app.provision_personal_identity (migrations 0003/0004).
_INSUFFICIENT_PRIVILEGE = "42501"
_INVALID_ARGUMENT = "22023"
_SUBJECT_CONFLICT = "VS001"
_EMAIL_CONFLICT = "VS002"
_BINDING_REQUIRED = "VS003"


class IdentityService:
    """Pre-tenant operations. Never accepts a caller-supplied tenant id as authority."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_signup_allowed(self, email: str) -> bool:
        result = await self._session.execute(_SIGNUP_ALLOWED, {"email": normalize_email(email)})
        return bool(result.scalar_one())

    async def find_user(
        self, *, email: str | None = None, external_id: str | None = None
    ) -> UserRecord | None:
        """Exact-key identity lookup. One of `email` or verified `external_id`."""
        if not email and not external_id:
            raise ValueError("find_user requires an email or an external_id")
        row = (
            await self._session.execute(
                _FIND_USER,
                {"email": normalize_email(email) if email else None, "external_id": external_id},
            )
        ).first()
        return None if row is None else UserRecord(*row)

    async def provision_personal_tenant(
        self, *, email: str, external_id: str, tenant_name: str | None = None
    ) -> ProvisionedIdentity:
        """Atomically create (or re-resolve) one user, personal tenant and membership.

        Idempotent under duplicate and concurrent calls. A rejected signup aborts
        the whole function, so no orphan user, tenant or membership is left behind.

        `external_id` is **required** and must come from a verified authentication
        adapter, never from a request payload. An email alone can neither resolve
        nor claim an account: migration 0004 removed the defaulted argument, so a
        subjectless call also fails at the SQL layer, but rejecting it here keeps
        the error typed and avoids opening a transaction at all.
        """
        subject = external_id.strip() if external_id else ""
        if not subject:
            raise SubjectRequired("a verified external subject is required")
        try:
            row = (
                await self._session.execute(
                    _PROVISION,
                    {
                        "email": email,
                        "external_id": subject,
                        "tenant_name": tenant_name,
                    },
                )
            ).one()
        except DBAPIError as error:
            raise _translate(error) from None
        return ProvisionedIdentity(*row)

    async def list_memberships(self, user_id: UUID) -> list[TenantMembership]:
        """Active memberships for one user, personal tenant first.

        Needed before a tenant context exists, so it goes through the bootstrap
        surface rather than a tenant-scoped policy.
        """
        rows = await self._session.execute(_LIST_MEMBERSHIPS, {"user_id": user_id})
        return [TenantMembership(*row) for row in rows]

    async def resolve_membership(self, user_id: UUID, tenant_id: UUID) -> MembershipRecord | None:
        """Authorized access resolution.

        Returns a membership only when the membership, the user and the tenant are
        all active. This is the check that must succeed before `tenant_scope`.
        """
        row = (
            await self._session.execute(_RESOLVE, {"user_id": user_id, "tenant_id": tenant_id})
        ).first()
        return None if row is None else MembershipRecord(*row)


def _translate(error: DBAPIError) -> Exception:
    """Map deliberate SQLSTATEs to typed domain errors.

    Matched by SQLSTATE only. The driver message is consulted for exactly one
    disambiguation and is never carried into the returned error, because it can
    contain the statement and its parameters. Anything unrecognised is returned
    unchanged, so an unexpected database failure stays a database failure rather
    than being flattened into a policy decision.
    """
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    message = str(getattr(error, "orig", ""))
    if sqlstate == _INVALID_ARGUMENT:
        # A missing email or a blank subject. Invalid input, not an outage.
        return InvalidIdentityInput("identity values were rejected by the bootstrap surface")
    if sqlstate == _SUBJECT_CONFLICT:
        return ExternalSubjectConflict("email belongs to a different external subject")
    if sqlstate == _EMAIL_CONFLICT:
        return EmailAlreadyBound("email already belongs to another identity")
    if sqlstate == _BINDING_REQUIRED:
        return SubjectBindingRequired("account exists without a bound external subject")
    if sqlstate == _INSUFFICIENT_PRIVILEGE:
        if "identity disabled" in message:
            return IdentityDisabled("identity is disabled")
        return SignupNotAllowed("email is not on the signup allowlist")
    return error
