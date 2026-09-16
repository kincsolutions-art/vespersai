"""Server-to-server WorkOS User Management lookup.

The access token proves *which* WorkOS subject is calling, but it carries no
email. The email is therefore fetched from WorkOS directly, keyed by the verified
`sub`, using the server-side API key:

    GET {api_base}/user_management/users/{id}
    Authorization: Bearer <WORKOS_API_KEY>

This is the whole point of the design: the browser and the Next.js BFF never get
to assert an email address or a verification flag. An upstream failure raises
ProfileUnavailable, which must surface as an error — never as a denial and never
as a successful provisioning.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from backend.auth.errors import ProfileUnavailable
from backend.config import Settings


@dataclass(frozen=True, slots=True)
class VerifiedProfile:
    subject: str
    email: str
    email_verified: bool


@runtime_checkable
class ProfileSource(Protocol):
    """What the API layer needs from a directory, so tests can supply a double."""

    async def fetch_profile(self, subject: str) -> "VerifiedProfile": ...


class WorkOSDirectory:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0, trust_env=False)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def fetch_profile(self, subject: str) -> VerifiedProfile:
        settings = self._settings
        if not settings.workos_configured or settings.workos_api_key is None:
            raise ProfileUnavailable("workos-not-configured")
        client = await self._http()
        url = f"{settings.workos_api_base.rstrip('/')}/user_management/users/{subject}"
        try:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {settings.workos_api_key.get_secret_value()}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as error:
            raise ProfileUnavailable(f"workos-unreachable:{type(error).__name__}") from None

        if response.status_code != 200:
            # Status only. Provider bodies can echo request content, so they are
            # never propagated into our errors or logs.
            raise ProfileUnavailable(f"workos-status:{response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise ProfileUnavailable("workos-malformed-json") from None
        if not isinstance(payload, dict):
            raise ProfileUnavailable("workos-malformed-profile")

        email = payload.get("email")
        verified = payload.get("email_verified")
        identifier = payload.get("id")
        if not isinstance(email, str) or not email.strip():
            raise ProfileUnavailable("workos-missing-email")
        if not isinstance(verified, bool):
            raise ProfileUnavailable("workos-missing-email-verified")
        if identifier != subject:
            # The directory must answer for the subject we asked about.
            raise ProfileUnavailable("workos-subject-mismatch")
        return VerifiedProfile(subject=subject, email=email.strip(), email_verified=verified)
