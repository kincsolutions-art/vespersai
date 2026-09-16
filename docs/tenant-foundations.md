# Tenant and identity foundations

Phase 3 storage: `users`, `tenants`, `memberships`, `signup_allowlist`, created by
migration `0002_tenant_foundations`. Tasks, schedules, credentials, memory,
conversations and every other product table remain future work.

## Access matrix

Ownership is deliberately non-uniform. `users` and `signup_allowlist` are global
platform tables and carry **no** `tenant_id`; adding one only to make the policies
look symmetrical would misrepresent what they are. Only `tenants` and
`memberships` sit inside the tenant boundary.

| Table | Owns | Runtime read | Runtime write | Uniqueness | Lifecycle |
| --- | --- | --- | --- | --- | --- |
| `signup_allowlist` | Platform | **none** (no grant, no policy) | **none** | `email_normalized` | Administrative provisioning before signup; see below |
| `users` | Global identity | Co-members of the current tenant only | **none** | `email_normalized`, `external_id` | Created by the bootstrap function; `status` disables |
| `tenants` | Itself (`id` is the scope key) | `id = app.current_tenant_id()` | `UPDATE (name, updated_at)` only | `owner_user_id` unique where `kind='personal'` | Created by the bootstrap function; `status` disables |
| `memberships` | Tenant | `tenant_id = app.current_tenant_id()` | **none** | `(user_id, tenant_id)` | Created by the bootstrap function or admin; `status` disables |

The runtime role has no `INSERT` or `DELETE` grant on any of the four tables.
Every write that creates identity goes through the bootstrap surface below, so
"membership manipulation grants access" is refused at the privilege layer before
any policy is consulted.

Foreign keys use `ON DELETE RESTRICT`. Account deletion is a deliberate future
workflow, not an accidental cascade from removing a user row.

`tenants` is writable only through **column-level** `UPDATE (name, updated_at)`.
A row policy cannot express "the ownership column did not change", so
`owner_user_id`, `id`, `kind` and `status` are simply not grantable to runtime.

### Cases this has to serve

- **Identity lookup before a tenant is selected** — `app.find_user()`; no tenant
  context exists yet, so no tenant-scoped policy could authorize it.
- **Membership verification** — `app.resolve_membership()` returns a row only when
  the membership, the user *and* the tenant are all `active`.
- **Atomic first personal tenant** — `app.provision_personal_identity()` creates
  user + tenant + membership in one function invocation.
- **One identity in several tenants later** — `memberships` is a full join table;
  `users` is visible through co-membership of whichever tenant is current.
- **Administrative allowlist provisioning before signup** — owner-only, out of band.
- **Disabled identities/tenants/memberships** — enforced inside
  `app.resolve_membership`, not in Python.

## Bootstrap trust boundary

`app.current_tenant_id()` reads the transaction-local GUC `vespers.tenant_id`.

**This is an enforcement input, not authentication.** PostgreSQL will faithfully
isolate whatever tenant id it is given; it cannot tell whether that id was
authorized. The trusted backend must call `IdentityService.resolve_membership()`
and get a row back *before* opening `tenant_scope()`. Nothing in the database
prevents a caller that already has arbitrary SQL execution with the runtime
credentials from setting any tenant id it likes — these controls stop context
leaking, stop payloads redirecting the scope, and stop cross-tenant reads and
writes through ordinary application code paths. They are not a defence against a
fully compromised runtime credential.

### Why SECURITY DEFINER, and how it is bounded

Identity lookup, eligibility checks and first-tenant creation all happen before a
tenant is selected, so no tenant-scoped policy can authorize them. Rather than a
blanket bypass flag or a generic privileged repository, the pre-tenant surface is
seven narrow functions — **five SECURITY DEFINER, two SECURITY INVOKER**. (An
earlier revision of this document said "five functions"; the count below is the
one verified against the migrations.) Each one:

- has a fixed `SET search_path = pg_catalog, pg_temp`;
- fully qualifies every object it touches (`public.users`, `app.normalize_email`);
- takes exact identity keys and returns at most the rows for those keys;
- has `EXECUTE` revoked from `PUBLIC` and granted only to `vespers_app`.

