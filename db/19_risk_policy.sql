-- 19_risk_policy.sql
-- Which agent actions a human has to approve, decided by policy rather than by
-- whatever the agent remembered to declare.
--
-- Until now the human gate fired only when an agent set human_review on its own
-- result. An agent that forgets -- or a new agent written next month that never
-- knew -- ships straight to the client's customers. The orchestrator now asks
-- this table before it lets work out, so forgetting is not possible.
--
-- A row with tenant_id NULL is the platform default. A tenant may add its own
-- row to make an action stricter; the API refuses to make one looser, because a
-- client talking themselves out of a safety gate is not a feature.

BEGIN;

CREATE TABLE IF NOT EXISTS risk_policies (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid REFERENCES tenants(id) ON DELETE CASCADE,
  action            text NOT NULL,
  risk              text NOT NULL,
  requires_approval boolean NOT NULL,
  reason            text NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT risk_policies_risk_ck CHECK (risk IN ('low','medium','high')),
  -- High risk always needs a human. The point of calling something high risk is
  -- that nobody can later flip the switch and leave the label in place.
  CONSTRAINT risk_policies_high_is_gated_ck CHECK (risk <> 'high' OR requires_approval)
);

CREATE UNIQUE INDEX IF NOT EXISTS risk_policies_default_uniq
  ON risk_policies (action) WHERE tenant_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS risk_policies_tenant_uniq
  ON risk_policies (tenant_id, action) WHERE tenant_id IS NOT NULL;

ALTER TABLE risk_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_policies FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON risk_policies;
CREATE POLICY tenant_isolation ON risk_policies
  -- Everyone reads the platform defaults; nobody but an admin writes them.
  USING (is_admin() OR tenant_id = current_tenant() OR tenant_id IS NULL)
  WITH CHECK (is_admin() OR tenant_id = current_tenant());

-- The defaults. The dividing line is simple: does the action reach a real
-- customer, spend real money, or change a number someone will be billed on.
INSERT INTO risk_policies (tenant_id, action, risk, requires_approval, reason) VALUES
  (NULL, 'publish.post',          'high',   true,  'Goes out in the business name to their public audience'),
  (NULL, 'publish.story',         'high',   true,  'Goes out in the business name to their public audience'),
  (NULL, 'publish.reel',          'high',   true,  'Goes out in the business name to their public audience'),
  (NULL, 'whatsapp.broadcast',    'high',   true,  'Reaches real customers directly and cannot be recalled'),
  (NULL, 'ads.launch',            'high',   true,  'Spends the client money'),
  (NULL, 'ads.budget_change',     'high',   true,  'Spends the client money'),
  (NULL, 'ads.create',            'high',   true,  'Spends the client money once live'),
  (NULL, 'pricing.change',        'high',   true,  'A wrong price quoted to a customer is a wrong price honoured'),
  (NULL, 'offer.publish',         'high',   true,  'A published offer is a promise the business has to keep'),
  (NULL, 'invoice.void',          'high',   true,  'Changes a financial record'),
  (NULL, 'refund.issue',          'high',   true,  'Moves money'),
  (NULL, 'integration.disconnect','high',   true,  'Takes a live channel off the air'),
  (NULL, 'whatsapp.disconnect',   'high',   true,  'Stops the AI answering customers immediately'),
  (NULL, 'appointment.cancel',    'medium', true,  'A real person is expecting to be seen'),
  (NULL, 'customer.message',      'medium', true,  'One customer, in the business name'),
  (NULL, 'content.draft',         'low',    false, 'A draft nobody has seen yet'),
  (NULL, 'content.plan',          'low',    false, 'A plan, not an action'),
  (NULL, 'lead.qualify',          'low',    false, 'Internal classification'),
  (NULL, 'crm.update',            'low',    false, 'Internal record keeping'),
  (NULL, 'report.generate',       'low',    false, 'Reading, not acting'),
  (NULL, 'analysis.run',          'low',    false, 'Reading, not acting')
ON CONFLICT DO NOTHING;

DO $verify$
DECLARE defaults int; gated int; forced bool;
BEGIN
  SELECT relforcerowsecurity INTO forced FROM pg_class WHERE relname = 'risk_policies';
  IF NOT forced THEN RAISE EXCEPTION 'verify failed: row level security is not forced'; END IF;
  SELECT count(*) INTO defaults FROM risk_policies WHERE tenant_id IS NULL;
  IF defaults < 21 THEN RAISE EXCEPTION 'verify failed: expected 21 platform defaults, found %', defaults; END IF;
  SELECT count(*) INTO gated FROM risk_policies WHERE risk = 'high' AND NOT requires_approval;
  IF gated > 0 THEN RAISE EXCEPTION 'verify failed: % high-risk actions are ungated', gated; END IF;
  RAISE NOTICE 'verify ok: % platform risk policies, every high-risk action gated', defaults;
END $verify$;

COMMIT;
