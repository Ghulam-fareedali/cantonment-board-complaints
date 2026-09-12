"""Agent 07 - Hermes (Sales AI), wired to the live bot over HTTP.

TWO CONTROLS THIS FILE EXISTS TO ENFORCE

1. WHAT LEAVES THE PLATFORM  (_outbound_context)
   persistence.load_context puts the tenant's DECRYPTED Meta OAuth access token
   into ctx.store so the Analysis agent can read real follower numbers. Sending
   the whole store to Hermes shipped a live advertising credential to an
   external service - and into a language-model prompt - on every qualify and
   follow-up. The AES-GCM encryption in app/crypto.py protects those tokens at
   rest; it is worth nothing if the plaintext is handed out in transit. So this
   module forwards an explicit ALLOWLIST. A new context key is NOT sent until
   someone deliberately adds it here: the cost of forgetting is a bot with less
   context, not a leaked credential.

2. WHAT COMES BACK  (_screen_reply)
   Hermes is the one place in the platform where a language model's words reach
   a paying stranger, and it was the ONLY generated output no guard inspected.
   Every reply is now scanned against the tenant's own configuration. A price,
   discount or promise the config does not support parks the task and the reply
   is NOT delivered. When a customer asks about a service with no rate on file,
   the bot is passed the sentinel UNMATCHED rather than being left to fill the
   silence with a plausible number.
"""
from .contract import Agent, Task, Context, AgentResult
from .guards import unbacked_claims
from ..hermes_client import call_hermes, HermesNotConfigured, HermesUnavailable

UNMATCHED = "UNMATCHED"

# Everything Hermes may see from the shared context. Deliberately excluded:
#   meta_credentials / meta_credentials_error  a decrypted OAuth access token
#   integrations         internal connection state
#   learning             internal quality signals about our own agents
#   campaign_performance the tenant's spend and revenue figures
#   latest_analysis      scraped third-party text - untrusted input that has no
#                        place in a customer-conversation prompt
CONTEXT_ALLOWLIST = ("last_service_discussed",)

# Key-name fragments that must never appear in an outbound payload, whatever a
# future edit does to the allowlist above. Checked, not assumed.
FORBIDDEN_SUBSTRINGS = ("token", "secret", "credential", "password", "api_key",
                        "access_key", "private")


def _carry(payload: dict) -> dict:
    """Forward the attribution reference, and only when it actually exists."""
    out = {}
    for key in ("click_id", "campaign_id"):
        if payload.get(key):
            out[key] = payload[key]
    return out


def _scrub(obj, _depth: int = 0):
    """Drop any key whose name suggests a secret, at any depth.

    Belt and braces behind the allowlist: if someone later adds
    meta_credentials to CONTEXT_ALLOWLIST by mistake, this still refuses.
    """
    if _depth > 6:
        return None
    if isinstance(obj, dict):
        return {k: _scrub(v, _depth + 1) for k, v in obj.items()
                if not any(b in str(k).lower() for b in FORBIDDEN_SUBSTRINGS)}
    if isinstance(obj, list):
        return [_scrub(v, _depth + 1) for v in obj[:50]]
    return obj


def _outbound_context(ctx: Context, extra: dict | None = None) -> dict:
    """Exactly what Hermes is allowed to know. Allowlist first, then scrub."""
    store = ctx.store or {}
    safe = {k: store[k] for k in CONTEXT_ALLOWLIST if k in store}
    for k, v in (extra or {}).items():
        safe[k] = v
    return _scrub(safe)


