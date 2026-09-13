-- 20_risk_policy_real_actions.sql
-- Policy for the task types this platform actually runs.
--
-- Migration 19 wrote a clean dotted vocabulary (publish.post, ads.launch, ...)
-- that matched nothing: the orchestrator's real task types are schedule_posts,
-- make_creative, build_campaign, plan_growth, analyze_presence, plan_content and
-- qualify. A policy that never matches is worse than no policy, because it reads
-- as protection. The dotted names stay for agents written later; these are the
-- ones that fire today.

BEGIN;

INSERT INTO risk_policies (tenant_id, action, risk, requires_approval, reason) VALUES
  (NULL, 'schedule_posts',  'high',   true,  'Puts content out in the business name, where their customers see it'),
  (NULL, 'build_campaign',  'high',   true,  'Becomes an ad that spends the client money'),
  (NULL, 'make_creative',   'low',    false, 'A draft creative nobody has seen yet'),
  (NULL, 'plan_content',    'low',    false, 'A plan, not an action'),
  (NULL, 'plan_growth',     'low',    false, 'A recommendation for a human to read'),
  (NULL, 'analyze_presence','low',    false, 'Reading the outside world, changing nothing'),
  (NULL, 'qualify',         'low',    false, 'Internal classification of a lead')
ON CONFLICT DO NOTHING;

DO $verify$
DECLARE missing int;
BEGIN
  SELECT count(*) INTO missing
    FROM (VALUES ('schedule_posts'),('build_campaign'),('make_creative'),
                 ('plan_content'),('plan_growth'),('analyze_presence'),('qualify')) AS t(a)
   WHERE NOT EXISTS (SELECT 1 FROM risk_policies
                      WHERE tenant_id IS NULL AND action = t.a);
  IF missing > 0 THEN
    RAISE EXCEPTION 'verify failed: % live task types still have no policy', missing;
  END IF;
  RAISE NOTICE 'verify ok: every task type the orchestrator runs today has a policy';
END $verify$;

COMMIT;
