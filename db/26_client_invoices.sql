-- TechDeva's own invoices OUT to clients. Founder-only.
-- What was billed, not yet what arrived (client_payments is what arrived).
BEGIN;
 
CREATE TABLE IF NOT EXISTS client_invoices (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  amount       numeric NOT NULL CHECK (amount > 0),
  currency     text NOT NULL DEFAULT 'PKR',
  status       text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','sent','paid','void')),
  description  text NOT NULL DEFAULT '',
  issued_on    date NOT NULL DEFAULT current_date,
  due_on       date,
  sent_at      timestamptz,
  paid_at      timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS client_invoices_tenant_idx ON client_invoices (tenant_id, issued_on DESC);
 
ALTER TABLE client_invoices ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_invoices FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON client_invoices;
CREATE POLICY admin_only ON client_invoices USING (is_admin());
 
DO $verify$
BEGIN
  IF to_regclass('public.client_invoices') IS NULL THEN
    RAISE EXCEPTION 'client_invoices table missing';
  END IF;
  IF (SELECT count(*) FROM pg_policies WHERE tablename = 'client_invoices') <> 1 THEN
    RAISE EXCEPTION 'client_invoices is not policy-protected';
  END IF;
  RAISE NOTICE 'verify ok: client_invoices';
END $verify$;
 
COMMIT;
