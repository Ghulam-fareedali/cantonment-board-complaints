# TechDeva — state, and the plan to finish it
Written 25 August 2026. Every status here was checked against production, not inferred.

## 1. What a new tenant gets today, without anyone clicking anything

| Step | State |
|---|---|
| signup, 14-day trial, login, isolation between clients | works, proven under attack |
| business analysis of their website | works |
| growth plan | works |
| content in their voice, about their services | works, since the config intake |
| ad creatives | works |
| quality agent reviews before the client sees it | works |
| ads that actually spend | waits for the client to connect their own Meta |
| posting to Facebook / Instagram | blocked on Meta app review |
| leads and CRM screens | tables exist, nothing renders them |
| WhatsApp per tenant | needs QR pairing and one container per number |

The loop is autonomous: `worker.run_forever()` calls `scheduler.tick()`, which scans every
active tenant for work that has come due. Nothing starts it by hand.

## 2. The plan, in dependency order

### Phase 1 — make the trial sellable (days, no external blockers)
1. **Onboarding wizard in `web/`.** `GET /portal/config` already returns `onboarding.missing`;
   the screens do not exist. Seven steps: brand, services, prices, hours, location, AI voice,
   booking rules. Save per step, resumable.
2. **Logo upload** into `brand`. Needs a small file endpoint and a place to put it.
3. **Widen `leads.source`.** Only `manual` is accepted; `whatsapp`, `ad`, `instagram`,
   `referral` all 500. Until this is fixed nothing can be attributed and CPL/ROAS cannot
   be computed no matter what the ads agent does.
4. **Leads and appointments screens.** `leads`, `appointments`, `conversations`, `messages`,
   `revenue_events`, `campaigns` all exist and are RLS-scoped. None are rendered.
5. **Pin `SESSION_SECRET` in `.env`.** It is regenerated on every process start, so each
   deploy logs out every client. Fine today, unacceptable with clients.
6. **Decide `requires_approval`.** Deliverables currently return `false` and the portal shows
   `awaiting approval: 0`. "AI recommends, human approves" is the stated rule; set it on
   purpose before publishing can post.

### Phase 2 — connect the client's own channels
7. **Meta connect** — the OAuth route is now safe to expose (session-derived tenant,
   signed state). Wire the button in the dashboard.
8. **WhatsApp per tenant** — QR pairing plus one Hermes container per number.
   Measured 347-479 MB per container against ~3.6 GB free: about **seven more numbers**
   fit on this box. Past that is a second machine, not a config change.

### Phase 3 — outside our control, start early because they take weeks
9. Meta app review, before anything can be published automatically.
10. Two disabled ad accounts and one unpaid balance.

### Standing operational debt
- Monitor does not watch the WhatsApp bridges; it reported "all checks passed" while the
  clinic and salon bots were crash-looping.
- Clinic and salon WhatsApp have been logged out since 2 August. Needs a QR re-pair with a
  phone present.
- `ufw` inactive; ports 4000 (LiteLLM, no auth) and 32817 (Hermes workspace, no auth) are
  bound to 0.0.0.0. Provider-firewall coverage unverified — test from a phone on mobile data.
- Four unlabelled root SSH keys.
- Four endpoints return bare 500s on malformed input; negative `est_revenue` is accepted.
- No security headers on the API.

## 3. Traps that cost real hours — do not rediscover these

- **Compose passes a whitelist of environment variables.** `api` and `worker` have an explicit
  `environment:` block. A variable added to `.env` but not listed there never reaches the
  process. `OPENAI_BASE_URL` was set correctly and the platform still called `api.openai.com`
  with an OpenRouter key, producing a 401 that looked exactly like a bad key.
- **`docker compose up -d` after an `.env` change prints "Running" and changes nothing.**
  Use `--force-recreate`; "Recreated" in the output is the proof it took.
- **`ast.parse` is not a startup check.** A pydantic model defined inside a router closure
  parses fine, then crash-loops FastAPI at import because `from __future__ import annotations`
  makes the annotation an unresolvable string. Request models go at module scope.
- **Hermes runs on OpenRouter, never Bedrock.** LiteLLM on this box serves one Bedrock model
  and AWS refuses it. The platform uses `AI_PROVIDER=openai` with
  `OPENAI_BASE_URL=https://openrouter.ai/api` and `google/gemini-2.5-flash`.
- **A sandbox port scan of this VPS is meaningless** — only 80 and 443 egress.

## 4. Corrections to older notes

- **Build 6/7 was never missing.** `b7.zip` is on the server and the running image was built
  from it on 21 August. It was carried as BLOCKED for days.
- **There is no rate limiting to reuse.** Notes claimed `/auth/login` throttles to 429. It does
  not, anywhere, in the app or Traefik. The limiter on `/signup` is new.
- **Breadcrumb structured data on techdeva.net is valid**, contrary to an earlier audit.

## 5. Rules that do not bend

1. Never disturb the Beauty Aesthetic clinic. Live production, paying client, patient data.
2. Never build a second CRM — add a tenant row.
3. Evidence over documentation. Deployed is not done; a passing test is not a working feature.
4. Never invent testimonials, clients, results, ratings or capabilities.
5. Assert-guarded patches only. Back up first, verify anchor counts, abort on mismatch.
6. AI recommends, human approves.
