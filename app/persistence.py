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
from .learning import summarise as summarise_learning, WINDOW as LEARNING_WINDOW
from .crypto import decrypt_json, encrypt_json, CryptoError

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
EVENTS_Q = "events:queue"
TASKS_Q = "tasks:queue"


_TEST_EMAIL_SUFFIXES = ("@techdeva.test", "@example.com", "@example.org")


def _looks_like_test(email):
    """True when a signup is plainly a test run rather than a business.

    Deliberately narrow: an address whose shape we control, or a local part that
    says so outright. A real salon called "Test Kitchen" is not caught by this,
    and should not be. A tenant marked this way behaves normally in every respect
    except that it is left out of the TechDeva-wide counters.
    """
    e = (email or "").strip().lower()
    if not e:
        return False
    if e.endswith(_TEST_EMAIL_SUFFIXES):
        return True
    local = e.split("@", 1)[0]
    return local.startswith(("test+", "ratetest", "p3-", "ui-demo"))



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

    # Config is written by the client during onboarding. The agents already read
    # it (creative.py pulls price_list / working_hours / hermes_persona), so this
    # is the intake that was missing, not new capability.
    JSONB_CONFIG = ("services", "price_list", "departments",
                    "booking_rules", "brand", "geo", "working_hours")
    TEXT_CONFIG = ("hermes_persona", "automation")

    def save_config(self, tenant_id, patch: dict) -> dict:
        """Merge-update one tenant's configuration. Unknown keys are ignored."""
        import json as _json
        cols, vals = [], []
        for k in self.JSONB_CONFIG:
            if k in patch:
                cols.append(k)
                vals.append(_json.dumps(patch[k]))
        for k in self.TEXT_CONFIG:
            if k in patch:
                cols.append(k)
                vals.append(patch[k] if patch[k] is None else str(patch[k]))
        if not cols:
            return self.load_config(tenant_id)

        placeholders = []
        for k in cols:
            placeholders.append("%s::jsonb" if k in self.JSONB_CONFIG else "%s")
        assignments = ", ".join(f"{k} = EXCLUDED.{k}" for k in cols)
        sql = (f"INSERT INTO tenant_config (tenant_id, {', '.join(cols)}) "
               f"VALUES (current_tenant(), {', '.join(placeholders)}) "
               f"ON CONFLICT (tenant_id) DO UPDATE SET {assignments}")
        with self._sess(tenant_id) as cur:
            cur.execute(sql, vals)
        return self.load_config(tenant_id)

    def load_context(self, tenant_id) -> dict:
        with self._sess(tenant_id) as cur:
            cur.execute("SELECT key, value FROM context_store WHERE tenant_id = current_tenant()")
            ctx = {r["key"]: r["value"] for r in cur.fetchall()}
            # expose the tenant's account connections so agents can check them
            cur.execute("""SELECT provider, status, external_account, credentials
                           FROM integrations WHERE tenant_id = current_tenant()""")
            rows = cur.fetchall()
            ctx["integrations"] = {r["provider"]: r["status"] for r in rows}
            # Resolve the tenant's OWN Meta token for the Analysis agent, which reads
            # `meta_credentials` to fetch real follower/engagement numbers. Decrypted
            # here and never written back to context_store, never logged.
            for r in rows:
                if r["provider"] == "meta" and r["status"] == "connected":
                    try:
                        creds = decrypt_json(r["credentials"], tenant_id, "meta")
                    except CryptoError as e:
                        # fail closed and visibly: no token beats a wrong token
                        ctx["integrations"]["meta"] = "error"
                        ctx["meta_credentials_error"] = str(e)
                        break
                    token = (creds.get("access_token") or creds.get("token") or "")
                    if token:
                        ctx["meta_credentials"] = {
                            "access_token": token,
                            "page_id": creds.get("page_id") or r["external_account"],
                        }
            # ---- the learning loop, READ half ----
            # Every human correction ever logged, summarised per agent and handed
            # to the agents as advisory context. Empty table => empty dict => no
            # behaviour change at all. It can raise scrutiny; it can never lower
            # a human-approval gate.
            try:
                cur.execute("""SELECT agent, signal_type, reason
                               FROM learning_signals
                               WHERE tenant_id = current_tenant()
                               ORDER BY at DESC LIMIT %s""", (LEARNING_WINDOW,))
                ctx["learning"] = summarise_learning(
                    [dict(r) for r in cur.fetchall()])
            except Exception as e:
                # Never let an advisory read break the worker.
                print(f"[persistence] learning read skipped: {e}", flush=True)
                ctx["learning"] = {}

            # ---- real campaign performance for the Optimizer (13) ----
            # Real rows or nothing. A campaign with no recorded spend contributes
            # no row rather than a zero, so the optimizer cannot compute a CPL
            # out of an absence of data.
            try:
                cur.execute("""SELECT c.id, c.platform, c.status, c.name,
                                      c.spend, c.leads,
                                      (SELECT SUM(r.amount) FROM revenue_events r
                                        WHERE r.tenant_id = current_tenant()
                                          AND r.campaign_id = c.id)
                                        AS campaign_revenue
                               FROM campaigns c
                               WHERE c.tenant_id = current_tenant()
                                 AND c.status IN ('active','paused')""")
                rows = [dict(r) for r in cur.fetchall()]
                if rows:
                    # Per-campaign revenue now EXISTS (db/13_attribution.sql), so
                    # this is no longer account-wide. Revenue is the sum of
                    # revenue_events frozen to this campaign; a campaign with none
                    # reports None, never 0.
                    ctx["campaign_performance"] = {
                        "campaigns": [{
                            "id": str(r["id"]),
                            "name": r.get("name") or f"{r['platform']} campaign",
                            "platform": r["platform"],
                            "spend": float(r["spend"]) if r["spend"] is not None else None,
                            "leads": int(r["leads"]) if r["leads"] is not None else None,
                            "revenue": (float(r["campaign_revenue"])
                                        if r.get("campaign_revenue") is not None else None),
                        } for r in rows],
                        "as_of": "live",
                        "attribution": "first_touch_click",
                    }
            except Exception as e:
                print(f"[persistence] performance read skipped: {e}", flush=True)
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
            cur.execute("""UPDATE tasks SET status='running', attempts=attempts+1,
                             updated_at=now()
                           WHERE id=%s AND status='queued'
                             AND (run_after IS NULL OR run_after <= now())
                           RETURNING id, agent, type, payload, attempts, max_attempts""",
                        (task_id,))
            return cur.fetchone()

    def retry_or_fail(self, tenant_id, task_id, reason):
        """Transient failure: re-queue until max_attempts, then dead-letter."""
        with self._sess(tenant_id) as cur:
            cur.execute("SELECT attempts, max_attempts FROM tasks WHERE id=%s", (task_id,))
            row = cur.fetchone()
            if row and row["attempts"] < row["max_attempts"]:
                # run_after was written here and read by nothing, so three
                # attempts against a down Hermes burned through in milliseconds
                # and dead-lettered the task before it could recover.
                delay = min(30 * (2 ** max(0, row["attempts"] - 1)), 900)
                cur.execute("""UPDATE tasks SET status='queued', result=%s,
                                 run_after = now() + make_interval(secs => %s),
                                 updated_at=now() WHERE id=%s""",
                            (Json({"retry": reason, "attempt": row["attempts"],
                                   "retry_in_seconds": delay}), delay, task_id))
                outcome = "retry"
            else:
                cur.execute("""UPDATE tasks SET status='dead', result=%s, updated_at=now()
                               WHERE id=%s""", (Json({"dead": reason}), task_id))
                outcome = "dead"
        if outcome == "retry":
            self.r.rpush(TASKS_Q, json.dumps({"tenant_id": str(tenant_id), "task_id": str(task_id)}))
        return outcome

    STUCK_RUNNING_MINUTES = 15

    def requeue_lost_tasks(self, tenant_id) -> dict:
        """Re-queue work Redis lost. Deliberately conservative: a task is only
        reclaimed once it has been 'running' longer than any real task takes."""
        recovered, released, dead = [], [], []
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE tasks
                              SET status='queued', updated_at=now(),
                                  result = COALESCE(result,'{}'::jsonb)
                                           || '{"reaped":"worker lost this task"}'::jsonb
                            WHERE status='running'
                              AND updated_at < now() - make_interval(mins => %s)
                              AND attempts < max_attempts
                        RETURNING id""", (self.STUCK_RUNNING_MINUTES,))
            recovered = [str(r["id"]) for r in cur.fetchall()]

            cur.execute("""UPDATE tasks
                              SET status='dead', updated_at=now(),
                                  result = COALESCE(result,'{}'::jsonb)
                                           || '{"dead":"lost and out of attempts"}'::jsonb
                            WHERE status='running'
                              AND updated_at < now() - make_interval(mins => %s)
                              AND attempts >= max_attempts
                        RETURNING id""", (self.STUCK_RUNNING_MINUTES,))
            dead = [str(r["id"]) for r in cur.fetchall()]

            # 'queued' in Postgres but absent from Redis - the orphan case.
            # Re-pushing an id that IS still queued is harmless: the atomic
            # claim in load_task means only one worker ever wins it.
            cur.execute("""SELECT id FROM tasks
                            WHERE status='queued'
                              AND (run_after IS NULL OR run_after <= now())
                              AND updated_at < now() - interval '5 minutes'
                            LIMIT 200""")
            released = [str(r["id"]) for r in cur.fetchall()]

        for tid in set(recovered) | set(released):
            self.r.rpush(TASKS_Q, json.dumps({"tenant_id": str(tenant_id),
                                              "task_id": tid}))
        return {"recovered": len(recovered), "requeued": len(released),
                "dead_lettered": len(dead)}

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

    # ---------- attribution (per-campaign revenue) ----------
    def record_click(self, tenant_id, click: dict):
        """Store a click seen on a landing page, before any lead exists yet.

        Idempotent on (tenant, click_id): the same click arriving twice does not
        create a second row and cannot double-count anything downstream.
        """
        if not click.get("click_id"):
            return None
        campaign_id = self.resolve_campaign(tenant_id, click.get("ad_id"),
                                            click.get("campaign_external_id"),
                                            click.get("click_source"))
        with self._sess(tenant_id) as cur:
            cur.execute("""INSERT INTO click_map
                             (tenant_id, click_id, click_source, campaign_id,
                              ad_id, landing_url)
                           VALUES (current_tenant(), %s,%s,%s,%s,%s)
                           ON CONFLICT (tenant_id, click_id) DO UPDATE
                             SET campaign_id = COALESCE(click_map.campaign_id,
                                                        EXCLUDED.campaign_id)
                           RETURNING id""",
                        (click["click_id"], click.get("click_source"), campaign_id,
                         click.get("ad_id"), (click.get("landing_url") or "")[:2000]))
            row = cur.fetchone()
            return str(row["id"]) if row else None

    def resolve_campaign(self, tenant_id, ad_id=None, campaign_external_id=None,
                         platform=None):
        """Map a click to a campaign row, or return None.

        None is a correct and common answer. It must NEVER fall back to "the
        most recent campaign" or "the only active one" — a guessed attribution
        is worse than an absent one, because it looks like evidence.
        """
        if not (ad_id or campaign_external_id):
            return None
        with self._sess(tenant_id) as cur:
            if campaign_external_id:
                cur.execute("""SELECT id FROM campaigns
                               WHERE tenant_id = current_tenant()
                                 AND external_id = %s
                                 AND (%s IS NULL OR platform = %s)
                               LIMIT 1""",
                            (str(campaign_external_id), platform, platform))
                row = cur.fetchone()
                if row:
                    return row["id"]
            if ad_id:
                cur.execute("""SELECT id FROM campaigns
                               WHERE tenant_id = current_tenant()
                                 AND external_id = %s LIMIT 1""", (str(ad_id),))
                row = cur.fetchone()
                if row:
                    return row["id"]
        return None

    def attach_click_to_lead(self, tenant_id, lead_id, click: dict):
        """Write the click onto the lead. This is the capture step of the chain."""
        if not click.get("click_id"):
            return
        campaign_id = click.get("campaign_id")
        if campaign_id is None:
            # a click recorded earlier by the landing page may already know it
            with self._sess(tenant_id) as cur:
                cur.execute("""SELECT campaign_id, ad_id, click_source, landing_url
                               FROM click_map
                               WHERE tenant_id = current_tenant() AND click_id=%s""",
                            (click["click_id"],))
                prior = cur.fetchone()
            if prior:
                campaign_id = prior["campaign_id"]
                click.setdefault("ad_id", prior["ad_id"])
                click.setdefault("click_source", prior["click_source"])
                click.setdefault("landing_url", prior["landing_url"])
        if campaign_id is None:
            campaign_id = self.resolve_campaign(tenant_id, click.get("ad_id"),
                                                click.get("campaign_external_id"),
                                                click.get("click_source"))
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE leads
                              SET click_id=%s, click_source=%s, campaign_id=%s,
                                  ad_id=%s, landing_url=%s, attributed_at=now(),
                                  updated_at=now()
                            WHERE id=%s""",
                        (click["click_id"], click.get("click_source"), campaign_id,
                         click.get("ad_id"), (click.get("landing_url") or "")[:2000] or None,
                         lead_id))
            cur.execute("""UPDATE click_map SET matched_lead=%s
                            WHERE tenant_id = current_tenant() AND click_id=%s""",
                        (lead_id, click["click_id"]))
        return campaign_id

    def record_revenue(self, tenant_id, lead_id, amount, type_=None,
                       attributed_source="hermes", occurred_at=None):
        """Record money earned, FREEZING the lead's attribution onto the row.

        The freeze is the point. Attribution is resolved once, here, at the
        moment revenue is earned. Correcting the click->campaign mapping later
        can never silently rewrite a ROAS figure someone already acted on.
        """
        campaign_id = click_id = None
        with self._sess(tenant_id) as cur:
            if lead_id:
                cur.execute("SELECT campaign_id, click_id FROM leads WHERE id=%s",
                            (lead_id,))
                row = cur.fetchone()
                if row:
                    campaign_id, click_id = row["campaign_id"], row["click_id"]
            cur.execute("""INSERT INTO revenue_events
                             (tenant_id, lead_id, amount, type, attributed_source,
                              campaign_id, click_id, attribution_model, occurred_at)
                           VALUES (current_tenant(), %s,%s,%s,%s,%s,%s,%s,
                                   COALESCE(%s, now()))
                           RETURNING id""",
                        (lead_id, amount, type_, attributed_source, campaign_id,
                         click_id,
                         "first_touch_click" if campaign_id else None,
                         occurred_at))
            rev_id = str(cur.fetchone()["id"])
        return {"id": rev_id, "campaign_id": (str(campaign_id) if campaign_id else None),
                "attributed": campaign_id is not None}

    def attribution_rows(self, tenant_id) -> dict:
        """Real per-campaign spend, leads and revenue. Rows only, no inference."""
        with self._sess(tenant_id) as cur:
            cur.execute("""SELECT c.id, c.platform, c.external_id, c.name,
                                  c.spend,
                                  (SELECT COUNT(*) FROM leads l
                                    WHERE l.tenant_id = current_tenant()
                                      AND l.campaign_id = c.id)          AS leads,
                                  (SELECT SUM(r.amount) FROM revenue_events r
                                    WHERE r.tenant_id = current_tenant()
                                      AND r.campaign_id = c.id)          AS revenue
                             FROM campaigns c
                            WHERE c.tenant_id = current_tenant()""")
            campaigns = [dict(r) for r in cur.fetchall()]
            # Revenue that could not be placed. Reported, never redistributed.
            cur.execute("""SELECT SUM(amount) AS unattributed FROM revenue_events
                            WHERE tenant_id = current_tenant()
                              AND campaign_id IS NULL""")
            un = cur.fetchone()
            cur.execute("""SELECT SUM(spend) AS total_spend FROM campaigns
                            WHERE tenant_id = current_tenant()""")
            sp = cur.fetchone()
        return {"campaigns": campaigns,
                "unattributed_revenue": un["unattributed"] if un else None,
                "total_spend": sp["total_spend"] if sp else None}

    def upsert_campaign(self, tenant_id, platform, external_id, name=None,
                        objective=None, status="active", spend=None, leads=None):
        """Register a platform campaign so clicks have something to resolve to."""
        with self._sess(tenant_id) as cur:
            cur.execute("""INSERT INTO campaigns
                             (tenant_id, platform, external_id, name, objective,
                              status, spend, leads)
                           VALUES (current_tenant(), %s,%s,%s,%s,%s,
                                   COALESCE(%s,0), COALESCE(%s,0))
                           ON CONFLICT (tenant_id, platform, external_id)
                             WHERE external_id IS NOT NULL
                           DO UPDATE SET name=COALESCE(EXCLUDED.name, campaigns.name),
                                         status=EXCLUDED.status,
                                         spend=COALESCE(%s, campaigns.spend),
                                         leads=COALESCE(%s, campaigns.leads)
                           RETURNING id""",
                        (platform, str(external_id), name, objective, status,
                         spend, leads, spend, leads))
            return str(cur.fetchone()["id"])

    # ---------- scheduler support (time-based orchestration) ----------
    OPTIMIZATION_INTERVAL_HOURS = 24

    def due_optimizations(self) -> list[dict]:
        """Active tenants whose campaigns have not been reviewed in a day.

        Only tenants with a campaign that has ACTUALLY SPENT are returned —
        there is nothing to optimise about an account with no spend, and asking
        would produce an ops.alert for no reason.
        """
        from .db import tenant_session
        out = []
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT DISTINCT t.id AS tenant_id
                           FROM tenants t
                           JOIN campaigns c ON c.tenant_id = t.id
                           WHERE t.status IN ('trial','active')
                             AND c.status = 'active' AND c.spend > 0
                             AND NOT EXISTS (
                               SELECT 1 FROM context_store cs
                               WHERE cs.tenant_id = t.id
                                 AND cs.key = 'last_optimization_run'
                                 AND cs.updated_at > now()
                                     - make_interval(hours => %s))""",
                        (self.OPTIMIZATION_INTERVAL_HOURS,))
            out = [{"tenant_id": str(r["tenant_id"])} for r in cur.fetchall()]
        return out

    def mark_optimization_run(self, tenant_id):
        """Dedupe marker so one due-window fires exactly one optimisation."""
        self.write_context(tenant_id, "last_optimization_run",
                           {"at": "now"}, "scheduler")

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

    # ---------- Growth Engine: self-serve intake + provisioning ----------
    def create_signup(self, business_name, industry, website_url,
                      social_links, competitors, contact_email, contact_phone) -> str:
        """Record a public self-serve signup (admin-scoped intake queue)."""
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""INSERT INTO signups
                           (business_name, industry, website_url, social_links,
                            competitors, contact_email, contact_phone, status)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,'new') RETURNING id""",
                        (business_name, industry, website_url, Json(social_links or {}),
                         Json(competitors or []), contact_email, contact_phone))
            return str(cur.fetchone()["id"])

    def provision_tenant_from_signup(self, signup_id, owner_name=None,
                                     password_hash=None) -> str:
        """Turn a signup into a live TRIAL tenant: one tenants row + one
        tenant_config row (7-day trial). This is 'industry = configuration' —
        no new build, no new database. Runs as admin (tenants WITH CHECK is_admin).

        If `password_hash` is supplied, the client's own owner login is created in
        the same transaction, so they own their dashboard from the moment they sign
        up. Without it the tenant is still provisioned — they simply have no portal
        login yet (fail closed, never a default password)."""
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT business_name, industry, website_url, social_links,
                                  competitors, contact_email
                           FROM signups WHERE id=%s""", (signup_id,))
            s = cur.fetchone()
            if not s:
                raise ValueError("signup not found")
            cur.execute("""INSERT INTO tenants (name, industry, status, trial_ends_at, is_test)
                           VALUES (%s,%s,'trial', now() + interval '14 days', %s) RETURNING id""",
                        (s["business_name"], s["industry"], _looks_like_test(s.get("contact_email"))))
            tid = str(cur.fetchone()["id"])
            brand = {"website": s["website_url"], "social": s["social_links"]}
            cur.execute("""INSERT INTO tenant_config (tenant_id, brand, geo, automation)
                           VALUES (%s,%s,%s,'assisted')""",
                        (tid, Json(brand), Json({"country": "PK"})))
            cur.execute("UPDATE signups SET status='provisioned', tenant_id=%s, updated_at=now() WHERE id=%s",
                        (tid, signup_id))
            # the client's own login (Build 2). Email is the signup contact email.
            if password_hash and s["contact_email"]:
                cur.execute("""INSERT INTO users (tenant_id, name, email, role, password_hash)
                               VALUES (%s,%s,%s,'owner',%s)
                               ON CONFLICT (email) DO NOTHING""",
                            (tid, owner_name or s["business_name"],
                             s["contact_email"].strip().lower(), password_hash))
        # kick off the pipeline: analyze the client's whole presence
        self.emit(tid, "client.onboarded",
                  {"signup_id": str(signup_id),
                   "website_url": s["website_url"],
                   "social_links": s["social_links"],
                   "competitors": s["competitors"]}, "signup")
        return tid

    def save_analysis_report(self, tenant_id, signup_id, status, report, gaps):
        with self._sess(tenant_id) as cur:
            cur.execute("""INSERT INTO analysis_reports
                           (tenant_id, signup_id, status, report, gaps)
                           VALUES (current_tenant(), %s,%s,%s,%s)""",
                        (signup_id, status, Json(report), Json(gaps)))

    def latest_report(self, tenant_id) -> dict | None:
        with self._sess(tenant_id) as cur:
            cur.execute("""SELECT status, report, gaps, created_at FROM analysis_reports
                           ORDER BY created_at DESC LIMIT 1""")
            return cur.fetchone()

    def due_trial_expiries(self) -> list[dict]:
        """Tenants whose trial window has closed (for the scheduler)."""
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT id FROM tenants
                           WHERE status='trial' AND trial_ends_at IS NOT NULL
                             AND trial_ends_at <= now()""")
            return [{"tenant_id": str(r["id"])} for r in cur.fetchall()]

    def mark_trial_flagged(self, tenant_id):
        """Move an expired trial to 'paused' so it isn't re-flagged every tick."""
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE tenants SET status='paused' WHERE id=%s AND status='trial'",
                        (tenant_id,))

    # ================= Client Portal (Build 2): login, deliverables, approvals =================
    # The client's trial dashboard reads through here. Two rules shape all of it:
    #   * never show a number we did not observe (a missing report stays "pending")
    #   * approving irreversible work is the ONLY way a parked task ever resumes
    # ------------------------------------------------------------------------------------------
    def find_user_by_email(self, email: str) -> dict | None:
        """Look up a login. Runs as techdeva_admin deliberately: RLS on `users`
        keys off app.tenant_id, and at login time we do not yet know the tenant —
        an app-scoped session would correctly return zero rows and every login
        would fail as 'user not found'."""
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT id, tenant_id, name, email, role, password_hash
                           FROM users WHERE lower(email)=lower(%s)""", (email.strip(),))
            return cur.fetchone()

    def touch_login(self, user_id):
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE users SET last_login_at=now() WHERE id=%s", (user_id,))

    def set_user_password(self, email: str, password_hash: str) -> bool:
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE users SET password_hash=%s WHERE lower(email)=lower(%s)",
                        (password_hash, email.strip()))
            return cur.rowcount > 0

    # ---------- deliverables ----------
    def save_deliverables(self, tenant_id, task_id, agent, items: list,
                          status="draft", requires_approval=False) -> list:
        """Persist what an agent produced so the client can actually see it.
        ON CONFLICT DO NOTHING against the (task_id, kind) unique index: a retried
        or re-parked task must not create a duplicate approval card."""
        saved = []
        if not items:
            return saved
        with self._sess(tenant_id) as cur:
            for it in items:
                cur.execute("""INSERT INTO deliverables
                               (tenant_id, task_id, agent, kind, title, body,
                                status, requires_approval)
                               VALUES (current_tenant(), %s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT DO NOTHING
                               RETURNING id""",
                            (task_id, agent, it.get("kind", "artifact"),
                             it.get("title", it.get("kind", "artifact")),
                             Json(it.get("body", {})), status, requires_approval))
                row = cur.fetchone()
                if row:
                    saved.append(str(row["id"]))
        return saved

    def list_deliverables(self, tenant_id, limit=100) -> list:
        with self._sess(tenant_id) as cur:
            cur.execute("""SELECT id, task_id, agent, kind, title, body, status,
                                  requires_approval, approved_at, decision_reason, created_at
                           FROM deliverables ORDER BY created_at DESC LIMIT %s""", (limit,))
            return cur.fetchall()

    def get_deliverable(self, tenant_id, deliverable_id) -> dict | None:
        with self._sess(tenant_id) as cur:
            cur.execute("""SELECT id, task_id, agent, kind, title, body, status,
                                  requires_approval FROM deliverables WHERE id=%s""",
                        (deliverable_id,))
            return cur.fetchone()

    def approve_deliverable(self, tenant_id, deliverable_id, user_id) -> dict:
        """A human approved irreversible work. This is the ONE path that releases a
        task parked at the human-review gate: the approval flag is written by the
        server, into the stored task payload — it can never arrive from a client
        request body. Then the task is re-queued so the agent finishes the job.

        Returns {'approved': bool, 'resumed': bool}."""
        resumed = False
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE deliverables
                           SET status='approved', approved_by=%s, approved_at=now(),
                               updated_at=now()
                           WHERE id=%s AND status IN ('awaiting_approval','draft')
                           RETURNING task_id""", (user_id, deliverable_id))
            row = cur.fetchone()
            if not row:
                return {"approved": False, "resumed": False,
                        "reason": "not found or already decided"}
            task_id = row["task_id"]
            if task_id:
                # release the parked task, carrying the server-set approval marker
                cur.execute("""UPDATE tasks
                               SET status='queued',
                                   payload = payload || jsonb_build_object(
                                       'human_approved', true,
                                       'approved_by', %s::text),
                                   attempts=0, updated_at=now()
                               WHERE id=%s AND status='needs_human'
                               RETURNING id""", (str(user_id), task_id))
                resumed = cur.fetchone() is not None
        if resumed:
            self.r.rpush(TASKS_Q, json.dumps({"tenant_id": str(tenant_id),
                                              "task_id": str(task_id)}))
        return {"approved": True, "resumed": resumed}

    def reject_deliverable(self, tenant_id, deliverable_id, user_id, reason: str) -> dict:
        """Rejection is recorded with a reason (DB CHECK enforces that) and the
        parked task is closed as failed — never silently left hanging, never
        auto-retried into spending money."""
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE deliverables
                           SET status='rejected', decision_reason=%s,
                               approved_by=%s, updated_at=now()
                           WHERE id=%s AND status IN ('awaiting_approval','draft')
                           RETURNING task_id""", (reason, user_id, deliverable_id))
            row = cur.fetchone()
            if not row:
                return {"rejected": False, "reason": "not found or already decided"}
            if row["task_id"]:
                cur.execute("""UPDATE tasks SET status='failed', result=%s, updated_at=now()
                               WHERE id=%s AND status='needs_human'""",
                            (Json({"rejected_by_client": reason}), row["task_id"]))
        return {"rejected": True}

    def mark_task_deliverables_delivered(self, tenant_id, task_id) -> int:
        """After an approved task finally executes, flip its approved cards to
        'delivered'. Only 'approved' rows move — a draft can never skip the gate."""
        with self._sess(tenant_id) as cur:
            cur.execute("""UPDATE deliverables SET status='delivered', updated_at=now()
                           WHERE task_id=%s AND status='approved'""", (task_id,))
            return cur.rowcount

    # ---------- trial + dashboard header ----------
    def trial_status(self, tenant_id) -> dict:
        """Real trial state. Days left is computed from trial_ends_at, and is null
        (not 0, not 7) when no trial window was ever set."""
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT name, industry, status, plan, trial_ends_at,
                                  GREATEST(0, EXTRACT(EPOCH FROM (trial_ends_at - now()))/86400.0)
                                    AS days_left
                           FROM tenants WHERE id=%s""", (tenant_id,))
            row = cur.fetchone()
        if not row:
            return {}
        return {
            "name": row["name"],
            "industry": row["industry"],
            "status": row["status"],
            "plan": row["plan"],
            "trial_ends_at": str(row["trial_ends_at"]) if row["trial_ends_at"] else None,
            "days_left": (round(float(row["days_left"]), 1)
                          if row["trial_ends_at"] is not None else None),
        }

    def portal_summary(self, tenant_id) -> dict:
        """Everything the dashboard header needs, in one round trip."""
        summary = {"trial": self.trial_status(tenant_id)}
        with self._sess(tenant_id) as cur:
            cur.execute("SELECT status, count(*) c FROM deliverables GROUP BY status")
            summary["deliverables_by_status"] = {r["status"]: r["c"] for r in cur.fetchall()}
            cur.execute("SELECT status, count(*) c FROM tasks GROUP BY status")
            summary["work_by_status"] = {r["status"]: r["c"] for r in cur.fetchall()}
            cur.execute("SELECT provider, status FROM integrations")
            summary["integrations"] = {r["provider"]: r["status"] for r in cur.fetchall()}
            cur.execute("""SELECT count(*) c FROM analysis_reports""")
            summary["analysis_runs"] = cur.fetchone()["c"]
        return summary

    # ---------- observability ----------
    def alert_techdeva(self, tenant_id, provider=None, message=""):
        """Surface a problem to TechDeva (Faheem) — logged as an event the admin view reads."""
        self.emit(tenant_id, "techdeva.alert",
                  {"provider": provider, "message": message}, "orchestrator")

    def techdeva_overview(self) -> dict:
        """Every connected business at a glance - in ONE query, not 1 + 5N."""
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""
                SELECT t.id, t.name, t.industry, t.status, t.plan, t.trial_ends_at,
                       COALESCE(l.total,0) AS leads, COALESCE(l.booked,0) AS booked,
                       COALESCE(l.sold,0) AS sold,
                       COALESCE(k.running,0) AS running,
                       COALESCE(k.needs_human,0) AS needs_human,
                       COALESCE(k.dead,0) AS dead, COALESCE(k.waiting,0) AS waiting,
                       COALESCE(d.awaiting,0) AS awaiting_approval,
                       COALESCE(a.alerts,0) AS alerts,
                       r.revenue, i.integrations, e.last_event_at
                  FROM live_tenants t
                  LEFT JOIN LATERAL (SELECT count(*) AS total,
                         count(*) FILTER (WHERE status='booked') AS booked,
                         count(*) FILTER (WHERE status='sold') AS sold
                       FROM leads WHERE tenant_id=t.id) l ON true
                  LEFT JOIN LATERAL (SELECT
                         count(*) FILTER (WHERE status='running') AS running,
                         count(*) FILTER (WHERE status='needs_human') AS needs_human,
                         count(*) FILTER (WHERE status='dead') AS dead,
                         count(*) FILTER (WHERE status='waiting') AS waiting
                       FROM tasks WHERE tenant_id=t.id) k ON true
                  LEFT JOIN LATERAL (SELECT count(*) AS awaiting FROM deliverables
                       WHERE tenant_id=t.id AND status='awaiting_approval') d ON true
                  LEFT JOIN LATERAL (SELECT count(*) AS alerts FROM events
                       WHERE tenant_id=t.id AND type IN ('techdeva.alert','ops.alert')
                         AND at > now() - interval '1 day') a ON true
                  LEFT JOIN LATERAL (SELECT SUM(amount) AS revenue FROM revenue_events
                       WHERE tenant_id=t.id) r ON true
                  LEFT JOIN LATERAL (SELECT jsonb_object_agg(provider,status) AS integrations
                       FROM integrations WHERE tenant_id=t.id) i ON true
                  LEFT JOIN LATERAL (SELECT max(at) AS last_event_at FROM events
                       WHERE tenant_id=t.id) e ON true
                 ORDER BY t.created_at DESC""")
            rows = [dict(r) for r in cur.fetchall()]
        out = [{
            "tenant_id": str(r["id"]), "name": r["name"], "industry": r["industry"],
            "status": r["status"], "plan": r["plan"],
            "trial_ends_at": str(r["trial_ends_at"]) if r["trial_ends_at"] else None,
            "leads": r["leads"], "booked": r["booked"], "sold": r["sold"],
            "running": r["running"], "needs_human": r["needs_human"],
            "dead": r["dead"], "waiting": r["waiting"],
            "awaiting_approval": r["awaiting_approval"], "alerts": r["alerts"],
            # A tenant with no revenue rows recorded nothing. 0.0 is the honest
            # figure here because it is a count of rows, not a missing metric.
            "recorded_revenue": float(r["revenue"] or 0),
            "integrations": r["integrations"] or {},
            "last_event_at": str(r["last_event_at"]) if r["last_event_at"] else None,
        } for r in rows]
        return {"tenants": out, "count": len(out)}

    def techdeva_health(self) -> dict:
        """Platform-wide health for the founder view.

        Every number is counted, never estimated. A figure the platform cannot
        observe is returned as None so the page says "not measured" instead of
        drawing a zero - those are different claims.
        """
        from .db import tenant_session
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("""SELECT count(*) AS tenants,
                   count(*) FILTER (WHERE status='trial')  AS trial,
                   count(*) FILTER (WHERE status='active') AS active,
                   count(*) FILTER (WHERE status='paused') AS paused,
                   count(*) FILTER (WHERE status='trial'
                        AND trial_ends_at < now())         AS trial_expired,
                   count(*) FILTER (WHERE status='trial'
                        AND trial_ends_at BETWEEN now()
                            AND now() + interval '3 days') AS trial_ending_soon
                 FROM live_tenants""")
            t = dict(cur.fetchone())

            cur.execute("SELECT count(*) c FROM users WHERE role='techdeva_admin'")
            t["admin_users"] = cur.fetchone()["c"]

            cur.execute("""SELECT status, count(*) c FROM tasks
                           WHERE tenant_id IN (SELECT id FROM live_tenants)
                            WHERE updated_at > now() - interval '7 days'
                            GROUP BY status""")
            t["tasks_7d"] = {r["status"]: r["c"] for r in cur.fetchall()}

            cur.execute("""SELECT count(*) c FROM tasks WHERE status='running'
                            AND updated_at < now() - interval '15 minutes'""")
            t["stuck_running"] = cur.fetchone()["c"]

            cur.execute("SELECT count(*) c FROM tasks WHERE status='waiting'")
            t["tasks_waiting"] = cur.fetchone()["c"]

            cur.execute("""
                SELECT * FROM (
                  SELECT DISTINCT ON (e.tenant_id, e.payload->>'message')
                         t.name, e.type, e.payload, e.at
                    FROM events e JOIN tenants t ON t.id = e.tenant_id
                   WHERE e.type IN ('techdeva.alert','ops.alert')
                     AND e.at > now() - interval '3 days'
                   ORDER BY e.tenant_id, e.payload->>'message',
                            (e.type = 'ops.alert') DESC, e.at DESC
                ) d ORDER BY d.at DESC LIMIT 25""")
            t["recent_alerts"] = [{"tenant": r["name"], "type": r["type"],
                                   "message": (r["payload"] or {}).get("message", ""),
                                   "provider": (r["payload"] or {}).get("provider", ""),
                                   "at": str(r["at"])} for r in cur.fetchall()]

            cur.execute("SELECT count(*) c FROM deliverables WHERE status='awaiting_approval'")
            t["awaiting_approval"] = cur.fetchone()["c"]

            cur.execute("SELECT count(*) c, SUM(amount) s FROM revenue_events")
            row = cur.fetchone()
            t["revenue_events"] = row["c"]
            t["recorded_revenue"] = float(row["s"] or 0)

            # Nothing syncs ad spend yet. None means "not measured"; 0 would be a
            # claim that nothing was spent, which we cannot make.
            cur.execute("SELECT SUM(spend) s, count(*) c FROM campaigns")
            row = cur.fetchone()
            t["campaigns"] = row["c"]
            t["recorded_spend"] = float(row["s"]) if row["s"] is not None else None

        try:
            t["queue"] = {"events": self.r.llen(EVENTS_Q), "tasks": self.r.llen(TASKS_Q)}
        except Exception as e:
            t["queue"] = {"error": str(e)[:80]}
        return t

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
