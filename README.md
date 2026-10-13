# TechDeva AIBOS — Platform (Phases A + B)

The foundational "under one roof" layer, running:
- **Phase A** — multi-tenant Postgres core with Row-Level Security; Beauty Aesthetic seeded as tenant #1.
- **Phase B** — the Master Orchestrator is **alive**: Redis-backed worker consumes events, applies the rulebook, and dispatches agent tasks through the Agent Contract. No human in the loop unless a human steps in.

> Golden Rule: **AI executes. Humans supervise. AI learns. Humans decide.**

## What runs today (verified end-to-end)
**1. Autonomous pipeline** — create one lead, the system runs itself:
```
lead.created → hermes.qualify → lead.qualified
   → notify.sent (🔥 hot-lead alert)
   → hermes.book → appointment.booked
       → lead moved to BOOKED + crm.update + reporting.refresh
```
8 queue items, zero human action.

**2. Human-in-the-loop halt** — `human.took_over` makes Hermes stand down:
control events get queue priority AND a run-time guard checks the conversation,
so Hermes never sends a duplicate once a human owns the chat. Task is halted,
conversation flips to `human_active`.

## Layout
```
db/
  00_role.sql · schema.sql · seed_beauty_aesthetic.sql
app/
  db.py              # per-request tenant/role RLS session
  persistence.py     # Postgres (durable) + Redis (queue) side-effect sink
  worker.py          # consumes events+tasks, drives the orchestrator
  main.py            # FastAPI: /health /leads /events /tasks
  agents/
    contract.py      # THE Agent Contract
    hermes.py        # Agent 07 (wraps the live bot)
    registry.py      # Agent 08 CRM, 09 Reporting + lookup
  orchestrator/
    rules.py         # the rulebook (the first 5 rules)
    core.py          # event handling, task dispatch, HITL guard
docker-compose.yml   # db + redis + api + worker
```

## Run
```bash
cp .env.example .env
docker compose up --build
# API http://localhost:8000 ; the worker starts automatically
# create a lead and watch /tasks fill in on its own:
curl -X POST localhost:8000/leads -H 'X-Tenant-Id: 11111111-1111-1111-1111-111111111111' \
     -H 'X-Role: owner' -H 'Content-Type: application/json' \
     -d '{"name":"Sana","service":"HydraFacial","source":"hermes","est_revenue":12000}'
curl localhost:8000/tasks -H 'X-Tenant-Id: 11111111-1111-1111-1111-111111111111' -H 'X-Role: owner'
```

## Rules that already replace manual coordination (orchestrator/rules.py)
1. `lead.created` → Hermes qualifies
2. `lead.qualified` → book + hot-lead alert to owner
3. `human.took_over` → **halt Hermes** (human authority)
4. `appointment.booked` → move lead + CRM + reporting
5. `revenue.recorded` → mark sold + reporting

Add a rule = automate one more thing you do by hand. That is how you get replaced as orchestrator.

## Important
- App connects as `techdeva_app` (non-superuser) so RLS is enforced.
- Replace `price_list` in the seed with the real current price sheet.
- Auth is stubbed (X-Tenant-Id / X-Role); RLS enforces isolation regardless.

## The Master Orchestrator (what it now does)
Beyond the event rulebook, the orchestrator is a full coordinator:
- **Event rules** (`orchestrator/rules.py`) — react to `lead.created`, `human.took_over`, etc.
- **Scheduler** (`scheduler.py`) — time-based: scans active tenants each tick and fires due
  follow-ups (`lead.followup_due` → Hermes follows up) with no human reminder.
- **Dependency sequencing** — a task with `depends_on` is held as `waiting` and released only when
  all its dependencies are `done` (e.g. design → publish).
- **Retries + dead-letter** — a failing task is retried up to `max_attempts`, then quarantined as
  `dead` (never silently lost).
- **Concurrency-safe** — tasks are claimed atomically, so you can run many workers without any task
  running twice.
- **Observability** — `GET /orchestrator/status` returns tasks-by-status, tasks-by-agent,
  leads-by-status, and recent events for a tenant.
- **HITL** — human takeover halts Hermes (event priority + run-time guard).

All six verified end-to-end via the local test recipe.

