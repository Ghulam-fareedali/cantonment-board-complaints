"""Build the ONE combined analysis report the orchestrator consumes.

Takes the raw fetch outputs (website, own social, competitor sites, competitor
ads) and produces a single structured object:

  {
    "client":     {website scorecard + observed signals},
    "channels":   {facebook/instagram/linkedin presence or gap},
    "competitors":[{site scorecard, active ads}],
    "opportunities":[ actionable, evidence-backed items ],
    "summary":    {scores + one-line positioning read},
  }

Plus a flat `gaps` list of every channel we could NOT get real data for.
Scores are computed ONLY from observed signals — if a signal wasn't observed it
lowers confidence rather than inventing a value.
"""
from __future__ import annotations
from .fetchers import gap


def _score_site(site: dict) -> tuple[int, list[str], list[str]]:
    """Return (0-100 score, strengths, weaknesses) from REAL observed signals."""
    home = site.get("home", {})
    if home.get("error"):
        return 0, [], [f"website unreachable ({home['error']})"]
    s = home.get("signals", {})
    score, strengths, weaknesses = 0, [], []

    def check(cond, pts, good, bad):
        nonlocal score
        if cond:
            score += pts; strengths.append(good)
        else:
            weaknesses.append(bad)

    check(home.get("https"), 10, "HTTPS secure", "no HTTPS")
    check(s.get("has_title") and 10 <= s.get("title_len", 0) <= 65, 15,
          "clear page title", "missing / poor-length title tag")
    check(s.get("has_meta_description"), 15, "meta description present", "no meta description (hurts SEO/CTR)")
    check(s.get("has_viewport"), 10, "mobile-friendly viewport", "not mobile-optimized")
    check(s.get("has_og_tags"), 10, "social share tags set", "no Open Graph tags (poor link previews)")
    check(s.get("has_structured_data"), 10, "structured data present", "no schema.org structured data")
    check(home.get("word_count", 0) >= 300, 10, "substantial page content", "thin content (<300 words)")
    check(s.get("has_analytics"), 10, "analytics installed", "no analytics/tracking detected")
    check(s.get("has_pixel"), 10, "retargeting pixel present", "no ad pixel (can't retarget visitors)")
    if s.get("img_missing_alt", 0) > 0:
        weaknesses.append(f"{s['img_missing_alt']} images missing alt text")
    return min(score, 100), strengths, weaknesses


