-- Local development credentials only. Production must provision independent secrets.
CREATE ROLE vespers_app LOGIN PASSWORD 'local_app_only' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE vespers_dbos LOGIN PASSWORD 'local_dbos_only' NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE vespers_development OWNER vespers_app;
CREATE DATABASE vespers_dbos_development OWNER vespers_dbos;
REVOKE ALL ON DATABASE vespers_development FROM PUBLIC;
REVOKE ALL ON DATABASE vespers_dbos_development FROM PUBLIC;
