"""Scheduler — the time-based half of the Master Orchestrator.

The rulebook reacts to events; the scheduler reacts to *time*. On each tick it
scans every active tenant for work that has come due (e.g. a lead whose
follow-up time has arrived) and emits an event, which then flows through the
same rulebook as everything else. This is what lets the orchestrator chase
follow-ups and run periodic reports without a human remembering to.
"""


class Scheduler:
    def __init__(self, persist):
        self.p = persist

    def tick(self) -> int:
        """Run one scheduling pass across all active tenants. Returns work emitted."""
        emitted = 0
        for tenant_id in self.p.list_active_tenants():
            for lead in self.p.due_followups(tenant_id):
                self.p.emit(tenant_id, "lead.followup_due",
                            {"lead_id": str(lead["id"]),
                             "conversation_id": (str(lead["conversation_id"])
                                                 if lead.get("conversation_id") else None)},
                            "scheduler")
                self.p.clear_followup(tenant_id, lead["id"])   # dedupe: don't re-fire
                emitted += 1

        # Growth Engine: fire trial expiries so TechDeva can convert-or-drop.
        if hasattr(self.p, "due_trial_expiries"):
            for t in self.p.due_trial_expiries():
                self.p.emit(t["tenant_id"], "trial.expired",
                            {"tenant_id": t["tenant_id"]}, "scheduler")
                self.p.mark_trial_flagged(t["tenant_id"])       # dedupe: don't re-fire
                emitted += 1

        # Oversight: ask the Optimizer (13) to review performance on a cadence.
        # It only ever PROPOSES; the human approval gate is unchanged. Guarded by
        # hasattr so a persistence layer without the helper simply skips this.
        if hasattr(self.p, "due_optimizations"):
            for t in self.p.due_optimizations():
                self.p.emit(t["tenant_id"], "optimization.due",
                            {"tenant_id": t["tenant_id"]}, "scheduler")
                self.p.mark_optimization_run(t["tenant_id"])    # dedupe: don't re-fire
                emitted += 1
        return emitted
