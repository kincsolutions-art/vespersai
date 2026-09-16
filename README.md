# Vespers

A Telegram assistant with connected apps, schedules, memory, and useful proactive
suggestions. This repository currently contains the development scaffold plus the
tenant and identity storage foundations, and WorkOS AuthKit sign-in. Telegram, the
agent loop, connected-app actions, schedules, memory and discovery are not implemented.

## Local setup

Prerequisites: Git, uv 0.12.14, Node.js 24.19.0 with npm, and Docker with Compose v2.
Python 3.12 is pinned in `.python-version`; uv can install it automatically.

These create the two local files only when they are absent. Both are Git-ignored
and may already hold real credentials, so neither command overwrites one:

```sh
[ -f .env ] || cp .env.example .env
[ -f .env.migrations ] || cp .env.migrations.example .env.migrations
make install
docker compose up -d postgres
# Host-side Alembic; reads .env.migrations and connects on the published port
# 127.0.0.1:5433. The Compose `migrate` service uses the internal 5432 instead.
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

Compose reads `./.env` for `${...}` interpolation and passes the two `VESPERS_*`
values to `api` and `worker`. `.env.dashboard` is loaded by the `dashboard`
service as an `env_file`, server-side only, and is the **single source** for the
five values above: `compose.yaml` deliberately sets none of those keys, because a
key present in a service's `environment:` silently overrides the same key from
its `env_file`. Compose does set `VESPERS_API_BASE_URL` and `VESPERS_ENVIRONMENT`
for the dashboard, and those *do* override the file — they describe container
topology, not WorkOS configuration.

Nothing here reaches the browser bundle. Keep real values out of tracked files,
images, command arguments and logs.

**Production requires HTTPS.** With `VESPERS_ENVIRONMENT=production` the
dashboard refuses to offer sign-in unless `NEXT_PUBLIC_WORKOS_REDIRECT_URI` is
`https://` — including on `localhost`, because the AuthKit session cookie is
marked `Secure` from that scheme alone. `/auth/sign-in` then returns
`503 redirect-uri-not-https` and `/account` says so. Plain HTTP is permitted only
under `development` or `test`. `NODE_ENV` is not the discriminator: the Compose
image serves a production Next.js build on your laptop.

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

### Manual verification checklist

These six checks are **pending**; nothing in the repository records them as done.
See [authentication](docs/authentication.md#live-evidence) for what *is* evidenced
and how strongly.

Never paste an access token into a shell, a chat message or a ticket. Nothing
below needs one: the browser holds an opaque sealed cookie, and the
database-side decision is readable directly.

```sh
# The backend's authorization decision for an address, read from the database
# alone — no token, no session, no WorkOS call. Prints the same conditions
# authorize_local_access re-checks on every protected request.
docker compose exec -T api python -m backend.identity.access_check you@example.com
```

Use a **second, disposable WorkOS user** for every denial check. Do not remove
the real account's allowlist entry, disable it, or send invitations.

| # | Check | How | Expected |
| --- | --- | --- | --- |
| 1 | Allowed-user sign-in | Sign in at http://localhost:3000 as the allowlisted account | `/account` shows the email, personal tenant name, tenant ID and role; `access_check` prints `Decision: permitted` |
| 2 | Valid but unallowlisted identity is denied | Sign in as a second WorkOS user that was never allowlisted | `/account` shows "Access denied (not-allowlisted)"; `access_check` for that address prints `Decision: DENIED — not-allowlisted` |
| 3 | Anonymous direct API call | `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/account` | `401` — a *different* check from 2: this proves the API needs its own credential, not that an authenticated identity is refused |
| 4 | Session refresh | Leave `/account` open past the access-token lifetime (AuthKit default 5 minutes), then reload | The page still renders the account; the proxy refreshed the sealed session without a new sign-in |
| 5 | Logout | Click "Sign out", then open `/account` again | "Sign in to continue"; the WorkOS session is ended, not merely the local cookie |
| 6 | Established-session revocation | While signed in, remove the allowlist row as the migration owner, then reload `/account` **without signing out** | "Access denied (not-allowlisted)" on the very next request, not at token expiry |

Check 6 mutates state. Restore it immediately afterwards — the row is
recreated by the same command used in step 4 of the setup above, and the user,
tenant and membership rows are never deleted by revocation, so access returns on
the next request:

Pass bare values here too: psql's `:'name'` form quotes and escapes them itself,
so a pre-quoted `"'you@example.com'"` would store or match the quotes literally.

```sh
# Revoke:
docker compose exec -T postgres psql -U vespers_owner -d vespers_development \
  -v ON_ERROR_STOP=1 -v email=you@example.com <<'SQL'
DELETE FROM public.signup_allowlist WHERE email_normalized = lower(btrim(:'email'));
SQL

# Restore:
docker compose exec -T postgres psql -U vespers_owner -d vespers_development \
  -v ON_ERROR_STOP=1 -v email=you@example.com -v note='first account' \
  < infra/allowlist-add.sql
```

See [authentication](docs/authentication.md) for the trust boundary, the full
revocation table, application binding of the token, identity-binding rules, and
what remains unverified.

## Tenants, identity, and the signup allowlist

`users`, `tenants`, `memberships` and `signup_allowlist` are created by migration
`0002`, with fail-closed row-level security. The runtime role holds no INSERT or
DELETE grant on any of them and cannot read the allowlist at all.

Existing development databases need only `make migrate`; the migration is additive
and creates no data.

Fresh initialization grants the runtime role **nothing** on a new table:
`infra/init-db.sql` sets no default privileges, and migration `0003` revokes the
permissive ones it used to set on databases created before that change. Migration
`0002` still revokes and re-grants explicitly, because it predates `0003` and ran
against databases that had them. **Any future table must grant the runtime role
its access explicitly** — the absence of default privileges is what makes that a
deliberate act rather than something a migration can forget.

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
