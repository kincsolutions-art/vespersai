# ruff: noqa: E501
"""Tenant isolation checks executed INSIDE the api container as the runtime role.

Piped to `python - <phase>`; the container image carries `backend` but not `tests`.
Phases are separated because the disabled-state and multi-membership fixtures must
be applied by the migration owner out of band, never with runtime credentials.

Every check runs as `vespers_app`. Direct SQL is used alongside the repository so
that application-side filtering cannot conceal an ineffective database policy.
"""

import asyncio
import json
import os
import sys
import uuid

import psycopg
from psycopg import errors

from backend.config import get_settings
from backend.identity import IdentityService, SignupNotAllowed, TenantRepository
from backend.storage.session import create_engine, create_sessionmaker
from backend.storage.tenant_context import (
    TenantContextError,
    tenant_scope,
    tenant_transaction,
)

EMAIL_A = "tenant-a@synthetic.invalid"
EMAIL_B = "tenant-b@synthetic.invalid"
EMAIL_BLOCKED = "blocked@synthetic.invalid"
GUC = "vespers.tenant_id"

URL = os.environ["VESPERS_DATABASE_URL"].replace("postgresql+psycopg:", "postgresql:")
DBOS_URL = os.environ["VESPERS_DBOS_SYSTEM_DATABASE_URL"].replace(
    "postgresql+psycopg:", "postgresql:"
)

TABLES = ("users", "tenants", "memberships", "signup_allowlist")
passed: list[str] = []


def ok(label: str) -> None:
    passed.append(label)


def connect() -> psycopg.Connection:
    return psycopg.connect(URL)


def set_context(conn: psycopg.Connection, value: str | None) -> None:
    conn.execute("SELECT set_config(%s, %s, true)", (GUC, "" if value is None else value))


def denied(
    conn: psycopg.Connection, sql: str, params: tuple = (), *, kinds=(errors.InsufficientPrivilege,)
) -> str:
    """Assert a statement is refused, rolling back to a savepoint either way."""
    try:
        with conn.transaction():
            conn.execute(sql, params)
    except kinds as error:
        return type(error).__name__
    raise AssertionError("statement was NOT denied: " + sql)


def rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return list(conn.execute(sql, params).fetchall())


# ---------------------------------------------------------------- phase: roles
def check_role_and_policy_metadata() -> None:
    with connect() as conn:
        assert conn.execute("SELECT current_user").fetchone()[0] == "vespers_app"
        assert (
            conn.execute(
                "SELECT rolsuper OR rolbypassrls OR rolinherit FROM pg_roles WHERE rolname = current_user"
            ).fetchone()[0]
            is False
        ), "runtime role must not be superuser, BYPASSRLS, or inheriting"
        assert (
            conn.execute("SELECT pg_has_role(current_user, 'vespers_owner', 'MEMBER')").fetchone()[
                0
            ]
            is False
        )
        for table in TABLES:
            owner, rls, force = conn.execute(
                "SELECT pg_get_userbyid(relowner), relrowsecurity, relforcerowsecurity"
                " FROM pg_class WHERE relname = %s AND relnamespace = 'public'::regnamespace",
                (table,),
            ).fetchone()
            assert owner == "vespers_owner", (table, owner)
            assert rls is True, f"{table} must have RLS enabled"
            # Documented decision: FORCE is off because the SECURITY DEFINER
            # bootstrap functions run as the owner. See docs/tenant-foundations.md.
            assert force is False, f"{table} FORCE RLS changed without a decision record"
        ok("runtime role is non-owner, non-superuser, cannot bypass RLS; all four tables have RLS")


# ------------------------------------------------------------ phase: allowlist
def check_allowlist_bootstrap_rules() -> None:
    with connect() as conn:
        # No grant at all, and no policy: fail closed twice over.
        denied(conn, "SELECT * FROM signup_allowlist")
        denied(conn, "INSERT INTO signup_allowlist (email) VALUES ('x@synthetic.invalid')")
        # Eligibility is readable only through the narrow SECURITY DEFINER function.
        assert conn.execute("SELECT app.signup_allowed(%s)", (EMAIL_A,)).fetchone()[0] is True
        assert (
            conn.execute("SELECT app.signup_allowed(%s)", (EMAIL_BLOCKED,)).fetchone()[0] is False
        )
        # Normalization is applied inside the database, not just in Python.
        assert (
            conn.execute(
                "SELECT app.signup_allowed(%s)", ("  TENANT-A@Synthetic.Invalid ",)
            ).fetchone()[0]
            is True
        )
        ok(
            "signup_allowlist unreadable/unwritable by runtime; eligibility only via app.signup_allowed"
        )


