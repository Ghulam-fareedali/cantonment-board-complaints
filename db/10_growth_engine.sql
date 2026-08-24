-- ============================================================
-- TechDeva AIBOS — Growth Engine migration (additive, reversible)
-- Adds the self-serve SaaS layer on top of the existing platform:
--   * signups          — public self-serve intake, BEFORE a tenant exists
--   * analysis_reports  — the ONE combined multi-channel + competitor report
-- Nothing existing is altered. Same RLS shape as schema.sql.
-- ============================================================

DO $$ BEGIN
  CREATE TYPE signup_status   AS ENUM ('new','analyzing','provisioned','rejected');
  CREATE TYPE analysis_status AS ENUM ('pending','running','complete','partial','failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------- public intake (pre-tenant) ----------
-- A signup has NO tenant until we provision one, so it is NOT tenant-scoped the
-- usual way — only techdeva_admin can see the intake queue. Once provisioned,
-- tenant_id is filled in for traceability.
CREATE TABLE IF NOT EXISTS signups (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_name  text NOT NULL,
  industry       text NOT NULL DEFAULT 'general',
  website_url    text,
  social_links   jsonb NOT NULL DEFAULT '{}',   -- {facebook, linkedin, instagram, ...}
  competitors    jsonb NOT NULL DEFAULT '[]',   -- [{name?, website?, facebook?}] (optional; auto-discovered if empty)
  contact_email  text,
  contact_phone  text,
  status         signup_status NOT NULL DEFAULT 'new',
  tenant_id      uuid REFERENCES tenants(id) ON DELETE SET NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------- the combined analysis report ----------
-- One row per analysis run for a tenant. `report` is the full structured object
-- the orchestrator/GrowthPlanner reads. `gaps` records, honestly, every channel
-- we could NOT get real data for (missing access), so nothing is ever fabricated.
CREATE TABLE IF NOT EXISTS analysis_reports (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  signup_id   uuid REFERENCES signups(id) ON DELETE SET NULL,
  status      analysis_status NOT NULL DEFAULT 'pending',
  report      jsonb NOT NULL DEFAULT '{}',
  gaps        jsonb NOT NULL DEFAULT '[]',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS analysis_reports_tenant_idx ON analysis_reports(tenant_id, created_at);

-- ---------- RLS ----------
-- analysis_reports: standard tenant isolation (admin sees all).
ALTER TABLE analysis_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_reports FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY tenant_isolation ON analysis_reports
    USING (is_admin() OR tenant_id = current_tenant())
    WITH CHECK (is_admin() OR tenant_id = current_tenant());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- signups: admin-only (it's the pre-tenant intake queue; no tenant to scope to).
ALTER TABLE signups ENABLE ROW LEVEL SECURITY;
ALTER TABLE signups FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY admin_only ON signups
    USING (is_admin())
    WITH CHECK (is_admin());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- grants for the non-superuser app role (same pattern as 00_role.sql default privs)
GRANT SELECT, INSERT, UPDATE, DELETE ON signups TO techdeva_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON analysis_reports TO techdeva_app;
