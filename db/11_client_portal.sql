-- ============================================================
-- TechDeva AIBOS — Client Portal migration (Build 2)
-- ADDITIVE ONLY. Reversible. uuid-only. Nothing existing is dropped,
-- renamed, backfilled or restructured.
--
-- Adds what the client actually sees during the 7-day trial:
--   * users.password_hash / last_login_at  — the client owns their own login
--   * deliverables                          — what the agents produced, made visible
--                                             and (for irreversible work) approvable
--
-- Beauty Aesthetic safety: password_hash is NULLABLE with no default, so every
-- existing user row is untouched and every existing query keeps working. A user
-- with a NULL password_hash simply cannot log in to the portal (fail closed) —
-- exactly the behaviour before this migration existed.
--
-- Rollback (safe, in this order):
--   DROP TABLE IF EXISTS deliverables;
--   DROP TYPE  IF EXISTS deliverable_status;
--   ALTER TABLE users DROP COLUMN IF EXISTS password_hash;
--   ALTER TABLE users DROP COLUMN IF EXISTS last_login_at;
-- ============================================================

DO $$ BEGIN
  CREATE TYPE deliverable_status AS ENUM
    ('draft','awaiting_approval','approved','rejected','delivered');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------- the client's own login ----------
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at timestamptz;

COMMENT ON COLUMN users.password_hash IS
  'PBKDF2-SHA256 (pbkdf2_sha256$iterations$salt$hash). NULL = cannot log in.';

-- ---------- deliverables: the work, made visible to the client ----------
-- One row per artifact an agent produced (content plan, creative set, campaign
-- draft, publishing schedule). This is what the trial dashboard renders — and
-- for irreversible work (ads spend / public posting) it is the approval gate the
-- client actually clicks.
CREATE TABLE IF NOT EXISTS deliverables (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  task_id           uuid REFERENCES tasks(id) ON DELETE SET NULL,
  agent             text NOT NULL,               -- content | design | ads | publishing | ...
  kind              text NOT NULL,               -- content_plan | creative_set | campaign | post_schedule
  title             text NOT NULL,
  body              jsonb NOT NULL DEFAULT '{}', -- the agent's structured output, verbatim
  status            deliverable_status NOT NULL DEFAULT 'draft',
  requires_approval boolean NOT NULL DEFAULT false,
  approved_by       uuid REFERENCES users(id) ON DELETE SET NULL,
  approved_at       timestamptz,
  decision_reason   text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),

  -- STRUCTURAL GUARANTEE, not application discipline:
  -- an 'approved' row can never exist without a named human approver + timestamp.
  -- Money and public posting cannot be waved through by a code path that forgot.
  CONSTRAINT deliverable_approval_complete CHECK (
    status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)
  ),
  -- a rejection must say why, so the agent/human has something to act on
  CONSTRAINT deliverable_rejection_has_reason CHECK (
    status <> 'rejected' OR decision_reason IS NOT NULL
  )
);

CREATE INDEX IF NOT EXISTS deliverables_tenant_idx
  ON deliverables(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS deliverables_pending_idx
  ON deliverables(tenant_id, status) WHERE status = 'awaiting_approval';

-- A task that is retried (or re-parked) must not spawn a second copy of the same
-- deliverable — the client would see duplicate approval cards for one campaign.
CREATE UNIQUE INDEX IF NOT EXISTS deliverables_task_kind_uniq
  ON deliverables(task_id, kind) WHERE task_id IS NOT NULL;

-- ---------- RLS: same shape as every other tenant table ----------
ALTER TABLE deliverables ENABLE ROW LEVEL SECURITY;
ALTER TABLE deliverables FORCE ROW LEVEL SECURITY;   -- FORCE: the owner does not bypass it
DO $$ BEGIN
  CREATE POLICY tenant_isolation ON deliverables
    USING (is_admin() OR tenant_id = current_tenant())
    WITH CHECK (is_admin() OR tenant_id = current_tenant());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON deliverables TO techdeva_app;
