"""Access-token validation against real signatures.

These tests sign genuine RSA/EC JWTs with keys generated here and feed them to
the production validator. The validator itself is never mocked — only the JWKS
key source is, which is exactly the boundary a real deployment fetches over the
network. Claiming "signature verification is tested" while stubbing the decoder
would prove nothing.
"""

import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from backend.auth.tokens import AccessTokenValidator, AuthNotConfigured, TokenError
from backend.config import Settings

ISSUER = "https://api.workos.com/"
CLIENT_ID = "client_synthetic"
SUBJECT = "user_synthetic_01"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "environment": "test",
        "database_url": "postgresql+psycopg://u:x@127.0.0.1:1/app_test",
        "dbos_system_database_url": "postgresql+psycopg://u:x@127.0.0.1:1/dbos_test",
        "workos_client_id": CLIENT_ID,
        "workos_api_key": "sk_test_synthetic",
        "workos_issuers": [ISSUER],
    }
    values.update(overrides)
    return Settings(**values)


class StubKeySource:
    """Stands in for the JWKS endpoint, returning a real public key."""

    def __init__(self, key: Any) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> Any:
        class _Key:
            key = self._key

        return _Key()


@pytest.fixture(scope="module")
def rsa_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_rsa_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(key: Any, *, algorithm: str = "RS256", **claims: Any) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "sid": "session_synthetic",
        "iat": now,
        "exp": now + 300,
    }
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm=algorithm)


def validator(key: Any, **overrides: Any) -> AccessTokenValidator:
    private = key if isinstance(key, bytes | str) else key.public_key()
    return AccessTokenValidator(settings(**overrides), jwk_client=StubKeySource(private))  # type: ignore[arg-type]


# ---- Accepted ------------------------------------------------------------


def test_valid_token_yields_the_verified_subject(rsa_key: Any) -> None:
    result = validator(rsa_key).verify(make_token(rsa_key))
    assert result.subject == SUBJECT
    assert result.session_id == "session_synthetic"


# ---- Rejected ------------------------------------------------------------


def test_missing_token_is_rejected(rsa_key: Any) -> None:
    with pytest.raises(TokenError, match="missing-token"):
        validator(rsa_key).verify("   ")


@pytest.mark.parametrize("malformed", ["not-a-jwt", "a.b", "a.b.c", "....", "eyJhbGciOiJub25lIn0"])
def test_malformed_tokens_are_rejected(rsa_key: Any, malformed: str) -> None:
    with pytest.raises(TokenError):
        validator(rsa_key).verify(malformed)


def test_expired_token_is_rejected(rsa_key: Any) -> None:
    now = int(time.time())
    token = make_token(rsa_key, exp=now - 3600, iat=now - 7200)
    with pytest.raises(TokenError, match="expired"):
        validator(rsa_key, workos_leeway_seconds=0).verify(token)


def test_wrong_issuer_is_rejected(rsa_key: Any) -> None:
    token = make_token(rsa_key, iss="https://evil.example/")
    with pytest.raises(TokenError, match="wrong-issuer"):
        validator(rsa_key).verify(token)


def test_wrong_audience_is_rejected_when_an_audience_is_configured(rsa_key: Any) -> None:
    token = make_token(rsa_key, aud="someone-else")
    with pytest.raises(TokenError, match="wrong-audience"):
        validator(rsa_key, workos_audience="vespers-api").verify(token)


def test_matching_audience_is_accepted_when_configured(rsa_key: Any) -> None:
    token = make_token(rsa_key, aud="vespers-api")
    assert validator(rsa_key, workos_audience="vespers-api").verify(token).subject == SUBJECT


def test_audience_is_not_required_by_default(rsa_key: Any) -> None:
    """AuthKit access tokens carry no documented aud claim."""
    assert validator(rsa_key).verify(make_token(rsa_key)).subject == SUBJECT


def test_signature_from_a_different_key_is_rejected(rsa_key: Any, other_rsa_key: Any) -> None:
    forged = make_token(other_rsa_key)
    with pytest.raises(TokenError, match="invalid-signature"):
        validator(rsa_key).verify(forged)


def test_unsupported_algorithm_is_rejected(rsa_key: Any) -> None:
    """An EC-signed token must not pass an RS256-only allowlist."""
    ec_key = ec.generate_private_key(ec.SECP256R1())
    token = make_token(ec_key, algorithm="ES256")
    source = StubKeySource(ec_key.public_key())
    checker = AccessTokenValidator(settings(), jwk_client=source)  # type: ignore[arg-type]
    with pytest.raises(TokenError):
        checker.verify(token)


def test_alg_none_is_rejected(rsa_key: Any) -> None:
    token = jwt.encode(
        {"iss": ISSUER, "sub": SUBJECT, "exp": int(time.time()) + 300}, key="", algorithm="none"
    )
    with pytest.raises(TokenError):
        validator(rsa_key).verify(token)


def test_symmetric_algorithms_cannot_be_configured() -> None:
    """HS256 plus a public JWKS key is the classic key-confusion attack."""
    from backend.config import ConfigurationError

    with pytest.raises(ConfigurationError):
        settings(workos_algorithms=["HS256"])


@pytest.mark.parametrize("missing", ["sub", "exp", "iss"])
def test_required_claims_are_enforced(rsa_key: Any, missing: str) -> None:
    now = int(time.time())
    payload = {"iss": ISSUER, "sub": SUBJECT, "exp": now + 300}
    del payload[missing]
    token = jwt.encode(payload, rsa_key, algorithm="RS256")
    with pytest.raises(TokenError):
        validator(rsa_key).verify(token)


