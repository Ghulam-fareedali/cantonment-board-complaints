-- 22_brand_assets.sql
-- The client's own logo, stored by the platform rather than linked from
-- somewhere else.
--
-- Onboarding already asked for a "Logo image URL", which assumes the client has
-- their logo hosted somewhere and can find the address. Most cannot. They have
-- the file on their phone. This holds the file.
--
-- Bytes live in Postgres on purpose: one backup covers it, there is no volume to
-- forget, and a logo is small. The size cap is enforced by the API.

BEGIN;

CREATE TABLE IF NOT EXISTS brand_assets (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  kind       text NOT NULL DEFAULT 'logo',
  mime       text NOT NULL,
  bytes      bytea NOT NULL,
  byte_size  int NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT brand_assets_kind_ck CHECK (kind IN ('logo','background')),
  CONSTRAINT brand_assets_mime_ck CHECK (mime IN ('image/png','image/jpeg','image/webp','image/svg+xml')),
  CONSTRAINT brand_assets_size_ck CHECK (byte_size > 0 AND byte_size <= 1048576),
  CONSTRAINT brand_assets_one_per_kind UNIQUE (tenant_id, kind)
);

ALTER TABLE brand_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand_assets FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON brand_assets;
CREATE POLICY tenant_isolation ON brand_assets
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());

DO $verify$
DECLARE forced bool;
BEGIN
  SELECT relforcerowsecurity INTO forced FROM pg_class WHERE relname = 'brand_assets';
  IF NOT forced THEN RAISE EXCEPTION 'verify failed: row level security is not forced'; END IF;
  RAISE NOTICE 'verify ok: brand_assets ready, one logo per tenant, tenant isolated';
END $verify$;

COMMIT;
