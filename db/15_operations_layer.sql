-- ============================================================================
-- 15_operations_layer.sql  ·  TechDeva Part 3  ·  13 September 2026
--
-- The money and operations layer: what was billed, what was paid, what was
-- spent, what was counted at the end of the day, what stock moved, and an
-- immutable record of who did what.
--
-- WHY `executions` IS A NEW TABLE AND NOT REUSED `events`
-- ------------------------------------------------------
-- `events` is the orchestrator's INPUT BUS: orchestrator/rules.py dispatches on
-- event["type"] to queue agent work, and the worker drains it. Audit records
-- must never trigger work and must never be drained. Putting them in the same
-- table would conflate "something happened that should cause work" with "a
-- record of what was already done", and would make queue-depth monitoring
-- meaningless. Different lifetimes, different consumers, different table.
--
-- FINANCIAL INTEGRITY
-- -------------------
-- Money rows are append-mostly. Corrections are made by voiding and reissuing,
-- not by silently rewriting history, and every write lands in `executions`.
-- Totals on an invoice are stored as they were agreed, so a later price change
-- on a service never rewrites what a customer was actually charged.
--
-- Additive only. Follows the existing convention exactly:
--   tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
--   ENABLE + FORCE ROW LEVEL SECURITY
--   POLICY tenant_isolation USING (is_admin() OR tenant_id = current_tenant())
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------- invoices
CREATE TABLE IF NOT EXISTS invoices (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id)   ON DELETE CASCADE,
    customer_id uuid REFERENCES customers(id)          ON DELETE SET NULL,
    location_id uuid REFERENCES locations(id)          ON DELETE SET NULL,
    number      text NOT NULL,
    status      text NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','issued','partial','paid','void')),
    issued_on   date NOT NULL DEFAULT CURRENT_DATE,
    due_on      date,
    currency    text NOT NULL DEFAULT 'PKR',
    subtotal    numeric(14,2) NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    discount    numeric(14,2) NOT NULL DEFAULT 0 CHECK (discount >= 0),
    tax         numeric(14,2) NOT NULL DEFAULT 0 CHECK (tax >= 0),
    total       numeric(14,2) NOT NULL DEFAULT 0 CHECK (total >= 0),
    notes       text,
    voided_at   timestamptz,
    void_reason text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT invoices_void_has_reason CHECK (voided_at IS NULL OR void_reason IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS invoices_tenant_number_uniq ON invoices (tenant_id, number);
CREATE INDEX IF NOT EXISTS invoices_tenant_issued_idx ON invoices (tenant_id, issued_on DESC);
CREATE INDEX IF NOT EXISTS invoices_customer_idx      ON invoices (customer_id);
CREATE INDEX IF NOT EXISTS invoices_status_idx        ON invoices (tenant_id, status);

CREATE TABLE IF NOT EXISTS invoice_items (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id)  ON DELETE CASCADE,
    invoice_id  uuid NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    service_id  uuid REFERENCES services(id) ON DELETE SET NULL,
    product_id  uuid REFERENCES products(id) ON DELETE SET NULL,
    package_id  uuid REFERENCES packages(id) ON DELETE SET NULL,
    -- description and unit_price are copied at billing time on purpose: a later
    -- price change must never rewrite what this customer was charged.
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    quantity    numeric(12,2) NOT NULL DEFAULT 1 CHECK (quantity > 0),
    unit_price  numeric(14,2) NOT NULL CHECK (unit_price >= 0),
    line_total  numeric(14,2) NOT NULL CHECK (line_total >= 0)
);
CREATE INDEX IF NOT EXISTS invoice_items_invoice_idx ON invoice_items (invoice_id);

-- ----------------------------------------------------------------- payments
CREATE TABLE IF NOT EXISTS payments (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id)  ON DELETE CASCADE,
    invoice_id  uuid REFERENCES invoices(id)          ON DELETE SET NULL,
    customer_id uuid REFERENCES customers(id)         ON DELETE SET NULL,
    location_id uuid REFERENCES locations(id)         ON DELETE SET NULL,
    amount      numeric(14,2) NOT NULL CHECK (amount > 0),
    currency    text NOT NULL DEFAULT 'PKR',
    method      text NOT NULL DEFAULT 'cash'
                CHECK (method IN ('cash','card','bank','online','other')),
    reference   text,
    notes       text,
    paid_at     timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payments_tenant_paid_idx ON payments (tenant_id, paid_at DESC);
CREATE INDEX IF NOT EXISTS payments_invoice_idx     ON payments (invoice_id);

