"""What competitors actually publish — cadence, themes, offers, platform mix.

The master prompt asks for competitors' **posts** and **running ads**. Those two
are not equally obtainable, and pretending otherwise would be the exact
fabrication this platform forbids:

* **Running ads — fully obtainable.** The Meta Ad Library is a public
  transparency API. We get live creative text, when each ad started, and which
  platforms it runs on. Everything in `ad_intel()` below is computed from that
  real data.
* **Organic social posts — NOT obtainable.** Meta removed public Page-post reads
  in 2018; reading another business's Facebook or Instagram posts requires that
  business to grant your app access, which a competitor will not do. Scraping
  breaks Meta's terms, is actively blocked, and would fail silently in
  production. So this module never claims to have read them: it raises a named
  gap saying what would actually unlock it.
* **Website publishing — obtainable.** A site's sitemap and RSS feed exist to be
  read by machines. `content_intel()` measures how often a competitor publishes
  and what about, from those.

The output is evidence with provenance, so the client can check any claim.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

from .fetchers import gap

STOP = set("""a an the and or but if then than that this these those for from with without
into onto over under about above below to of in on at by as is are was were be been being
you your yours we our ours us they them their it its i me my he she his her not no yes do
does did done have has had will would can could should may might must more most other some
any each new now get got make made just like also very best top great good free only all
one two three book call now today visit click here see learn""".split())

MONEYISH = re.compile(r"(?:rs\.?|pkr|usd|aed|\$|₨|€|£)\s?[\d][\d,]*|\b\d{1,3}\s?%\s*off\b", re.I)
CTA = re.compile(r"\b(book now|call now|order now|shop now|sign up|learn more|get a quote|"
                 r"whatsapp|dm us|message us|contact us|limited time|today only)\b", re.I)


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", (text or "").lower())
            if w not in STOP]


def _days_since(datestr: str) -> int | None:
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            d = datetime.strptime(datestr[:19] if "T" in datestr else datestr[:10], fmt[:19]
                                  if "T" in datestr else "%Y-%m-%d")
            return (datetime.now(timezone.utc).replace(tzinfo=None) - d).days
        except (ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
def ad_intel(ad_result: dict) -> dict:
    """Read one competitor's live ads into something actionable.

    `ad_result` is what fetchers.fetch_competitor_ads returned. Everything here is
    counted from real creatives — no estimation of spend, which the Ad Library
    does not give us and we therefore never state.
    """
    if not ad_result or ad_result.get("error"):
        return {"status": "unavailable", "reason": (ad_result or {}).get("error", "no data")}

    ads = ad_result.get("ads") or []
    if not ads:
        return {"status": "observed", "active_ads": 0,
                "note": "no active ads found for this competitor in this country"}

    copies = [a.get("copy") or "" for a in ads]
    platforms = Counter()
    for a in ads:
        for p in (a.get("platforms") or []):
            platforms[str(p).lower()] += 1

    ages = [d for d in (_days_since(a.get("since") or "") for a in ads) if d is not None]
    longest = max(ages) if ages else None
    newest = min(ages) if ages else None

    themes = [w for w, n in Counter(w for c in copies for w in set(_words(c))).most_common(8)
              if n > 1] or [w for w, _ in Counter(
                  w for c in copies for w in _words(c)).most_common(5)]
    offers = sorted({m.group(0).strip() for c in copies for m in MONEYISH.finditer(c)})
    ctas = [c for c, _ in Counter(m.group(0).lower()
                                  for c_ in copies for m in CTA.finditer(c_)).most_common(4)]

    return {
        "status": "observed",
        "active_ads": len(ads),
        "themes": themes,                       # what they keep saying
        "offers_advertised": offers[:6],        # THEIR claims, quoted, not ours
        "calls_to_action": ctas,
        "platform_mix": dict(platforms),
        "longest_running_days": longest,        # a long-running ad is a WINNING ad
        "newest_ad_days": newest,
        "sample_copy": [c[:200] for c in copies[:3]],
        "evidence": "Meta Ad Library (live, public transparency data)",
    }


def content_intel(cadence: dict) -> dict:
    """How often a competitor publishes, and about what — from their sitemap/feed."""
    if not cadence or cadence.get("error"):
        return {"status": "unavailable", "reason": (cadence or {}).get("error", "no data")}

    dates = cadence.get("dates") or []
    ages = [d for d in (_days_since(x) for x in dates) if d is not None]
    last_30 = sum(1 for d in ages if d <= 30)
    last_90 = sum(1 for d in ages if d <= 90)
    topics = [w for w, _ in Counter(
        w for u in cadence.get("recent_urls", [])
        for w in _words(re.sub(r"[-_/]", " ", u.rsplit("/", 2)[-1]))).most_common(8)]

    return {
        "status": "observed",
        "source": cadence.get("source"),
        "pages_with_dates": cadence.get("entries_with_dates"),
        "published_last_30_days": last_30,
        "published_last_90_days": last_90,
        "days_since_last_publish": min(ages) if ages else None,
        "recent_topics": topics,
        "evidence": f"the site's own {cadence.get('source', 'sitemap')}",
    }


def social_posts_gap(name: str) -> dict:
    """The honest position on reading a competitor's organic social posts."""
    return gap("competitor_posts", "no_lawful_api",
               f"organic Facebook/Instagram posts for '{name}' cannot be read: Meta removed "
               f"public Page-post access, and scraping breaks their terms. What we CAN see "
               f"is their live ads (below). A licensed social-listening data provider would "
               f"be the only lawful way to add organic posts.")


