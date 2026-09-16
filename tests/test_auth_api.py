"""End-to-end enforcement through the real FastAPI dependency chain.

The token validator is the production one (signatures are genuinely checked);
only the JWKS source, the WorkOS directory and the database session are stubbed.
"""

import io
import logging
import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.api.main import create_app
from backend.auth.errors import ProfileUnavailable
from backend.auth.ratelimit import FixedWindowLimiter
from backend.auth.tokens import AccessTokenValidator
from backend.auth.workos import VerifiedProfile
from backend.config import Settings
from backend.logging import JsonFormatter

# The value WorkOS actually issues: client-scoped, not the documented origin.
ISSUER = "https://api.workos.com/user_management/client_synthetic"
SUBJECT = "user_synthetic_01"
USER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
TENANT_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
OTHER_TENANT = uuid.UUID("33333333-3333-4333-8333-333333333333")
MEMBERSHIP_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "environment": "test",
        "database_url": "postgresql+psycopg://u:x@127.0.0.1:1/app_test",
        "dbos_system_database_url": "postgresql+psycopg://u:x@127.0.0.1:1/dbos_test",
        "workos_client_id": "client_synthetic",
        "workos_api_key": "sk_test_synthetic",
        "workos_issuers": [ISSUER],
    }
    values.update(overrides)
    return Settings(**values)


def token(**claims: Any) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "sid": "session_synthetic",
        "iat": now,
        "exp": now + 300,
    }
    payload.update(claims)
    return jwt.encode(payload, KEY, algorithm="RS256")


