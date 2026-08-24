# The onboarding intake — built 24 August 2026

## What was missing

`tenant_config` already existed with exactly the right shape:

```
tenant_id · services · price_list · departments · booking_rules
hermes_persona · brand · geo · working_hours · automation
```

And the agents **already read it** — `creative.py` pulls `price_list`, `working_hours`
and `hermes_persona` on every run; `hermes.py` reads the persona; `persistence.py` had
the full SELECT.

**Nothing could write it.** There was no API route. So every tenant looked identical to
the agents, and the AI wrote generic marketing copy because a website crawl was all it
had. This was never a capability gap — it was a missing intake.

## What was built

**`Persistence.save_config(tenant_id, patch)`** — merge-update, whitelisted columns,
jsonb-aware, RLS-scoped like everything else. Unknown keys ignored.

**`GET /portal/config`** — returns the config plus an onboarding state.
**`PUT /portal/config`** — owner-only, partial updates, returns what it saved.

Seven independent steps, so a client fills them in over several sittings and the
dashboard can show what is left:

```
brand · services · price_list · working_hours · geo · hermes_persona · booking_rules
```

## The proof

Config written for The Beauty Aesthetic & Lounge — 5 services, 5 prices in PKR,
Gulberg III, and Zara's persona. Onboarding went `0/7` to **`7/7 (100%)`**.

Then `POST /client/request {"intent":"campaign","service":"Hair PRP"}`.

**Before the config** — generic English, about website analytics:

> "Are you leaving insights on the table?"
> "Understanding how visitors interact with your website is crucial for growth."

**After the config** — six pieces about Hair PRP, in Roman Urdu, in the clinic's voice:

> "Baalon ka girna ab ek purani baat!"
> "Girte baalon se pareshan hain, Madam? Hair PRP aik revolutionary treatment hai…"
> "Kya aap ke baal patle aur bejaan lag rahe hain?"
> "Hair PRP: Haqeeqat ya Dhoka?"

Creatives came back as `Hair PRP Reel · Hair PRP Post · Hair PRP Story`.

Nothing about the agents changed. Only the config.

## One mistake worth recording

The first version defined `ConfigIn` **inside** `make_portal_router()`. With
`from __future__ import annotations` active, FastAPI could not resolve the forward
reference and the API crash-looped:

```
pydantic.errors.PydanticUndefinedAnnotation: name 'ConfigIn' is not defined
```

`ast.parse` passed — a syntax check is not a startup check. Restored from
`/root/portal.bak.*`, confirmed health, then reapplied with the model, the step list
and `onboarding_state()` at **module scope**. Route handlers stay in the closure.

## Next

- Onboarding wizard UI in `web/` driven by `onboarding.missing`
- Logo upload into `brand`
- Widen the `leads.source` constraint — only `"manual"` is accepted today, so no lead
  can be attributed to WhatsApp, an ad or a referral
- A leads/pipeline screen — the tables (`appointments`, `conversations`, `messages`,
  `revenue_events`, `campaigns`) all exist and nothing renders them
- WhatsApp QR provisioning per tenant (one Hermes container per number).
  RAM measured at 347-479 MB per container against ~3.6 GB free — about **7 more
  numbers** fit on this box. Scaling past that is a second machine, not a config change.
