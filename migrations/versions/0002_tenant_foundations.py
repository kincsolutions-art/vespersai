"""Tenant and identity foundations: users, tenants, memberships, signup allowlist.

Creates the tenant boundary, fail-closed row-level security, and the narrowly
scoped SECURITY DEFINER bootstrap surface. See docs/tenant-foundations.md.
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Least-privilege runtime role created by infra/init-db.sql. Grants are skipped
# (with a notice) when it is absent, so the migration stays portable.
RUNTIME_ROLE = "vespers_app"

# Transaction-local GUC holding the trusted tenant identity. A custom namespace is
# required so PostgreSQL accepts set_config() for an unknown parameter.
TENANT_GUC = "vespers.tenant_id"

FUNCTIONS = (
    "app.normalize_email(text)",
    "app.current_tenant_id()",
    "app.signup_allowed(text)",
    "app.find_user(text, text)",
    "app.provision_personal_identity(text, text, text)",
    "app.resolve_membership(uuid, uuid)",
)


def _sql(statement: str) -> None:
    # exec_driver_sql, not op.execute(): the latter routes through sqlalchemy.text(),
    # which scans PL/pgSQL bodies for ":name" bind parameters.
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    _sql("CREATE SCHEMA app")
    _sql("COMMENT ON SCHEMA app IS 'Bootstrap and policy helper functions.'")

    op.create_table(
        "signup_allowlist",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "email_normalized",
            sa.Text(),
            sa.Computed("lower(btrim(email))", persisted=True),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signup_allowlist"),
        sa.UniqueConstraint("email_normalized", name="uq_signup_allowlist_email_normalized"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "email_normalized",
            sa.Text(),
            sa.Computed("lower(btrim(email))", persisted=True),
            nullable=False,
        ),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
        sa.UniqueConstraint("external_id", name="uq_users_external_id"),
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), server_default="personal", nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_tenants_status"),
        sa.CheckConstraint("kind IN ('personal', 'team')", name="ck_tenants_kind"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_tenants_owner_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), server_default="member", nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_memberships_status"),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_memberships_role"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_memberships_tenant_id", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
    )

    op.create_index(
        "uq_memberships_user_id_tenant_id", "memberships", ["user_id", "tenant_id"], unique=True
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    # One personal tenant per owner: makes first-tenant provisioning idempotent
    # and safe under concurrency via ON CONFLICT inference.
    op.create_index(
        "uq_tenants_personal_owner",
        "tenants",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'personal'"),
    )

    # ---- Policy helper ---------------------------------------------------
    # SECURITY INVOKER: it only reads a GUC. Fail-closed by NULL when unset;
    # a malformed value raises, which is loud and still returns no rows.
    _sql(
        f"""
        CREATE FUNCTION app.current_tenant_id() RETURNS uuid
        LANGUAGE plpgsql STABLE PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE
            raw text;
        BEGIN
            raw := nullif(btrim(coalesce(current_setting('{TENANT_GUC}', true), '')), '');
            IF raw IS NULL THEN
                RETURN NULL;
            END IF;
            RETURN raw::uuid;
        END
        $fn$
        """
    )
    _sql(
        "COMMENT ON FUNCTION app.current_tenant_id() IS "
        "'Transaction-local tenant context. An enforcement input, not authentication: "
        "the trusted backend must verify membership before setting it.'"
    )
    _sql(
        """
        CREATE FUNCTION app.normalize_email(p_email text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp
        AS $fn$ SELECT lower(btrim(p_email)) $fn$
        """
    )

    # ---- Bootstrap surface -----------------------------------------------
    # SECURITY DEFINER is required because identity lookup, eligibility checks and
    # first-tenant creation all happen BEFORE any tenant context exists, so no
    # tenant-scoped policy can authorize them. Each function is narrow: fixed
    # search_path, fully qualified objects, exact-key lookups, EXECUTE revoked
    # from PUBLIC and granted only to the runtime role.
    _sql(
        """
        CREATE FUNCTION app.signup_allowed(p_email text) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT EXISTS (
                SELECT 1 FROM public.signup_allowlist a
                WHERE a.email_normalized = app.normalize_email(p_email)
            )
        $fn$
        """
    )
    _sql(
        """
        CREATE FUNCTION app.find_user(p_email text, p_external_id text)
        RETURNS TABLE (id uuid, email text, external_id text, status text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT u.id, u.email, u.external_id, u.status
            FROM public.users u
            WHERE (p_external_id IS NOT NULL AND u.external_id = p_external_id)
               OR (p_external_id IS NULL
                   AND p_email IS NOT NULL
                   AND u.email_normalized = app.normalize_email(p_email))
            LIMIT 1
        $fn$
        """
    )
    _sql(
        """
        CREATE FUNCTION app.resolve_membership(p_user_id uuid, p_tenant_id uuid)
        RETURNS TABLE (membership_id uuid, user_id uuid, tenant_id uuid, role text)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
            SELECT m.id, m.user_id, m.tenant_id, m.role
            FROM public.memberships m
            JOIN public.users u ON u.id = m.user_id
            JOIN public.tenants t ON t.id = m.tenant_id
            WHERE m.user_id = p_user_id
              AND m.tenant_id = p_tenant_id
              AND m.status = 'active'
              AND u.status = 'active'
              AND t.status = 'active'
        $fn$
        """
    )
    # Atomic and idempotent. ON CONFLICT DO UPDATE (not DO NOTHING) always returns
    # the row and blocks on a concurrent inserter, so duplicate/concurrent calls
    # converge on one user, one personal tenant and one membership. Any raised
    # exception aborts the whole function, leaving no orphan rows.
    _sql(
        """
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
            -- OUT names carry an out_ prefix: unprefixed names would shadow the
            -- columns referenced in the ON CONFLICT target lists above.
        END
        $fn$
        """
    )

    # ---- Row-level security ----------------------------------------------
    # FORCE ROW LEVEL SECURITY is deliberately NOT enabled; see the decision
    # record in docs/tenant-foundations.md.
    for table in ("users", "tenants", "memberships", "signup_allowlist"):
        _sql(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")

    _sql(
        f"""
        CREATE POLICY tenants_select ON public.tenants
            FOR SELECT TO {RUNTIME_ROLE}
            USING (id = app.current_tenant_id())
        """
    )
    _sql(
        f"""
        CREATE POLICY tenants_update ON public.tenants
            FOR UPDATE TO {RUNTIME_ROLE}
            USING (id = app.current_tenant_id() AND status = 'active')
            WITH CHECK (id = app.current_tenant_id() AND status = 'active')
        """
    )
    _sql(
        f"""
        CREATE POLICY memberships_select ON public.memberships
            FOR SELECT TO {RUNTIME_ROLE}
            USING (tenant_id = app.current_tenant_id())
        """
    )
    # Users are global, so they are exposed only through co-membership of the
    # current tenant. The subquery reads memberships, whose own policy does not
    # reference users, so there is no policy recursion.
    _sql(
        f"""
        CREATE POLICY users_select ON public.users
            FOR SELECT TO {RUNTIME_ROLE}
            USING (EXISTS (
                SELECT 1 FROM public.memberships m
                WHERE m.user_id = users.id
                  AND m.tenant_id = app.current_tenant_id()
                  AND m.status = 'active'
            ))
        """
    )
    # signup_allowlist intentionally has no policy: fail-closed for every
    # non-owner role, on top of holding no grant at all.

    # ---- Grants -----------------------------------------------------------
    # Historical note (the applied behaviour below is unchanged): at the time
    # this revision was written, infra/init-db.sql set ALTER DEFAULT PRIVILEGES
    # granting full DML on new tables to the runtime role, so every table here
    # had to be revoked and re-granted. Migration 0003 removed those defaults and
    # init-db.sql no longer sets them, so on a fresh database these REVOKEs are
    # no-ops — they remain correct, and necessary, for databases initialized
    # before that change.
    grants = "\n".join(
        [
            "REVOKE ALL ON public.users, public.tenants, public.memberships,"
            f" public.signup_allowlist FROM {RUNTIME_ROLE};",
            f"GRANT USAGE ON SCHEMA app TO {RUNTIME_ROLE};",
            f"GRANT SELECT ON public.users TO {RUNTIME_ROLE};",
            f"GRANT SELECT ON public.memberships TO {RUNTIME_ROLE};",
            f"GRANT SELECT ON public.tenants TO {RUNTIME_ROLE};",
            # Column-level UPDATE: row policies cannot express "ownership column
            # unchanged", so tenant ownership and identity are unwritable here.
            f"GRANT UPDATE (name, updated_at) ON public.tenants TO {RUNTIME_ROLE};",
        ]
        + [f"GRANT EXECUTE ON FUNCTION {fn} TO {RUNTIME_ROLE};" for fn in FUNCTIONS]
    )
    _sql("REVOKE ALL ON SCHEMA app FROM PUBLIC")
    for function in FUNCTIONS:
        _sql(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
    _sql(
        f"""
        DO $do$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                {grants}
            ELSE
                RAISE NOTICE 'Runtime role {RUNTIME_ROLE} absent; skipped runtime grants';
            END IF;
        END
        $do$
        """
    )


def downgrade() -> None:
    # users_select reads public.memberships, so that policy must go before the
    # table it references; the remaining policies fall with their own tables.
    _sql("DROP POLICY IF EXISTS users_select ON public.users")
    # Then tables in foreign-key order, which also removes the policies that
    # depend on app.current_tenant_id() before the functions are dropped.
    op.drop_table("memberships")
    op.drop_table("tenants")
    op.drop_table("users")
    op.drop_table("signup_allowlist")
    for function in FUNCTIONS:
        _sql(f"DROP FUNCTION IF EXISTS {function}")
    _sql("DROP SCHEMA IF EXISTS app")
