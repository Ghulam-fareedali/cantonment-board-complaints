"""Agent registry + Agent 08 (CRM) stub.

The orchestrator looks up which agent handles a task type here. Adding an agent
(Reporting=09, then BI, Content, ...) means: implement the contract, register it.
Nothing else in the system changes.
"""
from .contract import Agent, Task, Context, AgentResult
from .hermes import HermesAgent
from .specialists import ContentAgent, DesignAgent, AdsAgent, PublishingAgent
from .growth_agents import AnalysisAgent, GrowthPlannerAgent
from .oversight import QualityControlAgent, CampaignOptimizerAgent
from ..attribution import build_attribution_report


class CrmAgent(Agent):
    name = "crm"

    def handles(self, task_type: str) -> bool:
        return task_type in {"update", "create_lead", "merge"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        # Propose only — agents are read-only (Agent Contract). A
        # lead.updated event is verified against a real row in `leads`
        # by the orchestrator (core.py) before this "done" is trusted;
        # it used to return status="done" with no write ever happening.
        fields = {}
        if task.payload.get("status"):
            fields["status"] = task.payload["status"]
        if not fields:
            return AgentResult(status="done",
                                result={"updated": None, "reason": "nothing to update"})
        return AgentResult(
            status="done",
            result={"proposed_fields": fields},
            events=[{"type": "lead.updated",
                     "payload": {"lead_id": task.payload.get("lead_id"), "fields": fields}}],
        )


class ReportingAgent(Agent):
    """Agent 09 — real per-campaign CPL/ROAS from attributed revenue.

    Was a stub returning {"refreshed": True}. It now reads the attribution rows
    the platform actually has and reports them with three disciplines:
      * a missing denominator is null, never zero;
      * revenue that could not be placed is reported as unattributed, never
        spread across campaigns to make a total look tidy;
      * every report names its attribution model and its coverage, so nobody
        mistakes a thin figure for a solid one.
    """
    name = "reporting"

    def handles(self, task_type: str) -> bool:
        return task_type in {"refresh", "daily_report", "weekly_report"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        rows = (task.payload.get("attribution")
                or ctx.store.get("attribution_rows") or {})
        campaigns = rows.get("campaigns") or []

        if not campaigns:
            # Honest degradation: no campaigns is not "ROAS 0".
            return AgentResult(
                status="done",
                result={"refreshed": True, "report": None,
                        "reason": "no campaigns recorded — nothing to attribute"},
                context_writes={"last_report": {"report": None}})

        report = build_attribution_report(
            campaigns,
            unattributed_revenue=rows.get("unattributed_revenue"),
            total_spend=rows.get("total_spend"),
            currency=(ctx.config.get("brand") or {}).get("currency"))

        events = []
        cov = report.get("attribution_coverage")
        # Low coverage means the per-campaign numbers are thin. Say so, loudly,
        # rather than letting someone scale a budget on 20% of the picture.
        if cov is not None and cov < 0.5:
            events.append({"type": "ops.alert", "payload": {
                "provider": "attribution",
                "message": f"Only {int(cov * 100)}% of revenue could be traced to a "
                           f"campaign. Per-campaign ROAS is unreliable until more "
                           f"leads carry a click id."}})

        return AgentResult(
            status="done",
            result={"refreshed": True, "report": report},
            context_writes={"last_report": report,
                            "campaign_performance": {
                                "campaigns": [{
                                    "id": c["campaign_id"], "name": c["name"],
                                    "platform": c["platform"], "spend": c["spend"],
                                    "leads": c["leads"], "revenue": c["revenue"],
                                } for c in report["campaigns"]],
                                "as_of": "live",
                                "attribution": report["attribution_model"],
                                "currency": report.get("currency"),
                            }},
            events=events)


# register every live agent; order doesn't matter, lookup is by handles()
REGISTRY: list[Agent] = [
    HermesAgent(),      # 07 — client front door (queries + intake)
    CrmAgent(),         # 08
    ReportingAgent(),   # 09
    ContentAgent(),     # 02
    DesignAgent(),      # 03
    PublishingAgent(),  # 05
    AdsAgent(),         # 06
    AnalysisAgent(),      # 10 — multi-channel + competitor analysis
    GrowthPlannerAgent(), # 11 — turns the report into a growth pipeline
    QualityControlAgent(),   # 12 — reviews output before it reaches a client
    CampaignOptimizerAgent(),# 13 — proposes budget changes (never executes)
]


def agent_for(task_type: str) -> Agent | None:
    for a in REGISTRY:
        if a.handles(task_type):
            return a
    return None
