"""Offline guards for the tenant context and repository boundary.

Real isolation is proved against PostgreSQL by tests/integration/checks/
tenant_isolation.py; these tests cover the Python-side invariants that no
database can enforce for us.
"""

import asyncio
import inspect
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from backend.identity import (
    EmailAlreadyBound,
    ExternalSubjectConflict,
    IdentityService,
    SubjectBindingRequired,
    SubjectRequired,
    TenantRepository,
    normalize_email,
)
from backend.identity.service import _translate
from backend.storage.models import EMAIL_NORMALIZATION_SQL
from backend.storage.tenant_context import (
    TENANT_GUC,
    TenantContextError,
    active_tenant,
    tenant_scope,
)

TENANT_A = UUID("11111111-1111-4111-8111-111111111111")
TENANT_B = UUID("22222222-2222-4222-8222-222222222222")


class FakeSession:
    """Minimal AsyncSession stand-in recording the statements a scope issues."""

    def __init__(self, *, in_transaction: bool = True) -> None:
        self._in_transaction = in_transaction
        self.info: dict[str, Any] = {}
        self.executed: list[tuple[str, Any]] = []
        self.rows: list[tuple[Any, ...]] = []

    def in_transaction(self) -> bool:
        return self._in_transaction

    async def execute(self, statement: Any, params: Any = None) -> "FakeResult":
        self.executed.append((str(statement), params))
        return FakeResult(self.rows)


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def one(self) -> tuple[Any, ...]:
        return self._rows[0]

    def first(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


def session_as_async(session: FakeSession) -> Any:
    return session


# ---- Email normalization ------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Person@Example.com", "person@example.com"),
        ("  person@example.com  ", "person@example.com"),
        ("PERSON@EXAMPLE.COM", "person@example.com"),
    ],
)
def test_normalize_email_handles_case_and_whitespace(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["per.son@example.com", "person+tag@example.com"],
)
def test_normalize_email_makes_no_provider_specific_alias_assumptions(raw: str) -> None:
    """Dots and plus tags identify distinct addresses at most providers."""
    assert normalize_email(raw) == raw
    assert normalize_email(raw) != "person@example.com"


def test_python_normalization_matches_the_generated_column_expression() -> None:
    """Python and the email_normalized generated column must not drift apart."""
    assert EMAIL_NORMALIZATION_SQL == "lower(btrim(email))"


# ---- Tenant context guards ----------------------------------------------


def test_context_statement_is_parameterized_and_transaction_local() -> None:
    from backend.storage.tenant_context import _SET_CONTEXT

    sql = str(_SET_CONTEXT)
    assert ":name" in sql and ":value" in sql, "tenant id must be a bind parameter"
    assert "true" in sql, "set_config must be transaction-local"
    assert TENANT_GUC == "vespers.tenant_id"


def test_scope_requires_an_active_transaction() -> None:
    async def run() -> None:
        session = FakeSession(in_transaction=False)
        with pytest.raises(TenantContextError, match="active transaction"):
            async with tenant_scope(session_as_async(session), TENANT_A):
                pass

    asyncio.run(run())


def test_scope_rejects_a_non_uuid_tenant_id() -> None:
    async def run() -> None:
        session = FakeSession()
        with pytest.raises(TenantContextError, match="UUID"):
            async with tenant_scope(
                session_as_async(session), "11111111-1111-4111-8111-111111111111"
            ):  # type: ignore[arg-type]
                pass

    asyncio.run(run())


def test_scope_sets_then_clears_the_context() -> None:
    async def run() -> None:
        session = FakeSession()
        async with tenant_scope(session_as_async(session), TENANT_A):
            assert active_tenant(session_as_async(session)) == TENANT_A
        assert active_tenant(session_as_async(session)) is None
        values = [params["value"] for _, params in session.executed]
        assert values == [str(TENANT_A), ""], values

    asyncio.run(run())


def test_scope_clears_the_context_when_the_body_raises() -> None:
    async def run() -> None:
        session = FakeSession()
        with pytest.raises(ValueError, match="boom"):
            async with tenant_scope(session_as_async(session), TENANT_A):
                raise ValueError("boom")
        assert active_tenant(session_as_async(session)) is None
        assert [params["value"] for _, params in session.executed] == [str(TENANT_A), ""]

    asyncio.run(run())