def _screen_reply(reply: str, ctx: Context) -> list:
    """Claims in a customer-facing reply the tenant's config cannot back."""
    if not reply:
        return []
    return unbacked_claims(reply, ctx.config or {})


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

    def _qualify(self, task: Task, ctx: Context) -> AgentResult:
        service = task.payload.get("service")
        price = self.price_of(ctx, service) if service else None
        # A service we were asked about but hold no rate for. The bot is told
        # UNMATCHED rather than being left to invent a number.
        quoted = price if price is not None else (UNMATCHED if service else None)
        try:
            out = self._invoke(task, ctx, extra={"service": service, "price": quoted})
        except HermesUnavailable as e:
            return self._degraded(e, "qualify")

        reply = out.get("reply", "")
        claims = _screen_reply(reply, ctx)
        if claims:
            return self._fabrication_stop(claims, reply, "qualify", task, service)

        return AgentResult(
            status="done",
            result={"reply": reply, "quoted_price": quoted,
                    "price_matched": price is not None,
                    "meta": out.get("meta", {})},
            events=[{"type": "lead.qualified",
                     "payload": {"lead_id": task.payload.get("lead_id"),
                                 "service": service,
                                 "est_revenue": task.payload.get("est_revenue", 0),
                                 **_carry(task.payload)}}],
            context_writes={"last_service_discussed": service},
        )

    def _followup(self, task: Task, ctx: Context) -> AgentResult:
        try:
            out = self._invoke(task, ctx)
        except HermesUnavailable as e:
            return self._degraded(e, "followup")
        reply = out.get("reply", "")
        claims = _screen_reply(reply, ctx)
        if claims:
            return self._fabrication_stop(claims, reply, "followup", task, None)
        return AgentResult(status="done",
                           result={"reply": reply, "meta": out.get("meta", {})})

    def _book(self, task: Task, ctx: Context) -> AgentResult:
        # booking touches a real calendar -> gate if the tenant wants oversight
        needs_human = ctx.config.get("automation") == "oversight"
        return AgentResult(
            status="needs_human" if needs_human else "done",
            result={"proposed_slot": task.payload.get("slot")},
            events=[] if needs_human else
                   [{"type": "appointment.booked",
                     "payload": {"lead_id": task.payload.get("lead_id"),
                                 "slot": task.payload.get("slot"),
                                 "service": task.payload.get("service"),
                                 **_carry(task.payload)}}],
            human_review=needs_human,
        )

    def _invoke(self, task: Task, ctx: Context, extra: dict | None = None) -> dict:
        payload = {
            "tenant_id": task.tenant_id,
            "conversation_id": task.payload.get("conversation_id"),
            "lead_id": task.payload.get("lead_id"),
            "task_type": task.type,
            "message": task.payload.get("message", ""),
            "persona": ctx.config.get("hermes_persona", ""),
            # ALLOWLISTED. Never the whole ctx.store - that shipped the tenant's
            # decrypted Meta access token to an external service on every call.
            "context": _outbound_context(ctx, extra),
            "attribution": _carry(task.payload) or None,
        }
        try:
            return call_hermes(payload)
        except HermesNotConfigured:
            return {"reply": f"[hermes-stub] handled {task.type}", "meta": {"stub": True}}

    @staticmethod
    def _degraded(err: Exception, stage: str) -> AgentResult:
        # live Hermes down -> hand to a human, never invent a customer outcome
        return AgentResult(status="needs_human",
                           result={"error": str(err), "stage": stage,
                                   "action": "route_to_human"},
                           human_review=True)

    @staticmethod
    def _fabrication_stop(claims: list, reply: str, stage: str,
                          task: Task, service) -> AgentResult:
        """The bot stated something the tenant's configuration does not support.

        The reply is withheld, the conversation goes to a human, and TechDeva is
        told which lines were wrong. The draft is kept so the human can correct
        and send it rather than starting from nothing.
        """
        return AgentResult(
            status="needs_human", human_review=True,
            result={"blocked": "unbacked_claims", "stage": stage,
                    "claims": claims, "withheld_reply": reply,
                    "service": service, "action": "route_to_human"},
            events=[{"type": "ops.alert", "payload": {
                "provider": "hermes",
                "message": (f"Hermes reply WITHHELD on {stage} for lead "
                            f"{task.payload.get('lead_id')} - it stated facts not "
                            f"in the tenant's config: {'; '.join(claims[:5])}")}}])
