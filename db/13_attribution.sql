-- ============================================================
-- 13_attribution.sql — per-campaign revenue attribution
--
-- Additive, nullable, reversible, uuid-only. Re-running is safe.
-- No existing column is altered or dropped. Every new column defaults to NULL,
-- which means "not attributed" — never "attributed to nothing". That distinction
-- is the whole point of this migration and is enforced in the reporting code.
--
-- WHY THIS EXISTS
-- Spend is recorded per campaign. Revenue was recorded per lead *source* (an
-- enum: hermes/human/imported/api/manual). So ROAS could only ever be
-- account-wide: "your ads made 12x" but never "THIS ad made 12x". You cannot
-- honestly bill on profit share against a number you cannot slice.
--
-- THE CHAIN
--   ad click carries a click id (fbclid / gclid / ctwa_clid / ttclid ...)
--     -> captured on the lead at creation          (leads.click_id)
--     -> resolved to a campaign where possible     (leads.campaign_id)
--     -> FROZEN onto the revenue row when money is recorded
--                                                  (revenue_events.campaign_id)
--
-- The freeze matters: revenue is attributed at the moment it is earned, using
-- the lead's attribution as it stood then. Re-running the mapping later can
-- never silently rewrite last quarter's ROAS.
-- ============================================================

-- ---------- leads: where the click is captured ----------
ALTER TABLE leads ADD COLUMN IF NOT EXISTS click_id      text;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS click_source  text;   -- 'meta'|'google'|'tiktok'...
ALTER TABLE leads ADD COLUMN IF NOT EXISTS campaign_id   uuid REFERENCES campaigns(id) ON DELETE SET NULL;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS ad_id         text;   -- platform's ad/adset id
ALTER TABLE leads ADD COLUMN IF NOT EXISTS landing_url   text;   -- full referrer, for audit
ALTER TABLE leads ADD COLUMN IF NOT EXISTS attributed_at timestamptz;

-- ---------- revenue_events: the frozen attribution ----------
-- attributed_source (the enum) is untouched and still recorded. This is a new,
-- finer-grained column beside it, not a replacement.
ALTER TABLE revenue_events ADD COLUMN IF NOT EXISTS campaign_id      uuid REFERENCES campaigns(id) ON DELETE SET NULL;
ALTER TABLE revenue_events ADD COLUMN IF NOT EXISTS click_id         text;
ALTER TABLE revenue_events ADD COLUMN IF NOT EXISTS attribution_model text;  -- 'first_touch_click'|'manual'|null

-- ---------- campaigns: the join key back to the ad platform ----------
-- Without this there is nothing to match a click id against. external_id is the
-- platform's own campaign id (Meta/Google), so it is text, not uuid.
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS external_id text;
ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS name        text;

-- One campaign per platform id per tenant. Partial so existing rows with a NULL
-- external_id are unaffected and the migration cannot fail on legacy data.
CREATE UNIQUE INDEX IF NOT EXISTS campaigns_tenant_external_uniq
  ON campaigns (tenant_id, platform, external_id)
  WHERE external_id IS NOT NULL;

-- ---------- indexes on what is actually filtered ----------
CREATE INDEX IF NOT EXISTS leads_click_id_idx
  ON leads (tenant_id, click_id) WHERE click_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS leads_campaign_idx
  ON leads (tenant_id, campaign_id) WHERE campaign_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS revenue_campaign_idx
  ON revenue_events (tenant_id, campaign_id, occurred_at);

-- ---------- click_map: clicks seen before a lead exists ----------
-- A landing page can record a click seconds before the visitor ever messages.
-- This is the holding area so the click is not lost in between. Rows are
-- matched to a lead later by click_id.
CREATE TABLE IF NOT EXISTS click_map (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  click_id     text NOT NULL,
  click_source text,
  campaign_id  uuid REFERENCES campaigns(id) ON DELETE SET NULL,
  ad_id        text,
  landing_url  text,
  seen_at      timestamptz NOT NULL DEFAULT now(),
  matched_lead uuid REFERENCES leads(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS click_map_tenant_click_uniq
  ON click_map (tenant_id, click_id);

-- ---------- RLS: identical shape to every other tenant table ----------
ALTER TABLE click_map ENABLE ROW LEVEL SECURITY;
ALTER TABLE click_map FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY tenant_isolation ON click_map
    USING (is_admin() OR tenant_id = current_tenant())
    WITH CHECK (is_admin() OR tenant_id = current_tenant());
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON click_map TO techdeva_app;
