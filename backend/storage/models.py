"""Tenant and identity foundation tables.

Ownership is deliberately non-uniform: `users` and `signup_allowlist` are global
platform tables and carry no `tenant_id`. Only `tenants` and `memberships` are
tenant-scoped. See docs/tenant-foundations.md for the access matrix.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.storage.base import Base

ACTIVE = "active"
DISABLED = "disabled"
STATUSES = (ACTIVE, DISABLED)

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"
ROLES = (ROLE_OWNER, ROLE_MEMBER)

KIND_PERSONAL = "personal"
KIND_TEAM = "team"
KINDS = (KIND_PERSONAL, KIND_TEAM)

# Single source of truth for normalization, mirrored by backend.identity.normalize_email.
# Deliberately case- and whitespace-only: no provider-specific dot or plus-tag stripping.
EMAIL_NORMALIZATION_SQL = "lower(btrim(email))"


def _status_check() -> CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in STATUSES)
    return CheckConstraint(f"status IN ({allowed})", name="status")


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Bumped by the ORM; direct SQL updates do not touch it (no trigger by design).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SignupAllowlist(TimestampMixin, Base):
    """Platform onboarding eligibility. Administrative provisioning only.

    The runtime role holds no grant on this table and no policy permits it;
    eligibility is read solely through app.signup_allowed().
    """

    __tablename__ = "signup_allowlist"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    email_normalized: Mapped[str] = mapped_column(
        Text, Computed(EMAIL_NORMALIZATION_SQL, persisted=True), nullable=False, unique=True
    )
    note: Mapped[str | None] = mapped_column(Text)


class User(Base, TimestampMixin):
    """Global application identity. Exists before any tenant is selected."""

    __tablename__ = "users"
    __table_args__ = (_status_check(),)

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    email_normalized: Mapped[str] = mapped_column(
        Text, Computed(EMAIL_NORMALIZATION_SQL, persisted=True), nullable=False, unique=True
    )
    # Verified external subject (WorkOS user id later). NULL until an adapter supplies it.
    external_id: Mapped[str | None] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=ACTIVE)


class Tenant(Base, TimestampMixin):
    """Tenant boundary. `id` is the scope key compared against the tenant context."""

    __tablename__ = "tenants"
    __table_args__ = (
        _status_check(),
        CheckConstraint("kind IN ('personal', 'team')", name="kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=KIND_PERSONAL)
    # Explicit ownership. RESTRICT: account deletion is a deliberate future workflow.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=ACTIVE)


class Membership(Base, TimestampMixin):
    """Link between a global identity and a tenant. Tenant-scoped."""

    __tablename__ = "memberships"
    __table_args__ = (
        _status_check(),
        CheckConstraint("role IN ('owner', 'member')", name="role"),
        Index("uq_memberships_user_id_tenant_id", "user_id", "tenant_id", unique=True),
        # Serves the RLS predicate `tenant_id = app.current_tenant_id()`.
        Index("ix_memberships_tenant_id", "tenant_id"),
        Index("ix_memberships_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=ROLE_MEMBER)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=ACTIVE)
