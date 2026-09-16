"""Subject-first identity binding, plus explicit opt-in grants for future tables.

Two independent corrections, both found by auditing migration 0002 rather than
by a failing test:

1. `app.provision_personal_identity` resolved identity by *email* first and used
   `coalesce(u.external_id, EXCLUDED.external_id)`. A second WorkOS subject
   presenting the same verified email was handed the existing user's row: the
   `external_id` column was not rebound, but access was. Identity is now resolved
   by verified external subject first, and an email that already belongs to a
   different non-null subject fails closed.

2. `infra/init-db.sql` set ALTER DEFAULT PRIVILEGES granting full DML on every
   future migration-owned table to the runtime role. Grants are now opt-in.
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "vespers_app"
OWNER_ROLE = "vespers_owner"

# Added by this revision; dropped on downgrade.
LIST_MEMBERSHIPS = "app.list_memberships(uuid)"

# Custom SQLSTATEs. Class "VS" is outside the ranges PostgreSQL reserves, so these
# cannot collide with a built-in condition and can be matched exactly in Python.
SUBJECT_CONFLICT = "VS001"
EMAIL_CONFLICT = "VS002"


def _sql(statement: str) -> None:
    # exec_driver_sql, not op.execute(): the latter routes through sqlalchemy.text(),
    # which scans PL/pgSQL bodies for ":name" bind parameters.
    op.get_bind().exec_driver_sql(statement)


PROVISION = """
CREATE OR REPLACE FUNCTION app.provision_personal_identity(
    p_email text,
    p_external_id text DEFAULT NULL,
    p_tenant_name text DEFAULT NULL
)
RETURNS TABLE (out_user_id uuid, out_tenant_id uuid, out_membership_id uuid,
               out_created boolean)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
DECLARE
    v_email     text := app.normalize_email(p_email);
    v_subject   text := nullif(btrim(p_external_id), '');
    v_name      text := coalesce(nullif(btrim(p_tenant_name), ''), 'Personal');
    v_user_id   uuid;
    v_tenant_id uuid;
    v_member_id uuid;
    v_created   boolean := false;
    v_status    text;
    v_found     text;
    v_current   text;
