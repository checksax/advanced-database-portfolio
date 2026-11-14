-- =====================================================================
-- St. Grace Specialist Hospital - Database bootstrap
-- Run ONCE as the postgres superuser:
--   psql -U postgres -f 00_create_database.sql
-- Creates a dedicated application owner and the hospital database.
-- The password below is a local development value only.
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hospital_admin') THEN
        CREATE ROLE hospital_admin LOGIN PASSWORD 'hospital_pass' CREATEROLE;
    END IF;
END
$$;

SELECT 'CREATE DATABASE stgrace_hospital OWNER hospital_admin'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'stgrace_hospital')
\gexec

ALTER DATABASE stgrace_hospital SET search_path = hospital, public;

\connect stgrace_hospital

-- Extensions need superuser rights, so they are created here.
CREATE EXTENSION IF NOT EXISTS btree_gist;          -- exclusion constraint on appointments
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;  -- query statistics for Portfolio 2
