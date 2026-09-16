"""Server-side authorization report for one email address.

Answers "what would the backend decide for this account?" using only the
database: no token, no browser session and no WorkOS call. It exists so the
API-side decision can be checked without anyone copying a bearer token into a
shell, a chat message or a log.

    docker compose exec -T api python -m backend.identity.access_check you@example.com

It reads the same conditions `backend.api.account.authorize_local_access`
re-checks on every protected request — the allowlist, the user status, and an
active membership — through the same runtime role and the same SECURITY DEFINER
surface, so a disagreement between this report and a live request would itself
be a bug.

It prints no identifier beyond the address it was given, grants nothing, and
changes nothing. Exit status 0 means permitted, 1 means denied, 2 means misuse.
"""

import asyncio
import sys

from backend.config import get_settings
from backend.identity.service import IdentityService
from backend.storage.session import create_engine, create_sessionmaker


async def describe(service: IdentityService, email: str) -> tuple[list[str], list[str]]:
    """Return (report lines, denial reasons). Empty reasons means permitted."""
    lines: list[str] = []
    reasons: list[str] = []

    allowed = await service.is_signup_allowed(email)
    lines.append(f"allowlisted:      {'yes' if allowed else 'NO'}")
    if not allowed:
        reasons.append("not-allowlisted")

    user = await service.find_user(email=email)
    if user is None:
        lines.append("local account:    none (created on first sign-in)")
        return lines, reasons

    lines.append(f"local account:    yes (status {user.status})")
    if not user.is_active:
        reasons.append("identity-disabled")

    lines.append(f"external subject: {'bound' if user.external_id else 'NOT BOUND'}")
    if user.external_id is None:
        # Migration 0004: an unbound row is never claimed by signing in.
        reasons.append("subject-binding-required (see infra/bind-subject.sql)")

    memberships = await service.list_memberships(user.id)
    lines.append(f"active tenants:   {len(memberships)}")
    if not memberships:
        reasons.append("no-active-membership")
    return lines, reasons


async def report(email: str) -> int:
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    try:
        async with sessions() as session, session.begin():
            lines, reasons = await describe(IdentityService(session), email)
    finally:
        await engine.dispose()
    print("\n".join(lines))
    if reasons:
        print("\nDecision: DENIED — " + ", ".join(reasons))
        return 1
    print("\nDecision: permitted")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not argv[0].strip():
        print("usage: python -m backend.identity.access_check <email>", file=sys.stderr)
        return 2
    return asyncio.run(report(argv[0].strip()))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