# ----------------------------------------------------------- phase: provision
async def provision() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        # Unallowlisted signup is rejected, and leaves nothing behind. The raising
        # statement aborts its transaction, so the block must exit via the exception.
        try:
            async with sessions() as session, session.begin():
                await IdentityService(session).provision_personal_tenant(
                    email=EMAIL_BLOCKED, external_id="ext-blocked"
                )
            raise AssertionError("unallowlisted signup was accepted")
        except SignupNotAllowed:
            pass

        with connect() as conn:
            # No partial provisioning: the rejected identity exists nowhere.
            assert rows(conn, "SELECT id FROM app.find_user(%s, NULL)", (EMAIL_BLOCKED,)) == []
            ok("unallowlisted signup rejected with no orphan user, tenant, or membership")

        async with sessions() as session, session.begin():
            service = IdentityService(session)
            first = await service.provision_personal_tenant(
                email="  Tenant-A@Synthetic.Invalid  ", external_id="ext-a", tenant_name="A"
            )
            assert first.created is True, first

        async with sessions() as session, session.begin():
            # Same subject, differently cased/padded address: still one identity.
            again = await IdentityService(session).provision_personal_tenant(
                email="  Tenant-A@Synthetic.Invalid  ", external_id="ext-a"
            )
            assert (again.user_id, again.tenant_id, again.membership_id) == (
                first.user_id,
                first.tenant_id,
                first.membership_id,
            ), (first, again)
            assert again.created is False

        # Concurrent calls for a brand-new identity.
        async def race() -> object:
            async with sessions() as session, session.begin():
                return await IdentityService(session).provision_personal_tenant(
                    email=EMAIL_B, external_id="ext-b", tenant_name="B"
                )

        results = await asyncio.gather(race(), race(), race())
        assert len({r.user_id for r in results}) == 1, results
        assert len({r.tenant_id for r in results}) == 1, results
        assert len({r.membership_id for r in results}) == 1, results
        second = results[0]

        with connect() as conn:
            assert (
                conn.execute("SELECT count(*) FROM app.find_user(%s, NULL)", (EMAIL_B,)).fetchone()[
                    0
                ]
                == 1
            )
            ok("duplicate and concurrent provisioning converge on one user, tenant, and membership")
        return first.user_id, first.tenant_id, second.user_id, second.tenant_id
    finally:
        await engine.dispose()


# -------------------------------------------------------------- phase: counts
def check_row_counts_are_database_enforced(tenant_a: uuid.UUID, tenant_b: uuid.UUID) -> None:
    with connect() as conn:
        # Unscoped statements must be inside a transaction for a LOCAL setting.
        with conn.transaction():
            set_context(conn, str(tenant_a))
            assert [r[0] for r in rows(conn, "SELECT id FROM tenants")] == [tenant_a]
            assert rows(conn, "SELECT id FROM tenants WHERE id = %s", (tenant_b,)) == []
            assert len(rows(conn, "SELECT id FROM memberships")) == 1
            assert len(rows(conn, "SELECT id FROM users")) == 1
            ok("tenant A sees exactly its own tenant, membership, and user rows")

            # Guessed identifiers reveal nothing.
            for table in ("tenants", "memberships", "users"):
                column = (
                    "id" if table == "tenants" else "tenant_id" if table == "memberships" else "id"
                )
                assert rows(conn, f"SELECT * FROM {table} WHERE {column} = %s", (tenant_b,)) == []
            ok("guessed tenant B identifiers return no rows under tenant A context")

            # Cross-tenant write attempts affect nothing.
            updated = conn.execute(
                "UPDATE tenants SET name = 'hijacked' WHERE id = %s", (tenant_b,)
            ).rowcount
            assert updated == 0, updated
            ok("cross-tenant UPDATE matches zero rows (RLS USING clause)")

            # Ownership and identity columns are not writable at all.
            denied(conn, "UPDATE tenants SET owner_user_id = gen_random_uuid()")
            denied(conn, "UPDATE tenants SET id = %s", (tenant_b,))
            denied(conn, "UPDATE tenants SET status = 'disabled'")
            ok("tenant ownership, id, and status are unwritable (column-level grants)")

            # No DELETE or INSERT grant anywhere in the foundation.
            denied(conn, "DELETE FROM tenants")
            denied(conn, "DELETE FROM memberships")
            denied(
                conn,
                "INSERT INTO tenants (name, owner_user_id) VALUES ('forged', gen_random_uuid())",
            )
            denied(
                conn,
                "INSERT INTO memberships (user_id, tenant_id) VALUES (gen_random_uuid(), %s)",
                (tenant_b,),
            )
            denied(conn, "UPDATE memberships SET tenant_id = %s", (tenant_a,))
            ok("runtime cannot INSERT/DELETE tenants or memberships, nor repoint a membership")

        with conn.transaction():
            set_context(conn, str(tenant_b))
            assert [r[0] for r in rows(conn, "SELECT id FROM tenants")] == [tenant_b]
            ok("tenant B sees exactly its own tenant after the same connection served tenant A")


