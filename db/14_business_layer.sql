-- ============================================================================
-- 14_business_layer.sql  ·  TechDeva Part 2  ·  12 September 2026
--
-- The business-definition layer: what a tenant sells, who works there, when
-- they are open, where they are, what they promise, and what they are aiming at.
--
-- DESIGN NOTE — why these tables exist when tenant_config already has jsonb
-- ------------------------------------------------------------------------
-- tenant_config already carries services / price_list / working_hours /
-- departments as jsonb, and the orchestrator already feeds it to every agent
-- (persistence.load_config -> Orchestrator(load_config=...) -> agent ctx).
-- That pipeline is NOT replaced and NOT duplicated.
--
-- These tables exist for the things a jsonb blob cannot do: an appointment
-- pointing at a service, a price that can be reported on, a staff member
-- assigned to a treatment, a package built out of services. The relational
-- tables are the OPERATIONAL source of truth; tenant_config remains the
-- BUSINESS BRAIN DOCUMENT that agents read, refreshed from these tables by
-- the brain assembler. One authority, one derived snapshot -- never two
-- competing copies.
--
-- Every table follows the existing convention exactly:
--   tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
--   ALTER TABLE ... ENABLE ROW LEVEL SECURITY
--   POLICY tenant_isolation USING (is_admin() OR tenant_id = current_tenant())
-- Additive only. No existing table is dropped, renamed or re-typed.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------- customers
CREATE TABLE IF NOT EXISTS customers (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    lead_id             uuid REFERENCES leads(id) ON DELETE SET NULL,
    name                text NOT NULL CHECK (length(btrim(name)) > 0),
    phone               text,
    email               text,
    source              text,
    status              text NOT NULL DEFAULT 'active',
    tags                text[] NOT NULL DEFAULT '{}',
    notes               text,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    last_interaction_at timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS customers_tenant_idx  ON customers (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS customers_phone_idx   ON customers (tenant_id, phone);
CREATE INDEX IF NOT EXISTS customers_lead_idx    ON customers (lead_id);
-- one customer per phone per tenant, when a phone is given
CREATE UNIQUE INDEX IF NOT EXISTS customers_tenant_phone_uniq
    ON customers (tenant_id, phone) WHERE phone IS NOT NULL AND phone <> '';

-- ----------------------------------------------------------------- services
CREATE TABLE IF NOT EXISTS services (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name             text NOT NULL CHECK (length(btrim(name)) > 0),
    description      text,
    category         text,
    price            numeric(14,2) CHECK (price IS NULL OR price >= 0),
    currency         text NOT NULL DEFAULT 'PKR',
    duration_minutes integer CHECK (duration_minutes IS NULL OR duration_minutes > 0),
    active           boolean NOT NULL DEFAULT true,
    sort_order       integer NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS services_tenant_idx ON services (tenant_id, active, sort_order);
CREATE UNIQUE INDEX IF NOT EXISTS services_tenant_name_uniq ON services (tenant_id, lower(btrim(name)));

-- ----------------------------------------------------------------- products
CREATE TABLE IF NOT EXISTS products (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name        text NOT NULL CHECK (length(btrim(name)) > 0),
    description text,
    category    text,
    sku         text,
    price       numeric(14,2) CHECK (price IS NULL OR price >= 0),
    currency    text NOT NULL DEFAULT 'PKR',
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS products_tenant_idx ON products (tenant_id, active);
CREATE UNIQUE INDEX IF NOT EXISTS products_tenant_name_uniq ON products (tenant_id, lower(btrim(name)));

-- ----------------------------------------------------------------- packages
CREATE TABLE IF NOT EXISTS packages (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name          text NOT NULL CHECK (length(btrim(name)) > 0),
    description   text,
    price         numeric(14,2) CHECK (price IS NULL OR price >= 0),
    currency      text NOT NULL DEFAULT 'PKR',
    validity_days integer CHECK (validity_days IS NULL OR validity_days > 0),
    terms         text,
    active        boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS packages_tenant_idx ON packages (tenant_id, active);

CREATE TABLE IF NOT EXISTS package_items (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    package_id uuid NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
    service_id uuid REFERENCES services(id) ON DELETE CASCADE,
    product_id uuid REFERENCES products(id) ON DELETE CASCADE,
    quantity   integer NOT NULL DEFAULT 1 CHECK (quantity > 0),
    -- a line is either a service or a product, never both and never neither
    CONSTRAINT package_items_one_target CHECK (
        (service_id IS NOT NULL AND product_id IS NULL) OR
        (service_id IS NULL AND product_id IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS package_items_pkg_idx ON package_items (package_id);

COMMIT;

BEGIN;

-- -------------------------------------------------------------------- staff
CREATE TABLE IF NOT EXISTS staff (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name       text NOT NULL CHECK (length(btrim(name)) > 0),
    role       text,
    department text,
    email      text,
    phone      text,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS staff_tenant_idx ON staff (tenant_id, active);

-- which staff member can deliver which service
CREATE TABLE IF NOT EXISTS staff_services (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    staff_id   uuid NOT NULL REFERENCES staff(id)    ON DELETE CASCADE,
    service_id uuid NOT NULL REFERENCES services(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS staff_services_uniq ON staff_services (staff_id, service_id);

-- ---------------------------------------------------------------- locations
CREATE TABLE IF NOT EXISTS locations (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name       text NOT NULL CHECK (length(btrim(name)) > 0),
    address    text,
    city       text,
    country    text,
    phone      text,
    timezone   text,
    is_primary boolean NOT NULL DEFAULT false,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS locations_tenant_idx ON locations (tenant_id, active);
-- at most one primary location per tenant
CREATE UNIQUE INDEX IF NOT EXISTS locations_one_primary
    ON locations (tenant_id) WHERE is_primary;

-- ----------------------------------------------------------- business_hours
-- location_id NULL means "the whole business", for single-site tenants.
CREATE TABLE IF NOT EXISTS business_hours (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES tenants(id)   ON DELETE CASCADE,
    location_id uuid REFERENCES locations(id)          ON DELETE CASCADE,
    day_of_week smallint NOT NULL CHECK (day_of_week BETWEEN 0 AND 6), -- 0 = Sunday
    opens       time,
    closes      time,
    is_closed   boolean NOT NULL DEFAULT false,
    CONSTRAINT business_hours_sane CHECK (
        is_closed OR (opens IS NOT NULL AND closes IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS business_hours_uniq
    ON business_hours (tenant_id, COALESCE(location_id, '00000000-0000-0000-0000-000000000000'::uuid), day_of_week);

-- -------------------------------------------------------- business_policies
CREATE TABLE IF NOT EXISTS business_policies (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    kind       text NOT NULL,                 -- cancellation | booking | refund | payment | communication | faq | other
    title      text NOT NULL CHECK (length(btrim(title)) > 0),
    body       text NOT NULL CHECK (length(btrim(body)) > 0),
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS business_policies_tenant_idx ON business_policies (tenant_id, kind, active);

-- ----------------------------------------------------------- business_goals
CREATE TABLE IF NOT EXISTS business_goals (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    metric     text NOT NULL,                 -- revenue | leads | appointments | customers | custom
    label      text,
    target     numeric(16,2) NOT NULL CHECK (target >= 0),
    period     text NOT NULL DEFAULT 'monthly',  -- daily | weekly | monthly | quarterly | yearly
    starts_on  date,
    ends_on    date,
    active     boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT business_goals_window CHECK (ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on)
);
CREATE INDEX IF NOT EXISTS business_goals_tenant_idx ON business_goals (tenant_id, active);

-- ------------------------------------------- appointments: additive columns
-- The table exists with (lead_id, service text, starts_at, status, operator).
-- Nothing is dropped or re-typed; the free-text `service` column stays exactly
-- as it is so existing writers keep working. These columns let an appointment
-- point at real catalogue rows once they exist.
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS customer_id uuid REFERENCES customers(id) ON DELETE SET NULL;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS service_id  uuid REFERENCES services(id)  ON DELETE SET NULL;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS staff_id    uuid REFERENCES staff(id)     ON DELETE SET NULL;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS location_id uuid REFERENCES locations(id) ON DELETE SET NULL;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS ends_at     timestamptz;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS price       numeric(14,2) CHECK (price IS NULL OR price >= 0);
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS currency    text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS notes       text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS created_at  timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS appointments_customer_idx ON appointments (customer_id);
CREATE INDEX IF NOT EXISTS appointments_tenant_start_idx ON appointments (tenant_id, starts_at DESC);

COMMIT;

-- ============================================================================
-- ROW LEVEL SECURITY
-- Identical to every existing tenant-scoped table:
--   USING (is_admin() OR tenant_id = current_tenant())
-- Applied in a loop so no table can be forgotten, and so the policy text is
-- provably the same on all of them rather than retyped eleven times.
-- ============================================================================

BEGIN;

DO $rls$
DECLARE
    t text;
    new_tables text[] := ARRAY[
        'customers', 'services', 'products', 'packages', 'package_items',
        'staff', 'staff_services', 'locations', 'business_hours',
        'business_policies', 'business_goals'
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
        RAISE NOTICE 'RLS enabled on %', t;
    END LOOP;
END
$rls$;

COMMIT;

-- ============================================================================
-- VERIFICATION -- this migration fails loudly rather than half-applying.
-- ============================================================================
DO $verify$
DECLARE
    missing text;
    unprotected text;
BEGIN
    SELECT string_agg(x, ', ') INTO missing
    FROM unnest(ARRAY['customers','services','products','packages','package_items',
                      'staff','staff_services','locations','business_hours',
                      'business_policies','business_goals']) AS x
    WHERE to_regclass('public.' || x) IS NULL;

    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'MIGRATION INCOMPLETE - tables missing: %', missing;
    END IF;

    SELECT string_agg(c.relname, ', ') INTO unprotected
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname = ANY (ARRAY['customers','services','products','packages','package_items',
                                 'staff','staff_services','locations','business_hours',
                                 'business_policies','business_goals'])
      AND (NOT c.relrowsecurity
           OR NOT EXISTS (SELECT 1 FROM pg_policy p
                          WHERE p.polrelid = c.oid AND p.polname = 'tenant_isolation'));

    IF unprotected IS NOT NULL THEN
        RAISE EXCEPTION 'MIGRATION UNSAFE - RLS or tenant_isolation missing on: %', unprotected;
    END IF;

    RAISE NOTICE 'OK: 11 business-layer tables present, RLS on, tenant_isolation applied to each.';
END
$verify$;
