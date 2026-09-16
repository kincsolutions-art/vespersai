# Vespers

A Telegram assistant with connected apps, schedules, memory, and useful proactive
suggestions. This repository currently contains the development scaffold plus the
tenant and identity storage foundations, and WorkOS AuthKit sign-in. Telegram, the
agent loop, connected-app actions, schedules, memory and discovery are not implemented.

## Local setup

Prerequisites: Git, uv 0.12.14, Node.js 24.19.0 with npm, and Docker with Compose v2.
Python 3.12 is pinned in `.python-version`; uv can install it automatically.

```sh
cp .env.example .env
cp .env.migrations.example .env.migrations
make install
docker compose up -d postgres
make migrate
make dev-api
# In separate terminals:
make dev-dashboard
make worker
# Execute a no-side-effect durable workflow:
make smoke
```

Dashboard: http://localhost:3000. API: http://localhost:8000/docs.
`/health/live` checks the API process; `/health/ready` checks application DB access.
The dashboard exposes `/api/health`. No provider credentials are needed yet.

Alternatively run the complete development stack with `docker compose up --build -d`.
For automatic rebuilds after source changes, run `docker compose watch` in another
terminal. The dashboard runs the image’s standalone Next.js server; mounting the
source directory over `/app` hides its generated `server.js` and prevents startup.
Run its smoke workflow with
`docker compose run --rm worker python -m backend.workflows.worker --smoke`.
Stop with `docker compose down`; volumes persist. Local database credentials in
Compose and `.env.example` are public development placeholders, never production secrets.

## Checks and migrations

```sh
make check
npm run build --prefix apps/dashboard
make format
uv run alembic revision -m "describe change"
make migrate
uv run alembic downgrade -1
```

Migration `0001` is an empty baseline; `0002` adds the tenant foundations
(`users`, `tenants`, `memberships`, `signup_allowlist`) with row-level security;
`0003` makes identity binding subject-first and removes the permissive default
privileges, so a new migration-owned table grants the runtime role nothing until
a migration grants it explicitly; `0004` makes the verified external subject a
required argument, so an email address alone can never resolve or claim an
account.
Review every generated migration before applying it. CI runs static checks, tests,
a dashboard build, Compose startup, and a PostgreSQL-backed DBOS smoke workflow.

## Layout

- `apps/dashboard`: Next.js account-management shell. Brand PNGs live in
  `apps/dashboard/public/brand`; the favicon lives in `apps/dashboard/app/favicon.ico`.
- `backend/api`: FastAPI lifecycle, structured request logging, health endpoints.
- `backend/identity`: bootstrap identity service and the tenant-scoped repository.
- `backend/auth`: WorkOS token validation, directory lookup, and rate limiting.
- `backend/{credentials,agent,tools}`: reserved implementation packages.
- `backend/workflows`: shared DBOS worker and harmless smoke workflow.
- `backend/storage`: models, async sessions, and the transaction-local tenant context.
- `migrations`: Alembic revisions, including the tenant schema, policies and grants.
- `tests`, `infra`, `.github/workflows`: verification and development infrastructure.

## Environment boundaries

Settings validate `development`, `test`, or `production` and require matching database
name suffixes. Application and DBOS storage use different databases. Migrations use a dedicated
owner role and `.env.migrations`; API/worker use the restricted application role.
Compose injects the owner URL only into the migration service.
Provision independent credentials, databases, encryption keys, and provider accounts
for each environment; this scaffold has not provisioned test/production infrastructure.
Production requires an external encryption key but credential encryption is not yet
implemented. No live provider keys belong in environment examples or workflow inputs.

Compose is local development infrastructure. Production HTTPS, backups, retention, and
recovery drills remain future work. Do not expose this unauthenticated scaffold publicly.

See [locked scope](docs/scope.md), [provider spike](docs/provider-compatibility.md),
[agent instructions](AGENTS.md), and [implementation checklist](implementation-checklist.md).

## Local integration verification

Run `make integration` with Docker running. It builds a uniquely named disposable
Compose project with fresh test storage, no host ports, privilege probes, worker
readiness checks, and an actual interrupted PostgreSQL workflow. It removes only
its own containers/network/volume, including on failure.

Existing volumes need the non-destructive transition in
[local infrastructure verification](docs/local-verification.md) before starting the new services.

## WorkOS AuthKit setup

Sign-in is optional for local development: with no WorkOS configuration the
dashboard renders "sign-in unavailable", protected API routes return
`503 authentication-not-configured`, and every offline check still passes.

To enable it, create a WorkOS **development** environment, then:

**1. Register the redirect URLs.** In the WorkOS dashboard under *Redirects*,
add exactly:

| Field | Value |
| --- | --- |
| Redirect URI | `http://localhost:3000/auth/callback` |
| Logout redirect | `http://localhost:3000/` |

**2. Create the local configuration without overwriting anything.** Both files
are Git-ignored and excluded from Docker builds. These commands append or create
and never clobber an existing private file:

```sh
[ -f .env.dashboard ] || cp .env.dashboard.example .env.dashboard
grep -q '^VESPERS_WORKOS_CLIENT_ID=' .env || cat >> .env <<'EOF'
VESPERS_WORKOS_CLIENT_ID=
VESPERS_WORKOS_API_KEY=
EOF
```

Then edit both files and fill in:

