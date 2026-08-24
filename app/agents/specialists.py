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

Build 3 moved Content (02) and Design (03) into `creative.py`, where they now
generate real copy and real SVG artwork from the analysis report instead of
returning placeholders. They are re-exported from this module so existing imports
are unaffected. Ads (06) and Publishing (05) — the irreversible pair — stay here.

Build 2 additions (client portal):
  - Every agent now declares `deliverables` in its result. The orchestrator
    persists them, which is what makes the trial dashboard show real work instead
    of an empty page. Drafts land as 'draft'; gated work lands as
    'awaiting_approval' and becomes the card the client clicks.
  - Ads/Publishing recognise `payload['human_approved']`, a marker only the server
    writes (persistence.approve_deliverable), never a client request body. That is
    the single path by which a parked task resumes.

HONESTY, unchanged and load-bearing: approval does NOT make this code able to
spend money or post publicly. The real Meta/Google execution calls are not built
yet, so an approved task reports `executed: false` and raises an ops.alert asking
for the launch integration. It never claims an ad went live. `_execute` below is
the single, clearly-marked seam where Build 3 wires the real API call.
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


def _execute(action: str, provider: str, payload: dict) -> dict:
    """THE SEAM for real irreversible execution (launch an ad set, publish a post).

    Not implemented yet — and it must not pretend to be. Returning
    executed=False keeps the platform honest: the client sees "approved, waiting
    on the live integration" rather than a fabricated "your ad is running".
    Build 3 replaces the body of this function with the real API call using the
    tenant's own OAuth token; nothing else here has to change.
    """
    return {
        "executed": False,
        "action": action,
        "provider": provider,
        "reason": "live execution integration not built yet",
        "payload": payload,
    }


# Content (02) and Design (03) used to live here as stubs. Build 3 replaced them
# with agents that produce real work grounded in the analysis report — they now
# live in app/agents/creative.py and are re-exported below so every existing
# import path (`from .specialists import ContentAgent, DesignAgent`) keeps working.
from .creative import ContentAgent, DesignAgent   # noqa: E402,F401


class AdsAgent(Agent):
    name = "ads"

    def handles(self, t): return t in {"build_campaign", "launch_campaign", "optimize"}

    def run(self, task, ctx):
        alert = _needs("meta", ctx)
        if alert:  # can't spend without the client's connected ad account
            return AgentResult(status="needs_human", human_review=True,
                               result={"blocked": "meta_not_connected"}, events=[alert])

        campaign = {"service": task.payload.get("service"),
                    "brief": task.payload.get("brief"),
                    "budget": task.payload.get("budget", "TBD")}

        # ---- post-approval path: a human already said yes (server-set marker) ----
        if task.payload.get("human_approved"):
            outcome = _execute("launch_campaign", "meta", campaign)
            events = [{"type": "campaign.approved",
                       "payload": {**campaign, "approved_by": task.payload.get("approved_by")}}]
            if not outcome["executed"]:
                events.append({"type": "ops.alert", "payload": {
                    "provider": "meta",
                    "message": "Campaign APPROVED by the client but not launched: "
                               "the Meta Ads launch integration is not built. "
                               "Launch manually or ship it."}})
            return AgentResult(status="done", result={"campaign": campaign, **outcome},
                               events=events)

        # ---- default: building a paid campaign spends money -> human approves ----
        return AgentResult(
            status="needs_human", human_review=True,
            result={
                "campaign": {**campaign, "status": "awaiting_approval"},
                "deliverables": [{
                    "kind": "campaign",
                    "title": f"Ad campaign — {campaign['service'] or 'growth'} "
                             f"(budget {campaign['budget']})",
                    "body": campaign,
                }],
            },
            events=[{"type": "campaign.drafted", "payload": task.payload}])


class PublishingAgent(Agent):
    name = "publishing"

    def handles(self, t): return t in {"schedule_posts", "publish"}

    def run(self, task, ctx):
        alert = _needs("meta", ctx)
        if alert:
            return AgentResult(status="needs_human", human_review=True,
                               result={"blocked": "channel_not_connected"}, events=[alert])

        schedule = {"posts": task.payload.get("posts", 6),
                    "service": task.payload.get("service"),
                    "brief": task.payload.get("brief")}

        if task.payload.get("human_approved"):
            outcome = _execute("schedule_posts", "meta", schedule)
            events = [{"type": "posts.approved",
                       "payload": {**schedule, "approved_by": task.payload.get("approved_by")}}]
            if not outcome["executed"]:
                events.append({"type": "ops.alert", "payload": {
                    "provider": "meta",
                    "message": "Post schedule APPROVED by the client but nothing was "
                               "published: the publishing integration is not built."}})
            return AgentResult(status="done", result={"schedule": schedule, **outcome},
                               events=events)

        return AgentResult(
            status="needs_human", human_review=True,
            result={
                "scheduled": schedule["posts"], "status": "awaiting_approval",
                "deliverables": [{
                    "kind": "post_schedule",
                    "title": f"Publishing schedule — {schedule['posts']} posts",
                    "body": schedule,
                }],
            },
            events=[{"type": "posts.scheduled", "payload": task.payload}])