def build_report(*, client_site: dict, own_social: dict, competitor_sites: list[dict],
                competitor_ads: list[dict], links: dict,
                competitor_intel: list | None = None, client_content: dict | None = None,
                own_instagram: dict | None = None, moves: list | None = None,
                extra_gaps: list | None = None) -> tuple[dict, list[dict]]:
    """Assemble the ONE combined report.

    The Build 5 arguments are all optional so older callers (and the Build 1 tests)
    keep working unchanged: without them the report is exactly what it was before.
    """
    gaps: list[dict] = list(extra_gaps or [])

    # --- client website ---
    c_score, c_str, c_weak = _score_site(client_site)
    client_block = {
        "website": client_site.get("home", {}).get("final_url"),
        "score": c_score,
        "strengths": c_str,
        "weaknesses": c_weak,
        "title": client_site.get("home", {}).get("title"),
        "pages_checked": 1 + len(client_site.get("pages", [])),
    }
    if client_site.get("home", {}).get("error"):
        gaps.append(gap("website", "unreachable", client_site["home"]["error"]))

    # --- own social channels ---
    channels: dict = {}
    if own_social.get("error"):
        gaps.append(gap("facebook/instagram", "not_connected", own_social["error"]))
        channels["facebook_instagram"] = {"status": "needs_connection", "reason": own_social["error"]}
    else:
        channels["facebook_instagram"] = {
            "status": "observed",
            "followers": own_social.get("followers"),
            "recent_posts": own_social.get("recent_post_count"),
            "engagement_samples": own_social.get("recent_engagement", [])[:5],
        }
    # Instagram, read properly through the linked Page rather than assumed from Facebook
    if own_instagram is not None:
        if own_instagram.get("error"):
            gaps.append(gap("instagram", "not_readable", own_instagram["error"]))
            channels["instagram"] = {"status": "needs_connection",
                                     "reason": own_instagram["error"]}
        else:
            channels["instagram"] = {
                "status": "observed",
                "username": own_instagram.get("username"),
                "followers": own_instagram.get("followers"),
                "posts_published": own_instagram.get("media_count"),
                "engagement_samples": own_instagram.get("recent_engagement", [])[:5],
            }

    # LinkedIn / others we were given links for but have no accessible API
    for name in ("linkedin",):
        if links.get(name):
            gaps.append(gap(name, "no_public_api", "connect account or provide access for real data"))
            channels[name] = {"status": "needs_connection", "url": links[name]}

    # --- competitors ---
    competitors = []
    for site in competitor_sites:
        sc, cs, cw = _score_site(site)
        competitors.append({
            "website": site.get("home", {}).get("final_url") or site.get("requested"),
            "score": sc, "strengths": cs, "weaknesses": cw,
        })
        if site.get("home", {}).get("error"):
            gaps.append(gap("competitor_site", "unreachable",
                           site.get("home", {}).get("url", "")))
    for ad in competitor_ads:
        if ad.get("error"):
            gaps.append(gap("competitor_ads", ad["error"], ad.get("detail", "")))

    active_ads = [a for a in competitor_ads if not a.get("error")]

    # --- opportunities: evidence-backed, derived only from what we observed ---
    opportunities = []
    for w in c_weak:
        opportunities.append({"area": "website", "action": f"Fix: {w}", "evidence": "observed on site"})
    comp_scores = [c["score"] for c in competitors if c["score"] > 0]
    if comp_scores and c_score < max(comp_scores):
        opportunities.append({
            "area": "positioning",
            "action": f"Client site scores {c_score} vs top competitor {max(comp_scores)} — close the gap first",
            "evidence": "comparative site scoring",
        })
    for a in active_ads:
        if a.get("active_ad_count", 0) > 0:
            opportunities.append({
                "area": "ads",
                "action": f"{a['competitor']} runs {a['active_ad_count']} active ads — counter with a competing campaign",
                "evidence": "Meta Ad Library (live)",
            })
    if channels.get("facebook_instagram", {}).get("status") == "needs_connection":
        opportunities.append({
            "area": "social",
            "action": "Connect Facebook/Instagram so we can measure and grow real engagement",
            "evidence": "no connected account",
        })

    # --- Build 5: what competitors actually publish, and what to do about it ---
    intel = competitor_intel or []
    if moves:
        # evidence-backed moves lead: they are specific and checkable
        opportunities = moves + opportunities

    report = {
        "client": client_block,
        "client_content": client_content or {},
        "channels": channels,
        "competitors": competitors,
        "competitor_ads": active_ads,
        "competitor_intel": intel,
        "opportunities": opportunities,
        "summary": {
            "website_score": c_score,
            "competitors_analyzed": len(competitors) or len(intel),
            "competitors_discovered": sum(1 for c in intel
                                          if c.get("source", "").startswith(("meta", "search"))),
            "competitors_advertising": sum(1 for c in intel
                                           if (c.get("ads") or {}).get("active_ads", 0) > 0),
            "competitor_active_ads": sum(a.get("active_ad_count", 0) for a in active_ads),
            "channels_needing_connection": [k for k, v in channels.items()
                                            if v.get("status") == "needs_connection"],
            "data_confidence": "high" if not gaps else ("partial" if len(gaps) < 4 else "low"),
            "top_priority": opportunities[0]["action"] if opportunities else "presence looks healthy — focus on volume",
        },
    }
    return report, gaps
