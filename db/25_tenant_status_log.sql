-- Churn is not a number you can reconstruct afterwards. A tenant that went
-- paused six weeks ago looks identical today to one that was never active.
-- So record the change at the moment it happens. This log starts empty and
-- becomes true history from the day it is installed - it does not pretend
-- to know what happened before.
BEGIN;

CREATE TABLE IF NOT EXISTS tenant_status_log (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  from_status text,
  to_status   text NOT NULL,
  at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tenant_status_log_at_idx ON tenant_status_log (at DESC);

CREATE OR REPLACE FUNCTION log_tenant_status() RETURNS trigger AS $fn$
BEGIN
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    INSERT INTO tenant_status_log (tenant_id, from_status, to_status)
    VALUES (NEW.id, OLD.status::text, NEW.status::text);
  END IF;
  RETURN NEW;
END $fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tenants_status_change ON tenants;
CREATE TRIGGER tenants_status_change AFTER UPDATE ON tenants
  FOR EACH ROW EXECUTE FUNCTION log_tenant_status();

ALTER TABLE tenant_status_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_status_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON tenant_status_log;
CREATE POLICY admin_only ON tenant_status_log USING (is_admin());

DO $verify$
BEGIN
  IF to_regclass('public.tenant_status_log') IS NULL THEN
    RAISE EXCEPTION 'tenant_status_log missing';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='tenants_status_change') THEN
    RAISE EXCEPTION 'status trigger not installed';
  END IF;
  RAISE NOTICE 'verify ok: tenant_status_log + trigger';
END $verify$;

COMMIT;