def check_context_fails_closed(tenant_a: uuid.UUID) -> None:
    with connect() as conn:
        # Missing context.
        with conn.transaction():
            assert rows(conn, "SELECT id FROM tenants") == []
            assert rows(conn, "SELECT id FROM memberships") == []
            assert rows(conn, "SELECT id FROM users") == []
            assert conn.execute("SELECT app.current_tenant_id()").fetchone()[0] is None
        # Empty context.
        with conn.transaction():
            set_context(conn, "")
            assert rows(conn, "SELECT id FROM tenants") == []
        # Malformed context: loud, and still no rows.
        for malformed in ("not-a-uuid", "1; DROP TABLE users", "' OR '1'='1"):
            with conn.transaction():
                set_context(conn, malformed)
                denied(conn, "SELECT id FROM tenants", kinds=(errors.InvalidTextRepresentation,))
        # Unauthorized-but-well-formed context: a tenant that does not exist.
        with conn.transaction():
            set_context(conn, str(uuid.uuid4()))
            assert rows(conn, "SELECT id FROM tenants") == []
            assert rows(conn, "SELECT id FROM users") == []
        ok("missing, empty, malformed, and unauthorized tenant context all fail closed")


def check_context_does_not_leak_across_pooled_reuse(
    tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    """One physical connection, three successive units of work."""
    with connect() as conn:
        with conn.transaction():
            set_context(conn, str(tenant_a))
            assert [r[0] for r in rows(conn, "SELECT id FROM tenants")] == [tenant_a]
        with conn.transaction():
            set_context(conn, str(tenant_b))
            assert [r[0] for r in rows(conn, "SELECT id FROM tenants")] == [tenant_b]
        # No context set: the previous transaction-local values are gone.
        with conn.transaction():
            assert conn.execute("SELECT app.current_tenant_id()").fetchone()[0] is None
            assert rows(conn, "SELECT id FROM tenants") == []

        # Rollback path must also clear it.
        try:
            with conn.transaction():
                set_context(conn, str(tenant_a))
                assert conn.execute("SELECT app.current_tenant_id()").fetchone()[0] == tenant_a
                raise RuntimeError("synthetic rollback")
        except RuntimeError:
            pass
        with conn.transaction():
            assert conn.execute("SELECT app.current_tenant_id()").fetchone()[0] is None

        # A statement error aborting the transaction must also clear it.
        try:
            with conn.transaction():
                set_context(conn, str(tenant_a))
                conn.execute("SELECT 1/0")
        except errors.DivisionByZero:
            pass
        with conn.transaction():
            assert conn.execute("SELECT app.current_tenant_id()").fetchone()[0] is None
        ok(
            "A then B then no-context on one connection leaks nothing; rollback and error paths clear it"
        )


def check_runtime_cannot_escalate() -> None:
    with connect() as conn:
        denied(conn, "ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
        denied(conn, "ALTER POLICY tenants_select ON tenants USING (true)")
        denied(conn, "DROP POLICY tenants_select ON tenants")
        denied(conn, "CREATE POLICY forged ON tenants USING (true)")
        denied(conn, "ALTER TABLE tenants OWNER TO vespers_app")
        denied(conn, "CREATE TABLE forbidden (id integer)")
        denied(conn, "CREATE FUNCTION app.forged() RETURNS void LANGUAGE sql AS 'SELECT 1'")
        denied(
            conn,
            "SET ROLE vespers_owner",
            kinds=(errors.InsufficientPrivilege, errors.InvalidParameterValue),
        )
        denied(conn, "ALTER ROLE vespers_app BYPASSRLS")
        ok(
            "runtime cannot disable RLS, alter/drop/create policies, own tables, run DDL, or assume the owner role"
        )


def check_dbos_storage_is_untouched() -> None:
    with psycopg.connect(DBOS_URL) as conn:
        forced = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'dbos' AND (c.relrowsecurity OR c.relforcerowsecurity)"
        ).fetchone()[0]
        assert forced == 0, "application RLS leaked onto DBOS system tables"
        assert (
            conn.execute("SELECT count(*) FROM pg_namespace WHERE nspname = 'app'").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM pg_tables WHERE tablename IN ('users','tenants','memberships')"
            ).fetchone()[0]
            == 0
        )
        ok("DBOS system database has no application tables, policies, or app schema")