| File | Variable | Where it comes from |
| --- | --- | --- |
| `.env.dashboard` | `WORKOS_CLIENT_ID` | WorkOS → API keys → Client ID |
| `.env.dashboard` | `WORKOS_API_KEY` | WorkOS → API keys → Secret key |
| `.env.dashboard` | `WORKOS_COOKIE_PASSWORD` | `openssl rand -base64 32` (32+ chars) |
| `.env.dashboard` | `NEXT_PUBLIC_WORKOS_REDIRECT_URI` | `http://localhost:3000/auth/callback` |
| `.env.dashboard` | `VESPERS_ALLOWED_ORIGINS` | `http://localhost:3000` |
| `.env` | `VESPERS_WORKOS_CLIENT_ID` | the same client ID |
| `.env` | `VESPERS_WORKOS_API_KEY` | the same secret key |

Compose reads `./.env` for interpolation and passes the two `VESPERS_*` values to
`api` and `worker`; `.env.dashboard` is loaded by the `dashboard` service only,
server-side. Nothing here reaches the browser bundle. Keep real values out of
tracked files, images, command arguments and logs.

**3. Start the stack in order.** `depends_on` enforces this, so a single command
is enough — Postgres becomes healthy, `migrate` runs to completion as the
migration owner, then `api`, `worker` and `dashboard` start:

```sh
docker compose up --build -d --wait
```

Migrations are applied **only** by the `migrate` service, which is the only place
`VESPERS_MIGRATION_DATABASE_URL` exists; `api` and `worker` never receive it. To
re-run migrations alone: `docker compose run --rm migrate`. (`make migrate` is the
host-side equivalent and reads `.env.migrations`.)

**Legacy ownership transition.** `infra/transition-dev-owner.sql` is only for a
development volume created before the migration owner existed, where
`vespers_app` still owns the database. It **must not** run after migration 0002:
its `GRANT ... ON ALL TABLES` would widen the deliberately narrow grants on the
foundation tables, and the script now refuses to run once `public.users` exists.
A volume created from the current `infra/init-db.sql` never needs it. See
[local infrastructure verification](docs/local-verification.md).

**4. Allowlist the account you will sign in with.** There is deliberately no API
for this. Pass bare values — `infra/allowlist-add.sql` uses psql's `:'name'`
form, which quotes and escapes them itself:

```sh
docker compose exec -T postgres psql -U vespers_owner -d vespers_development \
  -v ON_ERROR_STOP=1 -v email=you@example.com -v note='first account' \
  < infra/allowlist-add.sql
```

Signing in without this returns `403 not-allowlisted` by design.

**5. Open http://localhost:3000** and sign in.

### Verifying it, without handling a bearer token

Never paste an access token into a shell, a chat message or a ticket. These
checks establish the same facts without one:

```sh
# The API requires its own credential and does not trust the dashboard.
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/account   # 401

# The backend's authorization decision for an address, read from the database
# alone — no token, no session, no WorkOS call. Prints the same conditions
# authorize_local_access re-checks on every protected request.
docker compose exec -T api python -m backend.identity.access_check you@example.com
```

- **Allowed account** — after allowlisting, sign in; `/account` shows your email,
  personal tenant name and tenant ID. `access_check` prints `Decision: permitted`.
- **Denied account** — sign in with a second WorkOS user that is *not* on the
  allowlist. `/account` shows "Access denied" with the backend's own reason
  string, because the page renders the FastAPI response verbatim rather than
  making a UI-side decision. `access_check` for that address prints
  `Decision: DENIED — not-allowlisted`.
- **Revocation** — remove the allowlist row
  (`DELETE FROM public.signup_allowlist WHERE email_normalized = '...'` as
  `vespers_owner`) and reload `/account` **without signing out**. The existing
  session is refused immediately; it does not wait for the token to expire.

See [authentication](docs/authentication.md) for the trust boundary, the full
revocation table, application binding of the token, identity-binding rules, and
what remains unverified.

## Tenants, identity, and the signup allowlist

`users`, `tenants`, `memberships` and `signup_allowlist` are created by migration
`0002`, with fail-closed row-level security. The runtime role holds no INSERT or
DELETE grant on any of them and cannot read the allowlist at all.

Existing development databases need only `make migrate`; the migration is additive
and creates no data. It revokes the broad DML that `infra/init-db.sql` default
privileges would otherwise grant, then re-grants narrowly, so **any future table
must repeat that revoke/re-grant step**.

Add the first account to the allowlist through the administrative path, as the
migration owner (see step 4 above). There is deliberately no API for this, and
no runtime grant on the table.

The other owner-only administrative path is `infra/bind-subject.sql`, which binds
a verified external subject to a user row that has none. It exists only for rows
predating migration 0004; signing in never adopts such a row.

The tenant context is transaction-local and is an *enforcement input, not
authentication*: the backend must resolve an active membership before opening a
scope. See [tenant foundations](docs/tenant-foundations.md) for the access matrix,
the bootstrap trust boundary, and the FORCE ROW LEVEL SECURITY decision record.

## Provider preparation and key handoff

The approved providers are Gemini Developer API BYOK, direct Tavily search with a
separate platform-managed key and per-tenant quotas, and Composio app actions. Nebius is unavailable to the project owner in Nepal. The rest
of the stack, including selected WorkOS AuthKit, is unchanged.

For explicit compatibility testing, copy `.env.spike.example` to `.env.spike`
only if that file does not exist, then edit it locally. The actual file is Git-ignored
and excluded from Docker builds. Do not paste keys into chat. API/worker startup,
Compose, and ordinary CI neither load it nor require provider keys.

The opt-in bounded live runner is `python -m backend.agent.live_spike`; keys alone
start no calls. Direct Tavily passed the live spike; Gemini discovery passed but
generation returned HTTP 504. Composio live actions are deferred until onboarding UI.
See [provider compatibility](docs/provider-compatibility.md) for fields, documented
provider configuration, data handling, costs, and remaining test prerequisites.
