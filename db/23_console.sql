-- 23_console.sql
-- Two things the business owner can change by typing a sentence.
--
-- 1. agent_lessons: what the owner has taught the WhatsApp sales agent. A row
--    per lesson rather than a paragraph appended to a blob, because the first
--    question after "teach it this" is always "what has it been taught?" and the
--    second is "remove that one, it is wrong".
--
-- 2. dashboard_prefs: how this business wants its own screen arranged. Their
--    own dashboard is theirs to change; nothing here reaches a customer.

BEGIN;

CREATE TABLE IF NOT EXISTS agent_lessons (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  agent       text NOT NULL DEFAULT 'whatsapp',
  lesson      text NOT NULL,
  said_as     text,
  created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
  active      boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  retired_at  timestamptz,
  CONSTRAINT agent_lessons_lesson_len_ck CHECK (length(lesson) BETWEEN 3 AND 2000),
  CONSTRAINT agent_lessons_retired_ck CHECK (active OR retired_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS agent_lessons_live_idx
  ON agent_lessons (tenant_id, agent) WHERE active;

ALTER TABLE agent_lessons ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_lessons FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON agent_lessons;
CREATE POLICY tenant_isolation ON agent_lessons
  USING (is_admin() OR tenant_id = current_tenant())
  WITH CHECK (is_admin() OR tenant_id = current_tenant());

ALTER TABLE tenant_config
  ADD COLUMN IF NOT EXISTS dashboard_prefs jsonb NOT NULL DEFAULT '{}'::jsonb;

DO $verify$
DECLARE forced bool; col int;
BEGIN
  SELECT relforcerowsecurity INTO forced FROM pg_class WHERE relname = 'agent_lessons';
  IF NOT forced THEN RAISE EXCEPTION 'verify failed: row level security is not forced'; END IF;
  SELECT count(*) INTO col FROM information_schema.columns
   WHERE table_name = 'tenant_config' AND column_name = 'dashboard_prefs';
  IF col <> 1 THEN RAISE EXCEPTION 'verify failed: dashboard_prefs missing'; END IF;
  RAISE NOTICE 'verify ok: lessons are per-row and removable, dashboard prefs are per tenant';
END $verify$;

COMMIT;
