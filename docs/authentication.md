# Authentication: WorkOS AuthKit

Implements verified-email sign-in and binds it to the tenant foundations in
[tenant foundations](tenant-foundations.md). Telegram linking, BYOK onboarding
and connected apps are **not** part of this.

## Transport

A server-side Next.js BFF, with FastAPI validating the access token
independently. Pinned SDKs: `@workos-inc/authkit-nextjs` 4.3.2,
`@workos-inc/node` 10.13.0, `PyJWT[crypto]` 2.14.0.

```
browser ──sealed HttpOnly session cookie──▶ Next.js (proxy.ts + AuthKit)
                                              │  Authorization: Bearer <access token>
                                              ▼
                                           FastAPI
                                              ├── verify JWT against WorkOS JWKS   → sub
                                              ├── GET /user_management/users/{sub}  → verified email
                                              ├── allowlist + provisioning (PostgreSQL)
                                              └── membership → tenant context → RLS
```

Next.js 16 uses `proxy.ts` with `authkitProxy()`; `middleware.ts` is the
pre-16 form. The session cookie is encrypted with `WORKOS_COOKIE_PASSWORD` and is
`HttpOnly`. **No access or refresh token is written to `localStorage`,
`sessionStorage`, or serialised into the page.** The browser never holds a bearer
token; it holds an opaque sealed cookie, and the BFF attaches the bearer
server-side.

## Trust boundary

FastAPI trusts exactly two things, in this order:

1. **The signature on the access token.** Verified against
   `https://api.workos.com/sso/jwks/<client_id>` — a key set scoped to our client
   id. Checked: signature, `iss`, `exp` (60s leeway), an algorithm allowlist
   (`RS256`), and the presence of `sub`.
2. **The WorkOS User Management API**, queried server-to-server with the API key
   for the verified `sub`, which yields `email` and `email_verified`.

Everything else is refused as input. The browser and the BFF cannot assert an
email, a verification flag, a user id or a tenant id — headers and payload fields
carrying those are ignored entirely. `sub` is the only identity fact that crosses
the boundary, and it arrives inside a signature we check ourselves.

## Application binding

Established from the pinned SDKs and current WorkOS documentation, not inferred
from the URL shape.

