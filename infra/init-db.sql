-- Public placeholders for local development only.
CREATE ROLE vespers_owner LOGIN PASSWORD 'local_owner_only' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE vespers_app LOGIN PASSWORD 'local_app_only' NOSUPERUSER NOBYPASSRLS NOINHERIT;
CREATE ROLE vespers_dbos LOGIN PASSWORD 'local_dbos_only' NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE vespers_development OWNER vespers_owner;
CREATE DATABASE vespers_dbos_development OWNER vespers_dbos;
REVOKE ALL ON DATABASE vespers_development FROM PUBLIC;
REVOKE ALL ON DATABASE vespers_dbos_development FROM PUBLIC;
GRANT CONNECT ON DATABASE vespers_development TO vespers_app;
\connect vespers_development
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO vespers_app;
ALTER DEFAULT PRIVILEGES FOR ROLE vespers_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vespers_app;
ALTER DEFAULT PRIVILEGES FOR ROLE vespers_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO vespers_app;
