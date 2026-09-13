"""Runs INSIDE the api container. Proves the approval gate is the orchestrator's,
not the agent's.

The stub agent below returns a perfectly ordinary success: status done,
human_review False. Before Part 4 that went straight out. If the task ends up
parked at needs_human with a deliverable awaiting approval, the gate came from
the action's policy, which is the whole point.
"""
import json
import os
import sys

from app.persistence import Persistence
from app.orchestrator import core
from app.orchestrator.core import Orchestrator
from app.agents.contract import AgentResult

TID = os.environ["TID"]
ok = bad = 0


def check(name, good, note=""):
    global ok, bad
    if good:
        ok += 1
        print("PASS ", name, note)
    else:
        bad += 1
        print("FAIL ", name, note)


class Stub:
    """An agent that never asks for review -- exactly the failure mode."""
    name = "publishing"

    def handles(self, t):
        return True

    def run(self, task, ctx):
        return AgentResult(status="done", result={"posted": "would have gone live"})


core.agent_for = lambda task_type: Stub()

p = Persistence()
orch = Orchestrator(load_config=p.load_config, load_context=p.load_context, persist=p)


def make_task(task_type):
    with p._sess(TID) as cur:
        cur.execute("""INSERT INTO tasks (tenant_id, agent, type, status, payload)
                       VALUES (%s, 'publishing', %s, 'queued', '{}'::jsonb)
                       RETURNING id""", (TID, task_type))
        return str(cur.fetchone()["id"])


def status_of(task_id):
    with p._sess(TID) as cur:
        cur.execute("SELECT status FROM tasks WHERE id = %s", (task_id,))
        r = cur.fetchone()
        return r["status"] if r else None


def deliverable_for(task_id):
    with p._sess(TID) as cur:
        cur.execute("""SELECT status, requires_approval FROM deliverables
                        WHERE task_id = %s""", (task_id,))
        return cur.fetchone()


# 1. A high-risk action is held even though the agent asked for nothing.
high = make_task("schedule_posts")
orch.run_task(TID, {"id": high, "type": "schedule_posts", "payload": {}})
check("a high-risk task is held for approval", status_of(high) == "needs_human",
      "status=%s" % status_of(high))

# 2. Low-risk work is not held. A gate that stops everything is not a gate.
low = make_task("make_creative")
orch.run_task(TID, {"id": low, "type": "make_creative", "payload": {}})
check("low-risk work still runs", status_of(low) == "done", "status=%s" % status_of(low))

# 3. An action nobody classified, in a namespace that reaches customers,
#    is held rather than assumed safe.
unknown = make_task("publish.something_new")
orch.run_task(TID, {"id": unknown, "type": "publish.something_new", "payload": {}})
check("an unclassified external action is held", status_of(unknown) == "needs_human",
      "status=%s" % status_of(unknown))

# 4. The hold is visible to the client as an approval card, not a silent stall.
d = deliverable_for(high)
check("the held work appears as an approval card",
      bool(d) and d["status"] == "awaiting_approval" and d["requires_approval"],
      json.dumps(dict(d), default=str) if d else "no deliverable")

# clean up the tasks this test made
with p._sess(TID) as cur:
    cur.execute("DELETE FROM deliverables WHERE task_id = ANY(%s)", ([high, low, unknown],))
    cur.execute("DELETE FROM tasks WHERE id = ANY(%s)", ([high, low, unknown],))

print("\n=== %d passed, %d failed ===" % (ok, bad))
sys.exit(1 if bad else 0)
