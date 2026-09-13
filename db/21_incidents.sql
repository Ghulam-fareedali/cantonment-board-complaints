-- 21_incidents.sql
-- Somewhere for "this is broken" to live that a person will actually look at.
--
-- The monitor has been running every five minutes and writing "all checks
-- passed" to a log file on the server. That is fine for Faheem with an SSH
-- session open and useless to everyone else: when the AI workforce went quiet
-- for weeks, nothing in the product said so. An incident row is the same
-- finding, in a place the dashboard can read.
--
-- tenant_id NULL means the platform itself is affected -- everyone's problem.

BEGIN;

CREATE TABLE IF NOT EXISTS incidents (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid REFERENCES tenants(id) ON DELETE CASCADE,
  kind         text NOT NULL,
  severity     text NOT NULL,
  title        text NOT NULL,
  detail       text,
  status       text NOT NULL DEFAULT 'open',
  seen_count   int  NOT NULL DEFAULT 1,
  opened_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz,
  CONSTRAINT incidents_severity_ck CHECK (severity IN ('info','warn','critical')),
  CONSTRAINT incidents_status_ck CHECK (status IN ('open','resolved')),
  CONSTRAINT incidents_resolved_has_time_ck CHECK (status <> 'resolved' OR resolved_at IS NOT NULL)
);

-- One open incident per kind, so a check failing every five minutes for a day
-- is one row with a count, not 288 rows nobody reads.
CREATE UNIQUE INDEX IF NOT EXISTS incidents_open_platform_uniq
  ON incidents (kind) WHERE status = 'open' AND tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS incidents_open_tenant_uniq
  ON incidents (tenant_id, kind) WHERE status = 'open' AND tenant_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS incidents_recent_idx ON incidents (last_seen_at DESC);

ALTER TABLE incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE incidents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON incidents;
CREATE POLICY tenant_isolation ON incidents
  -- A platform outage is every tenant's business; another tenant's is not.
  USING (is_admin() OR tenant_id = current_tenant() OR tenant_id IS NULL)
  WITH CHECK (is_admin() OR tenant_id = current_tenant());

DO $verify$
DECLARE forced bool;
BEGIN
  SELECT relforcerowsecurity INTO forced FROM pg_class WHERE relname = 'incidents';
  IF NOT forced THEN RAISE EXCEPTION 'verify failed: row level security is not forced'; END IF;
  RAISE NOTICE 'verify ok: incidents table ready, tenant isolated';
END $verify$;

COMMIT;
