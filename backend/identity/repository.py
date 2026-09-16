"""Tenant-scoped repository.

Every method reads the tenant boundary from the transaction-local context set by
`backend.storage.tenant_context.tenant_scope`. No method takes a tenant id, so a
request payload cannot widen or redirect the scope, and PostgreSQL row-level
security remains the authority rather than application-side filtering.
"""

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.storage.models import Membership, Tenant, User
from backend.storage.tenant_context import TenantContextError, active_tenant


@dataclass(frozen=True, slots=True)
class TenantRecord:
    id: UUID
    name: str
    kind: str
    owner_user_id: UUID
    status: str


@dataclass(frozen=True, slots=True)
class MemberRecord:
    user_id: UUID
    email: str
    role: str
    status: str


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        if active_tenant(session) is None:
            raise TenantContextError("TenantRepository requires an active tenant scope")
        self._session = session

    async def get_tenant(self) -> TenantRecord | None:
        """The current tenant. RLS restricts the result; no id is supplied."""
        row = (
            await self._session.execute(
                select(Tenant.id, Tenant.name, Tenant.kind, Tenant.owner_user_id, Tenant.status)
            )
        ).first()
        return None if row is None else TenantRecord(*row)

    async def rename_tenant(self, name: str) -> bool:
        """Rename the current tenant.

        The runtime role holds column-level UPDATE on (name, updated_at) only, so
        ownership and identity columns are unwritable even with a forged statement.
        """
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("tenant name must not be blank")
        # Deliberately unfiltered: row-level security scopes the statement. Adding a
        # redundant WHERE here would let a broken policy pass the isolation tests.
        result = cast(
            CursorResult[Any], await self._session.execute(update(Tenant).values(name=cleaned))
        )
        return bool(result.rowcount)

    async def list_members(self) -> list[MemberRecord]:
        """Members of the current tenant. Users outside it are invisible to RLS."""
        rows = await self._session.execute(
            select(Membership.user_id, User.email, Membership.role, Membership.status)
            .join(User, User.id == Membership.user_id)
            .order_by(User.email)
        )
        return [MemberRecord(*row) for row in rows]
