-- 18_whatsapp_connections.sql
-- One row per tenant recording where that tenant's WhatsApp number lives and
-- what state it is in. The pairing control URL is held here, server side, and
-- is never accepted from a caller: which agent a tenant belongs to is a
-- TechDeva decision, not something a client can name in a request body.

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_connections (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agent_key       text NOT NULL,
  pairctl_url     text NOT NULL,
  status          text NOT NULL DEFAULT 'never_connected',
  phone_number    text,
  display_name    text,
  last_qr_at      timestamptz,
  connected_at    timestamptz,
  disconnected_at timestamptz,
  last_error      text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT whatsapp_connections_tenant_key UNIQUE (tenant_id),
  CONSTRAINT whatsapp_connections_status_ck CHECK (
    status IN ('never_connected','pairing','connected','disconnected','error')),
  -- Same rule as the integrations table: a connection may only call itself
  -- connected if it can point at the moment it connected and the number it got.
  CONSTRAINT whatsapp_connections_proof_ck CHECK (
    status <> 'connected' OR (connected_at IS NOT NULL AND phone_number IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS whatsapp_connections_status_idx
  ON whatsapp_connections (tenant_id, status);

ALTER TABLE whatsapp_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_connections FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON whatsapp_connections;
CREATE POLICY tenant_isolation ON whatsapp_connections
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());

DO $verify$
DECLARE forced bool; pol int;
BEGIN
  SELECT relforcerowsecurity INTO forced FROM pg_class WHERE relname = 'whatsapp_connections';
  IF NOT forced THEN
    RAISE EXCEPTION 'verify failed: row level security is not forced on whatsapp_connections';
  END IF;
  SELECT count(*) INTO pol FROM pg_policies
   WHERE tablename = 'whatsapp_connections' AND policyname = 'tenant_isolation';
  IF pol <> 1 THEN
    RAISE EXCEPTION 'verify failed: expected one tenant_isolation policy, found %', pol;
  END IF;
  RAISE NOTICE 'verify ok: whatsapp_connections is tenant isolated';
END $verify$;

COMMIT;
