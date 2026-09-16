"""Close the subjectless identity path.

Migration 0003 made identity binding subject-*first*, but the function still
accepted `p_external_id => NULL` and, in that case, resolved an existing account
by email alone. That left a runtime-callable account-claim shortcut: anything
able to call the bootstrap surface with only an email could be handed a
subject-bound account. 0003's own comment called this the "offline/administrative
path", but the capability was granted to the *runtime* role, so it was not
separated from authentication at all.

This revision:

1. Makes the verified external subject a **required** argument. The default is
   removed, so a two-argument call fails at the SQL layer with "function does not
   exist" before any row is touched — defence in depth behind the Python check.
2. Rejects a blank or whitespace-only subject with SQLSTATE 22023.
3. Removes first-subject adoption of an existing subjectless user. After this
   revision no code path can create a user without a subject, so adoption could
   only ever apply to a row predating it or inserted directly by the migration
   owner. Those now fail closed with SQLSTATE VS003 and are rebound by the
   deliberate administrative procedure in `infra/bind-subject.sql`, which is
   owner-only and has no runtime grant.

Unchanged: the allowlist check on every call, the subject/email conflict rules,
concurrency handling, and the guarantee that a rejected call leaves no rows.
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "vespers_app"
PROVISION_SIGNATURE = "app.provision_personal_identity(text, text, text)"

# Custom SQLSTATEs. Class "VS" is outside the ranges PostgreSQL reserves.
SUBJECT_CONFLICT = "VS001"
EMAIL_CONFLICT = "VS002"
BINDING_REQUIRED = "VS003"


def _sql(statement: str) -> None:
    # exec_driver_sql, not op.execute(): the latter routes through sqlalchemy.text(),
    # which scans PL/pgSQL bodies for ":name" bind parameters.
    op.get_bind().exec_driver_sql(statement)


PROVISION = """
CREATE FUNCTION app.provision_personal_identity(
    p_email text,
    p_external_id text,
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

    -- A verified external subject is mandatory. Authentication is the only
    -- caller, and it always has one; anything without one is not authentication.
    IF v_subject IS NULL THEN
        RAISE EXCEPTION 'vespers: verified subject required' USING ERRCODE = '22023';
    END IF;

    -- Checked on EVERY call, against the email being presented. This is what makes
    -- allowlist revocation effective at the next provisioning call, and what stops
    -- an email change from moving an account to an address never approved.
    IF NOT app.signup_allowed(v_email) THEN
        RAISE EXCEPTION 'vespers: signup not allowed' USING ERRCODE = '42501';
    END IF;

    -- (1) The verified external subject is the primary identity.
    SELECT u.id, u.status, u.email_normalized
      INTO v_user_id, v_status, v_current
      FROM public.users u
     WHERE u.external_id = v_subject
       FOR UPDATE;

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
        -- (2) No record for this subject: an exact email match must NOT hand the
        -- row over. Either it belongs to another subject, or it has no subject
        -- and needs a deliberate administrative binding. Both fail closed.
        SELECT u.id, u.external_id
          INTO v_user_id, v_found
          FROM public.users u
         WHERE u.email_normalized = v_email
           FOR UPDATE;

        IF v_user_id IS NOT NULL THEN
            IF v_found IS NULL THEN
                RAISE EXCEPTION 'vespers: subject binding required'
                    USING ERRCODE = '@BINDING_REQUIRED@';
            END IF;
            RAISE EXCEPTION 'vespers: external subject conflict'
                USING ERRCODE = '@SUBJECT_CONFLICT@';
        END IF;

        INSERT INTO public.users (email, external_id)
        VALUES (btrim(p_email), v_subject)
        ON CONFLICT (email_normalized) DO NOTHING
        RETURNING id, status INTO v_user_id, v_status;

        IF v_user_id IS NULL THEN
            -- A concurrent caller inserted first; ON CONFLICT DO NOTHING waited
            -- for it to commit, so re-read and apply the same rule. A concurrent
            -- insert for the SAME subject converges; a different one fails closed.
            SELECT u.id, u.status, u.external_id
              INTO v_user_id, v_status, v_found
              FROM public.users u
             WHERE u.email_normalized = v_email
               FOR UPDATE;
            IF v_found IS NULL THEN
                RAISE EXCEPTION 'vespers: subject binding required'
                    USING ERRCODE = '@BINDING_REQUIRED@';
            END IF;
            IF v_found <> v_subject THEN
                RAISE EXCEPTION 'vespers: external subject conflict'
                    USING ERRCODE = '@SUBJECT_CONFLICT@';
            END IF;
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

    IF (SELECT t.status FROM public.tenants t WHERE t.id = v_tenant_id) <> 'active' THEN
        -- A disabled tenant must not be re-enabled by signing in again, and must
        -- not be returned as a usable tenant.
        RAISE EXCEPTION 'vespers: identity disabled' USING ERRCODE = '42501';
    END IF;

    INSERT INTO public.memberships AS m (user_id, tenant_id, role)
    VALUES (v_user_id, v_tenant_id, 'owner')
    ON CONFLICT (user_id, tenant_id) DO UPDATE
        SET updated_at = m.updated_at
    RETURNING m.id INTO v_member_id;

    IF (SELECT m.status FROM public.memberships m WHERE m.id = v_member_id) <> 'active' THEN
        RAISE EXCEPTION 'vespers: identity disabled' USING ERRCODE = '42501';
    END IF;

    RETURN QUERY SELECT v_user_id, v_tenant_id, v_member_id, v_created;
    -- OUT names carry an out_ prefix: unprefixed names would shadow the columns
    -- referenced in the ON CONFLICT target lists above.
END
$fn$
"""
# Token substitution, not %/format: it keeps literal "%" out of the statement,
# which exec_driver_sql would otherwise hand to psycopg as a placeholder.
for _token, _value in (
    ("@SUBJECT_CONFLICT@", SUBJECT_CONFLICT),
    ("@EMAIL_CONFLICT@", EMAIL_CONFLICT),
    ("@BINDING_REQUIRED@", BINDING_REQUIRED),
):
    PROVISION = PROVISION.replace(_token, _value)


COMMENT = (
    "Subject-first identity binding. A verified external subject is REQUIRED; "
    "an email alone can never resolve or claim an account. Callers must supply "
    "values from a verified session, never browser-supplied ones."
)


def upgrade() -> None:
    # DROP then CREATE, not CREATE OR REPLACE: PostgreSQL refuses to remove a
    # parameter default from an existing function, and removing it is the point.
    _sql(f"DROP FUNCTION {PROVISION_SIGNATURE}")
    _sql(PROVISION)
    _sql(f"REVOKE ALL ON FUNCTION {PROVISION_SIGNATURE} FROM PUBLIC")
    _sql(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT EXECUTE ON FUNCTION {PROVISION_SIGNATURE} TO {RUNTIME_ROLE};
            ELSE
                RAISE NOTICE 'Runtime role {RUNTIME_ROLE} absent; skipped grant';
            END IF;
        END
        $do$
        """
    )
    _sql(f"COMMENT ON FUNCTION {PROVISION_SIGNATURE} IS '{COMMENT}'")