| Function | Definer? | Purpose |
| --- | --- | --- |
| `app.current_tenant_id()` | No (invoker) | Reads the GUC for policies |
| `app.normalize_email(text)` | No (invoker) | `lower(btrim(...))` |
| `app.signup_allowed(text)` | **Yes** | Boolean only; the allowlist is never readable |
| `app.find_user(text, text)` | **Yes** | One row, by exact email or verified `external_id` |
| `app.resolve_membership(uuid, uuid)` | **Yes** | One membership, with all three status checks |
| `app.provision_personal_identity(text, text, text)` | **Yes** | Atomic, idempotent first-tenant creation; the verified subject is **required** |
| `app.list_memberships(uuid)` | **Yes** | One user's active tenants, before a tenant context exists |

None of them accepts a tenant id as authority, and none returns bulk data.

### Administrative allowlist provisioning

The runtime role holds no grant and no policy on `signup_allowlist`, so there is
no API surface for it by construction. Use `infra/allowlist-add.sql` as the
migration owner:

```sh
docker compose exec -T postgres psql -U vespers_owner -d vespers_development \
  -v ON_ERROR_STOP=1 -v email="'person@example.com'" -v note="'first account'" \
  < infra/allowlist-add.sql
```

## Row-level security

All four tables have `ENABLE ROW LEVEL SECURITY`. `signup_allowlist` deliberately
has **no policy at all**, so it is fail-closed for every non-owner role on top of
holding no grant.

```sql
tenants_select      SELECT  USING (id = app.current_tenant_id())
tenants_update      UPDATE  USING      (id = app.current_tenant_id() AND status = 'active')
                            WITH CHECK (id = app.current_tenant_id() AND status = 'active')
memberships_select  SELECT  USING (tenant_id = app.current_tenant_id())
users_select        SELECT  USING (EXISTS (SELECT 1 FROM public.memberships m
                                            WHERE m.user_id = users.id
                                              AND m.tenant_id = app.current_tenant_id()
                                              AND m.status = 'active'))
```

Missing or empty context makes `app.current_tenant_id()` return `NULL`, and
`= NULL` is never true, so every policy denies. A malformed value raises
`invalid input syntax for type uuid` — loud, and still no rows.

**Recursion.** `users_select` reads `memberships`, and the `memberships` policy
references no table at all, so the dependency terminates. No policy references
its own table.

### FORCE ROW LEVEL SECURITY: not enabled (decision record)

`FORCE` is **off**, verified by assertion rather than left to chance.

`FORCE` only changes behaviour for the *table owner*; `vespers_app` is not the
owner and has `NOBYPASSRLS`/`NOINHERIT`, so policies already apply to it
unconditionally. The owner is exactly who legitimately needs to cross tenant
boundaries: the `SECURITY DEFINER` bootstrap functions run as `vespers_owner`,
and so does the administrative allowlist path. Turning `FORCE` on and then adding
owner-permitting policies would be a bypass wearing a different hat.

Migration and bootstrap implication: enabling `FORCE` later would break all five
definer functions and the admin path. Doing it properly would mean introducing a
dedicated `NOLOGIN`, non-owner bootstrap role with its own explicit policies, and
re-pointing the definer functions at it.

**Residual risk:** any application query run with the *migration* credentials
bypasses isolation entirely. Mitigations: separate URLs in `backend/config.py`,
Compose supplying `VESPERS_MIGRATION_DATABASE_URL` only to the `migrate` service,
and an integration assertion that neither `api` nor `worker` can see it.

## Transaction-local context

`backend/storage/tenant_context.py` issues
`SELECT set_config('vespers.tenant_id', :value, true)` — the tenant id is a bind
parameter, never interpolated, and `true` makes the setting **transaction-local**.

- It cannot survive a pooled-connection checkout: `COMMIT` and `ROLLBACK` both
  discard it.
- Exceptions clear it, because the transaction unwinds.
- `tenant_scope` refuses to run outside a transaction, where a `LOCAL` setting
  would silently vanish and leave every policy disabled.
