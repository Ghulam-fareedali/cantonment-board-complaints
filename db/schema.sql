-- ============================================================
-- TechDeva AIBOS — Phase A schema (multi-tenant + Row-Level Security)
-- One engine, every client isolated at the database layer.
-- Isolation rule: every tenant row is invisible unless the session
-- sets app.tenant_id, EXCEPT techdeva_admin which sees across tenants.
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()

-- ---------- enums ----------
DO $$ BEGIN
  CREATE TYPE tenant_status  AS ENUM ('trial','active','paused');
  CREATE TYPE plan_kind      AS ENUM ('subscription','performance','hybrid');
  CREATE TYPE billing_cycle  AS ENUM ('monthly','annual');
  CREATE TYPE user_role      AS ENUM ('techdeva_admin','owner','operator');
  CREATE TYPE lead_status    AS ENUM ('new','qualified','booked','showed','sold','lost','fake');
  CREATE TYPE lead_source    AS ENUM ('hermes','human','imported','api','manual');
  CREATE TYPE convo_status   AS ENUM ('ai_active','human_active','closed');
  CREATE TYPE task_status    AS ENUM ('queued','waiting','running','done','failed','needs_human','dead');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------- tenancy & identity ----------
CREATE TABLE tenants (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name          text NOT NULL,
  industry      text NOT NULL,                 -- 'aesthetic_clinic','salon','dental',...
  status        tenant_status NOT NULL DEFAULT 'trial',
  plan          plan_kind,
  billing       billing_cycle,
  trial_ends_at timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- the "industry = configuration, not code" row
CREATE TABLE tenant_config (
  tenant_id      uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  services       jsonb NOT NULL DEFAULT '[]',   -- [{name, price, department}]
  price_list     jsonb NOT NULL DEFAULT '{}',
  departments    jsonb NOT NULL DEFAULT '[]',
  booking_rules  jsonb NOT NULL DEFAULT '{}',
  hermes_persona text,
  brand          jsonb NOT NULL DEFAULT '{}',   -- {colors, logo}
  geo            jsonb NOT NULL DEFAULT '{}',
  working_hours  jsonb NOT NULL DEFAULT '{}',
  automation     text  NOT NULL DEFAULT 'assisted' -- 'full_auto' | 'assisted' | 'oversight'
);

CREATE TABLE users (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid REFERENCES tenants(id) ON DELETE CASCADE,  -- NULL for techdeva_admin
  name       text NOT NULL,
  email      text UNIQUE NOT NULL,
  phone      text,
  role       user_role NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------- business records ----------
CREATE TABLE leads (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name              text,
  phone             text,
  email             text,
  service           text,
  department        text,
  status            lead_status NOT NULL DEFAULT 'new',
  source            lead_source NOT NULL,            -- transparency + audit (required)
  assigned_operator uuid REFERENCES users(id),
  assigned_agent    text,                            -- e.g. 'hermes'
  appointment_at    timestamptz,
  followup_at       timestamptz,
  priority          int NOT NULL DEFAULT 3,          -- 1 high .. 5 low
  est_revenue       numeric(12,2) NOT NULL DEFAULT 0,
  tags              text[] NOT NULL DEFAULT '{}',
  notes             text,
  created_by        text,                            -- user id or 'hermes'
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON leads(tenant_id, status);
CREATE INDEX ON leads(tenant_id, followup_at);

CREATE TABLE conversations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id     uuid REFERENCES leads(id) ON DELETE CASCADE,
  channel     text NOT NULL DEFAULT 'whatsapp',
  handled_by  text NOT NULL DEFAULT 'hermes',        -- 'hermes' | 'human'
  status      convo_status NOT NULL DEFAULT 'ai_active',
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON conversations(tenant_id, status);

CREATE TABLE messages (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  conversation_id  uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  direction        text NOT NULL,                    -- 'in' | 'out'
  author           text NOT NULL,                    -- 'lead' | 'hermes' | 'human'
  body             text,
  media            jsonb,
  sent_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON messages(conversation_id, sent_at);

CREATE TABLE appointments (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id    uuid REFERENCES leads(id) ON DELETE CASCADE,
  service    text,
  starts_at  timestamptz,
  status     text NOT NULL DEFAULT 'booked',         -- booked/showed/no_show/cancelled
  operator   uuid REFERENCES users(id)
);
CREATE INDEX ON appointments(tenant_id, starts_at);

-- each client connects their OWN accounts (Meta, Google, WhatsApp, ...). Credentials
-- are per-tenant and scoped by RLS — one client's tokens never touch another's.
CREATE TABLE integrations (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  provider   text NOT NULL,                          -- 'meta' | 'google' | 'whatsapp' | 'instagram' | 'tiktok'
  status     text NOT NULL DEFAULT 'disconnected',   -- connected | disconnected | error | expired
  external_account text,                             -- ad account id / page id / etc
  credentials jsonb NOT NULL DEFAULT '{}',           -- oauth tokens (encrypt at rest in prod)
  connected_at timestamptz,
  last_error text,
  UNIQUE (tenant_id, provider)
);
CREATE INDEX ON integrations(tenant_id, status);

CREATE TABLE campaigns (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  platform   text NOT NULL,                          -- 'meta' | 'google'
  objective  text,
  status     text,
  spend      numeric(12,2) NOT NULL DEFAULT 0,
  leads      int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE revenue_events (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id           uuid REFERENCES leads(id) ON DELETE SET NULL,
  amount            numeric(12,2) NOT NULL,
  type              text,                             -- procedure/course/product
  attributed_source lead_source,                      -- attribution baked in day one
  occurred_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON revenue_events(tenant_id, occurred_at);

-- ---------- orchestration records ----------
CREATE TABLE tasks (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agent      text NOT NULL,                           -- which agent runs it
  type       text NOT NULL,                           -- 'qualify','book','weekly_report'...
  payload    jsonb NOT NULL DEFAULT '{}',
  status     task_status NOT NULL DEFAULT 'queued',
  priority    int NOT NULL DEFAULT 3,                  -- 1 high .. 5 low
  depends_on uuid[],
  attempts    int NOT NULL DEFAULT 0,                  -- retry bookkeeping
  max_attempts int NOT NULL DEFAULT 3,
  run_after   timestamptz,                             -- backoff / scheduled execution
  result     jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON tasks(tenant_id, status);
CREATE INDEX ON tasks(agent, status);

CREATE TABLE context_store (
  tenant_id        uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  key              text NOT NULL,
  value            jsonb NOT NULL DEFAULT '{}',
  updated_by_agent text,
  updated_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, key)
);

CREATE TABLE events (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  type       text NOT NULL,                           -- 'lead.created','human.took_over'...
  payload    jsonb NOT NULL DEFAULT '{}',
  emitted_by text,
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON events(tenant_id, at);
CREATE INDEX ON events(type, at);

CREATE TABLE learning_signals (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agent       text NOT NULL,
  signal_type text NOT NULL,                          -- 'edited_reply','ignored','status_change'...
  before      jsonb,
  after       jsonb,
  human_id    uuid REFERENCES users(id),
  reason      text,
  at          timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- ROW-LEVEL SECURITY
-- The app sets, per request:
--   SET app.tenant_id = '<uuid>';
--   SET app.role      = 'owner' | 'operator' | 'techdeva_admin';
-- techdeva_admin bypasses the tenant filter (sees across all clients).
-- ============================================================

CREATE OR REPLACE FUNCTION current_tenant() RETURNS uuid AS $$
  SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION is_admin() RETURNS boolean AS $$
  SELECT current_setting('app.role', true) = 'techdeva_admin';
$$ LANGUAGE sql STABLE;

-- apply the same policy shape to every tenant-scoped table
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'tenant_config','users','leads','conversations','messages','appointments',
    'integrations','campaigns','revenue_events','tasks','context_store','events','learning_signals'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', t);
    EXECUTE format($f$
      CREATE POLICY tenant_isolation ON %I
      USING (is_admin() OR tenant_id = current_tenant())
      WITH CHECK (is_admin() OR tenant_id = current_tenant());
    $f$, t);
  END LOOP;
END $$;

-- tenants table: admin sees all; a tenant user sees only their own tenant row
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_self ON tenants
  USING (is_admin() OR id = current_tenant())
  WITH CHECK (is_admin());

-- NOTE: create a dedicated app role (NOT the postgres superuser) so RLS is enforced:
--   CREATE ROLE techdeva_app LOGIN PASSWORD '...';
--   GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO techdeva_app;
-- Superusers bypass RLS — the app must connect as techdeva_app.
