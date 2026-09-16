# Local infrastructure verification

## Configuration and ownership

All consumers require `postgresql+psycopg://` URLs with valid ports and an environment
suffix. SQLAlchemy/Alembic/DBOS receive this driver URL; direct psycopg probes convert
only the validated scheme to `postgresql://`. Configuration errors name fields and
corrective requirements, never their values or original exception chain.

Host development uses `.env` for API/worker and `.env.migrations` for Alembic.
Create them from the corresponding `.example` files only when absent
(`[ -f .env ] || cp .env.example .env`); both may already hold real credentials.
`.env.migrations` targets the port Compose publishes to the host
(`127.0.0.1:5433`), not the container-internal `5432` the `migrate` service uses. Do not export migration credentials into
API/worker shells. Compose supplies `VESPERS_MIGRATION_DATABASE_URL` only to migrate.
Runtime `vespers_app` has CONNECT and schema USAGE, plus whatever a migration has
granted it explicitly. `infra/init-db.sql` sets no default privileges, so a new
migration-owned table grants it nothing (see migration `0003`). It has no
ownership, role membership, DDL, superuser, BYPASSRLS, or inheritance privileges. `vespers_owner` owns app objects;
`vespers_dbos` owns only the separate DBOS database.

Migration `0002` narrows this for the tenant foundations: it revokes the broad DML
that the *historical* `init-db.sql` default privileges granted, and re-grants only
SELECT on `users` and `memberships`,
SELECT plus column-level `UPDATE (name, updated_at)` on `tenants`, nothing at all
on `signup_allowlist`, and EXECUTE on the bootstrap functions (seven after
migration `0004`: five SECURITY DEFINER, two SECURITY INVOKER). See
[tenant foundations](tenant-foundations.md). Product tables beyond these four
(tasks, schedules, credentials, memory, Telegram links) still do not exist.

## Existing development volume: non-destructive transition

**When this is needed:** only for a development volume created before the
migration owner existed, where `vespers_app` still owns the database. A volume
created from the current `infra/init-db.sql` already has `vespers_owner` and must
not run it.

**When it must not run:** after migration `0002` has been applied. The script
predates the tenant schema and its `GRANT ... ON ALL TABLES` would widen the
deliberately narrow grants on `users`, `tenants`, `memberships` and
`signup_allowlist`. It now refuses to run once `public.users` exists, so the
guard is enforced rather than merely documented — but the ordering still matters:
transition first, migrate second, never the reverse.

To check which case applies, without changing anything:

```sh
docker compose exec -T postgres psql -U postgres -d vespers_development -tAc \
  "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database();"
docker compose exec -T postgres psql -U postgres -d vespers_development -tAc \
  "SELECT to_regclass('public.users') IS NOT NULL;"
```

Owner `vespers_owner` or `users` already present → skip the transition entirely.

The init script runs only on fresh volumes. Do not remove/reset an existing volume.
For the original scaffold's local database and public development role names:

1. Stop API/worker writers; keep PostgreSQL running.
2. Take a backup to a private directory (the dump may contain sensitive data).
3. Apply the transactional transition as the local administrator.
4. Rebuild/start the services with the new migration environment.

```sh
docker compose stop api worker
mkdir -p "$HOME/.vespers-backups"
chmod 700 "$HOME/.vespers-backups"
(umask 077; docker compose exec -T postgres pg_dump -U postgres -d vespers_development > "$HOME/.vespers-backups/before-owner-transition-$(date +%Y%m%d-%H%M%S).sql")
docker compose exec -T postgres psql -U postgres -d vespers_development -v ON_ERROR_STOP=1 < infra/transition-dev-owner.sql
test -e .env.migrations || cp .env.migrations.example .env.migrations
docker compose up --build -d --wait
```

Copy the example only if `.env.migrations` does not already contain your configuration.
The transition preserves rows and objects, transfers ownership, and sets explicit
grants; it sets no default privileges. It can be repeated; the integration harness verifies data preservation on
two applications. It rejects the wrong database, other database ownership, or remaining
indirect owner membership. Custom role layouts require review; this is not a production
credential migration. No existing database was modified during this batch.

## Resident readiness

The worker binds an HTTP probe to container loopback port 9091 only after `DBOS.launch()`
returns. Each request checks the stopping flag and a fresh connection to the DBOS
PostgreSQL database (two-second connect timeout). Compose uses the readiness module
with a three-second client timeout. Shutdown marks it unready and closes the server;
process death destroys the socket, so no readiness file survives restart. Database
loss fails the probe; reconnection restores it. This proves initialized-process and
DBOS-database reachability, not queue throughput or readiness of connected apps.

## Interrupted recovery harness

Run `make integration`. Requires Docker Compose with `!override`/`!reset` support
(tested with 5.5.1) and image/package download access. No provider keys.
The harness creates a unique project and fresh volume, disables host ports, checks
role permissions, startup, migration, HTTP health, smoke execution, and readiness.
Cleanup removes only that project's containers/network/volume.

`VESPERS_RECOVERY_PROBE_ENABLED=true` is accepted only in the test environment.
Only that worker registers the test queue/workflow. `DBOSClient` enqueues stable ID
`interrupted`; the submitter never calls `DBOS.launch()` or executes the workflow.
The resident worker records its hostname/PID in a committed completed step, then
signals a controlled unfinished boundary in a second step. The harness verifies its
hostname and PENDING state, sends SIGKILL, releases the boundary, and restarts the
worker. It waits for SUCCESS, result `recovered`, and exactly one completed-step row.
Polling checks explicit database conditions with bounded deadlines, not guessed delays.

