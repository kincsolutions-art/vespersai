class IdentityError(Exception):
    """Base for identity failures. Messages carry no credential or tenant data."""


class SignupNotAllowed(IdentityError):
    """Email is not on the platform signup allowlist."""


class IdentityDisabled(IdentityError):
    """User, tenant, or membership is disabled."""


class ExternalSubjectConflict(IdentityError):
    """The verified email already belongs to a different external subject.

    Never resolved automatically: merging accounts on a shared email address is
    exactly how one identity takes over another. A human must intervene.
    """


class EmailAlreadyBound(IdentityError):
    """A known subject presented an email that belongs to another identity."""


class SubjectRequired(IdentityError):
    """Provisioning was attempted without a verified external subject.

    Authentication always has one. A call without one is not authentication, and
    must never be able to resolve or claim an account by email alone.
    """


class SubjectBindingRequired(IdentityError):
    """An account exists for this email but carries no external subject.

    Only reachable for rows predating migration 0004 or inserted directly by the
    migration owner. Binding is a deliberate administrative act
    (`infra/bind-subject.sql`), never an automatic consequence of signing in.
    """
