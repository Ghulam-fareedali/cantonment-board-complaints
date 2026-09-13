-- TechDeva's own commercial side. Not the client's money - Faheem's.
-- What each client agreed to pay, and what they have actually paid.
-- Admin-only: a client must never read the platform's own book.
BEGIN;

CREATE TABLE IF NOT EXISTS client_accounts (
  tenant_id        uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  monthly_fee      numeric NOT NULL DEFAULT 0,
  setup_fee        numeric NOT NULL DEFAULT 0,
  performance_pct  numeric NOT NULL DEFAULT 0,
  currency         text NOT NULL DEFAULT 'PKR',
  started_on       date,
  next_invoice_on  date,
  contact          text NOT NULL DEFAULT '',
  notes            text NOT NULL DEFAULT '',
  updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS client_payments (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  amount     numeric NOT NULL CHECK (amount > 0),
  currency   text NOT NULL DEFAULT 'PKR',
  paid_on    date NOT NULL DEFAULT current_date,
  method     text NOT NULL DEFAULT 'bank',
  reference  text NOT NULL DEFAULT '',
  notes      text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS client_payments_tenant_idx ON client_payments (tenant_id, paid_on DESC);

ALTER TABLE client_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_accounts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON client_accounts;
CREATE POLICY admin_only ON client_accounts USING (is_admin());

ALTER TABLE client_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_payments FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS admin_only ON client_payments;
CREATE POLICY admin_only ON client_payments USING (is_admin());

DO $verify$
BEGIN
  IF to_regclass('public.client_accounts') IS NULL
     OR to_regclass('public.client_payments') IS NULL THEN
    RAISE EXCEPTION 'techdeva account tables missing';
  END IF;
  IF (SELECT count(*) FROM pg_policies
       WHERE tablename IN ('client_accounts','client_payments')) <> 2 THEN
    RAISE EXCEPTION 'account tables are not policy-protected';
  END IF;
  RAISE NOTICE 'verify ok: client_accounts + client_payments';
END $verify$;

COMMIT;
