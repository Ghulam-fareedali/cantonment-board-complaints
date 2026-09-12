-- 16 — Keep test signups out of the numbers Faheem reads.
--
-- Before this, 54 of 61 tenants on the platform were throwaway test tenants and
-- they carried 340 of the 376 tasks. The TechDeva overview was therefore mostly
-- reporting on itself. A tenant created from a test address is now marked, and the
-- admin view counts only live ones. Nothing is hidden from the tenant itself.

ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_test boolean NOT NULL DEFAULT false;

-- security_invoker matters: without it Postgres runs the view as its owner and
-- row level security on tenants would be bypassed by anyone able to read the view.
CREATE OR REPLACE VIEW live_tenants WITH (security_invoker = true) AS
  SELECT * FROM tenants WHERE is_test = false;

DO $verify$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name = 'tenants' AND column_name = 'is_test') THEN
    RAISE EXCEPTION 'tenants.is_test was not created';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.views
                 WHERE table_name = 'live_tenants') THEN
    RAISE EXCEPTION 'live_tenants view was not created';
  END IF;
END $verify$;