BEGIN
    IF v_email IS NULL OR v_email = '' THEN
        RAISE EXCEPTION 'vespers: email required' USING ERRCODE = '22023';
    END IF;

    -- Checked on EVERY call, against the email being presented. This is what makes
    -- allowlist revocation effective on the next sign-in, and what stops an email
    -- change from moving an account to an address that was never approved.
    IF NOT app.signup_allowed(v_email) THEN
        RAISE EXCEPTION 'vespers: signup not allowed' USING ERRCODE = '42501';
    END IF;

    -- (1) The verified external subject is the primary identity.
    IF v_subject IS NOT NULL THEN
        SELECT u.id, u.status, u.email_normalized
          INTO v_user_id, v_status, v_current
          FROM public.users u
         WHERE u.external_id = v_subject
           FOR UPDATE;
    END IF;

    IF v_user_id IS NOT NULL THEN
        -- Known subject whose email changed upstream. Never merge: if the new
        -- address already belongs to someone else, fail closed.
        IF v_current IS DISTINCT FROM v_email THEN
            IF EXISTS (SELECT 1 FROM public.users u
                        WHERE u.email_normalized = v_email AND u.id <> v_user_id) THEN
                RAISE EXCEPTION 'vespers: email already bound to another identity'
                    USING ERRCODE = '@EMAIL_CONFLICT@';
            END IF;
            UPDATE public.users SET email = btrim(p_email), updated_at = now()
             WHERE id = v_user_id;
        END IF;
    ELSE
        -- (2) No record for this subject: fall back to an exact email match.
        SELECT u.id, u.status, u.external_id
          INTO v_user_id, v_status, v_found
          FROM public.users u
         WHERE u.email_normalized = v_email
           FOR UPDATE;

        IF v_user_id IS NULL THEN
            INSERT INTO public.users (email, external_id)
            VALUES (btrim(p_email), v_subject)
            ON CONFLICT (email_normalized) DO NOTHING
            RETURNING id, status INTO v_user_id, v_status;

            IF v_user_id IS NULL THEN
                -- A concurrent caller inserted first; ON CONFLICT DO NOTHING waited
                -- for it to commit, so re-read and re-apply the same conflict rule.
                SELECT u.id, u.status, u.external_id
                  INTO v_user_id, v_status, v_found
                  FROM public.users u
                 WHERE u.email_normalized = v_email
                   FOR UPDATE;
                -- Conflict only when a subject IS presented and differs. A call
                -- with no subject (offline/administrative provisioning) rebinds
                -- nothing, so it may resolve the existing record.
                IF v_subject IS NOT NULL AND v_found IS NOT NULL
                   AND v_found <> v_subject THEN
                    RAISE EXCEPTION 'vespers: external subject conflict'
                        USING ERRCODE = '@SUBJECT_CONFLICT@';
                END IF;
                IF v_found IS NULL AND v_subject IS NOT NULL THEN
                    UPDATE public.users SET external_id = v_subject, updated_at = now()
                     WHERE id = v_user_id;
                END IF;
            END IF;
        ELSIF v_subject IS NOT NULL AND v_found IS NOT NULL AND v_found <> v_subject THEN
            -- The address belongs to a different verified subject. Do not rebind,
            -- do not merge, do not hand over the row.
            RAISE EXCEPTION 'vespers: external subject conflict'
                USING ERRCODE = '@SUBJECT_CONFLICT@';
        ELSIF v_found IS NULL AND v_subject IS NOT NULL THEN
            -- Documented claim path: a record created without a subject (for
            -- example by an offline provisioning call) adopts its first subject.
            UPDATE public.users SET external_id = v_subject, updated_at = now()
             WHERE id = v_user_id;
        END IF;
    END IF;

    IF v_status <> 'active' THEN
        RAISE EXCEPTION 'vespers: identity disabled' USING ERRCODE = '42501';
    END IF;

    INSERT INTO public.tenants AS t (name, kind, owner_user_id)
    VALUES (v_name, 'personal', v_user_id)
    ON CONFLICT (owner_user_id) WHERE kind = 'personal' DO UPDATE
        SET updated_at = t.updated_at
    RETURNING t.id, (t.xmax::text::bigint = 0) INTO v_tenant_id, v_created;

    INSERT INTO public.memberships AS m (user_id, tenant_id, role)
    VALUES (v_user_id, v_tenant_id, 'owner')
    ON CONFLICT (user_id, tenant_id) DO UPDATE
        SET updated_at = m.updated_at
    RETURNING m.id INTO v_member_id;

    RETURN QUERY SELECT v_user_id, v_tenant_id, v_member_id, v_created;
    -- OUT names carry an out_ prefix: unprefixed names would shadow the columns
    -- referenced in the ON CONFLICT target lists above.
END
$fn$
"""
# Token substitution, not %/format: it keeps literal "%" out of the statement,
# which exec_driver_sql would otherwise hand to psycopg as a placeholder.
PROVISION = PROVISION.replace("@SUBJECT_CONFLICT@", SUBJECT_CONFLICT).replace(
    "@EMAIL_CONFLICT@", EMAIL_CONFLICT
)


LIST_MEMBERSHIPS_SQL = """
CREATE FUNCTION app.list_memberships(p_user_id uuid)
RETURNS TABLE (out_membership_id uuid, out_tenant_id uuid, out_tenant_name text,
               out_tenant_kind text, out_role text)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT m.id, t.id, t.name, t.kind, m.role
      FROM public.memberships m
      JOIN public.users u ON u.id = m.user_id
      JOIN public.tenants t ON t.id = m.tenant_id
     WHERE m.user_id = p_user_id
       AND m.status = 'active'
       AND u.status = 'active'
       AND t.status = 'active'
     ORDER BY (t.kind = 'personal') DESC, t.name
