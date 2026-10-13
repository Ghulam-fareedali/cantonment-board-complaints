-- ============================================================================
-- 27_cantonment_complaints.sql · TechDeva Cantonment Board Module
--
-- Production schema for Cantonment Board Complaint Management & Zero-Touch Automation.
-- Follows TechDeva multi-tenant convention with strict Row-Level Security:
--   tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
--   ENABLE + FORCE ROW LEVEL SECURITY
--   POLICY tenant_isolation USING (is_admin() OR tenant_id = current_tenant())
-- ============================================================================

BEGIN;

-- 1. Cantonment Departments
CREATE TABLE IF NOT EXISTS complaint_departments (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name               text NOT NULL CHECK (length(btrim(name)) > 0),
    code               text NOT NULL CHECK (length(btrim(code)) > 0),
    description        text,
    supervisor_name    text,
    supervisor_phone   text,
    supervisor_email   text,
    manager_name       text,
    manager_phone      text,
    escalation_contact text,
    active             boolean NOT NULL DEFAULT true,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS complaint_departments_code_uniq
    ON complaint_departments (tenant_id, lower(code));
CREATE INDEX IF NOT EXISTS complaint_departments_tenant_idx
    ON complaint_departments (tenant_id, active);

-- 2. Cantonment Areas & Zones
CREATE TABLE IF NOT EXISTS complaint_areas (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                 text NOT NULL CHECK (length(btrim(name)) > 0),
    zone                 text NOT NULL DEFAULT 'General Zone',
    department_id        uuid REFERENCES complaint_departments(id) ON DELETE SET NULL,
    supervisor_in_charge text,
    contact_phone        text,
    active               boolean NOT NULL DEFAULT true,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS complaint_areas_name_uniq
    ON complaint_areas (tenant_id, lower(name));
CREATE INDEX IF NOT EXISTS complaint_areas_tenant_idx
    ON complaint_areas (tenant_id, active);

-- 3. Field Personnel & Supervisors
CREATE TABLE IF NOT EXISTS complaint_staff (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                   text NOT NULL CHECK (length(btrim(name)) > 0),
    phone                  text NOT NULL CHECK (length(btrim(phone)) > 0),
    whatsapp_number        text,
    email                  text,
    department_id          uuid NOT NULL REFERENCES complaint_departments(id) ON DELETE CASCADE,
    area_id                uuid REFERENCES complaint_areas(id) ON DELETE SET NULL,
    role                   text NOT NULL DEFAULT 'field_staff'
                           CHECK (role IN ('field_staff', 'supervisor', 'manager', 'operator')),
    active                 boolean NOT NULL DEFAULT true,
    active_complaints_count integer NOT NULL DEFAULT 0 CHECK (active_complaints_count >= 0),
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS complaint_staff_dept_idx
    ON complaint_staff (tenant_id, department_id, active);
CREATE INDEX IF NOT EXISTS complaint_staff_area_idx
    ON complaint_staff (tenant_id, area_id, active);

-- 4. Complaint Categories
CREATE TABLE IF NOT EXISTS complaint_categories (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id             uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name                  text NOT NULL CHECK (length(btrim(name)) > 0),
    code                  text NOT NULL CHECK (length(btrim(code)) > 0),
    default_department_id uuid REFERENCES complaint_departments(id) ON DELETE SET NULL,
    default_priority      text NOT NULL DEFAULT 'normal'
                          CHECK (default_priority IN ('low', 'normal', 'high', 'urgent')),
    sla_hours             integer NOT NULL DEFAULT 24 CHECK (sla_hours > 0),
    description           text,
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS complaint_categories_code_uniq
    ON complaint_categories (tenant_id, lower(code));

-- 5. Primary Complaints Table
CREATE TABLE IF NOT EXISTS complaints (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    ticket_number          text NOT NULL,
    customer_name          text NOT NULL CHECK (length(btrim(customer_name)) > 0),
    customer_phone         text NOT NULL CHECK (length(btrim(customer_phone)) > 0),
    customer_whatsapp      text,
    customer_address       text,
    raw_message            text NOT NULL CHECK (length(btrim(raw_message)) > 0),
    whatsapp_message_id    text,
    category               text NOT NULL,
    complaint_type         text,
    location_name          text NOT NULL,
    area_id                uuid REFERENCES complaint_areas(id) ON DELETE SET NULL,
    area_name              text,
    department_id          uuid REFERENCES complaint_departments(id) ON DELETE SET NULL,
    department_name        text,
    assigned_staff_id      uuid REFERENCES complaint_staff(id) ON DELETE SET NULL,
    assigned_staff_name    text,
    priority               text NOT NULL DEFAULT 'normal'
                           CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    urgency                text NOT NULL DEFAULT 'medium'
                           CHECK (urgency IN ('low', 'medium', 'high', 'critical')),
    status                 text NOT NULL DEFAULT 'NEW'
                           CHECK (status IN (
                               'NEW',
                               'AI_PROCESSING',
                               'PENDING_VERIFICATION',
                               'VERIFIED',
                               'ASSIGNED',
                               'IN_PROGRESS',
                               'PENDING_DEPARTMENT_VERIFICATION',
                               'RESOLUTION_REJECTED',
                               'RESOLVED',
                               'PENDING_CUSTOMER_CONFIRMATION',
                               'CUSTOMER_CONFIRMED',
                               'REOPENED',
                               'ESCALATED',
                               'CLOSED',
                               'CANCELLED'
                           )),
    confidence_score       numeric(5,2) NOT NULL DEFAULT 0.00 CHECK (confidence_score >= 0 AND confidence_score <= 100),
    routing_mode           text NOT NULL DEFAULT 'manual'
                           CHECK (routing_mode IN ('zero_touch', 'quick_verify', 'manual', 'operator_override')),
    ai_classification      jsonb NOT NULL DEFAULT '{}',
    duplicate_of_id        uuid REFERENCES complaints(id) ON DELETE SET NULL,
    operator_notes         text,
    rejection_reason       text,
    sla_hours              integer NOT NULL DEFAULT 24 CHECK (sla_hours > 0),
    sla_deadline           timestamptz NOT NULL,
    sla_status             text NOT NULL DEFAULT 'within_sla'
                           CHECK (sla_status IN ('within_sla', 'at_risk', 'breached')),
    received_at            timestamptz NOT NULL DEFAULT now(),
    verified_at            timestamptz,
    assigned_at            timestamptz,
    in_progress_at         timestamptz,
    resolved_at            timestamptz,
    closed_at              timestamptz,
    reopened_count         integer NOT NULL DEFAULT 0 CHECK (reopened_count >= 0),
    google_sheet_synced    boolean NOT NULL DEFAULT false,
    google_sheet_synced_at timestamptz,
    google_sheet_sync_error text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS complaints_ticket_uniq
    ON complaints (tenant_id, ticket_number);
CREATE INDEX IF NOT EXISTS complaints_tenant_status_idx
    ON complaints (tenant_id, status);
CREATE INDEX IF NOT EXISTS complaints_tenant_dept_idx
    ON complaints (tenant_id, department_id);
CREATE INDEX IF NOT EXISTS complaints_tenant_staff_idx
    ON complaints (tenant_id, assigned_staff_id);
CREATE INDEX IF NOT EXISTS complaints_tenant_sla_idx
    ON complaints (tenant_id, sla_deadline, sla_status);
CREATE INDEX IF NOT EXISTS complaints_tenant_received_idx
    ON complaints (tenant_id, received_at DESC);

-- 6. Customer Evidence (Original Photos / Media)
CREATE TABLE IF NOT EXISTS complaint_evidence (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    complaint_id    uuid NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    file_type       text NOT NULL DEFAULT 'image'
                    CHECK (file_type IN ('image', 'video', 'document', 'audio')),
    file_url        text NOT NULL CHECK (length(btrim(file_url)) > 0),
    file_name       text,
    file_size_bytes bigint,
    label           text NOT NULL DEFAULT 'CUSTOMER EVIDENCE',
    caption         text,
    uploaded_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS complaint_evidence_complaint_idx
    ON complaint_evidence (complaint_id);

-- 7. Resolutions Table (Preserves Multi-Cycle Attempts)
CREATE TABLE IF NOT EXISTS complaint_resolutions (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    complaint_id           uuid NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    cycle_number           integer NOT NULL DEFAULT 1 CHECK (cycle_number >= 1),
    resolved_by_staff_id   uuid REFERENCES complaint_staff(id) ON DELETE SET NULL,
    resolved_by_name       text NOT NULL,
    resolution_description text NOT NULL CHECK (length(btrim(resolution_description)) > 0),
    resolution_location    text,
    remarks                text,
    status                 text NOT NULL DEFAULT 'pending_verification'
                           CHECK (status IN ('pending_verification', 'approved', 'rejected')),
    submitted_at           timestamptz NOT NULL DEFAULT now(),
    verified_by_user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
    verified_by_name       text,
    verified_at            timestamptz,
    rejection_reason       text
);
CREATE INDEX IF NOT EXISTS complaint_resolutions_complaint_idx
    ON complaint_resolutions (complaint_id, cycle_number);

-- 8. Resolution Evidence (Proof Photos)
CREATE TABLE IF NOT EXISTS complaint_resolution_evidence (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    resolution_id uuid NOT NULL REFERENCES complaint_resolutions(id) ON DELETE CASCADE,
    complaint_id  uuid NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    file_type     text NOT NULL DEFAULT 'image',
    file_url      text NOT NULL CHECK (length(btrim(file_url)) > 0),
    file_name     text,
    label         text NOT NULL DEFAULT 'RESOLUTION PROOF',
    caption       text,
    uploaded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS complaint_res_evidence_res_idx
    ON complaint_resolution_evidence (resolution_id);
CREATE INDEX IF NOT EXISTS complaint_res_evidence_complaint_idx
    ON complaint_resolution_evidence (complaint_id);

-- 9. Customer Feedback Loop
CREATE TABLE IF NOT EXISTS complaint_feedback (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    complaint_id       uuid NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    resolution_id      uuid REFERENCES complaint_resolutions(id) ON DELETE SET NULL,
    customer_confirmed boolean NOT NULL,
    rating             integer CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
    feedback_text      text,
    channel            text NOT NULL DEFAULT 'whatsapp',
    submitted_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS complaint_feedback_complaint_idx
    ON complaint_feedback (complaint_id);

-- 10. Immutable Audit Timeline
CREATE TABLE IF NOT EXISTS complaint_timeline (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    complaint_id   uuid NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    actor_type     text NOT NULL
                   CHECK (actor_type IN ('system', 'ai', 'operator', 'staff', 'supervisor', 'customer')),
    actor_name     text NOT NULL,
    action         text NOT NULL,
    previous_state text,
    new_state      text,
    notes          text,
    metadata       jsonb NOT NULL DEFAULT '{}',
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS complaint_timeline_complaint_idx
    ON complaint_timeline (complaint_id, created_at ASC);

-- 11. Escalations Log
CREATE TABLE IF NOT EXISTS complaint_escalations (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    complaint_id   uuid NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    level          text NOT NULL
                   CHECK (level IN (
                       'reminder_12h',
                       'reminder_18h',
                       'final_warning_22h',
                       'breach_24h',
                       'supervisor',
                       'manager',
                       'control_room',
                       'senior_mgmt'
                   )),
    notified_to    text NOT NULL,
    channel        text NOT NULL DEFAULT 'in_app',
    action_taken   text,
    triggered_at   timestamptz NOT NULL DEFAULT now(),
    acknowledged   boolean NOT NULL DEFAULT false,
    acknowledged_at timestamptz
);
CREATE INDEX IF NOT EXISTS complaint_escalations_complaint_idx
    ON complaint_escalations (complaint_id, triggered_at DESC);

-- 12. Automation & SLA Configuration
CREATE TABLE IF NOT EXISTS complaint_automation_settings (
    tenant_id                    uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    auto_dispatch_threshold      numeric(5,2) NOT NULL DEFAULT 95.00 CHECK (auto_dispatch_threshold >= 0 AND auto_dispatch_threshold <= 100),
    quick_verify_threshold       numeric(5,2) NOT NULL DEFAULT 80.00 CHECK (quick_verify_threshold >= 0 AND quick_verify_threshold <= 100),
    default_sla_hours            integer NOT NULL DEFAULT 24 CHECK (default_sla_hours > 0),
    warning_sla_hours            integer NOT NULL DEFAULT 6 CHECK (warning_sla_hours > 0),
    reminder_1_hours             integer NOT NULL DEFAULT 12,
    reminder_2_hours             integer NOT NULL DEFAULT 18,
    reminder_3_hours             integer NOT NULL DEFAULT 22,
    zero_touch_enabled           boolean NOT NULL DEFAULT true,
    google_sheets_sync_enabled   boolean NOT NULL DEFAULT true,
    google_sheets_spreadsheet_id text,
    google_sheets_sheet_name     text DEFAULT 'Complaints_Log',
    google_sheets_webhook_url    text,
    updated_at                   timestamptz NOT NULL DEFAULT now()
);

-- 13. AI Learning Signals (Continuous Zero-Touch Improvement)
CREATE TABLE IF NOT EXISTS complaint_learning_signals (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    complaint_id         uuid NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    correction_type      text NOT NULL
                         CHECK (correction_type IN (
                             'department_override',
                             'area_override',
                             'staff_override',
                             'category_override',
                             'priority_override',
                             'reopened_after_resolution'
                         )),
    ai_prediction        jsonb NOT NULL DEFAULT '{}',
    human_correction     jsonb NOT NULL DEFAULT '{}',
    corrected_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    corrected_by_name    text,
    created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS complaint_learning_signals_tenant_idx
    ON complaint_learning_signals (tenant_id, created_at DESC);

-- 14. Google Sheets Sync Queue & Audit
CREATE TABLE IF NOT EXISTS complaint_google_sync (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    complaint_id uuid NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'synced', 'failed')),
    payload      jsonb NOT NULL DEFAULT '{}',
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    last_error   text,
    synced_at    timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS complaint_google_sync_status_idx
    ON complaint_google_sync (tenant_id, status);

-- ============================================================================
-- ROW-LEVEL SECURITY ENFORCEMENT
-- ============================================================================
DO $$
DECLARE tbl text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY[
    'complaint_departments',
    'complaint_areas',
    'complaint_staff',
    'complaint_categories',
    'complaints',
    'complaint_evidence',
    'complaint_resolutions',
    'complaint_resolution_evidence',
    'complaint_feedback',
    'complaint_timeline',
    'complaint_escalations',
    'complaint_automation_settings',
    'complaint_learning_signals',
    'complaint_google_sync'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY;', tbl);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY;', tbl);
    EXECUTE format($f$
      DROP POLICY IF EXISTS tenant_isolation ON %I;
      CREATE POLICY tenant_isolation ON %I
        USING (is_admin() OR tenant_id = current_tenant())
        WITH CHECK (is_admin() OR tenant_id = current_tenant());
    $f$, tbl, tbl);
  END LOOP;
END $$;

COMMIT;
