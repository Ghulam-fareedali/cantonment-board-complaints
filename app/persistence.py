"""Persistence — the side-effect sink the orchestrator writes through.

Durable records live in Postgres (RLS-scoped per tenant). The work queue lives
in Redis: two lists, `events:queue` and `tasks:queue`, each carrying a small
JSON envelope with the tenant_id so the worker can open a correctly-scoped DB
session. Every emitted event flows back through the queue, so the whole system
is uniformly event-driven.
"""
import os
import json
import redis
from psycopg.types.json import Json
from .db import tenant_session

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
EVENTS_Q = "events:queue"
TASKS_Q = "tasks:queue"


class Persistence:
    def __init__(self):
        self.r = redis.from_url(REDIS_URL)

    def _sess(self, tenant_id):
        # worker acts within a single tenant's scope (RLS enforced)
        return tenant_session(tenant_id, "owner")

    # ---------- config + context (read side for agents) ----------
    def load_config(self, tenant_id) -> dict:
        with self._sess(tenant_id) as cur:
            cur.execute("""SELECT services, price_list, departments, booking_rules,
                                  hermes_persona, brand, geo, working_hours, automation
                           FROM tenant_config WHERE tenant_id = current_tenant()""")
            row = cur.fetchone()
            return row or {}

    def load_context(self, tenant_id) -> dict:
        with self._sess(tenant_id) as cur:
            cur.execute("SELECT key, value FROM context_store WHERE tenant_id = current_tenant()")
            ctx = {r["key"]: r["value"] for r in cur.fetchall()}
            # expose the tenant's account connections so agents can check them
            cur.execute("SELECT provider, status FROM integrations WHERE tenant_id = current_tenant()")
            ctx["integrations"] = {r["provider"]: r["status"] for r in cur.fetchall()}
            return ctx

    # ---------- events ----------
    def emit(self, tenant_id, type_, payload, emitted_by="system"):
        """Persist an event (audit log) AND push it onto the queue to be processed."""
        with self._sess(tenant_id) as cur:
            cur.execute("""INSERT INTO events (tenant_id, type, payload, emitted_by)
                           VALUES (current_tenant(), %s, %s, %s)""",
                        (type_, Json(payload), emitted_by))
        self.r.rpush(EVENTS_Q, json.dumps({"tenant_id": str(tenant_id),
                                            "type": type_, "payload": payload}))

    # alias used by the orchestrator core
    def emit_event(self, tenant_id, type_, payload, emitted_by="agent"):
        self.emit(tenant_id, type_, payload, emitted_by)

    # ---------- tasks ----------
    def has_open_task(self, tenant_id, agent, type_, payload) -> bool:
        with self._sess(tenant_id) as cur:
            cur.execute("""SELECT 1 FROM tasks
                           WHERE agent=%s AND type=%s AND status IN ('queued','running')
                             AND (payload->>'lead_id') IS NOT DISTINCT FROM %s
                           LIMIT 1""",
                        (agent, type_, payload.get("lead_id")))
            return cur.fetchone() is not None

    def enqueue_task(self, tenant_id, spec: dict):
        deps = spec.get("depends_on")
        priority = spec.get("priority", 3)
        status = "queued"
        with self._sess(tenant_id) as cur:
            if deps:  # hold as 'waiting' until every dependency is done
                cur.execute("SELECT count(*) c FROM tasks WHERE id = ANY(%s) AND status <> 'done'",
                            (deps,))
                if cur.fetchone()["c"] > 0:
                    status = "waiting"
            cur.execute("""INSERT INTO tasks (tenant_id, agent, type, payload, priority,
                                              depends_on, max_attempts, status)
                           VALUES (current_tenant(), %s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (spec["agent"], spec["type"], Json(spec.get("payload", {})),
                         priority, deps, spec.get("max_attempts", 3), status))
            tid = cur.fetchone()["id"]
        if status == "queued":
            self.r.rpush(TASKS_Q, json.dumps({"tenant_id": str(tenant_id), "task_id": str(tid)}))
        return tid

    def load_task(self, tenant_id, task_id) -> dict | None:
        """Concurrency-safe claim: only one worker wins a task. Returns None if it
        was already claimed by another worker or isn't runnable."""
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE tasks SET status='running', attempts=attempts+1, updated_at=now()
                           WHERE id=%s AND status='queued'
                           RETURNING id, agent, type, payload, attempts, max_attempts""",
                        (task_id,))
            return cur.fetchone()

    def retry_or_fail(self, tenant_id, task_id, reason):
        """Transient failure: re-queue until max_attempts, then dead-letter."""
        with self._sess(tenant_id) as cur:
            cur.execute("SELECT attempts, max_attempts FROM tasks WHERE id=%s", (task_id,))
            row = cur.fetchone()
            if row and row["attempts"] < row["max_attempts"]:
                cur.execute("""UPDATE tasks SET status='queued',
                                 result=%s, run_after=now(), updated_at=now() WHERE id=%s""",
                            (Json({"retry": reason, "attempt": row["attempts"]}), task_id))
                outcome = "retry"
            else:
                cur.execute("""UPDATE tasks SET status='dead', result=%s, updated_at=now()
                               WHERE id=%s""", (Json({"dead": reason}), task_id))
                outcome = "dead"
        if outcome == "retry":
            self.r.rpush(TASKS_Q, json.dumps({"tenant_id": str(tenant_id), "task_id": str(task_id)}))
        return outcome

    def release_dependents(self, tenant_id, done_task_id):
        """When a task finishes, promote any 'waiting' tasks whose deps are now all done."""
        released = []
        with self._sess(tenant_id) as cur:
            cur.execute("""SELECT id, depends_on FROM tasks
                           WHERE status='waiting' AND %s = ANY(depends_on)""", (done_task_id,))
            for r in cur.fetchall():
                cur.execute("SELECT count(*) c FROM tasks WHERE id = ANY(%s) AND status <> 'done'",
                            (r["depends_on"],))
                if cur.fetchone()["c"] == 0:
                    cur.execute("UPDATE tasks SET status='queued', updated_at=now() WHERE id=%s",
                                (r["id"],))
                    released.append(str(r["id"]))
        for tid in released:
            self.r.rpush(TASKS_Q, json.dumps({"tenant_id": str(tenant_id), "task_id": tid}))
        return released

    def complete_task(self, tenant_id, task_id, result):
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE tasks SET status='done', result=%s, updated_at=now()
                           WHERE id=%s""", (Json(result), task_id))

    def fail_task(self, tenant_id, task_id, reason):
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE tasks SET status='failed', result=%s, updated_at=now()
                           WHERE id=%s""", (Json({"error": reason}), task_id))

    def park_for_human(self, tenant_id, task_id, result):
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE tasks SET status='needs_human', result=%s, updated_at=now()
                           WHERE id=%s""", (Json(result), task_id))

    # ---------- context writes ----------
    def write_context(self, tenant_id, key, value, agent):
        with self._sess(tenant_id) as cur:
            cur.execute("""INSERT INTO context_store (tenant_id, key, value, updated_by_agent)
                           VALUES (current_tenant(), %s, %s, %s)
                           ON CONFLICT (tenant_id, key)
                           DO UPDATE SET value=EXCLUDED.value,
                                         updated_by_agent=EXCLUDED.updated_by_agent,
                                         updated_at=now()""",
                        (key, Json(value), agent))

    # ---------- lead + human-in-the-loop actions ----------
    def update_lead(self, tenant_id, lead_id, fields: dict):
        if not fields:
            return
        cols = ", ".join(f"{k}=%s" for k in fields)
        with self._sess(tenant_id) as cur:
            cur.execute(f"UPDATE leads SET {cols}, updated_at=now() WHERE id=%s",
                        (*fields.values(), lead_id))

    def is_conversation_human(self, tenant_id, conversation_id) -> bool:
        """True if a human has taken over this conversation (Hermes must stand down)."""
        if not conversation_id:
            return False
        with self._sess(tenant_id) as cur:
            cur.execute("SELECT handled_by, status FROM conversations WHERE id=%s",
                        (conversation_id,))
            row = cur.fetchone()
            return bool(row) and (row["handled_by"] == "human" or row["status"] == "human_active")

    def notify(self, tenant_id, role, message):
        # Phase B: record as an event; a notifier agent/channel consumes it later
        self.emit(tenant_id, "notify.sent", {"role": role, "message": message}, "orchestrator")

    def halt_agent(self, tenant_id, agent, scope: dict):
        """HUMAN-IN-THE-LOOP: cancel queued/running tasks for this agent + scope."""
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE tasks SET status='failed',
                              result='{"halted":"human_took_over"}'::jsonb, updated_at=now()
                           WHERE agent=%s AND status IN ('queued','running')""", (agent,))
            if scope.get("conversation_id"):
                cur.execute("""UPDATE conversations SET status='human_active', handled_by='human'
                               WHERE id=%s""", (scope["conversation_id"],))

    def log_learning_signal(self, tenant_id, agent, signal_type, before, after, human_id, reason):
        with self._sess(tenant_id) as cur:
            cur.execute("""INSERT INTO learning_signals
                           (tenant_id, agent, signal_type, before, after, human_id, reason)
                           VALUES (current_tenant(), %s,%s,%s,%s,%s,%s)""",
                        (agent, signal_type, Json(before), Json(after), human_id, reason))

    # ---------- scheduler support (time-based orchestration) ----------
    def list_active_tenants(self) -> list[str]:
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id FROM tenants WHERE status IN ('trial','active')")
            return [str(r["id"]) for r in cur.fetchall()]

    def due_followups(self, tenant_id) -> list[dict]:
        with self._sess(tenant_id) as cur:
            cur.execute("""SELECT l.id, c.id AS conversation_id
                           FROM leads l
                           LEFT JOIN conversations c ON c.lead_id = l.id
                           WHERE l.followup_at IS NOT NULL AND l.followup_at <= now()
                             AND l.status NOT IN ('sold','lost','fake')""")
            return cur.fetchall()

    def clear_followup(self, tenant_id, lead_id):
        with self._sess(tenant_id) as cur:
            cur.execute("UPDATE leads SET followup_at=NULL, updated_at=now() WHERE id=%s", (lead_id,))

    # ---------- observability ----------
    def alert_techdeva(self, tenant_id, provider=None, message=""):
        """Surface a problem to TechDeva (Faheem) — logged as an event the admin view reads."""
        self.emit(tenant_id, "techdeva.alert",
                  {"provider": provider, "message": message}, "orchestrator")

    def techdeva_overview(self) -> dict:
        """Cross-tenant view for TechDeva: every connected business at a glance."""
        from .db import tenant_session
        rows = []
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id, name, industry, status, plan FROM tenants ORDER BY created_at")
            tenants = cur.fetchall()
            for t in tenants:
                tid = t["id"]
                cur.execute("SELECT count(*) c FROM leads WHERE tenant_id=%s", (tid,))
                leads = cur.fetchone()["c"]
                cur.execute("SELECT status, count(*) c FROM tasks WHERE tenant_id=%s GROUP BY status", (tid,))
                tb = {r["status"]: r["c"] for r in cur.fetchall()}
                cur.execute("""SELECT count(*) c FROM events
                               WHERE tenant_id=%s AND type='techdeva.alert'
                                 AND at > now() - interval '1 day'""", (tid,))
                alerts = cur.fetchone()["c"]
                cur.execute("""SELECT provider, status FROM integrations WHERE tenant_id=%s""", (tid,))
                integ = {r["provider"]: r["status"] for r in cur.fetchall()}
                rows.append({
                    "tenant_id": str(tid), "name": t["name"], "industry": t["industry"],
                    "status": t["status"], "plan": t["plan"],
                    "leads": leads, "running": tb.get("running", 0),
                    "needs_human": tb.get("needs_human", 0), "dead": tb.get("dead", 0),
                    "alerts": alerts, "integrations": integ,
                })
        return {"tenants": rows, "count": len(rows)}

    def status_snapshot(self, tenant_id) -> dict:
        with self._sess(tenant_id) as cur:
            cur.execute("SELECT status, count(*) c FROM tasks GROUP BY status")
            by_status = {r["status"]: r["c"] for r in cur.fetchall()}
            cur.execute("SELECT agent, status, count(*) c FROM tasks GROUP BY agent, status ORDER BY agent")
            by_agent = [{"agent": r["agent"], "status": r["status"], "count": r["c"]}
                        for r in cur.fetchall()]
            cur.execute("SELECT status, count(*) c FROM leads GROUP BY status")
            leads = {r["status"]: r["c"] for r in cur.fetchall()}
            cur.execute("SELECT type, at FROM events ORDER BY at DESC LIMIT 5")
            recent = [{"type": r["type"], "at": str(r["at"])} for r in cur.fetchall()]
        return {"tasks_by_status": by_status, "tasks_by_agent": by_agent,
                "leads_by_status": leads, "recent_events": recent}
