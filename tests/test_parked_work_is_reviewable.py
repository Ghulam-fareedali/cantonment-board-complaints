"""A task parked for a human must leave the human something to decide on.

The orchestrator parked agent-requested reviews and saved no deliverable,
because _save_deliverables only persists what an agent declared under
result['deliverables'] and no agent declares that when it asks for a human.
93 tasks sat in 'needs_human' in production with zero approval cards, and the
only release path (approving a deliverable) had nothing to act on.
"""
import pytest

from app.agents.contract import AgentResult
from app.orchestrator import core


class FakePersist:
    def __init__(self):
        self.parked = []
        self.deliverables = []

    def park_for_human(self, tenant_id, task_id, result):
        self.parked.append((tenant_id, task_id, result))

    def save_deliverables(self, tenant_id, task_id, agent, items,
                          status="draft", requires_approval=False):
        self.deliverables.append({"task_id": task_id, "agent": agent,
                                  "items": items, "status": status,
                                  "requires_approval": requires_approval})
        return items

    def emit_event(self, *a, **k):
        pass

    def fail_task(self, *a, **k):
        raise AssertionError("task should be parked, not failed")

    def is_conversation_human(self, *a, **k):
        return False


class AsksForAHuman:
    name = "content"

    def handles(self, t):
        return True

    def run(self, task, ctx):
        return AgentResult(status="needs_human",
                           result={"draft": "a post the client must see"},
                           human_review=True)


class DeclaresItsOwn:
    name = "design"

    def handles(self, t):
        return True

    def run(self, task, ctx):
        return AgentResult(status="needs_human", human_review=True,
                           result={"deliverables": [{"kind": "image",
                                                     "title": "Flyer v2",
                                                     "body": {}}]})


def _run(agent_cls, monkeypatch):
    monkeypatch.setattr(core, "agent_for", lambda task_type: agent_cls())
    p = FakePersist()
    orch = core.Orchestrator(load_config=lambda t: {},
                             load_context=lambda t: {},
                             persist=p)
    orch.run_task("tenant-1", {"id": "task-1", "type": "plan_content",
                               "payload": {}})
    return p


def test_parked_task_gets_an_approval_card(monkeypatch):
    p = _run(AsksForAHuman, monkeypatch)
    assert p.parked, "the task must still be parked"
    assert p.deliverables, "parking without a card leaves the gate unreachable"
    saved = p.deliverables[0]
    assert saved["status"] == "awaiting_approval"
    assert saved["requires_approval"] is True
    card = saved["items"][0]
    assert card["kind"] == "approval"
    assert "plan_content" in card["title"]
    # the client must be able to see the work being approved, not just its name
    assert card["body"]["work"]["draft"] == "a post the client must see"


def test_an_agents_own_deliverables_are_not_replaced(monkeypatch):
    p = _run(DeclaresItsOwn, monkeypatch)
    card = p.deliverables[0]["items"][0]
    assert card["kind"] == "image" and card["title"] == "Flyer v2"
