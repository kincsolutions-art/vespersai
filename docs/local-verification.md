# Local infrastructure verification

## Configuration and ownership

All consumers require `postgresql+psycopg://` URLs with valid ports and an environment
suffix. SQLAlchemy/Alembic/DBOS receive this driver URL; direct psycopg probes convert
only the validated scheme to `postgresql://`. Configuration errors name fields and
corrective requirements, never their values or original exception chain.

Host development uses `.env` for API/worker and `.env.migrations` for Alembic.
Copy the corresponding `.example` files. Do not export migration credentials into
API/worker shells. Compose supplies `VESPERS_MIGRATION_DATABASE_URL` only to migrate.
Runtime `vespers_app` has CONNECT, schema USAGE, table SELECT/INSERT/UPDATE/DELETE,
and sequence USAGE/SELECT. It has no ownership, role membership, DDL, superuser,
BYPASSRLS, or inheritance privileges. `vespers_owner` owns app objects; `vespers_dbos`
owns only the separate DBOS database. No tenant product tables exist yet.

## Existing development volume: non-destructive transition

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
The transition preserves rows and objects, transfers ownership, and sets grants/default
privileges. It can be repeated; the integration harness verifies data preservation on
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