Executor identity is `vespers-single-worker`, application version `scaffold-v2`.
DBOS 2.31.1 startup recovery uses that stable identity and version. This configuration
is for one resident worker only; do not duplicate that executor across machines.
One-off smoke executors use unique identities so they cannot recover resident work.
Changing application version during unfinished work needs a separate deployment plan.

Passing proves replay of this harmless interrupted PostgreSQL workflow skips its
previously recorded completed step. It does not prove exactly-once external effects,
Telegram delivery safety, distributed failover, arbitrary crash-window correctness,
or full Pydantic AI durability.

Historical provider compatibility was pending at the time of the infrastructure audit.
The subsequent bounded spike is recorded in `docs/provider-compatibility.md`:
Tavily passed; Gemini discovery passed but generation returned HTTP 504; Composio
live actions are deferred until onboarding UI. Remote CI remains unobserved.

## Pydantic AI adapter probe (2026-09-15)

The disposable harness now also runs `adapter_workflow` through the resident worker,
using Pydantic AI 2.43.0 DBOSDurability, DBOS 2.31.1, a FunctionModel and a local tool
explicitly decorated with `@DBOS.step()`. It synchronizes through PostgreSQL, kills
the worker during the unfinished final model call, then recovers the same workflow.
Successful execution counters: first model 1, local tool 1, final model 2; result 42.
Without the local tool's explicit step decorator, the tool replayed; the adapter alone
must not be assumed to make plain local tools durable.

The synthetic credential closure sentinel was absent from worker logs, DBOS workflow
rows and operation outputs (including base64-decoded strings). This is a narrow probe,
not complete credential-security coverage or a test of live provider-client serialization.
It does not prove exactly-once external effects, delivery safety, distributed recovery,
or duplicate-free inference billing. The test environment contains no live provider keys.

The harness resets development bind mounts only in its isolated override so tests run
the built artifacts. Startup, role/RLS denials, legacy ownership transition twice,
original resident recovery, adapter recovery, outage/readiness and reconnection passed.
Only its disposable project was removed; no development database was migrated.

## Tenant isolation harness (2026-09-16)

`make integration` now also runs `tests/integration/checks/tenant_isolation.py`
inside the api container as `vespers_app`, using direct SQL alongside the
repository so that application-side filtering cannot conceal a broken policy.

It asserts the runtime role is neither owner nor superuser and cannot bypass RLS;
that the allowlist is unreadable and unwritable by runtime and eligibility flows
only through `app.signup_allowed`; that an unallowlisted signup leaves no orphan
rows; that duplicate and concurrent provisioning converge on one user, tenant and
membership; that tenant A cannot list, read by guessed id, update or delete tenant
B's rows; that ownership, id and status columns are unwritable; that missing,
empty, malformed and unauthorized context all fail closed; that one connection
serving A then B then no context leaks nothing, including on rollback and
statement-error paths; that disabled memberships, identities and tenants are each
rejected by access resolution; that runtime cannot alter policies, run DDL or
assume the owner role; and that DBOS storage carries no application tables,
policies or `app` schema.

Fixtures that require owner rights (allowlist seeding, the second membership, and
the disablements) are applied through `psql -U vespers_owner`, never with runtime
credentials. The harness also runs `alembic downgrade -1` and `upgrade head`
against the disposable database only.

This proves database-enforced isolation for these four tables. It does not prove
isolation for tables that do not exist yet, and it is not a defence against an
attacker holding the runtime credentials with arbitrary SQL execution.

## Authentication and default-grant checks (2026-09-16)

`make integration` also runs `tests/integration/checks/identity_binding.py` inside
the api container as `vespers_app`, covering migrations `0003` and `0004`:

- two verified subjects provision two separate identities and tenants;
- a duplicate callback for the same subject is idempotent;
- **a second subject presenting an existing verified email fails closed and
  creates nothing** — the defect `0003` fixes;
- an upstream email change follows the subject without transferring ownership;
- a known subject cannot claim an address already bound to another identity;
- an unallowlisted subject is refused with no orphan rows;
- four concurrent callbacks for one subject converge on a single identity;
- membership listing returns only the requested user's active tenants;
- **a table created by the migration owner after `0003` grants the runtime role
  nothing**, while the explicit grants on the foundation tables survive;
- **a missing or blank subject cannot authenticate, claim an account, or create
  one** — refused by the Python guard, by an explicit NULL argument (SQLSTATE
  22023), and by the dropped two-argument form, which no longer resolves to a
  function at all;
- **a user row with no bound subject fails closed** (`VS003`) instead of being
  adopted by whoever presents its address;
- administrative binding, applied by the migration owner, is what makes such a
  row usable — signing in never does;
- **removing an allowlist entry and disabling a tenant revoke access on the next
  call**, with the account, user row and membership left intact.

Every administrative state change in that last group is applied from the harness
with the **migration owner**, not from inside the api container, so the assertion
that `api` and `worker` never hold migration credentials still holds.

The migration round trip now runs `alembic downgrade 0001` followed by
`upgrade head`, so baseline-to-head is exercised rather than a single step.

`infra/init-db.sql` no longer sets `ALTER DEFAULT PRIVILEGES`. The harness's own
`privilege_probe` table therefore carries an explicit `GRANT SELECT`, so that
probe keeps testing the RLS policy rather than the absence of a grant.

No live WorkOS request is made anywhere in the harness.