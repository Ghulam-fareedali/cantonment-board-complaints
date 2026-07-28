-- ============================================================
-- Seed: Beauty Aesthetic & Lounge = TENANT #1 (the lab)
-- Run as a superuser / migration role (bypasses RLS for seeding).
-- Replace the price_list values with the real current price sheet.
-- ============================================================

-- fixed ids so the app and other seeds can reference them
\set clinic '11111111-1111-1111-1111-111111111111'

INSERT INTO tenants (id, name, industry, status, plan, billing, trial_ends_at)
VALUES (:'clinic', 'The Beauty Aesthetic & Lounge', 'aesthetic_clinic',
        'active', 'performance', 'monthly', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO tenant_config (
  tenant_id, services, price_list, departments, booking_rules,
  hermes_persona, brand, geo, working_hours, automation
) VALUES (
  :'clinic',
  '[
     {"name":"HydraFacial","department":"aesthetic","price":12000},
     {"name":"Hair PRP","department":"aesthetic","price":15000},
     {"name":"Laser Full Body","department":"aesthetic","price":25000},
     {"name":"Microblading","department":"aesthetic","price":30000}
   ]'::jsonb,
  '{"currency":"PKR","note":"REPLACE WITH REAL CURRENT PRICE SHEET"}'::jsonb,
  '["aesthetic","academy","salon"]'::jsonb,
  '{"consult":"free","deposit_required":false,"slot_minutes":60}'::jsonb,
  'Zara — warm, professional, doctor-led clinic tone. Hinglish. Qualifies, educates, books. Never gives medical guarantees.',
  '{"colors":{"blue":"#2FA8E8","green":"#5FD44A"},"logo":"techdeva-logo.png"}'::jsonb,
  '{"city":"Lahore","area":"Gulberg 3","address":"90-B-1 MM Alam Road","radius_km":25}'::jsonb,
  '{"mon_sat":"11:00-20:00","sun":"closed"}'::jsonb,
  'assisted'   -- human-in-the-loop on by default
) ON CONFLICT (tenant_id) DO NOTHING;

-- platform admin (Faheem) — tenant_id NULL, sees across all clients
INSERT INTO users (tenant_id, name, email, role)
VALUES (NULL, 'Faheem', 'faheem@techdeva.net', 'techdeva_admin')
ON CONFLICT (email) DO NOTHING;

-- clinic owner + one operator
INSERT INTO users (tenant_id, name, email, role) VALUES
  (:'clinic', 'Clinic Owner', 'owner@beautyaesthetic.pk', 'owner'),
  (:'clinic', 'Front Desk',   'desk@beautyaesthetic.pk',  'operator')
ON CONFLICT (email) DO NOTHING;

-- a couple of sample leads so the dashboard has data on first run
INSERT INTO leads (tenant_id, name, phone, service, department, status, source, priority, est_revenue, created_by)
VALUES
  (:'clinic','Ayesha','+923001234567','HydraFacial','aesthetic','qualified','hermes',2,12000,'hermes'),
  (:'clinic','Bilal','+923007654321','Hair PRP','aesthetic','new','manual',3,15000,'owner@beautyaesthetic.pk');

-- the clinic's own account connections (Meta connected, Google needs re-auth to show alerts)
INSERT INTO integrations (tenant_id, provider, status, external_account, connected_at) VALUES
  (:'clinic', 'whatsapp', 'connected', '0318 406 9727', now()),
  (:'clinic', 'meta',     'connected', 'act_beauty_aesthetic', now()),
  (:'clinic', 'instagram','connected', '@thebeautyaesthetic', now()),
  (:'clinic', 'google',   'expired',   'gbp_beauty_aesthetic', now())
ON CONFLICT (tenant_id, provider) DO NOTHING;
