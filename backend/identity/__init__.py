"""Identity and tenant foundations: bootstrap service plus tenant-scoped repository."""

from backend.identity.errors import (
    EmailAlreadyBound,
    ExternalSubjectConflict,
    IdentityDisabled,
    IdentityError,
    InvalidIdentityInput,
    SignupNotAllowed,
    SubjectBindingRequired,
    SubjectRequired,
)
from backend.identity.repository import MemberRecord, TenantRecord, TenantRepository
from backend.identity.service import (
    IdentityService,
    MembershipRecord,
    ProvisionedIdentity,
    TenantMembership,
    UserRecord,
    normalize_email,
)

__all__ = [
    "EmailAlreadyBound",
    "ExternalSubjectConflict",
    "IdentityDisabled",
    "IdentityError",
    "IdentityService",
    "InvalidIdentityInput",
    "MemberRecord",
    "MembershipRecord",
    "ProvisionedIdentity",
    "SignupNotAllowed",
    "SubjectBindingRequired",
    "SubjectRequired",
    "TenantMembership",
    "TenantRecord",
    "TenantRepository",
    "UserRecord",
    "normalize_email",
]