def test_scope_rejects_switching_tenant_inside_one_unit_of_work() -> None:
    async def run() -> None:
        session = FakeSession()
        async with tenant_scope(session_as_async(session), TENANT_A):
            with pytest.raises(TenantContextError, match="cannot switch tenant"):
                async with tenant_scope(session_as_async(session), TENANT_B):
                    pass
            # The original binding survives the rejected switch.
            assert active_tenant(session_as_async(session)) == TENANT_A

    asyncio.run(run())


def test_scope_is_reentrant_for_the_same_tenant_without_resetting_early() -> None:
    async def run() -> None:
        session = FakeSession()
        async with tenant_scope(session_as_async(session), TENANT_A):
            async with tenant_scope(session_as_async(session), TENANT_A):
                pass
            assert active_tenant(session_as_async(session)) == TENANT_A
        assert active_tenant(session_as_async(session)) is None
        assert [params["value"] for _, params in session.executed] == [str(TENANT_A), ""]

    asyncio.run(run())


# ---- Repository boundary -------------------------------------------------


def test_repository_refuses_a_session_without_tenant_scope() -> None:
    session = FakeSession()
    with pytest.raises(TenantContextError, match="active tenant scope"):
        TenantRepository(session_as_async(session))


@pytest.mark.parametrize("name", ["get_tenant", "rename_tenant", "list_members"])
def test_repository_methods_accept_no_tenant_identity(name: str) -> None:
    """A payload must not be able to redirect or widen the tenant scope."""
    parameters = set(inspect.signature(getattr(TenantRepository, name)).parameters) - {"self"}
    assert not {p for p in parameters if "tenant" in p or "id" in p}, parameters


def test_rename_rejects_a_blank_name() -> None:
    async def run() -> None:
        session = FakeSession()
        session.info["vespers_tenant_scope"] = TENANT_A
        repository = TenantRepository(session_as_async(session))
        with pytest.raises(ValueError, match="blank"):
            await repository.rename_tenant("   ")

    asyncio.run(run())


def test_find_user_requires_an_exact_identity_key() -> None:
    async def run() -> None:
        service = IdentityService(session_as_async(FakeSession()))
        with pytest.raises(ValueError, match="email or an external_id"):
            await service.find_user()

    asyncio.run(run())


def test_provisioning_never_accepts_a_caller_supplied_tenant_id() -> None:
    parameters = set(inspect.signature(IdentityService.provision_personal_tenant).parameters) - {
        "self"
    }
    assert parameters == {"email", "external_id", "tenant_name"}, parameters


def test_uuids_used_by_the_offline_guards_are_distinct() -> None:
    assert TENANT_A != TENANT_B != uuid4()


# ---- The subjectless provisioning path is closed -------------------------


def test_provisioning_requires_a_verified_external_subject() -> None:
    """A subject is mandatory, and the signature no longer defaults it to None."""
    signature = inspect.signature(IdentityService.provision_personal_tenant)
    subject = signature.parameters["external_id"]
    assert subject.default is inspect.Parameter.empty
    assert signature.parameters["tenant_name"].default is None


@pytest.mark.parametrize("subject", ["", "   ", "\t\n"])
def test_a_blank_subject_is_refused_without_touching_the_database(subject: str) -> None:
    async def run() -> None:
        session = FakeSession()
        service = IdentityService(session_as_async(session))
        with pytest.raises(SubjectRequired):
            await service.provision_personal_tenant(
                email="person@synthetic.invalid", external_id=subject
            )
        # No statement issued at all: a rejected call cannot leave partial rows.
        assert session.executed == []

    asyncio.run(run())


def test_the_subject_is_passed_through_stripped() -> None:
    async def run() -> None:
        session = FakeSession()
        session.rows = [(uuid4(), uuid4(), uuid4(), True)]
        service = IdentityService(session_as_async(session))
        await service.provision_personal_tenant(
            email="person@synthetic.invalid", external_id="  user_01ABC  "
        )
        sql, params = session.executed[-1]
        assert "app.provision_personal_identity" in sql
        assert params["external_id"] == "user_01ABC"

    asyncio.run(run())


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [
        ("VS001", ExternalSubjectConflict),
        ("VS002", EmailAlreadyBound),
        ("VS003", SubjectBindingRequired),
    ],
)
def test_conflict_sqlstates_map_to_distinct_typed_errors(
    sqlstate: str, expected: type[Exception]
) -> None:
    """Matched by SQLSTATE, never by scraping a message."""

    class Orig(Exception):
        def __init__(self) -> None:
            self.sqlstate = sqlstate

    error = DBAPIError("SELECT 1", {}, Orig())
    assert isinstance(_translate(error), expected)
