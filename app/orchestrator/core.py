"""Master Orchestrator core.

Not a chatbot — a task router / state machine. Loop:
  1. read new events for a tenant
  2. for each, look up rules -> actions
  3. apply actions: queue tasks (deduped), notify, halt agents (HITL)
  4. run queued tasks through the agent contract
  5. apply each AgentResult: persist result, emit its events, write shared context,
     and if human_review is set, park the task as needs_human instead of acting.

This module is deliberately storage-light so it reads clearly; wire the marked
sections to real tables (events/tasks/context_store) from db.py in Phase B.
"""
import dataclasses

from ..agents.registry import agent_for
from ..agents.contract import Task, Context
from .rules import rules_for


class Orchestrator:
    def __init__(self, load_config, load_context, persist):
        # dependency-injected so this stays testable without a live DB
        self.load_config = load_config      # (tenant_id) -> dict
        self.load_context = load_context    # (tenant_id) -> dict
        self.persist = persist              # side-effect sink (tasks, events, ctx, notify)

    # ---- event intake ----
    def handle_event(self, tenant_id: str, event: dict):
        for action in rules_for(event):
            self._apply(tenant_id, action)

    def _apply(self, tenant_id: str, action: dict):
        if "queue_task" in action:
            self._queue(tenant_id, action["queue_task"])
        elif "pipeline" in action:
            self._queue_pipeline(tenant_id, action["pipeline"])
        elif "notify" in action:
            self.persist.notify(tenant_id, **action["notify"])
        elif "alert_techdeva" in action:
            self.persist.alert_techdeva(tenant_id, **action["alert_techdeva"])
        elif "halt_agent" in action:
            # HUMAN-IN-THE-LOOP: cancel/park all queued+running tasks for this agent+scope
            self.persist.halt_agent(tenant_id, **action["halt_agent"])
        elif "update_lead" in action:
            self.persist.update_lead(tenant_id, **action["update_lead"])

    def _queue_pipeline(self, tenant_id: str, steps: list):
        """Queue a chain of tasks where each waits for the previous to finish."""
        prev = None
        for step in steps:
            spec = dict(step)
            if prev:
                spec["depends_on"] = [prev]
            prev = self.persist.enqueue_task(tenant_id, spec)

    def _queue(self, tenant_id: str, spec: dict):
        # prevent duplicate work: don't queue an identical open task
        if self.persist.has_open_task(tenant_id, spec["agent"], spec["type"], spec.get("payload", {})):
            return
        self.persist.enqueue_task(tenant_id, spec)

    # ---- task execution ----
    def run_task(self, tenant_id: str, task_row: dict):
        agent = agent_for(task_row["type"])
        if agent is None:
            self.persist.fail_task(tenant_id, task_row["id"], f"no agent for {task_row['type']}")
            return

        payload = task_row.get("payload", {})

        # HUMAN-IN-THE-LOOP guard: if a human owns this conversation, Hermes stands
        # down at execution time — authority wins regardless of queue ordering.
        if agent.name == "hermes" and self.persist.is_conversation_human(
                tenant_id, payload.get("conversation_id")):
            self.persist.fail_task(tenant_id, task_row["id"], "human_active: hermes stood down")
            return

        ctx = Context(tenant_id=tenant_id,
                      config=self.load_config(tenant_id),
                      store=self.load_context(tenant_id))
        task = Task(id=task_row["id"], tenant_id=tenant_id,
                    agent=agent.name, type=task_row["type"],
                    payload=payload)

        res = None
        try:
            res = agent.run(task, ctx)
        except Exception as e:  # transient/agent error -> retry then dead-letter
            outcome = self.persist.retry_or_fail(tenant_id, task.id, f"exception: {e}")
            return

        # explicit failure from the agent -> retry then dead-letter
        if res.status == "failed":
            self.persist.retry_or_fail(tenant_id, task.id, str(res.result))
            return

        # Booking honesty: an agent may PROPOSE a slot as booked, but only a
        # real row in `appointments` makes it true. Verify here, once, for
        # every agent, instead of trusting a claimed appointment.booked event.
        if res.status == "done" and any(
                ev.get("type") == "appointment.booked" for ev in res.events):
            booked_ev = next(ev for ev in res.events if ev.get("type") == "appointment.booked")
            appt = self.persist.create_appointment(tenant_id, **booked_ev.get("payload", {}))
            if not appt or not appt.get("id"):
                reason = (appt or {}).get("reason", "could not confirm the slot")
                res = dataclasses.replace(
                    res, status="needs_human", human_review=True,
                    result={**(res.result or {}), "booking_failed": reason},
                    events=[ev for ev in res.events if ev.get("type") != "appointment.booked"])
            else:
                booked_ev["payload"]["appointment_id"] = appt["id"]

        # CRM honesty: mirrors the booking check above. CrmAgent used to
        # return status="done" for an update/create_lead/merge task with no
        # write ever happening — a lead.updated event is now verified
        # against a real row in `leads` before "done" is trusted.
        if res.status == "done" and any(
                ev.get("type") == "lead.updated" for ev in res.events):
            updated_ev = next(ev for ev in res.events if ev.get("type") == "lead.updated")
            p = updated_ev.get("payload", {})
            outcome = self.persist.update_lead(tenant_id, p.get("lead_id"), p.get("fields", {}))
            if not outcome or not outcome.get("id"):
                reason = (outcome or {}).get("reason", "could not confirm the update")
                res = dataclasses.replace(
                    res, status="needs_human", human_review=True,
                    result={**(res.result or {}), "update_failed": reason},
                    events=[ev for ev in res.events if ev.get("type") != "lead.updated"])

        # The policy gate. Part 4 found the human gate depended entirely on the
        # agent setting human_review on its own result: an agent that forgets
        # ships straight to the client's customers. The action's own policy is
        # asked here, so forgetting is no longer possible.
        gate = None
        if not (res.human_review or res.status == "needs_human"):
            try:
                from ..policy import decide
                gate = decide(self.persist.policy_for(tenant_id, task.type), task.type)
            except Exception as e:
                print(f"[orchestrator] policy check failed: {e}", flush=True)
                gate = None
            if gate and gate["requires_approval"]:
                self.persist.park_for_human(tenant_id, task.id, res.result)
                # An agent held by policy usually declared no deliverable -- it
                # did not think it needed one. Without a card the client sees a
                # task stuck at needs_human and nothing to decide, which is the
                # silent stall this gate exists to avoid. Make the card.
                held = res
                body = dict(res.result or {})
                if not body.get("deliverables"):
                    body["deliverables"] = [{
                        "kind": "approval",
                        "title": "%s is waiting for your approval" % task.type,
                        "body": {"action": task.type,
                                 "risk": gate["risk"],
                                 "why": gate["reason"],
                                 "agent": agent.name,
                                 "work": res.result},
                    }]
                    held = dataclasses.replace(res, result=body)
                self._save_deliverables(tenant_id, task.id, agent.name, held,
                                        status="awaiting_approval", requires_approval=True)
                for ev in res.events:
                    self.persist.emit_event(tenant_id, ev["type"], ev.get("payload", {}), agent.name)
                print(f"[orchestrator] {task.type} held for approval "
                      f"({gate['risk']}, {gate['source']}): {gate['reason']}", flush=True)
                return

        # human gate on irreversible actions -> park, but STILL emit its events
        # (e.g. an ops.alert raised while blocking must reach TechDeva)
        if res.human_review or res.status == "needs_human":
            self.persist.park_for_human(tenant_id, task.id, res.result)
            # Client portal hook: surface the parked work as an approval card so the
            # gate is something the client can actually see and decide on, instead of
            # a task quietly sitting in 'needs_human' where nobody looks.
            self._save_deliverables(tenant_id, task.id, agent.name, res,
                                    status="awaiting_approval", requires_approval=True)
            for ev in res.events:
                self.persist.emit_event(tenant_id, ev["type"], ev.get("payload", {}), agent.name)
            return

        # success: persist, write shared context, release dependents, emit follow-on events.
        # Growth Engine hook: if an agent produced a full analysis report, store it
        # durably in analysis_reports (in addition to the shared context write).
        if "persist_report" in res.result and hasattr(self.persist, "save_analysis_report"):
            pr = res.result["persist_report"]
            self.persist.save_analysis_report(
                tenant_id, signup_id=pr.get("signup_id"), status=pr.get("status", "complete"),
                report=res.result.get("report", {}), gaps=res.result.get("gaps", []))

        # Client portal hook: everything an agent produced becomes a visible
        # deliverable. Work that already passed the human gate (payload carries the
        # server-set human_approved marker) settles as 'delivered' rather than
        # creating a second card.
        if payload.get("human_approved") and hasattr(self.persist, "mark_task_deliverables_delivered"):
            self.persist.mark_task_deliverables_delivered(tenant_id, task.id)
        else:
            self._save_deliverables(tenant_id, task.id, agent.name, res, status="draft")

        self.persist.complete_task(tenant_id, task.id, res.result)
        self.persist.release_dependents(tenant_id, task.id)
        for k, v in res.context_writes.items():
            self.persist.write_context(tenant_id, k, v, agent.name)
        for ev in res.events:
            self.persist.emit_event(tenant_id, ev["type"], ev.get("payload", {}), agent.name)

    # ---- client portal ----
    def _save_deliverables(self, tenant_id, task_id, agent_name, res, status,
                           requires_approval=False):
        """Persist an agent's declared deliverables, if any. Guarded by hasattr so
        a persistence backend without the Build 2 tables still runs unchanged."""
        items = (res.result or {}).get("deliverables")
        if not items or not hasattr(self.persist, "save_deliverables"):
            return
        try:
            self.persist.save_deliverables(tenant_id, task_id, agent_name, items,
                                           status=status,
                                           requires_approval=requires_approval)
        except Exception as e:
            # a dashboard write must never roll back or fail the actual work
            print(f"[orchestrator] deliverable persist failed ({agent_name}): {e}", flush=True)

    # ---- learning loop (HITL) ----
    def record_learning(self, tenant_id, agent, signal_type, before, after, human_id, reason):
        self.persist.log_learning_signal(tenant_id, agent, signal_type,
                                          before, after, human_id, reason)
