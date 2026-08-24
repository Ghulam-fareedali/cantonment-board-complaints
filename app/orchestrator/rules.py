"""The orchestrator RULEBOOK.

Each thing Faheem does manually today is ONE rule: when an event fires, queue a
task (or notify). Start with these 5, add more over time. This is literally how
he replaces himself — one rule at a time.

A rule maps an event.type -> a list of actions. Actions are plain dicts the
orchestrator core knows how to apply:
    {"queue_task": {"agent","type","payload"}}
    {"notify":     {"role","message"}}
    {"halt_agent": {"agent","scope"}}          # human-in-the-loop
    {"update_lead":{"lead_id","fields"}}
"""

HOT_LEAD_MIN_REVENUE = 12000  # PKR — tune per tenant later via config


def rules_for(event: dict) -> list[dict]:
    et = event["type"]
    p = event.get("payload", {})

    # 1. New lead -> Hermes qualifies (no human action)
    if et == "lead.created":
        return [{"queue_task": {"agent": "hermes", "type": "qualify",
                                "payload": {"lead_id": p.get("lead_id"),
                                            "service": p.get("service"),
                                            "est_revenue": p.get("est_revenue", 0)}}}]

    # 2. Qualified + valuable -> alert the owner (hot lead), and move toward booking
    if et == "lead.qualified":
        actions = [{"queue_task": {"agent": "hermes", "type": "book",
                                   "payload": {"lead_id": p.get("lead_id")}}}]
        if p.get("est_revenue", 0) >= HOT_LEAD_MIN_REVENUE:
            actions.append({"notify": {"role": "owner",
                                       "message": f"🔥 Hot lead {p.get('lead_id')}"}})
        return actions

    # 3. Human takes over a conversation -> HALT Hermes immediately (human authority)
    if et == "human.took_over":
        return [{"halt_agent": {"agent": "hermes",
                                "scope": {"conversation_id": p.get("conversation_id")}}}]

    # 3b. A follow-up came due (from the scheduler) -> Hermes follows up
    if et == "lead.followup_due":
        return [{"queue_task": {"agent": "hermes", "type": "followup",
                                "payload": {"lead_id": p.get("lead_id"),
                                            "conversation_id": p.get("conversation_id")}}}]

    # 4. Appointment booked -> move lead, CRM update + reporting refresh
    if et == "appointment.booked":
        return [
            {"update_lead": {"lead_id": p.get("lead_id"), "fields": {"status": "booked"}}},
            {"queue_task": {"agent": "crm", "type": "update",
                            "payload": {"lead_id": p.get("lead_id"), "status": "booked"}}},
            {"queue_task": {"agent": "reporting", "type": "refresh", "payload": {}}},
        ]

    # 5. Revenue recorded -> mark sold + refresh reports.
    #    The revenue row already froze its own campaign attribution at write
    #    time; the refresh just recomputes the per-campaign report from rows.
    if et == "revenue.recorded":
        return [
            {"update_lead": {"lead_id": p.get("lead_id"), "fields": {"status": "sold"}}},
            {"queue_task": {"agent": "reporting", "type": "refresh",
                            "payload": {"campaign_id": p.get("campaign_id")}}},
        ]

    # 6. A client asked Hermes for work -> orchestrator routes to the specialist agents.
    #    Hermes captured the intent; it does NOT do the work itself.
    if et == "client.requested":
        intent = p.get("intent")
        base = {"service": p.get("service"), "brief": p.get("brief"),
                "budget": p.get("budget")}
        if intent in ("campaign", "ads"):
            # sequenced: content -> design -> ads -> publishing (each waits for the last)
            return [{"pipeline": [
                {"agent": "content",    "type": "plan_content",   "payload": base},
                {"agent": "design",     "type": "make_creative",  "payload": base},
                {"agent": "ads",        "type": "build_campaign", "payload": base},
                {"agent": "publishing", "type": "schedule_posts", "payload": base},
            ]}]
        if intent in ("content", "post"):
            return [{"pipeline": [
                {"agent": "content",    "type": "plan_content",  "payload": base},
                {"agent": "design",     "type": "make_creative", "payload": base},
                {"agent": "publishing", "type": "schedule_posts","payload": base},
            ]}]
        # anything conversational -> Hermes handles the query
        return [{"queue_task": {"agent": "hermes", "type": "followup",
                                "payload": {"lead_id": p.get("lead_id"),
                                            "conversation_id": p.get("conversation_id"),
                                            "message": p.get("brief")}}}]

    # 7. Any agent (or Hermes) flags a problem -> tell TechDeva to correct it.
    if et == "ops.alert":
        return [{"alert_techdeva": {"provider": p.get("provider"),
                                    "message": p.get("message", "operational issue")}}]

    # ---- Growth Engine (self-serve SaaS) rules ----

    # 8. A new client finished self-serve signup -> analyze their whole presence.
    #    One task; the Analysis agent fetches website + social + competitors + ads
    #    and produces the single combined report. No human action.
    if et == "client.onboarded":
        return [{"queue_task": {"agent": "analysis", "type": "analyze_presence",
                                "payload": {"signup_id": p.get("signup_id"),
                                            "website_url": p.get("website_url"),
                                            "social_links": p.get("social_links", {}),
                                            "competitors": p.get("competitors", [])}}}]

    # 9. Analysis report is ready -> hand it to the GrowthPlanner, which turns it
    #    into a growth campaign via the existing content->design->ads->publishing
    #    pipeline (ads/publishing still gated for human approval).
    if et == "analysis.completed":
        return [{"queue_task": {"agent": "growth_planner", "type": "plan_growth",
                                "payload": {"signup_id": p.get("signup_id")}}}]

    # ---- Oversight rules (Quality Control 12 + Campaign Optimizer 13) ----

    # 11. Content or design finished -> Quality Control reviews the actual artefact
    #     BEFORE it can reach the client or feed a paid campaign. QC blocks on
    #     fabricated facts, unsafe artwork, placeholders and brand violations.
    if et in ("content.ready", "creative.ready"):
        return [{"queue_task": {"agent": "quality", "type": "quality_check",
                                "payload": {"produced_by": p.get("produced_by"),
                                            "subject": p.get("output", {}),
                                            "service": p.get("service")}}}]

    # 12. QC blocked something -> tell TechDeva. The task is already parked; this
    #     makes the failure visible instead of silent.
    if et == "quality.blocked":
        return [{"alert_techdeva": {"provider": "quality",
                                    "message": f"Quality check BLOCKED output from "
                                               f"{p.get('produced_by')} — "
                                               f"{len(p.get('findings', []))} blocking issue(s)."}}]

    # 13. A campaign went live, or the scheduler says a review is due -> the
    #     Optimizer reads real performance and PROPOSES changes. It never executes.
    if et in ("campaign.approved", "optimization.due"):
        return [{"queue_task": {"agent": "optimizer", "type": "optimize_campaign",
                                "payload": {"campaign_id": p.get("campaign_id"),
                                            "performance": p.get("performance")}}}]

    # 14. Optimizer proposed budget changes -> money moves, so a human decides.
    #     The task is already parked at the gate; this surfaces it.
    if et == "optimization.proposed":
        return [{"notify": {"role": "owner",
                            "message": f"{p.get('changes_proposed')} budget change(s) "
                                       f"proposed — your approval needed"}}]

    # 10. A trial reached its end (from the scheduler) -> flag for TechDeva to
    #     convert or drop. Billing/conversion is a human decision.
    if et == "trial.expired":
        return [{"alert_techdeva": {"provider": "billing",
                                    "message": f"Trial ended for tenant {p.get('tenant_id')} "
                                               f"— convert to paid or drop."}}]

    return []