-- ----------------------------------------------------------------- expenses
CREATE TABLE IF NOT EXISTS expenses (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    location_id uuid REFERENCES locations(id)        ON DELETE SET NULL,
    category    text NOT NULL DEFAULT 'general',
    description text NOT NULL CHECK (length(btrim(description)) > 0),
    amount      numeric(14,2) NOT NULL CHECK (amount > 0),
    currency    text NOT NULL DEFAULT 'PKR',
    method      text,
    spent_on    date NOT NULL DEFAULT CURRENT_DATE,
    notes       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS expenses_tenant_date_idx ON expenses (tenant_id, spent_on DESC);
CREATE INDEX IF NOT EXISTS expenses_category_idx    ON expenses (tenant_id, category);

COMMIT;

BEGIN;

-- ------------------------------------------------------------ daily_closing
-- What the books say versus what was actually counted. Variance is stored, not
-- hidden: a closing that does not balance is a fact the owner needs to see.
CREATE TABLE IF NOT EXISTS daily_closing (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    location_id    uuid REFERENCES locations(id)        ON DELETE SET NULL,
    closing_date   date NOT NULL DEFAULT CURRENT_DATE,
    cash_counted   numeric(14,2) NOT NULL DEFAULT 0 CHECK (cash_counted   >= 0),
    card_counted   numeric(14,2) NOT NULL DEFAULT 0 CHECK (card_counted   >= 0),
    bank_counted   numeric(14,2) NOT NULL DEFAULT 0 CHECK (bank_counted   >= 0),
    other_counted  numeric(14,2) NOT NULL DEFAULT 0 CHECK (other_counted  >= 0),
    expected_total numeric(14,2) NOT NULL DEFAULT 0,
    counted_total  numeric(14,2) NOT NULL DEFAULT 0,
    expenses_total numeric(14,2) NOT NULL DEFAULT 0,
    variance       numeric(14,2) NOT NULL DEFAULT 0,
    notes          text,
    closed_by      uuid REFERENCES users(id) ON DELETE SET NULL,
    closed_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS daily_closing_uniq ON daily_closing
    (tenant_id, closing_date, COALESCE(location_id, '00000000-0000-0000-0000-000000000000'::uuid));

-- ---------------------------------------------------------- stock_movements
-- Stock on hand is DERIVED from these rows, never stored as a mutable number.
-- A running total you can edit is a running total you cannot trust; a ledger
-- you can only append to explains itself.
CREATE TABLE IF NOT EXISTS stock_movements (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenants(id)  ON DELETE CASCADE,
    product_id     uuid NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    location_id    uuid REFERENCES locations(id)         ON DELETE SET NULL,
    kind           text NOT NULL
                   CHECK (kind IN ('purchase','sale','adjustment','consumption','return','opening')),
    quantity_delta numeric(12,2) NOT NULL CHECK (quantity_delta <> 0),
    unit_cost      numeric(14,2) CHECK (unit_cost IS NULL OR unit_cost >= 0),
    reason         text,
    reference      text,
    occurred_at    timestamptz NOT NULL DEFAULT now(),
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS stock_movements_product_idx ON stock_movements (product_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS stock_movements_tenant_idx  ON stock_movements (tenant_id, occurred_at DESC);

-- products gains only the two flags inventory needs. Quantity stays derived.
ALTER TABLE products ADD COLUMN IF NOT EXISTS track_stock boolean NOT NULL DEFAULT false;
ALTER TABLE products ADD COLUMN IF NOT EXISTS low_stock_threshold numeric(12,2)
    CHECK (low_stock_threshold IS NULL OR low_stock_threshold >= 0);

-- --------------------------------------------------------------- executions
-- The audit trail. Append-only by intent: no UPDATE path is exposed by the API.
CREATE TABLE IF NOT EXISTS executions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    actor       text,                         -- user id, agent name, or 'system'
    actor_type  text NOT NULL DEFAULT 'human'
                CHECK (actor_type IN ('human','agent','system')),
    action      text NOT NULL,                -- create | update | delete | approve | publish | ...
    resource    text NOT NULL,                -- invoices | services | brain | ...
    resource_id text,
    status      text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error','refused')),
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    error       text,
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS executions_tenant_at_idx ON executions (tenant_id, at DESC);
CREATE INDEX IF NOT EXISTS executions_resource_idx  ON executions (tenant_id, resource, resource_id);
CREATE INDEX IF NOT EXISTS executions_actor_idx     ON executions (tenant_id, actor_type, at DESC);

COMMIT;

-- ============================================================================
-- ROW LEVEL SECURITY -- identical policy on every new table, applied in a loop
-- so none can be forgotten and the text is provably the same on all of them.
-- ============================================================================
BEGIN;
DO $rls$
DECLARE
    t text;
    new_tables text[] := ARRAY[
        'invoices','invoice_items','payments','expenses',
        'daily_closing','stock_movements','executions'
    ];
BEGIN
    FOREACH t IN ARRAY new_tables LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I FOR ALL '
            'USING (is_admin() OR tenant_id = current_tenant()) '
            'WITH CHECK (is_admin() OR tenant_id = current_tenant())', t);
    END LOOP;
END
$rls$;
COMMIT;

-- ============================================================================
-- VERIFICATION -- fail loudly rather than half-apply.
-- ============================================================================
DO $verify$
DECLARE
    expected text[] := ARRAY['invoices','invoice_items','payments','expenses',
                             'daily_closing','stock_movements','executions'];
    missing text;
    unprotected text;
    cols int;
BEGIN
    SELECT string_agg(x, ', ') INTO missing
    FROM unnest(expected) AS x WHERE to_regclass('public.' || x) IS NULL;
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'MIGRATION INCOMPLETE - tables missing: %', missing;
    END IF;

    SELECT string_agg(c.relname, ', ') INTO unprotected
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = ANY (expected)
      AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity
           OR NOT EXISTS (SELECT 1 FROM pg_policy p
                          WHERE p.polrelid = c.oid AND p.polname = 'tenant_isolation'));
    IF unprotected IS NOT NULL THEN
        RAISE EXCEPTION 'MIGRATION UNSAFE - RLS/FORCE/policy missing on: %', unprotected;
    END IF;

    SELECT count(*) INTO cols FROM information_schema.columns
    WHERE table_name = 'products' AND column_name IN ('track_stock','low_stock_threshold');
    IF cols <> 2 THEN
        RAISE EXCEPTION 'MIGRATION INCOMPLETE - products inventory columns missing';
    END IF;

    RAISE NOTICE 'OK: 7 operations tables present, RLS + FORCE + tenant_isolation on each, products extended.';
END
$verify$;
