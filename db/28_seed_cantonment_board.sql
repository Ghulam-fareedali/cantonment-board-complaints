-- ============================================================================
-- 28_seed_cantonment_board.sql · Cantonment Board Demo & Production Seed
-- ============================================================================

BEGIN;

-- 1. Cantonment Board Tenant
INSERT INTO tenants (id, name, industry, status, plan, billing)
VALUES (
    '22222222-2222-2222-2222-222222222222',
    'Cantonment Board Executive Directorate',
    'cantonment_board',
    'active',
    'subscription',
    'monthly'
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    industry = EXCLUDED.industry,
    status = EXCLUDED.status;

-- 2. Tenant Config (Business Brain for Cantonment Board)
INSERT INTO tenant_config (
    tenant_id,
    services,
    departments,
    hermes_persona,
    brand,
    geo,
    automation
)
VALUES (
    '22222222-2222-2222-2222-222222222222',
    '[
        {"name": "Solid Waste Collection", "department": "Sanitation Department", "sla_hours": 24},
        {"name": "Streetlight Repair", "department": "Electrical & Streetlights", "sla_hours": 24},
        {"name": "Water Pipeline Leakage", "department": "Water Supply & Sewerage", "sla_hours": 24},
        {"name": "Road Patching & Pothole Repair", "department": "Civil Works & Engineering", "sla_hours": 24},
        {"name": "Park & Green Belt Cleanup", "department": "Horticulture & Parks", "sla_hours": 24},
        {"name": "Mosquito & Vector Spray", "department": "Public Health & Vector Control", "sla_hours": 24}
    ]'::jsonb,
    '["Sanitation Department", "Electrical & Streetlights", "Water Supply & Sewerage", "Civil Works & Engineering", "Horticulture & Parks", "Public Health & Vector Control"]'::jsonb,
    'Zara — Cantonment Board AI Assistant. You handle citizen intake via WhatsApp with utmost respect, clarity, and precision. You detect complaints, extract locations and issues, provide ticket reference numbers, and guide citizens throughout the 24-hour resolution lifecycle.',
    '{"name": "Cantonment Board", "logo_text": "CB", "accent": "#4DA6F0", "primary": "#131A2C"}'::jsonb,
    '{"city": "Lahore", "country": "Pakistan", "timezone": "Asia/Karachi"}'::jsonb,
    'full_auto'
)
ON CONFLICT (tenant_id) DO UPDATE SET
    services = EXCLUDED.services,
    departments = EXCLUDED.departments,
    hermes_persona = EXCLUDED.hermes_persona;

