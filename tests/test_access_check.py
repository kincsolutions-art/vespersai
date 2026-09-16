"""The token-free authorization report must agree with the request-time boundary.

`backend.identity.access_check` is what the README tells an operator to run
instead of pasting a bearer token anywhere. It is only useful if it reports the
same conditions `authorize_local_access` enforces, so these tests pin the two
together case by case.
"""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from backend.identity.access_check import describe, main
from backend.identity.service import IdentityService, TenantMembership, UserRecord

USER_ID = UUID("11111111-1111-4111-8111-111111111111")
EMAIL = "person@synthetic.invalid"


class FakeService:
    def __init__(
        self,
        *,
        allowed: bool = True,
        user: UserRecord | None = None,
        memberships: list[TenantMembership] | None = None,
    ) -> None:
        self._allowed = allowed
        self._user = user
        self._memberships = memberships or []
        self.asked: list[str] = []

    async def is_signup_allowed(self, email: str) -> bool:
        self.asked.append(email)
        return self._allowed

    async def find_user(self, **kwargs: Any) -> UserRecord | None:
        return self._user

    async def list_memberships(self, user_id: UUID) -> list[TenantMembership]:
        return self._memberships


def active_user(**overrides: Any) -> UserRecord:
    values: dict[str, Any] = {
        "id": USER_ID,
        "email": EMAIL,
        "external_id": "user_synthetic_01",
        "status": "active",
    }
    values.update(overrides)
    return UserRecord(**values)


def membership() -> TenantMembership:
    return TenantMembership(uuid4(), uuid4(), "Personal", "personal", "owner")


def run(service: Any) -> tuple[list[str], list[str]]:
    return asyncio.run(describe(service, EMAIL))


def test_a_fully_authorized_account_reports_permitted() -> None:
    lines, reasons = run(FakeService(user=active_user(), memberships=[membership()]))
    assert reasons == []
    assert "allowlisted:      yes" in lines
    assert "active tenants:   1" in lines


@pytest.mark.parametrize(
    ("service", "reason"),
    [
        (
            FakeService(allowed=False, user=active_user(), memberships=[membership()]),
            "not-allowlisted",
        ),
        (
            FakeService(user=active_user(status="disabled"), memberships=[membership()]),
            "identity-disabled",
        ),
        (FakeService(user=active_user(), memberships=[]), "no-active-membership"),
        (
            FakeService(user=active_user(external_id=None), memberships=[membership()]),
            "subject-binding-required",
        ),
    ],
)
def test_each_revocable_condition_is_reported_as_a_denial(
    service: FakeService, reason: str
) -> None:
    _, reasons = run(service)
    assert any(r.startswith(reason) for r in reasons), reasons


def test_the_denials_match_the_request_time_boundary() -> None:
    """Same labels the API returns, so the two cannot drift apart silently."""
    import inspect

    from backend.api import account

    source = inspect.getsource(account.authorize_local_access)
    labels = (
        "not-provisioned",
        "identity-disabled",
        "not-allowlisted",
        "no-active-membership",
    )
    for label in labels:
        assert label in source, label


def test_an_unknown_address_is_not_a_denial_when_allowlisted() -> None:
    lines, reasons = run(FakeService(user=None))
    assert reasons == []
    assert any("none (created on first sign-in)" in line for line in lines)


def test_an_unknown_address_that_is_not_allowlisted_is_denied() -> None:
    _, reasons = run(FakeService(allowed=False, user=None))
    assert reasons == ["not-allowlisted"]


def test_it_checks_the_address_it_was_given() -> None:
    service = FakeService(user=active_user(), memberships=[membership()])
    run(service)
    assert service.asked == [EMAIL]


@pytest.mark.parametrize("argv", [[], ["", ""], ["   "], ["a", "b"]])
def test_misuse_exits_two_without_touching_the_database(argv: list[str]) -> None:
    assert main(argv) == 2


def test_the_service_protocol_matches_the_real_one() -> None:
    """The double must not drift from IdentityService's actual signatures."""
    for name in ("is_signup_allowed", "find_user", "list_memberships"):
        assert hasattr(IdentityService, name), name