# ---------------------------------------------------------------------------
def compare(client_intel: dict, competitors: list[dict]) -> list[dict]:
    """Turn the intel into moves, stated only where the evidence supports them."""
    moves: list[dict] = []
    observed = [c for c in competitors if (c.get("ads") or {}).get("status") == "observed"]
    advertising = [c for c in observed if (c["ads"].get("active_ads") or 0) > 0]

    if advertising:
        total = sum(c["ads"]["active_ads"] for c in advertising)
        moves.append({
            "area": "ads",
            "action": f"{len(advertising)} of {len(competitors)} competitors are advertising "
                      f"right now ({total} live ads between them) — being absent from the "
                      f"auction is a choice they are profiting from",
            "evidence": "Meta Ad Library (live)",
        })
        # a theme every rival uses is table stakes; one nobody uses is an opening
        counts = Counter(t for c in advertising for t in c["ads"].get("themes", []))
        crowded = [t for t, n in counts.items() if n >= max(2, len(advertising) // 2)]
        if crowded:
            moves.append({
                "area": "positioning",
                "action": f"Every competitor is saying the same thing ({', '.join(crowded[:4])}) "
                          f"— say something they are not, rather than louder",
                "evidence": f"recurring words across {len(advertising)} competitors' live ad copy",
            })
        longest = max((c for c in advertising if c["ads"].get("longest_running_days")),
                      key=lambda c: c["ads"]["longest_running_days"], default=None)
        if longest:
            moves.append({
                "area": "ads",
                "action": f"{longest['name']} has kept an ad running for "
                          f"{longest['ads']['longest_running_days']} days — an ad nobody "
                          f"turns off is one that works. Study that angle first",
                "evidence": "ad delivery start dates in the Ad Library",
            })
        offers = [(c["name"], o) for c in advertising for o in c["ads"].get("offers_advertised", [])]
        if offers:
            moves.append({
                "area": "offer",
                "action": "Competitors are advertising explicit offers (" +
                          "; ".join(f"{n}: {o}" for n, o in offers[:3]) +
                          ") — decide deliberately whether to match, beat, or refuse to compete "
                          "on price",
                "evidence": "quoted verbatim from their live ad creatives",
            })

    pub = [c for c in competitors if (c.get("content") or {}).get("status") == "observed"]
    if pub:
        rates = [(c["name"], c["content"].get("published_last_90_days") or 0) for c in pub]
        busiest = max(rates, key=lambda x: x[1], default=None)
        mine = (client_intel or {}).get("published_last_90_days")
        if busiest and busiest[1] > 0:
            if not isinstance(mine, int):
                # we could not measure the client's own cadence — state the fact only
                moves.append({
                    "area": "content",
                    "action": f"{busiest[0]} published {busiest[1]} pages in the last 90 days. "
                              f"We could not measure yours (no sitemap or feed found), so this "
                              f"is their number, not a comparison",
                    "evidence": "their sitemap/feed timestamps",
                })
            elif busiest[1] > mine:
                moves.append({
                    "area": "content",
                    "action": f"{busiest[0]} published {busiest[1]} pages in the last 90 days "
                              f"against your {mine} — publishing volume is the cheapest gap "
                              f"on this list",
                    "evidence": "sitemap/feed timestamps",
                })
            elif mine >= busiest[1]:
                # do NOT invent a problem: say plainly that they are ahead or level
                moves.append({
                    "area": "content",
                    "action": f"You published {mine} pages in the last 90 days versus "
                              f"{busiest[1]} for {busiest[0]} — you are ahead on volume here, "
                              f"so spend the effort on distribution rather than more pages",
                    "evidence": "sitemap/feed timestamps",
                })
    return moves
