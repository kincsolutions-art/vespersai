"""WorkOS AuthKit access-token validation.

FastAPI validates the token itself rather than trusting the Next.js BFF.

**Application binding**, established from the pinned SDK and current WorkOS
documentation rather than assumed from the URL shape:

- `@workos-inc/node` 10.13.0 builds the key-set URL as
  `${baseURL}/sso/jwks/${clientId}`, and `@workos-inc/authkit-nextjs` 4.3.2
  verifies with `createRemoteJWKSet(getJwksUrl(WORKOS_CLIENT_ID))`.
- WorkOS documents the signing keys as **unique per client id**, so a token
  issued to a different WorkOS application is signed by a different key and
  fails signature verification here. The key set is the binding mechanism.
- AuthKit session access tokens carry **no `aud` claim by default**. WorkOS's
  documented way to add one is a JWT template naming the API's URI, which is why
  `VESPERS_WORKOS_AUDIENCE` exists and is enforced whenever it is set.

Verified here: signature, issuer, expiry (with bounded leeway), an algorithm
allowlist, audience when configured, and the presence of a `sub`. This is
strictly stricter than the SDK's own `verifyAccessToken`, which checks the
signature alone. Everything else — email, membership, tenant — is resolved
server-side from `sub`, never read from the token or the browser.
"""

from dataclasses import dataclass

import jwt
from jwt import PyJWKClient
from jwt.types import Options

from backend.config import Settings


class TokenError(Exception):
    """Token rejected. Messages are fixed labels and never include the token."""


class AuthNotConfigured(TokenError):
    """WorkOS is not configured; protected routes must fail closed."""


@dataclass(frozen=True, slots=True)
class VerifiedSubject:
    """The only identity facts we accept from a token."""

    subject: str
    session_id: str | None
    expires_at: int


class AccessTokenValidator:
    def __init__(self, settings: Settings, jwk_client: PyJWKClient | None = None) -> None:
        self._settings = settings
        self._configured = settings.workos_configured
        # Key rotation, with the pinned PyJWT 2.14 semantics stated explicitly:
        # the JWK *set* is cached for `lifespan` seconds and an unknown `kid`
        # forces one refresh after `cooldown_duration`, so a newly rotated key is
        # picked up without a restart. `cache_keys` is deliberately left off: that
        # second-tier per-kid LRU has no expiry, so a key WorkOS has withdrawn
        # would keep verifying tokens until it happened to be evicted.
        self._jwks = jwk_client or (
            PyJWKClient(
                settings.workos_jwks_url,
                cache_keys=False,
                cache_jwk_set=True,
                lifespan=300,
                cooldown_duration=30,
                timeout=10,
            )
            if self._configured
            else None
        )

    def verify(self, token: str) -> VerifiedSubject:
        if self._jwks is None:
            raise AuthNotConfigured("workos-not-configured")
        if not token or not token.strip():
            raise TokenError("missing-token")

        settings = self._settings
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
        except jwt.PyJWTError as error:
            raise TokenError(f"unresolvable-signing-key:{type(error).__name__}") from None
        except Exception as error:  # JWKS fetch/transport failure
            raise TokenError(f"jwks-unavailable:{type(error).__name__}") from None

        options: Options = {
            "require": ["exp", "iss", "sub"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_iss": True,
            "verify_aud": settings.workos_audience is not None,
        }
        if settings.workos_audience is None:
            # PyJWT ignores `aud` entirely when verify_aud is off, so a token
            # carrying one would pass unchecked. WorkOS OAuth and MCP access
            # tokens do carry `aud` (defaulting to the environment client id);
            # those authorize a resource server and are not the first-party
            # AuthKit session token this API accepts, so refuse rather than
            # silently honour an audience we never validated.
            try:
                unverified = jwt.decode(token, options={"verify_signature": False})
            except jwt.PyJWTError:
                raise TokenError("invalid-token:DecodeError") from None
            if "aud" in unverified:
                raise TokenError("unexpected-audience")
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                # Never inferred from the token header: an attacker-chosen `alg`
                # is the classic confusion attack.
                algorithms=list(settings.workos_algorithms),
                issuer=list(settings.workos_accepted_issuers),
                audience=settings.workos_audience,
                leeway=settings.workos_leeway_seconds,
                options=options,
            )
        except jwt.ExpiredSignatureError:
            raise TokenError("expired") from None
        except jwt.InvalidIssuerError:
            raise TokenError("wrong-issuer") from None
        except jwt.InvalidAudienceError:
            raise TokenError("wrong-audience") from None
        except jwt.InvalidAlgorithmError:
            raise TokenError("unsupported-algorithm") from None
        except jwt.InvalidSignatureError:
            raise TokenError("invalid-signature") from None
        except jwt.MissingRequiredClaimError as error:
            raise TokenError(f"missing-claim:{error.claim}") from None
        except jwt.PyJWTError as error:
            raise TokenError(f"invalid-token:{type(error).__name__}") from None

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise TokenError("missing-claim:sub")
        session_id = claims.get("sid")
        return VerifiedSubject(
            subject=subject.strip(),
            session_id=session_id if isinstance(session_id, str) else None,
            expires_at=int(claims["exp"]),
        )
