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
-- No default privileges: a new migration-owned table grants the runtime role
-- nothing until a migration grants it explicitly. Migration 0003 revokes the
-- same defaults on databases initialized before this change.
