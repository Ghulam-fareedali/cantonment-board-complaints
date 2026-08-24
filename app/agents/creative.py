"""Real Content (02) and Design (03) — Build 3.

Until now these two were stubs: Content returned the string "3 reels, 2 carousels,
1 offer" and Design returned three filenames that did not exist. A trial client
opened their dashboard on day one and saw placeholders. This replaces both with
work grounded in what the Analysis agent actually observed.

  Content (02)  reads tenant config + `latest_analysis` from shared context
                -> real posts: hook, caption, hashtags, CTA, and WHICH observed
                   weakness or competitor angle each piece answers
                -> writes the full plan to context so Design can build from it

  Design (03)   reads that plan + the tenant's brand colours
                -> real SVG artwork, one creative per piece, sanitised before it
                   is ever stored (it renders inside the client's dashboard)

Three rules hold throughout, and each is enforced rather than asked for:

  * NEVER INVENT FACTS. Everything the model is allowed to state comes from the
    tenant's own config and the analysis report. Generated copy is then scanned by
    `guards.scan_plan`; a price, discount or promise that config does not support
    parks the task as needs_human with the offending lines named. It is never
    published and never silently deleted.
  * DEGRADE HONESTLY. No API key, an unreachable model, or unparseable output ->
    needs_human + ops.alert. There is no canned fallback copy, because a
    placeholder caption in a client's dashboard is worse than an empty slot with a
    reason attached.
  * NO ANALYSIS, NO WORK. Without a report there is nothing to ground the copy in,
    so the agent asks for one instead of writing something generic.
"""
from __future__ import annotations

import json

from .contract import Agent, Task, Context, AgentResult
from .guards import scan_plan, sanitize_svg, UnsafeSVG
from .. import ai

MAX_PIECES = 6
MAX_CREATIVES = 3


# ---------------------------------------------------------------------------
def _facts(ctx: Context) -> dict:
    """Everything the model is permitted to treat as true. Nothing else exists."""
    cfg = ctx.config or {}
    report = (ctx.store.get("latest_analysis") or {})
    return {
        "services": cfg.get("services") or [],
        "price_list": cfg.get("price_list") or {},
        "brand": cfg.get("brand") or {},
        "geo": cfg.get("geo") or {},
        "working_hours": cfg.get("working_hours") or {},
        "tone": cfg.get("hermes_persona") or "",
        "observed_weaknesses": (report.get("client") or {}).get("weaknesses", []),
        "observed_strengths": (report.get("client") or {}).get("strengths", []),
        "opportunities": report.get("opportunities", []),
        "competitor_ads": [
            {"competitor": a.get("competitor"),
             "sample_copy": [x.get("copy") for x in (a.get("ads") or [])][:3]}
            for a in report.get("competitor_ads", [])
        ],
        # Build 5: what rivals actually keep saying, so the copy can say something
        # else on purpose rather than by accident
        "competitor_intel": [
            {"competitor": c.get("name"),
             "their_themes": (c.get("ads") or {}).get("themes", []),
             "their_offers": (c.get("ads") or {}).get("offers_advertised", []),
             "their_ctas": (c.get("ads") or {}).get("calls_to_action", []),
             "active_ads": (c.get("ads") or {}).get("active_ads", 0),
             "publishes_per_90_days": (c.get("content") or {}).get("published_last_90_days")}
            for c in report.get("competitor_intel", [])
        ],
        "channels_needing_connection": (report.get("summary") or {})
            .get("channels_needing_connection", []),
    }


def _has_analysis(ctx: Context) -> bool:
    return bool(ctx.store.get("latest_analysis"))


def _unavailable(reason: str, what: str) -> AgentResult:
    """Honest stop: park for a human and tell TechDeva exactly what is missing."""
    return AgentResult(
        status="needs_human",
        result={"blocked": what, "reason": reason},
        events=[{"type": "ops.alert",
                 "payload": {"provider": "ai", "message": f"{what}: {reason}"}}])


