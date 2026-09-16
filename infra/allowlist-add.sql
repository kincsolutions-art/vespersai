-- Administrative signup allowlist provisioning.
--
-- The runtime role holds no grant and no policy on signup_allowlist, so this is
-- deliberately an owner-only, out-of-band operation with no API surface.
--
--   docker compose exec -T postgres psql -U vespers_owner -d vespers_development \
--     -v ON_ERROR_STOP=1 -v email=person@example.com -v note='first account' \
--     < infra/allowlist-add.sql
--
-- The SQL below uses psql's :'name' form, which quotes and escapes the value
-- itself, so the variables are passed as bare values. Passing a pre-quoted
-- "'person@example.com'" would store the quotes as part of the address.
--
-- email_normalized is generated (lower(btrim(email))), so re-running with a
-- differently cased or padded address updates the same row instead of duplicating.
\set ON_ERROR_STOP on
INSERT INTO public.signup_allowlist (email, note)
VALUES (:'email', :'note')
ON CONFLICT (email_normalized)
  DO UPDATE SET note = EXCLUDED.note, updated_at = now()
RETURNING id, email_normalized, created_at;
