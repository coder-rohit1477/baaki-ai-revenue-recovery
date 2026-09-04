-- Baaki — role bootstrap. Run ONCE as a superuser:
--   psql "$BAAKI_SUPERUSER_DSN" -v ON_ERROR_STOP=1 -v migrate_pw=... -v app_pw=... -v ops_pw=... -v agent_pw=... -v sim_pw=... -f bootstrap/roles.sql
-- ARCHITECTURE.md v3.2.1 §6.2. Idempotent: creates missing roles, then enforces attributes.
-- baaki_owner has NOLOGIN and therefore no password.  No role inherits from another;
-- baaki_migrate is the sole member of baaki_owner and must SET ROLE explicitly (NOINHERIT).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'baaki_owner')   THEN CREATE ROLE baaki_owner;   END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'baaki_migrate') THEN CREATE ROLE baaki_migrate; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'baaki_app')     THEN CREATE ROLE baaki_app;     END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'baaki_ops')     THEN CREATE ROLE baaki_ops;     END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'baaki_agent')   THEN CREATE ROLE baaki_agent;   END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'baaki_sim')     THEN CREATE ROLE baaki_sim;     END IF;
END $$;

ALTER ROLE baaki_owner   NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE baaki_migrate LOGIN   NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE baaki_app     LOGIN   NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE baaki_ops     LOGIN   NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE baaki_agent   LOGIN   NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
ALTER ROLE baaki_sim     LOGIN   NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

ALTER ROLE baaki_migrate PASSWORD :'migrate_pw';
ALTER ROLE baaki_app     PASSWORD :'app_pw';
ALTER ROLE baaki_ops     PASSWORD :'ops_pw';
ALTER ROLE baaki_agent   PASSWORD :'agent_pw';
ALTER ROLE baaki_sim     PASSWORD :'sim_pw';

-- The only membership in the system (H16).
GRANT baaki_owner TO baaki_migrate;