def _fabrication_stop(claims: list, what: str, draft) -> AgentResult:
    """Generated copy stated something the tenant's config does not support."""
    return AgentResult(
        status="needs_human",
        result={"blocked": "unbacked_claims", "claims": claims, "draft": draft},
        events=[{"type": "ops.alert",
                 "payload": {"provider": "ai",
                             "message": f"{what} withheld — generated copy claims facts "
                                        f"not in the tenant's config: {'; '.join(claims[:5])}"}}])


SYSTEM = (
    "You are a senior direct-response marketer writing for ONE specific business. "
    "You will be given that business's verified configuration and a factual analysis "
    "of its online presence and competitors.\n\n"
    "ABSOLUTE RULES:\n"
    "1. State no fact that is not in the data you are given. No prices, discounts, "
    "percentages, statistics, awards, certifications, guarantees, review counts or "
    "years in business unless they appear in the provided config.\n"
    "2. If you would need a number you were not given, write copy that does not need it.\n"
    "3. Ground every piece in a specific observed weakness, opportunity, or competitor "
    "angle from the analysis — name which one.\n"
    "4. Reply with ONLY a single valid JSON object. No markdown fences, no commentary."
)


# ---------------------------------------------------------------------------
class ContentAgent(Agent):
    """Agent 02 — real copy, grounded in the analysis report."""

    name = "content"

    def handles(self, t):
        return t in {"plan_content", "campaign_ideas", "write_copy"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        if not _has_analysis(ctx):
            return _unavailable("no analysis report in context yet — nothing to ground "
                                "copy in", "content plan")
        if not ai.available():
            return _unavailable(f"model not configured (AI_PROVIDER={ai.provider()})",
                                "content plan")

        facts = _facts(ctx)
        service = task.payload.get("service") or (
            (facts["services"] or [{}])[0].get("name") if facts["services"] else None)
        prompt = (
            f"BUSINESS CONFIG (the only facts that exist):\n{json.dumps(facts, indent=2)[:6000]}\n\n"
            f"CAMPAIGN BRIEF: {task.payload.get('brief') or 'grow this business'}\n"
            f"FOCUS SERVICE: {service or 'the main service'}\n\n"
            f"Produce a content plan of at most {MAX_PIECES} pieces. Return JSON:\n"
            '{"strategy": "<2-3 sentences: the angle, and why, citing the analysis>",\n'
            ' "pieces": [{"format": "reel|carousel|story|post",\n'
            '             "hook": "<first line that stops the scroll>",\n'
            '             "caption": "<full caption, ready to publish>",\n'
            '             "hashtags": ["..."],\n'
            '             "cta": "<one clear action>",\n'
            '             "addresses": "<the exact weakness/opportunity/competitor angle '
            'from the analysis this piece answers>"}]}')

        try:
            data = ai.generate_json(SYSTEM, prompt, required_keys=("strategy", "pieces"))
        except ai.AIUnavailable as e:
            return _unavailable(str(e), "content plan")

        pieces = [p for p in (data.get("pieces") or []) if isinstance(p, dict)][:MAX_PIECES]
        if not pieces:
            return _unavailable("model returned no usable pieces", "content plan")
        plan = {"strategy": data.get("strategy", ""), "service": service, "pieces": pieces,
                "model": ai.model_name()}

        claims = scan_plan(plan, ctx.config)
        if claims:
            return _fabrication_stop(claims, "content plan", plan)

        return AgentResult(
            status="done",
            result={"plan": plan,
                    "deliverables": [{
                        "kind": "content_plan",
                        "title": f"Content plan — {service or 'growth'} "
                                 f"({len(pieces)} pieces)",
                        "body": plan}]},
            events=[{"type": "content.ready",
                     # carry the produced work so Quality Control (12) can review
                     # the actual artefact rather than re-deriving it
                     "payload": {**task.payload, "pieces": len(pieces),
                                 "produced_by": "content",
                                 "output": {"plan": plan,
                                            "deliverables": [{
                                                "kind": "content_plan",
                                                "title": f"Content plan — {service or 'growth'} "
                                                         f"({len(pieces)} pieces)",
                                                "body": plan}]}}}],
            # Design reads this: the creatives illustrate THESE posts, not generic ones
            context_writes={"current_content_plan": plan})


# ---------------------------------------------------------------------------
class DesignAgent(Agent):
    """Agent 03 — real artwork as sanitised SVG, in the tenant's brand colours."""

    name = "design"

    def handles(self, t):
        return t in {"make_creative", "flyer", "carousel"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        plan = ctx.store.get("current_content_plan") or {}
        pieces = plan.get("pieces") or []
        if not pieces:
            return _unavailable("no content plan in context — design follows content",
                                "creatives")
        if not ai.available():
            return _unavailable(f"model not configured (AI_PROVIDER={ai.provider()})",
                                "creatives")

        brand = (ctx.config or {}).get("brand") or {}
        palette = brand.get("colors") or ["#0A0E1A", "#D4AF5F", "#EAF1FF"]
        subset = pieces[:MAX_CREATIVES]
        prompt = (
            f"BRAND: {json.dumps(brand)[:1200]}\n"
            f"PALETTE (use these exact colours): {json.dumps(palette)}\n"
            f"POSTS TO ILLUSTRATE:\n{json.dumps(subset, indent=2)[:4000]}\n\n"
            f"Design one social creative per post as a self-contained SVG.\n"
            "Requirements: 1080x1080 viewBox; the post's hook as the dominant headline; "
            "the CTA smaller at the bottom; shapes, gradients and text only; readable "
            "type sizes; no external images, no fonts to download, no script.\n"
            "Return JSON:\n"
            '{"creatives": [{"title": "<short name>", "alt": "<description for '
            'accessibility>", "svg": "<svg viewBox=\\"0 0 1080 1080\\" ...>...</svg>"}]}')

        try:
            data = ai.generate_json(SYSTEM.replace("senior direct-response marketer",
                                                   "senior graphic designer who writes SVG"),
                                    prompt, required_keys=("creatives",))
        except ai.AIUnavailable as e:
            return _unavailable(str(e), "creatives")

        creatives, rejected = [], []
        for c in (data.get("creatives") or [])[:MAX_CREATIVES]:
            if not isinstance(c, dict):
                continue
            try:
                safe, removed = sanitize_svg(c.get("svg", ""))
            except UnsafeSVG as e:
                rejected.append({"title": c.get("title"), "reason": str(e)})
                continue
            creatives.append({"title": c.get("title") or "Creative",
                              "alt": c.get("alt") or "",
                              "svg": safe,
                              "sanitised": removed})

        if not creatives:
            return _unavailable(
                "no usable artwork survived safety checks: " +
                "; ".join(r["reason"] for r in rejected) if rejected else "model produced none",
                "creatives")

        claims = scan_plan([{"title": c["title"], "alt": c["alt"]} for c in creatives],
                           ctx.config)
        if claims:
            return _fabrication_stop(claims, "creatives", [c["title"] for c in creatives])

        body = {"creatives": creatives, "rejected": rejected, "model": ai.model_name()}
        return AgentResult(
            status="done",
            result={"creatives": [c["title"] for c in creatives],
                    "deliverables": [{
                        "kind": "creative_set",
                        "title": f"Creative set — {len(creatives)} assets",
                        "body": body}]},
            events=[{"type": "creative.ready",
                     # carry the produced work so Quality Control (12) can review it
                     "payload": {**task.payload, "count": len(creatives),
                                 "produced_by": "design",
                                 "output": {"creatives": [c["title"] for c in creatives],
                                            "deliverables": [{
                                                "kind": "creative_set",
                                                "title": f"Creative set — {len(creatives)} assets",
                                                "body": body}]}}}])
