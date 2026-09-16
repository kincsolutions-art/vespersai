-- Administrative binding of a verified external subject to an existing user row.
--
-- This is deliberately NOT a database function and carries no runtime grant: it
-- is the only way a user row without an `external_id` can ever acquire one, and
-- keeping it out of the runtime surface is what stops "claim an account by
-- presenting its email" from existing as a callable capability.
--
-- After migration 0004 no code path creates a subjectless user, so this applies
-- only to rows that predate it or were inserted directly by the migration owner.
-- app.provision_personal_identity refuses those rows with SQLSTATE VS003
-- ("subject binding required") until this script has run.
--
--   docker compose exec -T postgres psql -U vespers_owner -d vespers_development \
--     -v ON_ERROR_STOP=1 -v email=person@example.com -v subject=user_01ABCDEF \
--     < infra/bind-subject.sql
--
-- Refuses to overwrite an existing binding: rebinding a live account to a
-- different subject is an account takeover, not an administrative correction.
\set ON_ERROR_STOP on
BEGIN;
-- psql does NOT substitute :'name' inside a dollar-quoted body, so the values are
-- bound here, outside the DO block, and read back with current_setting.
SELECT set_config('vespers.bind_email', :'email', true),
       set_config('vespers.bind_subject', :'subject', true);
DO $$
DECLARE
    v_email   text := app.normalize_email(current_setting('vespers.bind_email'));
    v_subject text := nullif(btrim(current_setting('vespers.bind_subject')), '');
    v_current text;
    v_id      uuid;
BEGIN
    IF v_email IS NULL OR v_email = '' THEN
        RAISE EXCEPTION 'A non-empty email is required';
    END IF;
    IF v_subject IS NULL THEN
        RAISE EXCEPTION 'A non-empty subject is required';
    END IF;
    SELECT u.id, u.external_id INTO v_id, v_current
      FROM public.users u WHERE u.email_normalized = v_email FOR UPDATE;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'No user with that email; sign-in provisions new users';
    END IF;
    IF v_current IS NOT NULL THEN
        RAISE EXCEPTION 'User already bound to an external subject; refusing to rebind';
    END IF;
    IF EXISTS (SELECT 1 FROM public.users u WHERE u.external_id = v_subject) THEN
        RAISE EXCEPTION 'That subject is already bound to a different user';
    END IF;
    UPDATE public.users SET external_id = v_subject, updated_at = now() WHERE id = v_id;
    RAISE NOTICE 'Bound user % to the supplied subject', v_id;
END
$$;
COMMIT;
