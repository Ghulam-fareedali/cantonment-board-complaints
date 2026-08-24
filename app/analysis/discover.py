"""Finding out who the client is actually competing against.

Until now the engine only examined competitors the client typed into the signup
form. Most business owners either leave that blank or name the rival they resent
rather than the one taking their customers. This finds them from evidence.

WHAT IS ACTUALLY OBTAINABLE — and what is not
---------------------------------------------
* **Meta Ad Library** (`ads_archive`) is a public transparency API. Searching it
  by industry terms and country returns the pages *currently spending money* in
  that market. Those are the real competitors: not everyone in the category, but
  everyone actively bidding for the same attention. This needs only the app token
  the platform already wants for competitor-ad reads.
* **Web search** finds the ones who rank rather than pay. There is no free,
  licence-clean search API, so it is a configurable adapter (Brave, Serper, or a
  Google Programmable Search engine). Unset means an honest gap, not a guess.
* **Scraping Google, Facebook or Instagram directly is not an option.** It breaks
  their terms, it is actively blocked, and a growth engine that depends on it
  breaks silently in production. We do not do it.

Everything returned here is evidence the client can check for themselves: a page
that is running ads, or a site that ranks. Nothing is inferred.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import requests

from .fetchers import GRAPH, HTTP_TIMEOUT, UA, gap

MAX_DISCOVERED = 5


def _host(url: str) -> str:
    if not url:
        return ""
    if not urlparse(url).scheme:
        url = "https://" + url
    return urlparse(url).netloc.lower().removeprefix("www.")


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _own_identities(website: str, social_links: dict, business_name: str) -> set:
    """Everything that IS the client, so we never hand them themselves as a rival."""
    own = {_norm_name(business_name)} if business_name else set()
    if website:
        own.add(_host(website))
        own.add(_norm_name(_host(website).split(".")[0]))
    for url in (social_links or {}).values():
        if not isinstance(url, str):
            continue
        own.add(_host(url))
        tail = url.rstrip("/").split("/")[-1]
        own.add(_norm_name(tail))
    return {o for o in own if o}


# ---------------------------------------------------------------------------
def discover_via_ad_library(app_token: str, terms: list[str], country: str,
                            own: set, limit: int = MAX_DISCOVERED) -> tuple[list, list]:
    """Pages actively advertising in this category and country.

    Returns (competitors, gaps). Each competitor carries the evidence that found
    it, so the client can verify the claim rather than trust it.
    """
    if not app_token:
        return [], [gap("competitor_discovery", "no_ad_library_token",
                        "set META_AD_LIBRARY_TOKEN (APP_ID|APP_SECRET) to discover "
                        "who is advertising in this market")]
    found: dict[str, dict] = {}
    gaps: list[dict] = []
    for term in [t for t in terms if t][:3]:
        try:
            r = requests.get(f"{GRAPH}/ads_archive", timeout=HTTP_TIMEOUT, params={
                "access_token": app_token,
                "search_terms": term,
                "ad_reached_countries": f'["{country}"]',
                "ad_active_status": "ACTIVE",
                "fields": "page_id,page_name,ad_creative_bodies,ad_delivery_start_time,"
                          "publisher_platforms",
                "limit": 50,
            })
        except requests.RequestException as e:
            gaps.append(gap("competitor_discovery", f"unreachable: {type(e).__name__}", term))
            continue
        if r.status_code >= 400:
            gaps.append(gap("competitor_discovery", f"adlib_{r.status_code}", r.text[:200]))
            continue
        for ad in r.json().get("data", []):
            name = ad.get("page_name") or ""
            key = _norm_name(name)
            if not key or key in own:
                continue
            entry = found.setdefault(key, {
                "name": name, "page_id": ad.get("page_id"),
                "source": "meta_ad_library",
                "evidence": f"actively advertising in {country} for '{term}'",
                "observed_ads": 0, "matched_terms": set(),
            })
            entry["observed_ads"] += 1
            entry["matched_terms"].add(term)
    # the ones spending most across the most search terms are the real rivals
    ranked = sorted(found.values(),
                    key=lambda c: (len(c["matched_terms"]), c["observed_ads"]), reverse=True)
    for c in ranked:
        c["matched_terms"] = sorted(c["matched_terms"])
    return ranked[:limit], gaps


def discover_via_search(terms: list[str], country: str, own: set,
                        limit: int = MAX_DISCOVERED) -> tuple[list, list]:
    """Competitors who rank rather than pay. Requires a configured search provider.

        SEARCH_PROVIDER=brave   BRAVE_SEARCH_KEY=...
        SEARCH_PROVIDER=serper  SERPER_KEY=...
    """
    provider = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
    if not provider:
        return [], [gap("competitor_discovery", "no_search_provider",
                        "set SEARCH_PROVIDER (brave|serper) + its API key to also find "
                        "competitors who rank organically")]
    query = f"{terms[0]} {country}" if terms else country
    try:
        if provider == "brave":
            key = os.environ.get("BRAVE_SEARCH_KEY", "")
            if not key:
                return [], [gap("competitor_discovery", "no_search_key", "BRAVE_SEARCH_KEY unset")]
            r = requests.get("https://api.search.brave.com/res/v1/web/search",
                             headers={"X-Subscription-Token": key, "Accept": "application/json"},
                             params={"q": query, "count": 20}, timeout=HTTP_TIMEOUT)
            items = [{"title": w.get("title"), "url": w.get("url")}
                     for w in (r.json().get("web", {}) or {}).get("results", [])]
        elif provider == "serper":
            key = os.environ.get("SERPER_KEY", "")
            if not key:
                return [], [gap("competitor_discovery", "no_search_key", "SERPER_KEY unset")]
            r = requests.post("https://google.serper.dev/search",
                              headers={"X-API-KEY": key}, json={"q": query, "num": 20},
                              timeout=HTTP_TIMEOUT)
            items = [{"title": w.get("title"), "url": w.get("link")}
                     for w in r.json().get("organic", [])]
        else:
            return [], [gap("competitor_discovery", "unknown_search_provider", provider)]
    except requests.RequestException as e:
        return [], [gap("competitor_discovery", f"search_unreachable: {type(e).__name__}", "")]
    except (ValueError, KeyError):
        return [], [gap("competitor_discovery", "search_response_unreadable", provider)]

    seen, out = set(), []
    # directories and marketplaces are not competitors in the sense that matters
    skip = re.compile(r"(facebook|instagram|linkedin|youtube|tiktok|twitter|x\.com|yelp|"
                      r"tripadvisor|wikipedia|olx|daraz|amazon|justdial|maps\.google)", re.I)
    for it in items:
        host = _host(it.get("url", ""))
        if not host or host in own or host in seen or skip.search(host):
            continue
        seen.add(host)
        out.append({"name": (it.get("title") or host).split("|")[0].split("-")[0].strip(),
                    "website": f"https://{host}", "source": f"search:{provider}",
                    "evidence": f"ranks organically for '{query}'"})
        if len(out) >= limit:
            break
    return out, []


# ---------------------------------------------------------------------------
def discover_competitors(*, business_name: str, industry: str, website: str,
                         social_links: dict, country: str, provided: list,
                         app_token: str, limit: int = MAX_DISCOVERED) -> tuple[list, list]:
    """Competitors the client named, plus the ones the evidence points to.

    Client-provided entries always come first and are never dropped — they know
    their market. Discovery only fills the remaining slots.
    """
    own = _own_identities(website, social_links, business_name)
    out = [dict(c, source=c.get("source", "client_provided"),
                evidence=c.get("evidence", "named by the client at signup"))
           for c in (provided or []) if c.get("name") or c.get("website")]
    known = {_norm_name(c.get("name", "")) for c in out} | {_host(c.get("website", "")) for c in out}
    known.discard("")

    city = ""
    terms = [t for t in [f"{industry} {city}".strip(), industry] if t]
    gaps: list[dict] = []

    for finder in (lambda: discover_via_ad_library(app_token, terms, country, own,
                                                   limit=limit),
                   lambda: discover_via_search(terms, country, own, limit=limit)):
        if len(out) >= limit:
            break
        found, g = finder()
        gaps += g
        for c in found:
            ident = _norm_name(c.get("name", "")) or _host(c.get("website", ""))
            if ident in known or ident in own:
                continue
            known.add(ident)
            out.append(c)
            if len(out) >= limit:
                break

    if not out:
        gaps.append(gap("competitors", "none_identified",
                        "no competitors were provided and none could be discovered — "
                        "add them at signup or connect the sources above"))
    return out[:limit], gaps
