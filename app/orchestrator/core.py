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

        # human gate on irreversible actions -> park, but STILL emit its events
        # (e.g. an ops.alert raised while blocking must reach TechDeva)
        if res.human_review or res.status == "needs_human":
            self.persist.park_for_human(tenant_id, task.id, res.result)
            for ev in res.events:
                self.persist.emit_event(tenant_id, ev["type"], ev.get("payload", {}), agent.name)
            return

        # success: persist, write shared context, release dependents, emit follow-on events.
        self.persist.complete_task(tenant_id, task.id, res.result)
        self.persist.release_dependents(tenant_id, task.id)
        for k, v in res.context_writes.items():
            self.persist.write_context(tenant_id, k, v, agent.name)
        for ev in res.events:
            self.persist.emit_event(tenant_id, ev["type"], ev.get("payload", {}), agent.name)

    # ---- learning loop (HITL) ----
    def record_learning(self, tenant_id, agent, signal_type, before, after, human_id, reason):
        self.persist.log_learning_signal(tenant_id, agent, signal_type,
                                          before, after, human_id, reason)
