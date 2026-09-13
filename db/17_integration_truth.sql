-- 17_integration_truth.sql
-- An integration row is a claim about the outside world. Until now the platform
-- could claim "connected" while holding no credentials at all, which is how the
-- Beauty tenant came to show four live integrations it had never connected.
-- After this migration the database itself refuses that claim.

BEGIN;

ALTER TABLE integrations ADD COLUMN IF NOT EXISTS scopes text[];
ALTER TABLE integrations ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE integrations ADD COLUMN IF NOT EXISTS last_checked_at timestamptz;
ALTER TABLE integrations ADD COLUMN IF NOT EXISTS health text NOT NULL DEFAULT 'unknown';
ALTER TABLE integrations ADD COLUMN IF NOT EXISTS disconnected_at timestamptz;
ALTER TABLE integrations ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

-- All four rows carry connected_at 2026-07-21 14:41:14.633959 to the microsecond,
-- which is one seed statement rather than four connections made at four moments,
-- and none of them holds a credential. The account identifiers are real and are
-- kept; the timestamp is not, so it goes.
UPDATE integrations
   SET status = 'never_connected', health = 'unknown',
       connected_at = NULL, updated_at = now()
 WHERE credentials = '{}'::jsonb
   AND status <> 'never_connected';

ALTER TABLE integrations DROP CONSTRAINT IF EXISTS integrations_status_ck;
ALTER TABLE integrations ADD CONSTRAINT integrations_status_ck
  CHECK (status IN ('never_connected','connecting','connected','expired','error','disconnected'));

ALTER TABLE integrations DROP CONSTRAINT IF EXISTS integrations_health_ck;
ALTER TABLE integrations ADD CONSTRAINT integrations_health_ck
  CHECK (health IN ('unknown','ok','degraded','failing'));

ALTER TABLE integrations DROP CONSTRAINT IF EXISTS integrations_connected_proof_ck;
ALTER TABLE integrations ADD CONSTRAINT integrations_connected_proof_ck
  CHECK (status <> 'connected' OR (credentials <> '{}'::jsonb AND connected_at IS NOT NULL));

ALTER TABLE integrations ALTER COLUMN status SET DEFAULT 'never_connected';

DO $verify$
DECLARE lying int; cols int;
BEGIN
  SELECT count(*) INTO lying FROM integrations
   WHERE status = 'connected' AND (credentials = '{}'::jsonb OR connected_at IS NULL);
  IF lying > 0 THEN
    RAISE EXCEPTION 'verify failed: % rows still claim connected without proof', lying;
  END IF;
  SELECT count(*) INTO cols FROM information_schema.columns
   WHERE table_name = 'integrations'
     AND column_name IN ('scopes','expires_at','last_checked_at','health','disconnected_at','updated_at');
  IF cols <> 6 THEN
    RAISE EXCEPTION 'verify failed: expected 6 new columns, found %', cols;
  END IF;
  RAISE NOTICE 'verify ok: integrations can no longer claim a connection they do not have';
END $verify$;

COMMIT;
