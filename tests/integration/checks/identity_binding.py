# ruff: noqa: E501
"""Identity-binding and default-grant checks against real PostgreSQL.

Runs INSIDE the api container as the runtime role `vespers_app`. These are the
regressions for the two defects migration 0003 corrects; a mocked database would
prove nothing about either, since both live entirely in SQL.
"""

import asyncio
import os
import sys
import uuid

import psycopg
from psycopg import errors

from backend.config import get_settings
from backend.identity import (
    EmailAlreadyBound,
    ExternalSubjectConflict,
    IdentityService,
    SignupNotAllowed,
    SubjectBindingRequired,
    SubjectRequired,
)
from backend.storage.session import create_engine, create_sessionmaker

URL = os.environ["VESPERS_DATABASE_URL"].replace("postgresql+psycopg:", "postgresql:")

ALICE = "alice@synthetic.invalid"
BOB = "bob@synthetic.invalid"
ALICE_NEW = "alice.new@synthetic.invalid"
UNLISTED = "nobody@synthetic.invalid"
LEGACY = "legacy@synthetic.invalid"

passed: list[str] = []


def ok(label: str) -> None:
    passed.append(label)


def connect() -> psycopg.Connection:
    return psycopg.connect(URL)


async def run() -> None:
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)

    async def provision(**kwargs: object) -> object:
        async with sessions() as session, session.begin():
            return await IdentityService(session).provision_personal_tenant(**kwargs)  # type: ignore[arg-type]

    try:
        # --- Baseline: two distinct subjects, two distinct accounts ---------
        alice = await provision(email=ALICE, external_id="workos_alice", tenant_name="Alice")
        bob = await provision(email=BOB, external_id="workos_bob", tenant_name="Bob")
        assert alice.user_id != bob.user_id  # type: ignore[attr-defined]
        ok("two verified subjects provision two separate identities and tenants")

        # --- Duplicate callback is idempotent -------------------------------
        again = await provision(email=ALICE, external_id="workos_alice")
        assert (again.user_id, again.tenant_id, again.membership_id) == (  # type: ignore[attr-defined]
            alice.user_id,  # type: ignore[attr-defined]
            alice.tenant_id,  # type: ignore[attr-defined]
            alice.membership_id,  # type: ignore[attr-defined]
        )
        assert again.created is False  # type: ignore[attr-defined]
        ok("duplicate callback for the same subject is idempotent")

        # --- THE DEFECT 0003 FIXES ------------------------------------------
        # A second subject presenting Alice's verified email must not be handed
        # Alice's account. Before 0003 this returned Alice's user_id.
        try:
            await provision(email=ALICE, external_id="workos_impostor")
            raise AssertionError("a different subject was handed an existing account")
        except ExternalSubjectConflict:
            pass
        with connect() as conn:
            row = conn.execute(
                "SELECT id, external_id FROM app.find_user(%s, NULL)", (ALICE,)
            ).fetchone()
            assert row is not None and str(row[0]) == str(alice.user_id)  # type: ignore[attr-defined]
            assert row[1] == "workos_alice", row[1]
            assert (
                conn.execute(
                    "SELECT count(*) FROM app.find_user(NULL, %s)", ("workos_impostor",)
                ).fetchone()[0]
                == 0
            )
        ok("a second subject claiming an existing verified email fails closed and creates nothing")

        # --- Email change keeps ownership, never merges ----------------------
        moved = await provision(email=ALICE_NEW, external_id="workos_alice")
        assert moved.user_id == alice.user_id  # type: ignore[attr-defined]
        assert moved.tenant_id == alice.tenant_id  # type: ignore[attr-defined]
        with connect() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM app.find_user(%s, NULL)", (ALICE_NEW,)
                ).fetchone()[0]
                == 1
            )
        ok("an upstream email change follows the subject without transferring ownership")

        # Alice's subject must not be able to take Bob's address.
        try:
            await provision(email=BOB, external_id="workos_alice")
            raise AssertionError("email belonging to another identity was merged")
        except EmailAlreadyBound:
            pass
        with connect() as conn:
            row = conn.execute("SELECT id FROM app.find_user(%s, NULL)", (BOB,)).fetchone()
            assert row is not None and str(row[0]) == str(bob.user_id)  # type: ignore[attr-defined]
        ok("a known subject cannot claim an address already bound to another identity")

        # --- Allowlist is enforced on every call, so revocation bites --------
        try:
            await provision(email=UNLISTED, external_id="workos_unlisted")
            raise AssertionError("unallowlisted signup accepted")
        except SignupNotAllowed:
            pass
        with connect() as conn:
            assert (
                conn.execute(
                    "SELECT count(*) FROM app.find_user(NULL, %s)", ("workos_unlisted",)
                ).fetchone()[0]
                == 0
            )
        ok("an unallowlisted subject is refused and leaves no orphan user, tenant, or membership")

        # --- Concurrent first sign-in ----------------------------------------
        async def race() -> object:
            return await provision(email=BOB, external_id="workos_bob")

        results = await asyncio.gather(race(), race(), race(), race())
        assert len({r.user_id for r in results}) == 1  # type: ignore[attr-defined]
        assert len({r.tenant_id for r in results}) == 1  # type: ignore[attr-defined]
        assert len({r.membership_id for r in results}) == 1  # type: ignore[attr-defined]
        ok("concurrent callbacks for one subject converge on a single identity and tenant")

        # --- The subjectless path is closed ----------------------------------
        # Every one of these must fail BEFORE resolving or creating anything. A
        # blank subject is not authentication, and an email alone must never
        # resolve an account that already belongs to a verified subject.
        for blank in ("", "   ", "\t"):
            try:
                await provision(email=ALICE_NEW, external_id=blank)
                raise AssertionError("a blank subject was accepted")
            except SubjectRequired:
                pass
        # The SQL layer refuses it too, independently of the Python guard: the
        # defaulted argument is gone, so a two-argument call does not resolve.
        with connect() as conn:
            try:
                with conn.transaction():
                    conn.execute("SELECT * FROM app.provision_personal_identity(%s)", (ALICE_NEW,))
            except (errors.UndefinedFunction, errors.InvalidParameterValue):
                pass
            else:
                raise AssertionError("provisioning resolved without a subject")
            try:
                with conn.transaction():
                    conn.execute(
                        "SELECT * FROM app.provision_personal_identity(%s, NULL)", (ALICE_NEW,)
                    )
            except errors.Error as error:
                assert error.sqlstate == "22023", error.sqlstate
            else:
                raise AssertionError("an explicit NULL subject was accepted")
        # And nothing moved: Alice still owns her address, with her subject.
        with connect() as conn:
            row = conn.execute(
                "SELECT id, external_id FROM app.find_user(%s, NULL)", (ALICE_NEW,)
            ).fetchone()
            assert row is not None and row[1] == "workos_alice", row
        ok("a missing or blank subject cannot authenticate, claim an account, or create one")

        # --- list_memberships is scoped to the requested user ----------------
        async with sessions() as session, session.begin():
            service = IdentityService(session)
            mine = await service.list_memberships(alice.user_id)  # type: ignore[attr-defined]
            assert [m.tenant_id for m in mine] == [alice.tenant_id]  # type: ignore[attr-defined]
            assert await service.list_memberships(uuid.uuid4()) == []
        ok("membership listing returns only the requested user's active tenants")
    finally:
        await engine.dispose()


