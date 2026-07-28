"""Agent 07 — Hermes (Sales AI), wired to the live bot over HTTP.

Hermes is NOT rewritten here — the live Zara/Hermes bot keeps running on the VPS
(or desktop). This adapter lets the orchestrator hand Hermes work through the
standard contract, calling it over one HTTP endpoint (see app/hermes_client.py).

Degradation policy (human-in-the-loop safe):
  - HERMES_URL unset  -> dev stub reply, pipeline flows (for local tests).
  - HERMES_URL set but unreachable -> DO NOT fabricate a customer outcome;
    return needs_human so a person picks up the conversation.
"""
from .contract import Agent, Task, Context, AgentResult
from ..hermes_client import call_hermes, HermesNotConfigured, HermesUnavailable


class HermesAgent(Agent):
    name = "hermes"

    def handles(self, task_type: str) -> bool:
        return task_type in {"qualify", "followup", "book"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        if task.type == "qualify":
            return self._qualify(task, ctx)
        if task.type == "book":
            return self._book(task, ctx)
        if task.type == "followup":
            return self._followup(task, ctx)
        return AgentResult(status="failed", result={"error": f"unhandled {task.type}"})

    # --- task handlers ---
    def _qualify(self, task: Task, ctx: Context) -> AgentResult:
        service = task.payload.get("service")
        price = self.price_of(ctx, service) if service else None
        try:
            out = self._invoke(task, ctx, extra={"service": service, "price": price})
        except HermesUnavailable as e:
            return self._degraded(e, "qualify")
        return AgentResult(
            status="done",
            result={"reply": out["reply"], "quoted_price": price, "meta": out.get("meta", {})},
            events=[{"type": "lead.qualified",
                     "payload": {"lead_id": task.payload.get("lead_id"),
                                 "service": service,
                                 "est_revenue": task.payload.get("est_revenue", 0)}}],
            context_writes={"last_service_discussed": service},
        )

    def _followup(self, task: Task, ctx: Context) -> AgentResult:
        try:
            out = self._invoke(task, ctx)
        except HermesUnavailable as e:
            return self._degraded(e, "followup")
        return AgentResult(status="done", result={"reply": out["reply"], "meta": out.get("meta", {})})

    def _book(self, task: Task, ctx: Context) -> AgentResult:
        # booking touches a real calendar -> gate on human review if tenant wants oversight
        needs_human = ctx.config.get("automation") == "oversight"
        return AgentResult(
            status="needs_human" if needs_human else "done",
            result={"proposed_slot": task.payload.get("slot")},
            events=[] if needs_human else
                   [{"type": "appointment.booked",
                     "payload": {"lead_id": task.payload.get("lead_id"),
                                 "slot": task.payload.get("slot")}}],
            human_review=needs_human,
        )

    # --- bridge to the running bot ---
    def _invoke(self, task: Task, ctx: Context, extra: dict | None = None) -> dict:
        payload = {
            "tenant_id": task.tenant_id,
            "conversation_id": task.payload.get("conversation_id"),
            "lead_id": task.payload.get("lead_id"),
            "task_type": task.type,
            "message": task.payload.get("message", ""),
            "persona": ctx.config.get("hermes_persona", ""),
            "context": {**ctx.store, **(extra or {})},
        }
        try:
            return call_hermes(payload)
        except HermesNotConfigured:
            # dev/local fallback so the pipeline + tests run without a live bot
            return {"reply": f"[hermes-stub] handled {task.type}", "meta": {"stub": True}}

    @staticmethod
    def _degraded(err: Exception, stage: str) -> AgentResult:
        # live Hermes down -> hand to a human, never invent a customer reply/outcome
        return AgentResult(status="needs_human",
                           result={"error": str(err), "stage": stage,
                                   "action": "route_to_human"},
                           human_review=True)
