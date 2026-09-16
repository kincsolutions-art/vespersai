-- Run as the local PostgreSQL administrator against vespers_development only.
-- Non-destructive, transactional transition from the scaffold owner role.
\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'vespers_development' THEN
    RAISE EXCEPTION 'Expected development database';
  END IF;
  IF EXISTS (SELECT FROM pg_database WHERE datdba=(SELECT oid FROM pg_roles WHERE rolname='vespers_app') AND datname <> current_database()) THEN
    RAISE EXCEPTION 'Runtime owns another database; review ownership manually';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='vespers_owner') THEN
    CREATE ROLE vespers_owner LOGIN PASSWORD 'local_owner_only' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;
REASSIGN OWNED BY vespers_app TO vespers_owner;
ALTER DATABASE vespers_development OWNER TO vespers_owner;
ALTER ROLE vespers_app NOSUPERUSER NOBYPASSRLS NOINHERIT;
REVOKE vespers_owner FROM vespers_app;
DO $$ BEGIN
  IF pg_has_role('vespers_app','vespers_owner','MEMBER') THEN
    RAISE EXCEPTION 'Indirect owner membership remains; review role grants';
  END IF;
END $$;
REVOKE ALL ON DATABASE vespers_development FROM PUBLIC;
REVOKE ALL ON DATABASE vespers_development FROM vespers_app;
GRANT CONNECT ON DATABASE vespers_development TO vespers_app;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM vespers_app;
GRANT USAGE ON SCHEMA public TO vespers_app;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM vespers_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vespers_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vespers_app;
ALTER DEFAULT PRIVILEGES FOR ROLE vespers_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vespers_app;
ALTER DEFAULT PRIVILEGES FOR ROLE vespers_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO vespers_app;
COMMIT;
