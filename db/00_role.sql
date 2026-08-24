-- SUPERSEDED by db/00_role.sh (Build 4).
-- This file hard-codes the password 'techdeva', which is published in the repo and
-- therefore identical on every deployment that ran it. It is kept only so existing
-- databases still match what created them. NEW databases use 00_role.sh, which takes
-- APP_DB_PASSWORD from the environment. To rotate an existing one, see SECURITY.md.
-- app role that does NOT bypass RLS (superusers do — never connect as postgres from the app)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'techdeva_app') THEN
    CREATE ROLE techdeva_app LOGIN PASSWORD 'techdeva';
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO techdeva_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO techdeva_app;
