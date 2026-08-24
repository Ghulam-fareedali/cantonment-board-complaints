"""Growth Engine agents — Analysis (10) and GrowthPlanner (11).

Both implement THE Agent Contract and nothing else. The orchestrator hands them
a Task + read-only Context; they return an AgentResult. They never call other
agents. They read business facts (website, links, competitors, tokens) from the
tenant's config/context — never hardcoded.

  Analysis (10)   handles 'analyze_presence'
    -> fetches website + own social + competitors + competitor ads (REAL)
    -> writes ONE combined report to context_store, persists an analysis_reports row
    -> emits 'analysis.completed' (hands the report to the orchestrator)
    -> for every channel with missing access, emits an 'ops.alert' (honest gap)

  GrowthPlanner (11) handles 'plan_growth'
    -> reads the report from context, decides the growth move
    -> emits 'client.requested' (intent=campaign) so the EXISTING, tested
       content -> design -> ads -> publishing pipeline runs. Ads/Publishing still
       stop at the human-approval gate. Reuse over reinvention.
"""
from __future__ import annotations
from .contract import Agent, Task, Context, AgentResult
from ..analysis.fetchers import (analyze_site, fetch_meta_presence,
                                 fetch_competitor_ads, fetch_competitor_site,
                                 fetch_instagram_presence, fetch_content_cadence)
from ..analysis.discover import discover_competitors
from ..analysis.competitor import ad_intel, content_intel, social_posts_gap, compare
from ..analysis.report import build_report
import os


class AnalysisAgent(Agent):
    name = "analysis"  # Agent 10

    def handles(self, task_type: str) -> bool:
        return task_type in {"analyze_presence"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        p = task.payload
        links = p.get("social_links") or {}
        website = p.get("website_url") or (ctx.config.get("brand", {}) or {}).get("website")
        provided = p.get("competitors") or []
        country = (ctx.config.get("geo", {}) or {}).get("country", "PK")
        industry = p.get("industry") or ctx.config.get("industry") or ""
        business_name = p.get("business_name") or (ctx.config.get("brand", {}) or {}).get("name", "")

        # 1) client website (real fetch + shallow crawl) + how often they publish
        client_site = analyze_site(website, max_pages=3) if website else {"home": {"error": "no_url"}}
        client_content = content_intel(fetch_content_cadence(website)) if website else {}

        # 2) own social presence via the tenant's OWN connected Meta token.
        #    Facebook and Instagram are read SEPARATELY — a Meta token covering both
        #    told the client nothing about Instagram specifically.
        integ = ctx.store.get("integrations", {}) or {}
        meta_creds = (ctx.store.get("meta_credentials") or {})
        own_social = {"error": "meta_not_connected"}
        own_instagram = {"error": "meta_not_connected"}
        if integ.get("meta") == "connected":
            token, page_id = meta_creds.get("access_token", ""), meta_creds.get("page_id")
            own_social = fetch_meta_presence(token, page_id)
            own_instagram = fetch_instagram_presence(token, page_id)

        # 3) WHO are the competitors? Client-provided first, then discovered from
        #    evidence (who is actually advertising in this market).
        ad_token = os.environ.get("META_AD_LIBRARY_TOKEN", "")
        competitors, extra_gaps = discover_competitors(
            business_name=business_name, industry=industry, website=website,
            social_links=links, country=country, provided=provided, app_token=ad_token)

        # 4) what each of them publishes: live ads, site quality, publishing cadence
        comp_sites, comp_ads, intel = [], [], []
        for c in competitors:
            entry = {"name": c.get("name") or c.get("website"),
                     "website": c.get("website"),
                     "source": c.get("source", "client_provided"),
                     "found_because": c.get("evidence", "")}

            if c.get("website"):
                s = fetch_competitor_site(c["website"]); s["requested"] = c["website"]
                comp_sites.append({"home": s, "pages": []})
                entry["content"] = content_intel(fetch_content_cadence(c["website"]))
            else:
                entry["content"] = {"status": "unavailable", "reason": "no website known"}

            name = c.get("name") or c.get("facebook")
            if name:
                raw_ads = fetch_competitor_ads(ad_token, name, country)
                comp_ads.append(raw_ads)
                entry["ads"] = ad_intel(raw_ads)
            else:
                entry["ads"] = {"status": "unavailable", "reason": "no page name to search"}

            # organic social posts are NOT lawfully readable — say so, once, per competitor
            extra_gaps.append(social_posts_gap(entry["name"]))
            intel.append(entry)

        # 5) turn the intel into specific, checkable moves
        moves = compare(client_content, intel)

        # 6) assemble the ONE report + honest gaps
        report, gaps = build_report(client_site=client_site, own_social=own_social,
                                    competitor_sites=comp_sites, competitor_ads=comp_ads,
                                    links=links, competitor_intel=intel,
                                    client_content=client_content,
                                    own_instagram=own_instagram, moves=moves,
                                    extra_gaps=extra_gaps)

        # events: hand the report onward + surface every access gap to TechDeva
        events = [{"type": "analysis.completed",
                   "payload": {"signup_id": p.get("signup_id"),
                               "website_score": report["summary"]["website_score"],
                               "confidence": report["summary"]["data_confidence"]}}]
        for g in gaps:
            events.append({"type": "ops.alert",
                           "payload": {"provider": g["channel"],
                                       "message": f"analysis gap: {g['channel']} — {g['reason']}"}})

        # store the full report in shared context (GrowthPlanner reads it) and
        # signal the persistence layer to also write an analysis_reports row.
        return AgentResult(
            status="done",
            result={"report": report, "gaps": gaps,
                    "persist_report": {"signup_id": p.get("signup_id"),
                                       "status": "complete" if not gaps else "partial"}},
            events=events,
            context_writes={"latest_analysis": report},
        )


class GrowthPlannerAgent(Agent):
    name = "growth_planner"  # Agent 11

    def handles(self, task_type: str) -> bool:
        return task_type in {"plan_growth"}

    def run(self, task: Task, ctx: Context) -> AgentResult:
        report = (ctx.store.get("latest_analysis") or {})
        opps = report.get("opportunities", [])
        summary = report.get("summary", {})

        # Decide the first growth move from the evidence. Build a brief the
        # existing specialist pipeline can act on. No fabricated claims — the
        # brief only references what the report actually found.
        top = opps[0]["action"] if opps else "increase content volume and consistency"
        brief = (f"Growth focus: {top}. "
                 f"Website score {summary.get('website_score', 'n/a')}, "
                 f"{summary.get('competitor_active_ads', 0)} competitor ads live. "
                 f"Address weaknesses and out-position competitors.")
        service = (ctx.config.get("services") or [{}])[0].get("name") if ctx.config.get("services") else None

        # Reuse the tested pipeline: emit client.requested (intent=campaign).
        # Ads + Publishing will still park at the human-approval gate.
        return AgentResult(
            status="done",
            result={"plan": {"top_priority": top, "brief": brief}},
            events=[{"type": "client.requested",
                     "payload": {"intent": "campaign", "service": service,
                                 "brief": brief, "budget": "TBD"}}],
            context_writes={"current_growth_plan": {"top_priority": top}},
        )
