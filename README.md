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