## Phase C — wiring the live Hermes (HTTP adapter) ✅ built
Agent 07 talks to the live bot over ONE HTTP endpoint (`app/hermes_client.py`). Hermes only has to
expose:
```
POST {HERMES_URL}{HERMES_REPLY_PATH}     # default path /reply
request : { tenant_id, conversation_id, lead_id, task_type,
            message, persona, context:{service, price, ...} }
response: { "reply": "<text>", "meta": { ... optional ... } }
```
Config via env (see `.env.example`): `HERMES_URL`, `HERMES_REPLY_PATH`, `HERMES_API_KEY`,
`HERMES_TIMEOUT`, `HERMES_RETRIES`.

Behavior, verified:
- `HERMES_URL` **unset** → dev stub reply (pipeline + tests run with no bot).
- `HERMES_URL` **set + reachable** → Hermes' real reply is used; price/context passed in.
- `HERMES_URL` **set + unreachable** → the lead is **parked for a human** (`needs_human`), never a
  fabricated reply. Human-in-the-loop safe.

## Next
- Point `HERMES_URL` at the real bot on the VPS/desktop.
- Make CRM + Reporting compute real ROAS/CPL/bookings from `revenue_events` + `campaigns`.
- Phase D: onboard a 2nd tenant with only a `tenant_config` row — the moment TechDeva becomes a product.

---

# Cantonment Board — Complaints & Zero-Touch Resolution Automation

A dedicated civic operations engine for Cantonment Boards, featuring automated citizen WhatsApp intake, multimodal Vision AI analysis, field staff photographic resolution proof, 24-hour SLA countdown with automated escalations, and 19-column Google Sheets synchronization.

## Key Features

1. **Autonomous Zero-Touch Dispatch & Routing**:
   - Ingests citizen complaints via WhatsApp or manual phone/walk-in counters.
   - Grounded rulebook routes tickets to municipal departments (Sanitation, Electrical, Water Supply, Road Engineering, Horticulture, Fumigation) based on configured keywords, zones, and field personnel without hallucinating departments.
   - When AI confidence exceeds 95% and no ambiguity exists, tickets auto-dispatch directly to field staff (`zero_touch`).

2. **Multimodal Vision AI Evidence Inspection**:
   - Analyzes incoming citizen photos via Google Gemini (`gemini-2.5-flash`), OpenAI (`gpt-4o`), or Anthropic Claude (`claude-sonnet-4-5`).
   - Automatically detects visible issues, assesses severity, flags immediate civic/safety hazards (e.g., exposed high-voltage wiring, burst drinking mains), and elevates priority/urgency.
   - Strict prompt-injection guards and fail-closed fallback: If API keys are unset or network fails, the system marks vision inspection as `unavailable` without halting or fabricating visual observations.
   - Clear distinction in the UI between AI observations and human-verified facts.

3. **Secure Photo Upload & Evidence Storage (`app/storage.py`)**:
   - Multi-layer file validation: File extension check, MIME type sniffing, and magic-bytes inspection (JPEG, PNG, WebP, GIF).
   - Strict 10 MB per-file limit (`MAX_UPLOAD_SIZE_BYTES`).
   - Safe UUID-based filename generation with path-traversal prevention.
   - SSRF protection (`safe_http`) prevents malicious external URLs when ingesting inbound WhatsApp media.
   - Dedicated endpoints: `POST /complaints/upload` and streaming `GET /complaints/media/{filename}`.

4. **Field Staff Resolution Proof & Human Oversight**:
   - Field personnel must upload photographic completion proof (`RESOLUTION PROOF`) before marking work completed.
   - Visual audit trail: Before/After comparison displays citizen evidence side-by-side with field staff completion photos.
   - Department supervisors verify resolution proof before citizen notification. Rejections automatically revert work to field staff with mandatory reason logs.
   - Citizen WhatsApp feedback closure: If citizen replies "NOT RESOLVED", ticket is automatically reopened without losing prior resolution history.

5. **24-Hour SLA Accountability Engine**:
   - Real-time countdown timer tracking each complaint against its statutory deadline.
   - Three SLA phases: `within_sla`, `at_risk` (less than 6 hours remaining), and `breached`.
   - Timed alerts at 12h, 18h, and 24h breach with automated escalation to the Executive Directorate.

6. **19-Column Google Sheets Auto-Sync**:
   - Real-time append/update across 19 standard municipal tracking columns upon complaint creation, department verification, and citizen closure.
   - Resilient retry queue with manual export fallback (`GET /complaints/google-sync/export`).

## Control Room Cockpit (`web/control_room.html`)

