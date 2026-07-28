-- app role that does NOT bypass RLS (superusers do — never connect as postgres from the app)
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'techdeva_app') THEN
    CREATE ROLE techdeva_app LOGIN PASSWORD 'techdeva';
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO techdeva_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO techdeva_app;