$fn$
"""


def upgrade() -> None:
    _sql(PROVISION)
    # A tenant must be resolved BEFORE any tenant context exists, so membership
    # listing cannot be authorized by a tenant-scoped policy. Narrow: exact user
    # key, active rows only, no bulk access.
    _sql(LIST_MEMBERSHIPS_SQL)
    _sql(f"REVOKE ALL ON FUNCTION {LIST_MEMBERSHIPS} FROM PUBLIC")
    _sql(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT EXECUTE ON FUNCTION {LIST_MEMBERSHIPS} TO {RUNTIME_ROLE};
            END IF;
        END
        $do$
        """
    )
    _sql(
        "COMMENT ON FUNCTION app.provision_personal_identity(text, text, text) IS "
        "'Subject-first identity binding. The caller must supply a verified external "
        "subject and a verified email; never browser-supplied values.'"
    )

    # Future migration-owned tables grant the runtime role nothing until a
    # migration says otherwise. Existing explicit grants are untouched: default
    # privileges only apply at CREATE time.
    _sql(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{RUNTIME_ROLE}')
               AND EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{OWNER_ROLE}')
            THEN
                ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public
                    REVOKE SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
                    ON TABLES FROM {RUNTIME_ROLE};
                ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public
                    REVOKE USAGE, SELECT, UPDATE ON SEQUENCES FROM {RUNTIME_ROLE};
            ELSE
                RAISE NOTICE 'Roles absent; skipped default-privilege revocation';
            END IF;
        END
        $do$
        """
    )


def downgrade() -> None:
    _sql(f"DROP FUNCTION IF EXISTS {LIST_MEMBERSHIPS}")
    # Restore the permissive defaults so the schema matches revision 0002 again.
    _sql(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{RUNTIME_ROLE}')
               AND EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{OWNER_ROLE}')
            THEN
                ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public
                    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {RUNTIME_ROLE};
                ALTER DEFAULT PRIVILEGES FOR ROLE {OWNER_ROLE} IN SCHEMA public
                    GRANT USAGE, SELECT ON SEQUENCES TO {RUNTIME_ROLE};
            END IF;
        END
        $do$
        """
    )
    _sql(
        """
        CREATE OR REPLACE FUNCTION app.provision_personal_identity(
            p_email text,
            p_external_id text DEFAULT NULL,
            p_tenant_name text DEFAULT NULL
        )
        RETURNS TABLE (out_user_id uuid, out_tenant_id uuid, out_membership_id uuid,
                       out_created boolean)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE
            v_email      text := app.normalize_email(p_email);
            v_name       text := coalesce(nullif(btrim(p_tenant_name), ''), 'Personal');
            v_user_id    uuid;
            v_tenant_id  uuid;
            v_member_id  uuid;
            v_created    boolean;
            v_status     text;
        BEGIN
            IF v_email IS NULL OR v_email = '' THEN
                RAISE EXCEPTION 'vespers: email required' USING ERRCODE = '22023';
            END IF;
            IF NOT app.signup_allowed(v_email) THEN
                RAISE EXCEPTION 'vespers: signup not allowed' USING ERRCODE = '42501';
            END IF;

            INSERT INTO public.users AS u (email, external_id)
            VALUES (btrim(p_email), nullif(btrim(p_external_id), ''))
            ON CONFLICT (email_normalized) DO UPDATE
                SET external_id = coalesce(u.external_id, EXCLUDED.external_id)
            RETURNING u.id, u.status INTO v_user_id, v_status;

            IF v_status <> 'active' THEN
                RAISE EXCEPTION 'vespers: identity disabled' USING ERRCODE = '42501';
            END IF;

            INSERT INTO public.tenants AS t (name, kind, owner_user_id)
            VALUES (v_name, 'personal', v_user_id)
            ON CONFLICT (owner_user_id) WHERE kind = 'personal' DO UPDATE
                SET updated_at = t.updated_at
            RETURNING t.id, (t.xmax::text::bigint = 0) INTO v_tenant_id, v_created;

            INSERT INTO public.memberships AS m (user_id, tenant_id, role)
            VALUES (v_user_id, v_tenant_id, 'owner')
            ON CONFLICT (user_id, tenant_id) DO UPDATE
                SET updated_at = m.updated_at
            RETURNING m.id INTO v_member_id;

            RETURN QUERY SELECT v_user_id, v_tenant_id, v_member_id, v_created;
        END
        $fn$
        """
    )