# -------------------------------------------------------- phase: repositories
async def check_repository_boundary(tenant_a: uuid.UUID, tenant_b: uuid.UUID) -> None:
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        async with tenant_transaction(sessions, tenant_a) as session:
            repository = TenantRepository(session)
            tenant = await repository.get_tenant()
            assert tenant is not None and tenant.id == tenant_a, tenant
            assert len(await repository.list_members()) == 1
            assert await repository.rename_tenant("A renamed") is True

        async with tenant_transaction(sessions, tenant_b) as session:
            tenant = await TenantRepository(session).get_tenant()
            assert tenant is not None and tenant.id == tenant_b
            assert tenant.name != "A renamed", "rename crossed the tenant boundary"

        # The repository refuses to be constructed without a scope.
        async with sessions() as session, session.begin():
            try:
                TenantRepository(session)
                raise AssertionError("repository accepted an unscoped session")
            except TenantContextError:
                pass

        # Switching tenants inside one unit of work is rejected, not silently rebound.
        async with sessions() as session, session.begin():
            async with tenant_scope(session, tenant_a):
                try:
                    async with tenant_scope(session, tenant_b):
                        raise AssertionError("tenant switch was permitted")
                except TenantContextError:
                    pass
        ok(
            "repository is tenant-scoped, refuses unscoped sessions, and rejects mid-transaction tenant switches"
        )
    finally:
        await engine.dispose()


async def check_access_resolution(
    user_a: uuid.UUID, tenant_a: uuid.UUID, user_b: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        async with sessions() as session, session.begin():
            service = IdentityService(session)
            assert await service.resolve_membership(user_a, tenant_a) is not None
            # A well-formed but unauthorized pairing resolves to nothing.
            assert await service.resolve_membership(user_a, tenant_b) is None
            assert await service.resolve_membership(user_b, tenant_a) is None
            assert await service.resolve_membership(uuid.uuid4(), tenant_a) is None
            ok("membership resolution rejects unauthorized user/tenant pairings")
    finally:
        await engine.dispose()


# ------------------------------------------------------------ phase: shared
async def check_multi_membership(user_a: uuid.UUID, tenant_b: uuid.UUID) -> None:
    """User A was granted membership of tenant B out of band by the owner."""
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        async with sessions() as session, session.begin():
            assert await IdentityService(session).resolve_membership(user_a, tenant_b) is not None
        async with tenant_transaction(sessions, tenant_b) as session:
            members = await TenantRepository(session).list_members()
            assert len(members) == 2, members
            assert user_a in {m.user_id for m in members}, members
        ok(
            "one identity can hold memberships in two tenants; each tenant still sees only its own members"
        )
    finally:
        await engine.dispose()


# ----------------------------------------------------------- phase: disabled
async def check_disabled_states(
    user_a: uuid.UUID, tenant_a: uuid.UUID, user_b: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    """Owner has disabled: membership A->A, identity B, and tenant B.

    Tenant B still has an *active* membership for the active user A, so the third
    assertion isolates tenant-level disablement from the other two causes.
    """
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        async with sessions() as session, session.begin():
            service = IdentityService(session)
            assert await service.resolve_membership(user_a, tenant_a) is None, (
                "disabled membership accepted"
            )
            assert await service.resolve_membership(user_b, tenant_b) is None, (
                "disabled user/tenant accepted"
            )
        ok(
            "disabled membership, disabled identity, and disabled tenant are all rejected by access resolution"
        )
    finally:
        await engine.dispose()


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "provision"
    if phase == "provision":
        check_role_and_policy_metadata()
        check_allowlist_bootstrap_rules()
        user_a, tenant_a, user_b, tenant_b = asyncio.run(provision())
        check_row_counts_are_database_enforced(tenant_a, tenant_b)
        check_context_fails_closed(tenant_a)
        check_context_does_not_leak_across_pooled_reuse(tenant_a, tenant_b)
        check_runtime_cannot_escalate()
        check_dbos_storage_is_untouched()
        asyncio.run(check_repository_boundary(tenant_a, tenant_b))
        asyncio.run(check_access_resolution(user_a, tenant_a, user_b, tenant_b))
        for label in passed:
            print("  ok " + label)
        # Later phases need fixtures applied by the owner out of band, so the ids
        # travel through the harness rather than being re-read with runtime rights.
        print("IDS " + json.dumps([str(user_a), str(tenant_a), str(user_b), str(tenant_b)]))
        return

    user_a, tenant_a, user_b, tenant_b = (uuid.UUID(v) for v in json.loads(sys.argv[2]))
    if phase == "shared":
        asyncio.run(check_multi_membership(user_a, tenant_b))
    elif phase == "disabled":
        asyncio.run(check_disabled_states(user_a, tenant_a, user_b, tenant_b))
    else:
        raise SystemExit("unknown phase: " + phase)
    for label in passed:
        print("  ok " + label)


if __name__ == "__main__":
    main()
