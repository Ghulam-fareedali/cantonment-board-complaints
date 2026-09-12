"""Execution / audit trail (Part 3).

One function, used everywhere something meaningful happens. It answers the
four questions the platform needs to be able to answer about itself:

    What did the AI do?  What did the human approve?  When?  What happened?

Deliberately separate from `events`: that table is the orchestrator's input bus
(rules.py dispatches on event["type"], the worker drains it). An audit record
must never trigger work and must never be consumed.

record() NEVER raises. An audit write that fails must not roll back the real
work it was describing -- losing the log is bad, losing the invoice is worse.
The failure is printed so it shows up in container logs rather than vanishing.
"""

import json


def record(cur, *, action, resource, resource_id=None, actor=None,
           actor_type="human", status="ok", detail=None, error=None):
    """Append one audit row using the caller's already tenant-scoped cursor.

    Runs inside the same transaction as the work it describes, so a rolled-back
    write leaves no audit row claiming it happened.
    """
    try:
        cur.execute(
            "INSERT INTO executions (tenant_id, actor, actor_type, action, resource, "
            "resource_id, status, detail, error) "
            "VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s)",
            (str(actor) if actor else None, actor_type, action, resource,
             str(resource_id) if resource_id else None, status,
             json.dumps(detail or {}), error))
    except Exception as exc:                      # noqa: BLE001 - see docstring
        print("[audit] failed to record {0} {1}: {2}".format(action, resource, exc))
