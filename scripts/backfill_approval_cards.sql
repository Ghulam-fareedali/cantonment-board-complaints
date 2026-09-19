-- Give already-parked work an approval card.
--
-- Tasks parked before the orchestrator fix have status 'needs_human' but no
-- deliverable, so the only release path (approving a deliverable) has nothing
-- to act on and the work is stuck for good. This creates exactly one approval
-- card per such task, carrying the agent's own stored result so the client
-- reviews the real work and not a placeholder.
--
-- Safe to re-run: the NOT EXISTS guard and the (task_id, kind) unique index
-- both prevent duplicates. Adds rows only; approves nothing.
INSERT INTO deliverables (tenant_id, task_id, agent, kind, title, body,
                          status, requires_approval)
SELECT t.tenant_id, t.id, t.agent, 'approval',
       t.type || ' is waiting for your approval',
       jsonb_build_object('action', t.type,
                          'risk', 'agent_requested',
                          'why', coalesce(t.result->>'reason',
                                          t.agent || ' asked for a human decision'),
                          'agent', t.agent,
                          'work', coalesce(t.result, '{}'::jsonb),
                          'backfilled', true),
       'awaiting_approval', true
  FROM tasks t
 WHERE t.status = 'needs_human'
   AND NOT EXISTS (SELECT 1 FROM deliverables d WHERE d.task_id = t.id)
ON CONFLICT DO NOTHING;