- Re-entering the scope with the **same** tenant is a no-op; entering with a
  **different** tenant raises `TenantContextError` rather than rebinding, so a
  nested call cannot switch tenants inside an active unit of work.

## Repository boundary

`TenantRepository` refuses construction without an active scope, and **no method
takes a tenant id**, so a request payload cannot widen or redirect the boundary.
`rename_tenant` deliberately issues an unfiltered `UPDATE`: row-level security is
the authority, and a redundant `WHERE` would let a broken policy pass the tests.

## Identity facts a future authentication adapter must supply

Out of scope here; WorkOS is not integrated. When it is, the adapter must provide,
from a **verified** session and never from a request payload:

- a stable external subject identifier → `users.external_id`, **required**;
- a **verified** email address → `users.email`;
- nothing else. `external_id` is written once and never overwritten, and an
  email address alone can neither resolve nor claim an account.

A successful sign-in alone must not grant access: `app.provision_personal_identity`
still enforces the allowlist, and `resolve_membership` still enforces status.
WorkOS AuthKit is now wired to exactly this contract; see
[authentication](authentication.md).

## Migration and runtime-role separation

`infra/init-db.sql` sets `ALTER DEFAULT PRIVILEGES` granting full DML on new
tables to `vespers_app`, so migration 0002 **revokes everything and re-grants
narrowly**. Adding a table without that revoke would silently hand runtime full
DML. Grants are wrapped in a `DO` block that skips them with a notice when the
role is absent, keeping the migration portable.

DBOS system storage is untouched: no application tables, no `app` schema, no
policies. Application RLS assumptions are never imposed on DBOS internals.

## Changes in migration 0003

**Subject-first identity binding.** `provision_personal_identity` originally
resolved by email and used `coalesce(u.external_id, EXCLUDED.external_id)`, so a
second WorkOS subject presenting the same verified email was handed the existing
user's row — the column was not rebound, but access was. Identity is now resolved
by verified external subject first. See
[authentication](authentication.md#identity-binding-rules) for the full table.

**Opt-in default grants.** `infra/init-db.sql` previously set
`ALTER DEFAULT PRIVILEGES` granting full DML on every future migration-owned table
to the runtime role, which meant a new table was writable until a migration
remembered to revoke. Fresh initialization no longer grants defaults, and 0003
revokes them on databases created earlier. Existing narrow grants are unaffected,
because default privileges only apply at CREATE time. **A new table now grants the
runtime role nothing until a migration grants it explicitly** — this is verified in
the integration harness by creating a table and asserting zero access.

`infra/transition-dev-owner.sql` now refuses to run once `public.users` exists: it
predates the tenant schema and its `GRANT ... ON ALL TABLES` would widen the narrow
grants on the foundation tables.

## Changes in migration 0004

**The verified subject is required.** 0003 resolved by subject *first* but still
accepted a NULL subject and then resolved by email, which left a runtime-callable
way to be handed a subject-bound account by presenting its address. The argument
default is gone, so a subjectless call does not resolve to a function at all, and
a blank subject raises SQLSTATE 22023.

**First-subject adoption removed.** No code path creates a subjectless user any
more, so adoption could only apply to rows predating 0004 or inserted by the
migration owner. Those now fail closed with SQLSTATE VS003; binding them is a
deliberate owner-only act through `infra/bind-subject.sql`, which has no runtime
grant and no API surface — the capability is separated from authentication rather
than shared with it.

**Disabled tenants and memberships fail closed during provisioning** instead of
being silently returned, so signing in again cannot resurrect disabled access.

The bootstrap surface is still **seven functions — five SECURITY DEFINER, two
SECURITY INVOKER**; 0004 replaces one in place and adds none.

## Readiness

The resident worker does **not** use these repositories yet, so its readiness
probe deliberately still checks only DBOS connectivity — adding an application
database dependency now would be misleading. **When the worker first executes
tenant-scoped work, `backend/workflows/readiness.py` must also check the
application database, with matching negative tests.**

An HTTP readiness probe proves the process initialized and the database is
reachable. It does not prove DBOS internal execution threads are healthy.
