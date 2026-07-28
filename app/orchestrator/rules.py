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

    # 5. Revenue recorded -> mark sold + refresh reports (attribution baked in)
    if et == "revenue.recorded":
        return [
            {"update_lead": {"lead_id": p.get("lead_id"), "fields": {"status": "sold"}}},
            {"queue_task": {"agent": "reporting", "type": "refresh", "payload": {}}},
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

    return []
