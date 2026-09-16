"""Transaction-local tenant context.

Trust boundary: the GUC read by `app.current_tenant_id()` is an *enforcement
input*, not authentication. PostgreSQL enforces isolation given a tenant id; it
cannot tell whether that id was authorized. The trusted backend must resolve an
active membership (see backend.identity) before opening a scope. These helpers
protect against context leaking or being switched mid-unit-of-work; they do not
protect against an attacker who already has arbitrary SQL execution with the
runtime credentials.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Custom GUC namespace; must match migration 0002.
TENANT_GUC = "vespers.tenant_id"

# Parameterized: the tenant id is a bind value, never interpolated into SQL.
# `true` makes the setting transaction-local, so COMMIT and ROLLBACK both discard
# it and a pooled connection cannot carry it into the next checkout.
_SET_CONTEXT = text("SELECT set_config(:name, :value, true)")

_SCOPE_KEY = "vespers_tenant_scope"


class TenantContextError(RuntimeError):
    """Misuse of the tenant scope. Never contains tenant data."""


async def _apply(session: AsyncSession, value: str) -> None:
    await session.execute(_SET_CONTEXT, {"name": TENANT_GUC, "value": value})


def active_tenant(session: AsyncSession) -> UUID | None:
    """Tenant currently bound to this session's unit of work, if any."""
    bound = session.info.get(_SCOPE_KEY)
    return bound if isinstance(bound, UUID) else None


@asynccontextmanager
async def tenant_scope(session: AsyncSession, tenant_id: UUID) -> AsyncIterator[AsyncSession]:
    """Bind a verified tenant id for the remainder of the current transaction.

    Re-entrant for the same tenant. Switching tenants inside an active unit of
    work is rejected rather than silently rebinding.
    """
    if not isinstance(tenant_id, UUID):
        raise TenantContextError("tenant_scope requires a UUID tenant id")
    if not session.in_transaction():
        # Outside a transaction a LOCAL setting would vanish with the implicit
        # single-statement transaction, silently disabling every policy.
        raise TenantContextError("tenant_scope requires an active transaction")

    bound = active_tenant(session)
    if bound is not None:
        if bound != tenant_id:
            raise TenantContextError("cannot switch tenant inside an active unit of work")
        yield session
        return

    await _apply(session, str(tenant_id))
    session.info[_SCOPE_KEY] = tenant_id
    try:
        yield session
    finally:
        session.info.pop(_SCOPE_KEY, None)
        if session.in_transaction():
            try:
                await _apply(session, "")
            except SQLAlchemyError:
                # The transaction is already failing; its rollback clears the
                # LOCAL setting regardless.
                pass


@asynccontextmanager
async def tenant_transaction(
    sessions: async_sessionmaker[AsyncSession], tenant_id: UUID
) -> AsyncIterator[AsyncSession]:
    """Open a session, begin a transaction, and bind the tenant context to it."""
    async with sessions() as session, session.begin(), tenant_scope(session, tenant_id):
        yield session
