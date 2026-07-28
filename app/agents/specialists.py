"""Specialist agents 02/03/05/06 — the workforce the orchestrator drives when a
client asks Hermes for a campaign or content.

Design intent:
  - Content (02) and Design (03) produce drafts -> safe to run autonomously.
  - Ads (06) spends money and Publishing (05) posts publicly -> IRREVERSIBLE, so
    they declare human_review=True. The orchestrator parks them as needs_human
    until TechDeva / the client approves. This is the money+publish safety gate.
  - Each agent that needs a client account checks the tenant's `integrations`.
    If the connection is missing/expired it raises an ops.alert instead of failing
    silently — that alert is what surfaces to TechDeva to "correct it".

These are stubs for the pipeline: they return structured drafts/plans. Wiring the
real work (Meta/Google MCP calls with the tenant's own credentials) is the next
build; the contract and routing here are final.
"""
from .contract import Agent, Task, Context, AgentResult


def _needs(provider, ctx: Context):
    """Return an ops.alert event if the tenant's integration isn't usable."""
    integ = (ctx.store.get("integrations") or {}).get(provider)
    if integ != "connected":
        return {"type": "ops.alert",
                "payload": {"provider": provider, "state": integ or "missing",
                            "message": f"{provider} not connected — client action needed"}}
    return None


class ContentAgent(Agent):
    name = "content"
    def handles(self, t): return t in {"plan_content", "campaign_ideas", "write_copy"}
    def run(self, task, ctx):
        service = task.payload.get("service", "the service")
        return AgentResult(status="done",
            result={"plan": f"Content plan for {service}: 3 reels, 2 carousels, 1 offer"},
            events=[{"type": "content.ready", "payload": task.payload}],
            context_writes={"current_content_plan": {"service": service}})


class DesignAgent(Agent):
    name = "design"
    def handles(self, t): return t in {"make_creative", "flyer", "carousel"}
    def run(self, task, ctx):
        return AgentResult(status="done",
            result={"creatives": ["hero.jpg", "carousel_1.jpg", "story.jpg"]},
            events=[{"type": "creative.ready", "payload": task.payload}])


class AdsAgent(Agent):
    name = "ads"
    def handles(self, t): return t in {"build_campaign", "launch_campaign", "optimize"}
    def run(self, task, ctx):
        alert = _needs("meta", ctx)
        if alert:  # can't spend without the client's connected ad account
            return AgentResult(status="needs_human", human_review=True,
                result={"blocked": "meta_not_connected"}, events=[alert])
        # building a paid campaign spends money -> require human approval before launch
        return AgentResult(status="needs_human", human_review=True,
            result={"campaign": {"service": task.payload.get("service"),
                                 "budget": task.payload.get("budget", "TBD"),
                                 "status": "awaiting_approval"}},
            events=[{"type": "campaign.drafted", "payload": task.payload}])


class PublishingAgent(Agent):
    name = "publishing"
    def handles(self, t): return t in {"schedule_posts", "publish"}
    def run(self, task, ctx):
        alert = _needs("meta", ctx)
        if alert:
            return AgentResult(status="needs_human", human_review=True,
                result={"blocked": "channel_not_connected"}, events=[alert])
        # posting publicly to the client's accounts -> human approves the schedule
        return AgentResult(status="needs_human", human_review=True,
            result={"scheduled": task.payload.get("posts", 6), "status": "awaiting_approval"},
            events=[{"type": "posts.scheduled", "payload": task.payload}])