class StubKeySource:
    def get_signing_key_from_jwt(self, _: str) -> Any:
        class _Key:
            key = KEY.public_key()

        return _Key()


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def first(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> tuple[Any, ...]:
        return self._rows[0]

    def scalar_one(self) -> Any:
        return self._rows[0][0]

    def __iter__(self) -> Any:
        return iter(self._rows)


class FakeSession:
    """Answers the exact statements the identity layer issues."""

    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.info: dict[str, Any] = {}
        self.statements: list[str] = []
        # Shared across every session this plan produces, so a test can inspect
        # what the request actually asked the database.
        self.calls: list[tuple[str, Any]] = plan.setdefault("calls", [])

    def in_transaction(self) -> bool:
        return True

    async def execute(self, statement: Any, params: Any = None) -> FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        self.calls.append((sql, params))
        if "set_config" in sql:
            return FakeResult([("",)])
        if "app.signup_allowed" in sql:
            return FakeResult([(self.plan.get("allowlisted", True),)])
        if "app.find_user" in sql:
            user = self.plan.get("user")
            return FakeResult([user] if user else [])
        if "app.list_memberships" in sql:
            return FakeResult(list(self.plan.get("memberships", [])))
        if "app.provision_personal_identity" in sql:
            error = self.plan.get("provision_error")
            if error is not None:
                raise error
            return FakeResult([self.plan["provisioned"]])
        if "FROM tenants" in sql:
            tenant = self.plan.get("tenant")
            return FakeResult([tenant] if tenant else [])
        raise AssertionError("unexpected statement: " + sql[:120])

    async def __aenter__(self) -> "FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def begin(self) -> "FakeSession":
        return self


def build(plan: dict[str, Any] | None = None, **overrides: Any) -> TestClient:
    plan = plan if plan is not None else default_plan()
    config = settings(**overrides)
    app = create_app(config)
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    app.state.sessions = lambda: FakeSession(plan)
    app.state.token_validator = AccessTokenValidator(config, jwk_client=StubKeySource())  # type: ignore[arg-type]
    app.state.workos_directory = plan.get("directory") or StubDirectory()
    app.state.auth_limiter = FixedWindowLimiter(plan.get("rate_limit", 100))
    return client


class StubDirectory:
    def __init__(self, profile: VerifiedProfile | None = None, error: Exception | None = None):
        self._profile = profile or VerifiedProfile(SUBJECT, "person@synthetic.invalid", True)
        self._error = error

    async def fetch_profile(self, subject: str) -> VerifiedProfile:
        if self._error is not None:
            raise self._error
        return self._profile


def default_plan() -> dict[str, Any]:
    return {
        "user": (USER_ID, "person@synthetic.invalid", SUBJECT, "active"),
        "memberships": [(MEMBERSHIP_ID, TENANT_ID, "Personal", "personal", "owner")],
        "tenant": (TENANT_ID, "Personal", "personal", USER_ID, "active"),
        "provisioned": (USER_ID, TENANT_ID, MEMBERSHIP_ID, True),
    }


# ---- Authentication ------------------------------------------------------


def test_no_token_means_no_access() -> None:
    assert build().get("/api/account").status_code == 401


@pytest.mark.parametrize(
    "header",
    ["", "Token abc", "Basic abc", "Bearer", "bearer ", "Bearer not-a-jwt"],
)
def test_bad_authorization_headers_are_rejected(header: str) -> None:
    response = build().get("/api/account", headers={"Authorization": header})
    assert response.status_code == 401, response.text


def test_expired_token_is_rejected() -> None:
    now = int(time.time())
    response = build(**{"workos_leeway_seconds": 0}).get(
        "/api/account", headers={"Authorization": f"Bearer {token(exp=now - 60, iat=now - 600)}"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "expired"


def test_wrong_issuer_is_rejected() -> None:
    response = build().get(
        "/api/account",
        headers={"Authorization": f"Bearer {token(iss='https://evil.example/')}"},
    )
    assert response.status_code == 401


def test_health_endpoints_do_not_require_authentication() -> None:
    assert build().get("/health/live").status_code == 200


def test_protected_routes_fail_closed_when_workos_is_unconfigured() -> None:
    config = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://u:x@127.0.0.1:1/app_test",
        dbos_system_database_url="postgresql+psycopg://u:x@127.0.0.1:1/dbos_test",
    )
    app = create_app(config)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/api/account", headers={"Authorization": f"Bearer {token()}"})
        assert response.status_code == 503
        assert response.json()["detail"] == "authentication-not-configured"


# ---- Local authorization -------------------------------------------------


def test_valid_token_alone_does_not_grant_access() -> None:
    """Authenticated with WorkOS, unknown to this application."""
    plan = default_plan() | {"user": None}
    response = build(plan).get("/api/account", headers={"Authorization": f"Bearer {token()}"})
    assert response.status_code == 403
    assert response.json()["detail"] == "not-provisioned"


def test_disabled_identity_is_refused() -> None:
    plan = default_plan() | {"user": (USER_ID, "person@synthetic.invalid", SUBJECT, "disabled")}
    response = build(plan).get("/api/account", headers={"Authorization": f"Bearer {token()}"})
    assert response.status_code == 403
    assert response.json()["detail"] == "identity-disabled"


def test_no_active_membership_is_refused() -> None:
    plan = default_plan() | {"memberships": []}
    response = build(plan).get("/api/account", headers={"Authorization": f"Bearer {token()}"})
    assert response.status_code == 403
    assert response.json()["detail"] == "no-active-membership"


def test_authorized_request_returns_the_account() -> None:
    response = build().get("/api/account", headers={"Authorization": f"Bearer {token()}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"] == str(TENANT_ID)
    assert body["email"] == "person@synthetic.invalid"


def test_forged_tenant_id_is_refused_not_redirected() -> None:
    response = build().get(
        f"/api/account?tenant_id={OTHER_TENANT}",
        headers={"Authorization": f"Bearer {token()}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "tenant-not-permitted"


def test_forged_identity_headers_are_ignored() -> None:
    response = build().get(
        "/api/account",
        headers={
            "Authorization": f"Bearer {token()}",
            "X-Tenant-Id": str(OTHER_TENANT),
            "X-User-Id": str(uuid.uuid4()),
            "X-Email": "attacker@synthetic.invalid",
            "X-Email-Verified": "true",
        },
    )
    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(TENANT_ID)
    assert response.json()["email"] == "person@synthetic.invalid"


def test_tenant_read_goes_through_a_real_tenant_scope() -> None:
    plan = default_plan()
    captured: list[FakeSession] = []
    config = settings()
    app = create_app(config)
    with TestClient(app, raise_server_exceptions=False) as client:

        def factory() -> FakeSession:
            session = FakeSession(plan)
            captured.append(session)
            return session

        app.state.sessions = factory
        app.state.token_validator = AccessTokenValidator(config, jwk_client=StubKeySource())  # type: ignore[arg-type]
        app.state.workos_directory = StubDirectory()
        app.state.auth_limiter = FixedWindowLimiter(100)
        assert (
            client.get("/api/account", headers={"Authorization": f"Bearer {token()}"}).status_code
            == 200
        )
    assert any("set_config" in s for session in captured for s in session.statements)


# ---- Provisioning --------------------------------------------------------


def test_provisioning_requires_a_verified_email() -> None:
    plan = default_plan() | {
        "directory": StubDirectory(VerifiedProfile(SUBJECT, "person@synthetic.invalid", False))
    }
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "email-not-verified"


def test_unallowlisted_identity_is_refused_at_the_backend() -> None:
    from backend.identity import SignupNotAllowed

    plan = default_plan() | {"provision_error": SignupNotAllowed("no")}
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "not-allowlisted"


def test_external_subject_conflict_is_a_conflict_not_an_account_takeover() -> None:
    from backend.identity import ExternalSubjectConflict

    plan = default_plan() | {"provision_error": ExternalSubjectConflict("conflict")}
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "external-subject-conflict"


def test_email_already_bound_is_a_conflict() -> None:
    from backend.identity import EmailAlreadyBound

    plan = default_plan() | {"provision_error": EmailAlreadyBound("conflict")}
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 409


def test_duplicate_callbacks_are_idempotent() -> None:
    client = build()
    first = client.post("/api/account/provision", headers={"Authorization": f"Bearer {token()}"})
    second = client.post("/api/account/provision", headers={"Authorization": f"Bearer {token()}"})
    assert first.status_code == second.status_code == 200
    assert first.json()["user_id"] == second.json()["user_id"]
    assert first.json()["tenant_id"] == second.json()["tenant_id"]


def test_provider_failure_is_not_reported_as_provisioning_success() -> None:
    plan = default_plan() | {
        "directory": StubDirectory(error=ProfileUnavailable("workos-status:500"))
    }
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 502
    assert "workos-status" in response.json()["detail"]


def test_database_failure_is_not_reported_as_provisioning_success() -> None:
    plan = default_plan() | {"provision_error": OperationalError("SELECT 1", {}, Exception("down"))}
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "provisioning-unavailable"


# ---- Rate limiting and leakage -------------------------------------------


def test_auth_requests_are_rate_limited_per_subject() -> None:
    client = build(default_plan() | {"rate_limit": 3})
    headers = {"Authorization": f"Bearer {token()}"}
    codes = [client.get("/api/account", headers=headers).status_code for _ in range(5)]
    assert codes.count(429) >= 1, codes
    assert codes[0] == 200


def test_error_bodies_never_contain_the_token_or_secrets() -> None:
    bad = token(iss="https://evil.example/")
    response = build().get("/api/account", headers={"Authorization": f"Bearer {bad}"})
    body = response.text
    assert bad not in body
    assert "sk_test_synthetic" not in body
    assert "client_synthetic" not in body


# ---- Revocation of application access ------------------------------------
#
# Every case here establishes a working session FIRST, then changes local state,
# then reuses the SAME token. That is the property that matters: an existing
# browser session must not outlive revoked application access. None of this is
# token revocation — the token stays cryptographically valid throughout.


def established_session() -> tuple[TestClient, dict[str, Any], dict[str, str]]:
    plan = default_plan()
    client = build(plan)
    headers = {"Authorization": f"Bearer {token()}"}
    assert client.get("/api/account", headers=headers).status_code == 200
    return client, plan, headers


def test_allowlist_removal_revokes_an_established_session() -> None:
    client, plan, headers = established_session()
    plan["allowlisted"] = False
    response = client.get("/api/account", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "not-allowlisted"


def test_user_disablement_revokes_an_established_session() -> None:
    client, plan, headers = established_session()
    plan["user"] = (USER_ID, "person@synthetic.invalid", SUBJECT, "disabled")
    response = client.get("/api/account", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "identity-disabled"


def test_tenant_or_membership_disablement_revokes_an_established_session() -> None:
    # app.list_memberships filters on membership, user AND tenant status, so both
    # disablements surface identically here: the row stops being returned.
    client, plan, headers = established_session()
    plan["memberships"] = []
    response = client.get("/api/account", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "no-active-membership"


def test_allowlist_removal_also_blocks_reprovisioning() -> None:
    from backend.identity import SignupNotAllowed

    client, plan, headers = established_session()
    plan["allowlisted"] = False
    plan["provision_error"] = SignupNotAllowed("removed")
    response = client.post("/api/account/provision", headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "not-allowlisted"


def test_revocation_is_rechecked_per_request_not_cached() -> None:
    """Restoring access takes effect immediately too — nothing is memoised."""
    client, plan, headers = established_session()
    plan["allowlisted"] = False
    assert client.get("/api/account", headers=headers).status_code == 403
    plan["allowlisted"] = True
    assert client.get("/api/account", headers=headers).status_code == 200


def test_the_allowlist_is_checked_against_the_stored_verified_email() -> None:
    """Not against anything the caller supplies, and without a WorkOS round trip."""
    plan = default_plan()
    plan["directory"] = StubDirectory(error=AssertionError("directory consulted on a read path"))
    client = build(plan)
    response = client.get(
        "/api/account",
        headers={"Authorization": f"Bearer {token()}", "X-Email": "attacker@synthetic.invalid"},
    )
    assert response.status_code == 200, response.text
    checks = [params for sql, params in plan["calls"] if "app.signup_allowed" in sql]
    assert checks, plan["calls"]
    # The stored verified address, never the header the caller sent.
    assert all(c["email"] == "person@synthetic.invalid" for c in checks), checks


def test_provisioning_reports_only_access_a_later_request_would_allow() -> None:
    """A disabled membership must not be reported as successful provisioning."""
    plan = default_plan() | {"memberships": []}
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "no-active-membership"


def test_invalid_identity_input_is_a_bad_request_not_an_outage() -> None:
    """SQLSTATE 22023 from the bootstrap surface: bad input, not a failed database.

    Reporting it as `503 provisioning-unavailable` would send an operator hunting
    a database they have no reason to suspect.
    """
    from backend.identity import InvalidIdentityInput

    plan = default_plan() | {"provision_error": InvalidIdentityInput("refused")}
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid-identity-input"


def test_an_unexpected_database_failure_stays_distinct_and_says_nothing() -> None:
    """503, and not one character of the driver's message."""
    secret = "sk_live_synthetic_do_not_echo host=db password=synthetic"
    plan = default_plan() | {"provision_error": OperationalError(secret, {}, Exception(secret))}
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "provisioning-unavailable"
    for fragment in ("sk_live_synthetic_do_not_echo", "password=synthetic", "host=db"):
        assert fragment not in response.text, response.text


# ---- Upstream email changes ----------------------------------------------


def test_an_upstream_email_change_does_not_affect_a_protected_read() -> None:
    """Read paths never call WorkOS, so they cannot see an upstream change.

    Authorization stays keyed to the verified subject and the *stored* address.
    An email the user can change upstream is deliberately not what grants access.
    """
    plan = default_plan()
    plan["directory"] = StubDirectory(
        # If a read path consulted WorkOS, this new address would surface.
        profile=VerifiedProfile(SUBJECT, "changed@synthetic.invalid", True)
    )
    client = build(plan)
    response = client.get("/api/account", headers={"Authorization": f"Bearer {token()}"})
    assert response.status_code == 200, response.text
    assert response.json()["email"] == "person@synthetic.invalid"
    checked = [params["email"] for sql, params in plan["calls"] if "app.signup_allowed" in sql]
    assert checked == ["person@synthetic.invalid"], checked


def test_revocation_of_the_stored_address_still_bites_immediately() -> None:
    """The counterpart: an administrator revokes the address we actually hold.

    So the slower email-change path is not a revocation gap — removing the stored
    address from the allowlist is refused on the very next request.
    """
    client, plan, headers = established_session()
    plan["directory"] = StubDirectory(
        profile=VerifiedProfile(SUBJECT, "changed@synthetic.invalid", True)
    )
    assert client.get("/api/account", headers=headers).status_code == 200
    plan["allowlisted"] = False
    denied = client.get("/api/account", headers=headers)
    assert denied.status_code == 403
    assert denied.json()["detail"] == "not-allowlisted"


def test_an_upstream_email_change_is_reconciled_only_by_provisioning() -> None:
    """And it still has to pass the allowlist in its own right."""
    plan = default_plan()
    plan["directory"] = StubDirectory(
        profile=VerifiedProfile(SUBJECT, "changed@synthetic.invalid", True)
    )
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 200, response.text
    # Provisioning is the only place the new address reaches the database.
    provisioned = [params for sql, params in plan["calls"] if "provision_personal_identity" in sql]
    assert provisioned and provisioned[0]["email"] == "changed@synthetic.invalid"
    assert provisioned[0]["external_id"] == SUBJECT


def test_subject_binding_required_is_a_conflict_not_an_account_handover() -> None:
    from backend.identity import SubjectBindingRequired

    plan = default_plan() | {"provision_error": SubjectBindingRequired("unbound")}
    response = build(plan).post(
        "/api/account/provision", headers={"Authorization": f"Bearer {token()}"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "subject-binding-required"


# ---- Application binding of the token ------------------------------------


@pytest.mark.parametrize("issuer", [ISSUER, ISSUER + "/"])
def test_both_trailing_slash_spellings_are_accepted(issuer: str) -> None:
    client = build(default_plan(), workos_issuers=[ISSUER])
    response = client.get("/api/account", headers={"Authorization": f"Bearer {token(iss=issuer)}"})
    assert response.status_code == 200, response.text


def test_another_applications_issuer_is_rejected_end_to_end() -> None:
    client = build(default_plan(), workos_issuers=[ISSUER])
    other = "https://api.workos.com/user_management/client_someone_else"
    response = client.get("/api/account", headers={"Authorization": f"Bearer {token(iss=other)}"})
    assert response.status_code == 401
    assert response.json()["detail"] == "wrong-issuer"


def test_the_issuer_rejection_reaches_neither_the_client_nor_the_log() -> None:
    """The label is fixed on both paths: the response body and the log line.

    A rejected `iss` is signature-verified but still attacker-influenced content,
    and this endpoint is reachable without any credential. The capture goes
    through the real JsonFormatter, so this covers what is actually written.
    """
    hostile = "https://evil.test/sk_live_synthetic_do_not_echo?cookie=session_synthetic"
    bearer = token(iss=hostile)
    client = build(default_plan(), workos_issuers=[ISSUER])

    # configure_logging() sets propagate=False on "vespers", so caplog's root
    # handler never sees these records. Attach to the real logger instead.
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("vespers")
    logger.addHandler(handler)
    try:
        response = client.get("/api/account", headers={"Authorization": f"Bearer {bearer}"})
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 401
    assert response.json()["detail"] == "wrong-issuer"

    logged = stream.getvalue()
    assert "auth_token_rejected" in logged
    assert '"reason": "wrong-issuer"' in logged
    for fragment in (bearer, "sk_live_synthetic_do_not_echo", "session_synthetic", "evil.test"):
        assert fragment not in logged, "log leaked token-controlled content"
        assert fragment not in response.text, "response leaked token-controlled content"


def test_a_token_carrying_an_audience_is_refused_when_none_is_configured() -> None:
    """An OAuth/MCP token carries `aud`; it is not a first-party session token."""
    response = build().get(
        "/api/account",
        headers={"Authorization": f"Bearer {token(aud='https://other.example')}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "unexpected-audience"


def test_a_configured_audience_is_enforced() -> None:
    client = build(default_plan(), workos_audience="https://api.vespers.example")
    good = client.get(
        "/api/account",
        headers={"Authorization": f"Bearer {token(aud='https://api.vespers.example')}"},
    )
    assert good.status_code == 200, good.text
    bad = client.get(
        "/api/account",
        headers={"Authorization": f"Bearer {token(aud='https://api.other.example')}"},
    )
    assert bad.status_code == 401
    assert bad.json()["detail"] == "wrong-audience"
