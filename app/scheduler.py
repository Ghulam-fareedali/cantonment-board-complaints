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
        return emitted