Access the Control Room UI at:
```
http://localhost:8000/control-room
```

Features included in the Cockpit:
- **Real-Time KPI Cards**: Total tickets, verification queues, in-progress tasks, SLA risks/breaches, and zero-touch automation rate.
- **Role Switcher**: Toggle between `Control Room Operator`, `Field Staff Duty Mode`, and `Sanitation Supervisor`.
- **Photo Upload Controls**: Drag-and-drop & camera capture controls with instant thumbnail preview, progress bar, and validation alerts (replaces manual URL text entry).
- **Slide-Over Detail Drawer**: Complete ticket timeline, Before/After photo comparison, and AI Visual Inspection Card with hazard badges.
- **WhatsApp Citizen Simulator**: Interactive simulator with realistic civic scenarios (Sanitation, Electrical, Water, Pothole) to test zero-touch classification and vision inspection.

## Architecture & Directory Layout

```
app/
  complaints.py          # REST API endpoints (/complaints, /upload, /media, /whatsapp/intake)
  complaint_ai.py        # Multimodal VisionInspector + text classification & routing
  complaints_store.py    # Resilient memory & PostgreSQL data store with RLS isolation
  complaint_state.py     # State machine transition rules & lifecycle guards
  complaint_sla.py       # 24-hour SLA countdown, reminder checks, and escalation triggers
  google_sheets_sync.py  # 19-column Google Sheets integration engine
  storage.py             # File storage abstraction, MIME validation, SSRF guards
db/
  27_cantonment_complaints.sql   # PostgreSQL table definitions with RLS policies
  28_seed_cantonment_board.sql   # Cantonment Board departments, areas, and staff seed
web/
  control_room.html      # Responsive Control Room Cockpit UI
tests/
  test_cantonment_complaints.py        # Workflow, SLA, and state machine tests
  test_complaints_api.py               # REST API HTTP integration tests
  test_complaints_evidence_upload.py   # Upload validation, storage, and vision AI tests
```

## API Reference

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/complaints/upload` | `POST` | Upload evidence photo (multipart/form-data, max 10MB) |
| `/complaints/media/{filename}` | `GET` | Stream stored evidence image with safe MIME headers |
| `/complaints/whatsapp/intake` | `POST` | Citizen WhatsApp intake with text, GPS, and media URLs |
| `/complaints` | `GET` | Filter complaints by department, area, priority, search |
| `/complaints` | `POST` | Register manual complaint ticket with photo upload |
| `/complaints/{id}` | `GET` | Full complaint details with Before/After pairs & timeline |
| `/complaints/{id}/verify` | `POST` | Operator verification & staff assignment |
| `/complaints/{id}/start` | `POST` | Transition ticket to `IN_PROGRESS` |
| `/complaints/{id}/resolve` | `POST` | Submit resolution description + mandatory proof photo |
| `/complaints/{id}/department-verify` | `POST` | Supervisor approval or rejection of resolution proof |
| `/complaints/{id}/customer-feedback` | `POST` | Citizen WhatsApp reply (confirm close or reopen) |
| `/complaints/{id}/evidence` | `POST` | Attach supplemental operator/citizen evidence |
| `/complaints/tick-sla` | `POST` | Scan active complaints and trigger due SLA escalations |
| `/complaints/summary` | `GET` | Live dashboard metrics and zero-touch percentage |
| `/complaints/google-sync/export` | `GET` | Export all complaints to municipal CSV format |

## Running the Automated Test Suite

Run the full 36-test suite:
```bash
python -m pytest tests/test_cantonment_complaints.py tests/test_complaints_api.py tests/test_complaints_evidence_upload.py -v
```
All 36 tests pass out of the box with zero external dependencies.

## Configuration (.env)

```env
# Evidence File Storage
STORAGE_BACKEND=local
STORAGE_DIR=uploads
MAX_UPLOAD_SIZE_BYTES=10485760

# Multimodal Vision AI (gemini, openai, anthropic, or none)
VISION_PROVIDER=gemini
VISION_MODEL=gemini-2.5-flash
GEMINI_API_KEY=your-gemini-api-key
# OPENAI_API_KEY=your-openai-api-key
# ANTHROPIC_API_KEY=your-anthropic-api-key

# Inbound Citizen WhatsApp Integration
# WHATSAPP_ACCESS_TOKEN=your-whatsapp-access-token
# WHATSAPP_PHONE_NUMBER_ID=your-whatsapp-phone-number-id
```

