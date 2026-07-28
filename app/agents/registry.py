"""Agent registry + Agent 08 (CRM) stub.

The orchestrator looks up which agent handles a task type here. Adding an agent
(Reporting=09, then BI, Content, ...) means: implement the contract, register it.
Nothing else in the system changes.
"""
from .contract import Agent, Task, Context, AgentResult
from .hermes import HermesAgent
from .specialists import ContentAgent, DesignAgent, AdsAgent, PublishingAgent


class CrmAgent(Agent):
    name = "crm"

    def handles(self, task_type: str) -> bool:
        return task_type in {"update", "create_lead", "merge"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        # in Phase C this writes to leads/appointments; here it updates lead status
        fields = {}
        if task.payload.get("status"):
            fields = {"status": task.payload["status"]}
        return AgentResult(status="done", result={"updated": task.payload},
                           context_writes={"last_crm_update": task.payload})


class ReportingAgent(Agent):
    name = "reporting"

    def handles(self, task_type: str) -> bool:
        return task_type in {"refresh", "daily_report", "weekly_report"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        # Phase C computes real ROAS/CPL from revenue_events; stubbed marker here
        return AgentResult(status="done", result={"refreshed": True})


# register every live agent; order doesn't matter, lookup is by handles()
REGISTRY: list[Agent] = [
    HermesAgent(),      # 07 — client front door (queries + intake)
    CrmAgent(),         # 08
    ReportingAgent(),   # 09
    ContentAgent(),     # 02
    DesignAgent(),      # 03
    PublishingAgent(),  # 05
    AdsAgent(),         # 06
]


def agent_for(task_type: str) -> Agent | None:
    for a in REGISTRY:
        if a.handles(task_type):
            return a
    return None
