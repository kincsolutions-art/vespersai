# Vespers

A Telegram assistant with connected apps, schedules, memory, and useful proactive
suggestions. This repository currently contains the development scaffold only.
Authentication, tenant tables/RLS, Telegram, and provider actions are not implemented.

## Local setup

Prerequisites: Git, uv 0.12.14, Node.js 24.19.0 with npm, and Docker with Compose v2.
Python 3.12 is pinned in `.python-version`; uv can install it automatically.

```sh
cp .env.example .env
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

The initial migration is an empty baseline; it does not claim tenant isolation.
Review every generated migration before applying it. CI runs static checks, tests,
a dashboard build, Compose startup, and a PostgreSQL-backed DBOS smoke workflow.

## Layout

- `apps/dashboard`: Next.js account-management shell. Brand PNGs live in
  `apps/dashboard/public/brand`; the favicon lives in `apps/dashboard/app/favicon.ico`.
- `backend/api`: FastAPI lifecycle, structured request logging, health endpoints.
- `backend/{identity,credentials,agent,tools}`: reserved implementation packages.
- `backend/workflows`: shared DBOS worker and harmless smoke workflow.
- `backend/storage`, `migrations`: application metadata and Alembic.
- `tests`, `infra`, `.github/workflows`: verification and development infrastructure.

## Environment boundaries

Settings validate `development`, `test`, or `production` and require matching database
name suffixes. Application and DBOS storage use different databases and local roles.
Provision independent credentials, databases, encryption keys, and provider accounts
for each environment; this scaffold has not provisioned test/production infrastructure.
Production requires an external encryption key but credential encryption is not yet
implemented. No live provider keys belong in environment examples or workflow inputs.

Compose is local development infrastructure. Production HTTPS, least-privilege
migration/runtime role separation, worker health monitoring, backups, retention, and
recovery drills remain future work. Do not expose this unauthenticated scaffold publicly.

See [locked scope](docs/scope.md), [provider spike](docs/provider-compatibility.md),
[agent instructions](AGENTS.md), and [implementation checklist](implementation-checklist.md).