def downgrade() -> None:
    """Restore the 0003 definition, defaulted subject and adoption path included."""
    _sql(f"DROP FUNCTION {PROVISION_SIGNATURE}")
    _sql(
        _restore()
        .replace("@SUBJECT_CONFLICT@", SUBJECT_CONFLICT)
        .replace("@EMAIL_CONFLICT@", EMAIL_CONFLICT)
    )
    _sql(f"REVOKE ALL ON FUNCTION {PROVISION_SIGNATURE} FROM PUBLIC")
    _sql(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT EXECUTE ON FUNCTION {PROVISION_SIGNATURE} TO {RUNTIME_ROLE};
            END IF;
        END
        $do$
        """
    )


def _restore() -> str:
    return """
CREATE FUNCTION app.provision_personal_identity(
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
    IF NOT app.signup_allowed(v_email) THEN
        RAISE EXCEPTION 'vespers: signup not allowed' USING ERRCODE = '42501';
    END IF;

    IF v_subject IS NOT NULL THEN
        SELECT u.id, u.status, u.email_normalized
          INTO v_user_id, v_status, v_current
          FROM public.users u
         WHERE u.external_id = v_subject
           FOR UPDATE;
    END IF;

    IF v_user_id IS NOT NULL THEN
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
                SELECT u.id, u.status, u.external_id
                  INTO v_user_id, v_status, v_found
                  FROM public.users u
                 WHERE u.email_normalized = v_email
                   FOR UPDATE;
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
            RAISE EXCEPTION 'vespers: external subject conflict'
                USING ERRCODE = '@SUBJECT_CONFLICT@';
        ELSIF v_found IS NULL AND v_subject IS NOT NULL THEN
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
END
$fn$
"""