def check_default_privileges_are_opt_in() -> None:
    """A newly created migration-owned table must grant runtime nothing."""
    with connect() as conn:
        for table, privilege in (
            ("privilege_probe_new", "SELECT"),
            ("privilege_probe_new", "INSERT"),
            ("privilege_probe_new", "UPDATE"),
            ("privilege_probe_new", "DELETE"),
        ):
            granted = conn.execute(
                "SELECT has_table_privilege(current_user, %s, %s)", (table, privilege)
            ).fetchone()[0]
            assert granted is False, f"runtime unexpectedly has {privilege} on {table}"
        try:
            with conn.transaction():
                conn.execute("SELECT * FROM privilege_probe_new")
        except errors.InsufficientPrivilege:
            pass
        else:
            raise AssertionError("runtime could read a table it was never granted")
        # And the explicitly granted foundation tables still work.
        assert (
            conn.execute(
                "SELECT has_table_privilege(current_user, 'tenants', 'SELECT')"
            ).fetchone()[0]
            is True
        )
        assert (
            conn.execute(
                "SELECT has_table_privilege(current_user, 'tenants', 'INSERT')"
            ).fetchone()[0]
            is False
        )
    ok("a new migration-owned table grants runtime nothing; existing narrow grants survive")


async def unbound() -> None:
    """A user row carrying no external subject must not be claimable by sign-in.

    The row was inserted by the migration owner before this phase, reproducing a
    database that predates migration 0004. After 0004 nothing creates one.
    """
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        async with sessions() as session, session.begin():
            try:
                await IdentityService(session).provision_personal_tenant(
                    email=LEGACY, external_id="workos_opportunist"
                )
                raise AssertionError("an unbound account was claimed by signing in")
            except SubjectBindingRequired:
                pass
        with connect() as conn:
            row = conn.execute(
                "SELECT external_id FROM app.find_user(%s, NULL)", (LEGACY,)
            ).fetchone()
            assert row is not None and row[0] is None, row
            assert (
                conn.execute(
                    "SELECT count(*) FROM app.find_user(NULL, %s)", ("workos_opportunist",)
                ).fetchone()[0]
                == 0
            )
        ok("a user row with no bound subject fails closed instead of being adopted")
    finally:
        await engine.dispose()


async def bound() -> None:
    """Administrative binding — not signing in — is what makes a legacy row usable."""
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        async with sessions() as session, session.begin():
            result = await IdentityService(session).provision_personal_tenant(
                email=LEGACY, external_id="workos_legacy"
            )
        assert result.created is True, result
        ok("administrative subject binding is what makes a legacy row usable, not sign-in")
    finally:
        await engine.dispose()


async def revoked() -> None:
    """Revocation bites on the next call, and does not delete the account.

    The allowlist entry was removed and the tenant disabled by the migration
    owner before this phase, with no restart and no token expiry in between.
    """
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        async with sessions() as session, session.begin():
            try:
                await IdentityService(session).provision_personal_tenant(
                    email=LEGACY, external_id="workos_legacy"
                )
                raise AssertionError("a de-allowlisted account was re-provisioned")
            except SignupNotAllowed:
                pass
        async with sessions() as session, session.begin():
            service = IdentityService(session)
            # These three reads are exactly what authorize_local_access re-runs
            # on every protected request, so a live session is refused too.
            assert await service.is_signup_allowed(LEGACY) is False
            user = await service.find_user(external_id="workos_legacy")
            assert user is not None and user.is_active, user
            assert await service.list_memberships(user.id) == []
        ok("allowlist removal and tenant disablement revoke access without deleting the account")
    finally:
        await engine.dispose()


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    if phase in ("all", "grants"):
        check_default_privileges_are_opt_in()
    if phase in ("all", "binding"):
        asyncio.run(run())
    if phase == "unbound":
        asyncio.run(unbound())
    if phase == "bound":
        asyncio.run(bound())
    if phase == "revoked":
        asyncio.run(revoked())
    for label in passed:
        print("  ok " + label)


if __name__ == "__main__":
    main()