-- 3. Users for Control Room & Department Supervision
INSERT INTO users (id, tenant_id, name, email, phone, role)
VALUES
    ('22222222-0001-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'Executive Officer Tariq', 'admin@cantonment.gov.pk', '+923001112233', 'owner'),
    ('22222222-0001-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'Fatima Ali (Control Room)', 'controlroom@cantonment.gov.pk', '+923004445566', 'operator'),
    ('22222222-0001-0000-0000-000000000003', '22222222-2222-2222-2222-222222222222', 'Engr. Haroon (Sanitation Sup)', 'sanitation.sup@cantonment.gov.pk', '+923007778899', 'operator')
ON CONFLICT (email) DO NOTHING;

-- 4. Departments
INSERT INTO complaint_departments (id, tenant_id, name, code, description, supervisor_name, supervisor_phone, manager_name)
VALUES
    ('d1111111-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'Sanitation Department', 'SAN', 'Municipal solid waste management, daily sweeping, dead animal clearance, and dump lifting.', 'Engr. Haroon Rasheed', '+923007778899', 'Director Municipal Services'),
    ('d1111111-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'Electrical & Streetlights', 'ELEC', 'Maintenance of cantt streetlights, electrical infrastructure, transformers, and public poles.', 'Khurram Shehzad', '+923001234567', 'Executive Engineer Electrical'),
    ('d1111111-0000-0000-0000-000000000003', '22222222-2222-2222-2222-222222222222', 'Water Supply & Sewerage', 'WAT', 'Clean water distribution, burst pipeline repairs, open gutter covering, and sewerage desilting.', 'Abdul Rehman', '+923009876543', 'Executive Engineer Water Works'),
    ('d1111111-0000-0000-0000-000000000004', '22222222-2222-2222-2222-222222222222', 'Civil Works & Engineering', 'ENG', 'Road patch work, pothole filling, pavement repairs, stormwater drains, and civil repairs.', 'Sohail Abbasi', '+923005556677', 'Director Engineering & Works'),
    ('d1111111-0000-0000-0000-000000000005', '22222222-2222-2222-2222-222222222222', 'Horticulture & Parks', 'HORT', 'Pruning hazardous tree branches, park maintenance, median plantation, and park cleaning.', 'Rashid Latif', '+923003334455', 'Horticulture Officer'),
    ('d1111111-0000-0000-0000-000000000006', '22222222-2222-2222-2222-222222222222', 'Public Health & Vector Control', 'HLTH', 'Mosquito fogging, dengue surveillance, water pond chemical treatment, and hygiene control.', 'Dr. Kamran Qureshi', '+923002221100', 'Chief Health Officer')
ON CONFLICT (tenant_id, lower(code)) DO NOTHING;

-- 5. Areas
INSERT INTO complaint_areas (id, tenant_id, name, zone, department_id, supervisor_in_charge, contact_phone)
VALUES
    ('a1111111-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'Mall Road Sector', 'Commercial Zone A', 'd1111111-0000-0000-0000-000000000001', 'Engr. Haroon Rasheed', '+923007778899'),
    ('a1111111-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'Cantt Bazaar Zone', 'Commercial Zone B', 'd1111111-0000-0000-0000-000000000001', 'Engr. Haroon Rasheed', '+923007778899'),
    ('a1111111-0000-0000-0000-000000000003', '22222222-2222-2222-2222-222222222222', 'Askari 10 Sector', 'Residential Zone A', 'd1111111-0000-0000-0000-000000000003', 'Abdul Rehman', '+923009876543'),
    ('a1111111-0000-0000-0000-000000000004', '22222222-2222-2222-2222-222222222222', 'R.A. Bazaar Sector', 'Commercial Zone C', 'd1111111-0000-0000-0000-000000000004', 'Sohail Abbasi', '+923005556677'),
    ('a1111111-0000-0000-0000-000000000005', '22222222-2222-2222-2222-222222222222', 'Tufail Road Sector', 'Residential Zone B', 'd1111111-0000-0000-0000-000000000001', 'Engr. Haroon Rasheed', '+923007778899'),
    ('a1111111-0000-0000-0000-000000000006', '22222222-2222-2222-2222-222222222222', 'Saddar Zone', 'Commercial Zone D', 'd1111111-0000-0000-0000-000000000002', 'Khurram Shehzad', '+923001234567'),
    ('a1111111-0000-0000-0000-000000000007', '22222222-2222-2222-2222-222222222222', 'Cavalry Ground Sector', 'Residential Zone C', 'd1111111-0000-0000-0000-000000000005', 'Rashid Latif', '+923003334455')
ON CONFLICT (tenant_id, lower(name)) DO NOTHING;

-- 6. Field Staff
INSERT INTO complaint_staff (id, tenant_id, name, phone, whatsapp_number, email, department_id, area_id, role, active_complaints_count)
VALUES
    ('s1111111-0000-0000-0000-000000000001', '22222222-2222-2222-2222-222222222222', 'Muhammad Imran', '+923211110001', '+923211110001', 'imran.san@cantonment.gov.pk', 'd1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-000000000001', 'field_staff', 2),
    ('s1111111-0000-0000-0000-000000000002', '22222222-2222-2222-2222-222222222222', 'Tariq Mehmood', '+923211110002', '+923211110002', 'tariq.san@cantonment.gov.pk', 'd1111111-0000-0000-0000-000000000001', 'a1111111-0000-0000-0000-000000000002', 'field_staff', 1),
    ('s1111111-0000-0000-0000-000000000003', '22222222-2222-2222-2222-222222222222', 'Zahid Hussain', '+923211110003', '+923211110003', 'zahid.elec@cantonment.gov.pk', 'd1111111-0000-0000-0000-000000000002', 'a1111111-0000-0000-0000-000000000006', 'field_staff', 3),
    ('s1111111-0000-0000-0000-000000000004', '22222222-2222-2222-2222-222222222222', 'Rashid Minhas', '+923211110004', '+923211110004', 'rashid.wat@cantonment.gov.pk', 'd1111111-0000-0000-0000-000000000003', 'a1111111-0000-0000-0000-000000000003', 'field_staff', 2),
    ('s1111111-0000-0000-0000-000000000005', '22222222-2222-2222-2222-222222222222', 'Bilal Qureshi', '+923211110005', '+923211110005', 'bilal.eng@cantonment.gov.pk', 'd1111111-0000-0000-0000-000000000004', 'a1111111-0000-0000-0000-000000000004', 'field_staff', 1)
ON CONFLICT DO NOTHING;

-- 7. Categories
INSERT INTO complaint_categories (tenant_id, name, code, default_department_id, default_priority, sla_hours, description)
VALUES
    ('22222222-2222-2222-2222-222222222222', 'Sanitation & Solid Waste', 'SAN_WASTE', 'd1111111-0000-0000-0000-000000000001', 'normal', 24, 'Garbage dumping, overflowing dumpsters, commercial area litter'),
    ('22222222-2222-2222-2222-222222222222', 'Streetlights & Electrical', 'ELEC_LIGHT', 'd1111111-0000-0000-0000-000000000002', 'normal', 24, 'Broken lights, flickering lamps, open wire hazard, blackout'),
    ('22222222-2222-2222-2222-222222222222', 'Water Supply & Leakage', 'WAT_LEAK', 'd1111111-0000-0000-0000-000000000003', 'high', 24, 'Main water supply line burst, low water pressure, contaminated water'),
    ('22222222-2222-2222-2222-222222222222', 'Road Damage & Potholes', 'ENG_ROAD', 'd1111111-0000-0000-0000-000000000004', 'urgent', 24, 'Potholes damaging traffic, broken manhole cover, collapsed curb'),
    ('22222222-2222-2222-2222-222222222222', 'Horticulture & Dangerous Trees', 'HORT_TREE', 'd1111111-0000-0000-0000-000000000005', 'normal', 24, 'Fallen branches on road, overgrown bush obstructing view, park cleaning'),
    ('22222222-2222-2222-2222-222222222222', 'Fumigation & Mosquito Spray', 'HLTH_SPRAY', 'd1111111-0000-0000-0000-000000000006', 'normal', 24, 'Dengue prevention spray request, stagnant water pest control')
ON CONFLICT (tenant_id, lower(code)) DO NOTHING;

-- 8. Automation & SLA Settings
INSERT INTO complaint_automation_settings (
    tenant_id,
    auto_dispatch_threshold,
    quick_verify_threshold,
    default_sla_hours,
    warning_sla_hours,
    reminder_1_hours,
    reminder_2_hours,
    reminder_3_hours,
    zero_touch_enabled,
    google_sheets_sync_enabled,
    google_sheets_sheet_name
)
VALUES (
    '22222222-2222-2222-2222-222222222222',
    95.00,
    80.00,
    24,
    6,
    12,
    18,
    22,
    true,
    true,
    'Complaints_Live_Sync'
)
ON CONFLICT (tenant_id) DO UPDATE SET
    auto_dispatch_threshold = EXCLUDED.auto_dispatch_threshold,
    zero_touch_enabled = EXCLUDED.zero_touch_enabled;

COMMIT;