def test_blank_subject_is_rejected(rsa_key: Any) -> None:
    with pytest.raises(TokenError, match="missing-claim:sub"):
        validator(rsa_key).verify(make_token(rsa_key, sub="   "))


def test_unconfigured_workos_fails_closed() -> None:
    bare = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://u:x@127.0.0.1:1/app_test",
        dbos_system_database_url="postgresql+psycopg://u:x@127.0.0.1:1/dbos_test",
    )
    with pytest.raises(AuthNotConfigured):
        AccessTokenValidator(bare).verify("anything")


def test_errors_never_echo_the_token(rsa_key: Any, other_rsa_key: Any) -> None:
    forged = make_token(other_rsa_key)
    try:
        validator(rsa_key).verify(forged)
    except TokenError as error:
        assert forged not in str(error)
        assert forged[:24] not in str(error)
    else:  # pragma: no cover
        raise AssertionError("forged token accepted")


def test_production_requires_workos_configuration() -> None:
    from backend.config import ConfigurationError

    with pytest.raises(ConfigurationError):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql+psycopg://u:x@127.0.0.1:1/app_production",
            dbos_system_database_url="postgresql+psycopg://u:x@127.0.0.1:1/dbos_production",
            encryption_key="synthetic-production-key",
        )


def test_jwks_url_is_scoped_to_the_client_id() -> None:
    """The client-scoped key set is what binds a token to this application."""
    assert settings().workos_jwks_url.endswith(f"/sso/jwks/{CLIENT_ID}")


# ---- Application binding and configuration --------------------------------


def test_the_jwks_url_matches_the_pinned_workos_sdk() -> None:
    """`@workos-inc/node` 10.13.0: `${baseURL}/sso/jwks/${clientId}`."""
    assert settings().workos_jwks_url == f"https://api.workos.com/sso/jwks/{CLIENT_ID}"


def test_a_custom_auth_domain_can_override_the_key_set_url() -> None:
    custom = "https://auth.example.com/sso/jwks/other"
    assert settings(workos_jwks_url_override=custom).workos_jwks_url == custom


@pytest.mark.parametrize("configured", ["https://api.workos.com", "https://api.workos.com/"])
@pytest.mark.parametrize("presented", ["https://api.workos.com", "https://api.workos.com/"])
def test_trailing_slash_issuer_spellings_are_interchangeable(
    rsa_key: Any, configured: str, presented: str
) -> None:
    """WorkOS documents both spellings of the same origin."""
    config = settings(workos_issuers=[configured])
    validator = AccessTokenValidator(config, jwk_client=StubKeySource(rsa_key.public_key()))
    assert validator.verify(make_token(rsa_key, iss=presented)).subject == SUBJECT


@pytest.mark.parametrize(
    "hostile",
    [
        "https://api.workos.com.evil.test/",
        "https://evil.test/api.workos.com/",
        "http://api.workos.com/",
        "https://api.workos.com/extra",
    ],
)
def test_lookalike_issuers_are_rejected(rsa_key: Any, hostile: str) -> None:
    validator = AccessTokenValidator(settings(), jwk_client=StubKeySource(rsa_key.public_key()))
    with pytest.raises(TokenError, match="wrong-issuer"):
        validator.verify(make_token(rsa_key, iss=hostile))


def test_an_empty_issuer_list_is_refused_at_configuration_time() -> None:
    with pytest.raises(ValueError):
        settings(workos_issuers=[])


def test_a_token_with_an_audience_is_refused_when_none_is_configured(rsa_key: Any) -> None:
    """WorkOS OAuth/MCP tokens carry `aud`; they are not first-party session tokens."""
    validator = AccessTokenValidator(settings(), jwk_client=StubKeySource(rsa_key.public_key()))
    with pytest.raises(TokenError, match="unexpected-audience"):
        validator.verify(make_token(rsa_key, aud="https://api.other.example"))


def test_a_configured_audience_is_required_and_checked(rsa_key: Any) -> None:
    config = settings(workos_audience="https://api.vespers.example")
    validator = AccessTokenValidator(config, jwk_client=StubKeySource(rsa_key.public_key()))
    assert validator.verify(make_token(rsa_key, aud="https://api.vespers.example")).subject
    with pytest.raises(TokenError, match="wrong-audience"):
        validator.verify(make_token(rsa_key, aud="https://api.other.example"))
    # A token with no `aud` at all must not satisfy a configured audience.
    with pytest.raises(TokenError):
        validator.verify(make_token(rsa_key))


def test_the_key_cache_has_no_unexpiring_per_kid_tier() -> None:
    """A key WorkOS withdraws must stop verifying within the JWK-set lifespan.

    PyJWT's second-tier `cache_keys` LRU has no time expiry, so enabling it would
    keep a withdrawn key usable until it happened to be evicted.
    """
    client = AccessTokenValidator(settings())._jwks
    assert client is not None
    assert getattr(client, "get_signing_key", None) is not None
    assert client.jwk_set_cache is not None  # first tier on, bounded by lifespan
    # lru_cache wrapping is what `cache_keys=True` installs; it must be absent.
    assert not hasattr(client.get_signing_key, "cache_info")


def test_production_refuses_an_http_key_set() -> None:
    with pytest.raises(ValueError):
        settings(
            environment="production",
            encryption_key="x" * 32,
            database_url="postgresql+psycopg://u:x@127.0.0.1:1/app_production",
            dbos_system_database_url="postgresql+psycopg://u:x@127.0.0.1:1/dbos_production",
            workos_jwks_url_override="http://api.workos.com/sso/jwks/abc",
        )