| Question | Answer | Source |
| --- | --- | --- |
| Key-set endpoint | `${baseURL}/sso/jwks/${clientId}` | `@workos-inc/node` 10.13.0 `getJwksUrl()`; `authkit-nextjs` 4.3.2 calls it with `WORKOS_CLIENT_ID` |
| Are keys client-specific? | **Yes** — WorkOS signs with a key set unique to the client id | [WorkOS: verifying access tokens](https://workos.com/blog/verify-workos-access-tokens-in-your-own-api) |
| `iss` | `https://api.workos.com/` (AuthKit sessions guide) or `https://api.workos.com` (API reference); **changes with a custom auth domain** and should be read from configuration | WorkOS docs |
| `aud` | **Absent** on AuthKit session tokens; addable through a JWT template; present natively on OAuth/MCP tokens | WorkOS docs |
| Required claims | `sub`, `sid`, `iss`, `exp`, `iat`; `org_id`/`role`/`permissions` when applicable | WorkOS docs |
| Algorithm | Asymmetric, `RS256` allowlisted; `HS*`/`none` refused at configuration time | pinned config |
| Key rotation | JWK set cached 300s; an unknown `kid` forces one refetch after a 30s cooldown | PyJWT 2.14 `PyJWKClient` |

**So the binding is the key set, not an audience claim.** A token minted for a
different WorkOS application is signed by a different key and fails signature
verification here.

Three consequences are configured deliberately:

- **Issuer.** Because WorkOS's own documentation spells the value both with and
  without a trailing slash, and because a custom auth domain changes it,
  `VESPERS_WORKOS_ISSUERS` is a list and each entry is accepted in both
  spellings of the same origin. A different origin — including
  `https://api.workos.com.evil.test/` — is refused.
- **Audience.** Off by default, because AuthKit session tokens carry none. If a
  JWT template adds one, set `VESPERS_WORKOS_AUDIENCE` and it is enforced. A
  token that carries an `aud` while none is configured is **refused**
  (`unexpected-audience`) rather than accepted with the claim unchecked — that
  is what a WorkOS OAuth or MCP token looks like, and those authorize a resource
  server rather than a first-party session.
- **Key caching.** PyJWT's second-tier per-`kid` LRU is deliberately disabled: it
  has no time expiry, so a key WorkOS withdrew would keep verifying tokens until
  it happened to be evicted.

Symmetric (`HS*`) and `none` algorithms are rejected at configuration time — a
public JWKS key replayed as an HMAC secret is the classic confusion attack.

This validation is strictly stricter than the SDK's own `verifyAccessToken`,
which checks the signature alone.

**Proven offline:** every rule above, against genuinely signed RSA/EC tokens with
locally generated keys. **Requires a real token:** that a live WorkOS token
actually carries the `iss`, `kid` and algorithm this configuration expects. A
trailing-slash or custom-domain mismatch would surface as `401 wrong-issuer`;
reading the `iss` from one decoded token is the fix, and the setting exists for
it. Bearer tokens are never logged, printed or persisted.

## Session lifecycle

| Event | Behaviour |
| --- | --- |
| Sign-in | AuthKit redirect → `/auth/callback` → sealed cookie → idempotent `POST /api/account/provision` |
| Refresh | `authkitProxy()` refreshes the sealed session on matched routes; FastAPI simply sees a newer access token |
| Expiry | FastAPI returns `401 expired`; the account page renders "session expired" |
| Logout | `signOut()` clears the cookie and ends the WorkOS session |
| Local disablement | see the revocation table below |

## Revocation: exactly what happens, and when

`authorize_local_access` in `backend/api/account.py` runs on **every** protected
request — not only at sign-in — and re-reads all four conditions from PostgreSQL
each time. Nothing is cached in the session or the token.

| Change (applied by an administrator) | Effect on an already-established session | Where enforced |
| --- | --- | --- |
| Allowlist entry removed | **Next request** refused `403 not-allowlisted`. The account, tenant and membership rows survive. | `app.signup_allowed`, re-checked per request against the **stored** verified email |
| `users.status = 'disabled'` | **Next request** refused `403 identity-disabled` | `find_user` status check, and `app.list_memberships` |
| `tenants.status = 'disabled'` | **Next request** refused `403 no-active-membership` | `app.list_memberships` joins and filters tenant status |
| `memberships.status = 'disabled'` | **Next request** refused `403 no-active-membership` | `app.list_memberships` filters membership status |
| Upstream email changed at WorkOS | Reconciled at the **next provisioning call**, not per request. The new address must itself be allowlisted or the change is refused. | `app.provision_personal_identity` |

Provisioning enforces the same conditions and additionally refuses a disabled
tenant or membership rather than silently reactivating one, so `POST
/api/account/provision` can never report access that a subsequent `GET` would
deny.

⚠️ **Application-access revocation is not token revocation.** An access token
already issued stays cryptographically valid until it expires; what the table
above revokes is the ability to *use* it against this application. We do not
implement a token denylist, and must not claim we do. The residual window is
therefore zero for application access and bounded by token lifetime for anything
that would trust the token without calling us.

## Access control

`GET /api/account` and `POST /api/account/provision` enforce, in order:

1. Bearer token present and valid, else `401`.
2. Per-subject rate limit, else `429`.
3. (provision only) `email_verified` true, else `403 email-not-verified`.
4. Local user exists and is active, else `403 not-provisioned` /
   `403 identity-disabled`.
5. Allowlist, checked against the stored verified email on every request and
   again inside `app.provision_personal_identity`, else `403 not-allowlisted`.
6. Active membership, else `403 no-active-membership`.
7. A requested `tenant_id` must match a verified membership, else
   `403 tenant-not-permitted` — never a silent fallback to a different tenant.

**A successful WorkOS sign-in grants nothing on its own.** Steps 4–6 live in the
database, so a direct API call cannot bypass what the dashboard displays.

Upstream failures are kept distinct from denials: WorkOS unreachable or
malformed → `502`; database failure → `503 provisioning-unavailable`. Neither is
ever reported as successful provisioning.

## Identity binding rules

The verified WorkOS `sub` is the **primary** identity; email is secondary.

| Situation | Result |
| --- | --- |
| Known subject, same email | Idempotent; returns the existing user/tenant/membership |
| Known subject, email changed upstream | Email follows the subject; ownership unchanged |
| Known subject, new email already belongs to someone else | `409 email-already-bound`; no merge |
| New subject, email belongs to a **different** subject | `409 external-subject-conflict`; nothing created |
| New subject, email exists with **no** subject | `409 subject-binding-required`; nothing created |
| No subject, or a blank one | `401`/refused before any row is read; not authentication |
| Any subject, email not allowlisted | `403 not-allowlisted`; nothing created |

There is no automatic account merging and no account recovery. A conflict is a
terminal error requiring a human decision.

### The verified subject is mandatory

`app.provision_personal_identity` takes the external subject as a **required**
argument — migration 0004 removed the default, so a subjectless call does not
even resolve to a function. An email address alone can neither resolve nor claim
an account. This closes the last runtime-callable account-claim shortcut: before
0004 a call with only an email could be handed an account that already belonged
to a verified subject.

**Administrative binding is separate from authentication.** A user row with no
`external_id` can only exist if it predates 0004 or was inserted directly by the
migration owner. Sign-in never adopts such a row; it fails closed with
`409 subject-binding-required`. Binding is a deliberate owner-only act with no
runtime grant and no API surface:

```sh
docker compose exec -T postgres psql -U vespers_owner -d vespers_development \
  -v ON_ERROR_STOP=1 -v email=person@example.com -v subject=user_01ABCDEF \
  < infra/bind-subject.sql
```

It refuses to rebind an account that already has a subject, and refuses a subject
already bound elsewhere.

**Allowlist revocation.** Re-checked on every provisioning call against the
email being presented, *and* on every protected request against the stored
verified email. See the revocation table above for the exact effect of each
change.

**Email change.** The new address must itself be allowlisted, otherwise the
change is refused. This is deliberate for a one-account MVP: it stops an upstream
email change from moving an account onto an address nobody approved.

## CSRF

The Next.js BFF route `POST /api/account` is cookie-authenticated, so the browser
attaches credentials automatically; it verifies the `Origin` header against
`VESPERS_ALLOWED_ORIGINS` and refuses anything else. The FastAPI endpoints are
**bearer-only** — they carry no ambient credential a cross-site form could abuse —
so browser CSRF assumptions are deliberately *not* applied to them. Applying a
CSRF token to a bearer API would be theatre.

## Redirect safety

`safeReturnPath()` accepts only same-site absolute paths. Absolute URLs,
`//evil.test`, backslash variants that browsers normalise, scheme-like prefixes,
control characters, whitespace and over-long values all fall back to `/account`.

## Rate limiting: scope and limits

`FixedWindowLimiter` is an in-memory fixed window keyed by verified subject,
default 10 requests/minute (`VESPERS_AUTH_RATE_LIMIT_PER_MINUTE`).

It is **per process**: N API workers permit up to N × the limit. It **resets on
restart**. It is **not shared** across containers or machines. It bounds
accidental retry storms and trivial scripted abuse without adding Redis. It is
not a defence against a distributed attacker and not a quota system.

## Cookie security

The SDK sets the session cookie `HttpOnly` with `SameSite=Lax` and marks it
`Secure` for `https://` redirect URIs. `http://localhost` is the development
exception and works only because the redirect URI is explicitly `http://`. In
production, `VESPERS_ENVIRONMENT=production` refuses to start without a WorkOS
client id, API key, an HTTPS API base and an HTTPS issuer.

## Fail-closed when unconfigured

With no WorkOS configuration, protected routes return
`503 authentication-not-configured`, the dashboard renders "sign-in unavailable",
and `/health/live`, `/health/ready` and the whole offline test suite keep working.
CI builds the dashboard with no WorkOS variables at all.

## What is NOT verified

**No real WorkOS sign-in has been performed.** Every test uses synthetic
identities, locally generated RSA/EC keys and mocked WorkOS HTTP responses. Token
validation is exercised against genuinely signed tokens — the validator itself is
never mocked — but that establishes our verification logic, not live WorkOS
compatibility. See "Manual setup" in the README for what is still required.
