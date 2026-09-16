"""WorkOS directory lookup and the in-process rate limiter.

No live WorkOS call: a mock transport stands in for the HTTP boundary.
"""

import asyncio
from typing import Any

import httpx
import pytest

from backend.auth.errors import ProfileUnavailable
from backend.auth.ratelimit import FixedWindowLimiter
from backend.auth.workos import WorkOSDirectory
from backend.config import Settings

SUBJECT = "user_synthetic_01"
API_KEY = "sk_test_SYNTHETIC_DO_NOT_LOG"


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "environment": "test",
        "database_url": "postgresql+psycopg://u:x@127.0.0.1:1/app_test",
        "dbos_system_database_url": "postgresql+psycopg://u:x@127.0.0.1:1/dbos_test",
        "workos_client_id": "client_synthetic",
        "workos_api_key": API_KEY,
    }
    values.update(overrides)
    return Settings(**values)


def directory(handler: Any, **overrides: Any) -> WorkOSDirectory:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, trust_env=False)
    return WorkOSDirectory(settings(**overrides), client=client)


def profile_response(**overrides: Any) -> httpx.Response:
    payload = {
        "object": "user",
        "id": SUBJECT,
        "email": "person@synthetic.invalid",
        "email_verified": True,
    }
    payload.update(overrides)
    return httpx.Response(200, json=payload)


def test_fetches_the_verified_email_for_the_subject() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return profile_response()

    profile = asyncio.run(directory(handler).fetch_profile(SUBJECT))
    assert profile.email == "person@synthetic.invalid"
    assert profile.email_verified is True
    assert seen["url"].endswith(f"/user_management/users/{SUBJECT}")
    # The API key travels server-to-server only.
    assert seen["auth"] == f"Bearer {API_KEY}"


def test_unverified_email_is_reported_faithfully() -> None:
    profile = asyncio.run(
        directory(lambda r: profile_response(email_verified=False)).fetch_profile(SUBJECT)
    )
    assert profile.email_verified is False


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_error_statuses_raise_profile_unavailable(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # A provider body can echo request content; it must never propagate.
        return httpx.Response(status, json={"message": API_KEY})

    with pytest.raises(ProfileUnavailable) as caught:
        asyncio.run(directory(handler).fetch_profile(SUBJECT))
    assert API_KEY not in str(caught.value)
    assert str(status) in str(caught.value)


def test_transport_failure_raises_profile_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("synthetic network failure")

    with pytest.raises(ProfileUnavailable, match="workos-unreachable"):
        asyncio.run(directory(handler).fetch_profile(SUBJECT))


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"id": SUBJECT, "email_verified": True}, "missing-email"),
        ({"id": SUBJECT, "email": "a@b.invalid"}, "missing-email-verified"),
        (
            {"id": SUBJECT, "email": "a@b.invalid", "email_verified": "yes"},
            "missing-email-verified",
        ),
        (
            {"id": "someone_else", "email": "a@b.invalid", "email_verified": True},
            "subject-mismatch",
        ),
    ],
)
def test_malformed_profiles_are_rejected(payload: dict[str, Any], match: str) -> None:
    with pytest.raises(ProfileUnavailable, match=match):
        asyncio.run(directory(lambda r: httpx.Response(200, json=payload)).fetch_profile(SUBJECT))


def test_non_json_response_is_rejected() -> None:
    handler = lambda r: httpx.Response(200, text="<html>nope</html>")  # noqa: E731
    with pytest.raises(ProfileUnavailable, match="malformed-json"):
        asyncio.run(directory(handler).fetch_profile(SUBJECT))


def test_unconfigured_directory_fails_closed() -> None:
    bare = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://u:x@127.0.0.1:1/app_test",
        dbos_system_database_url="postgresql+psycopg://u:x@127.0.0.1:1/dbos_test",
    )
    with pytest.raises(ProfileUnavailable, match="not-configured"):
        asyncio.run(WorkOSDirectory(bare).fetch_profile(SUBJECT))


# ---- Rate limiter --------------------------------------------------------


def test_limiter_allows_up_to_the_limit_then_refuses() -> None:
    limiter = FixedWindowLimiter(limit=3, window_seconds=60)
    assert [limiter.check("a") for _ in range(5)] == [True, True, True, False, False]


def test_limiter_is_keyed_per_subject() -> None:
    limiter = FixedWindowLimiter(limit=1, window_seconds=60)
    assert limiter.check("subject:a") is True
    assert limiter.check("subject:b") is True
    assert limiter.check("subject:a") is False


def test_limiter_window_expires() -> None:
    limiter = FixedWindowLimiter(limit=1, window_seconds=0.05)
    assert limiter.check("a") is True
    assert limiter.check("a") is False
    import time

    time.sleep(0.06)
    assert limiter.check("a") is True


def test_limiter_memory_is_bounded() -> None:
    limiter = FixedWindowLimiter(limit=1, window_seconds=60, max_keys=16)
    for index in range(200):
        limiter.check(f"key-{index}")
    assert len(limiter._hits) <= 200  # noqa: SLF001
