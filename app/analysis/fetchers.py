"""Real data fetchers for the Growth Engine analysis layer.

Design rule (inherited, non-negotiable): NEVER fabricate. Every fetch either
returns real observed data, or a structured `gap` explaining exactly what was
missing (unreachable page, no API token, blocked). The analysis agent turns
gaps into ops.alerts so TechDeva/the client can fix access — it never invents
follower counts, spend, or competitor claims.

What is real here:
  * Website + landing pages  -> real HTTP GET + HTML parse (requests + bs4)
  * Own Facebook/Instagram   -> real Meta Graph API using the TENANT'S OWN token
                                (already collected via the platform's OAuth /integrations)
  * Competitor running ads    -> real Meta Ad Library API (ads_archive) — public
                                transparency data, queried with an app token
  * LinkedIn / others w/o an accessible API -> honest gap (needs connection)

Networked deployment (the VPS) reaches these endpoints. In a sandbox without
open internet, each call degrades to a gap — which is the correct, honest
behavior, not a failure.
"""
from __future__ import annotations
import os
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..safe_http import get as safe_get, assert_fetchable, BlockedURL, normalise

UA = "Mozilla/5.0 (compatible; TechDevaGrowthBot/1.0; +https://techdeva.net/bot)"
HTTP_TIMEOUT = float(os.environ.get("ANALYSIS_HTTP_TIMEOUT", "12"))
GRAPH = "https://graph.facebook.com/v19.0"


def gap(channel: str, reason: str, detail: str = "") -> dict:
    """A structured 'we could not get real data here' marker. Never a fake value."""
    return {"channel": channel, "reason": reason, "detail": detail}


# ----------------------------------------------------------------------------
# WEBSITE + PAGES  (real HTTP + HTML analysis)
# ----------------------------------------------------------------------------
def fetch_website(url: str) -> dict:
    """Fetch a URL and extract real on-page + SEO signals. Returns {'error':...}
    on failure (caller converts to a gap) — never invented content."""
    url = normalise(url)
    if not url:
        return {"error": "no_url"}
    # SSRF: /signup is public, so this URL can come from a stranger. safe_get
    # refuses private, loopback and link-local addresses and re-checks every
    # redirect hop, so a public host cannot 302 us onto the Docker network.
    try:
        resp = safe_get(url, headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
    except BlockedURL as e:
        return {"error": f"blocked: {e}", "url": url}
    except requests.RequestException as e:
        return {"error": f"unreachable: {type(e).__name__}", "url": url}

    out: dict = {
        "url": url,
        "final_url": resp.url,
        "status": resp.status_code,
        "https": resp.url.startswith("https://"),
    }
    if resp.status_code >= 400:
        out["error"] = f"http_{resp.status_code}"
        return out

    soup = BeautifulSoup(resp.text, "html.parser")

    title = (soup.title.string or "").strip() if soup.title else ""
    def meta(name=None, prop=None):
        tag = soup.find("meta", attrs={"name": name}) if name else soup.find("meta", attrs={"property": prop})
        return (tag.get("content") or "").strip() if tag else ""

    h1s = [h.get_text(" ", strip=True) for h in soup.find_all("h1")][:10]
    h2s = [h.get_text(" ", strip=True) for h in soup.find_all("h2")][:20]
    body_text = soup.get_text(" ", strip=True)
    words = len(body_text.split())

    # collect internal links (for optional page crawl)
    host = urlparse(resp.url).netloc
    internal = []
    for a in soup.find_all("a", href=True):
        href = urljoin(resp.url, a["href"])
        if urlparse(href).netloc == host and href not in internal:
            internal.append(href)

    out.update({
        "title": title,
        "meta_description": meta(name="description"),
        "og_title": meta(prop="og:title"),
        "og_image": meta(prop="og:image"),
        "h1": h1s,
        "h2": h2s,
        "word_count": words,
        "internal_links": internal[:40],
        "signals": {
            "has_title": bool(title),
            "title_len": len(title),
            "has_meta_description": bool(meta(name="description")),
            "has_viewport": bool(soup.find("meta", attrs={"name": "viewport"})),
            "has_og_tags": bool(meta(prop="og:title") or meta(prop="og:image")),
            "has_structured_data": bool(soup.find_all("script", attrs={"type": "application/ld+json"})),
            "img_count": len(soup.find_all("img")),
            "img_missing_alt": sum(1 for i in soup.find_all("img") if not i.get("alt")),
            "has_analytics": bool(re.search(r"gtag\(|google-analytics|gtm\.js|fbq\(", resp.text)),
            "has_pixel": bool(re.search(r"fbq\(|facebook\.com/tr", resp.text)),
        },
        "sample_text": body_text[:1200],
    })
    return out


def analyze_site(url: str, max_pages: int = 3) -> dict:
    """Fetch the homepage and up to (max_pages-1) internal pages; summarize."""
    home = fetch_website(url)
    result = {"home": home, "pages": []}
    if home.get("error"):
        return result
    # shallow crawl: prioritize obvious money pages
    priority = re.compile(r"(service|pricing|price|book|contact|about|treatment|menu)", re.I)
    candidates = sorted(home.get("internal_links", []),
                        key=lambda u: (0 if priority.search(u) else 1))
    for link in candidates[: max(0, max_pages - 1)]:
        page = fetch_website(link)
        result["pages"].append({"url": link,
                                "title": page.get("title"),
                                "word_count": page.get("word_count"),
                                "error": page.get("error")})
    return result


# ----------------------------------------------------------------------------
# OWN SOCIAL PRESENCE  (real Meta Graph, tenant's own token)
# ----------------------------------------------------------------------------
def fetch_meta_presence(access_token: str, page_id: str | None) -> dict:
    """Real Facebook/Instagram presence for the CLIENT'S OWN page via Graph API,
    using the tenant's connected token. No token/page -> caller records a gap."""
    if not access_token or not page_id:
        return {"error": "meta_not_connected"}
    try:
        fields = "name,fan_count,followers_count,posts.limit(10){message,created_time,likes.summary(true),comments.summary(true)}"
        r = requests.get(f"{GRAPH}/{page_id}", params={"fields": fields, "access_token": access_token},
                        timeout=HTTP_TIMEOUT)
        if r.status_code >= 400:
            return {"error": f"graph_{r.status_code}", "detail": r.text[:300]}
        data = r.json()
        posts = (data.get("posts") or {}).get("data", [])
        return {
            "page_name": data.get("name"),
            "followers": data.get("followers_count") or data.get("fan_count"),
            "recent_post_count": len(posts),
            "recent_engagement": [
                {"likes": (p.get("likes") or {}).get("summary", {}).get("total_count", 0),
                 "comments": (p.get("comments") or {}).get("summary", {}).get("total_count", 0),
                 "when": p.get("created_time")}
                for p in posts
            ],
        }
    except requests.RequestException as e:
        return {"error": f"unreachable: {type(e).__name__}"}


# ----------------------------------------------------------------------------
# COMPETITOR ADS  (real Meta Ad Library — public transparency data)
# ----------------------------------------------------------------------------
def fetch_competitor_ads(app_token: str, page_name: str, country: str = "PK",
                        limit: int = 20) -> dict:
    """Real currently-running ads for a competitor via the Meta Ad Library API.
    Requires an app access token (public transparency endpoint). No token ->
    honest gap. This is how we see what competitors actively spend on."""
    if not app_token:
        return {"error": "no_ad_library_token"}
    try:
        params = {
            "access_token": app_token,
            "search_terms": page_name,
            "ad_reached_countries": f'["{country}"]',
            "ad_active_status": "ACTIVE",
            "fields": "page_name,ad_creative_bodies,ad_delivery_start_time,publisher_platforms",
            "limit": limit,
        }
        r = requests.get(f"{GRAPH}/ads_archive", params=params, timeout=HTTP_TIMEOUT)
        if r.status_code >= 400:
            return {"error": f"adlib_{r.status_code}", "detail": r.text[:300]}
        ads = r.json().get("data", [])
        return {
            "competitor": page_name,
            "active_ad_count": len(ads),
            "ads": [
                {"page": a.get("page_name"),
                 "copy": (a.get("ad_creative_bodies") or [""])[0][:280],
                 "since": a.get("ad_delivery_start_time"),
                 "platforms": a.get("publisher_platforms")}
                for a in ads
            ],
        }
    except requests.RequestException as e:
        return {"error": f"unreachable: {type(e).__name__}"}


def fetch_competitor_site(url: str) -> dict:
    """A competitor's website, analyzed the same way as the client's."""
    return fetch_website(url)


# ----------------------------------------------------------------------------
# OWN INSTAGRAM  (real Graph data via the linked Page — NOT the same as Facebook)
# ----------------------------------------------------------------------------
def fetch_instagram_presence(access_token: str, page_id: str | None) -> dict:
    """The client's OWN Instagram business account, reached through the Facebook
    Page it is linked to (this is the only supported route: there is no way to
    read an IG account that has not granted access).

    Previously Instagram was simply marked 'connected' because the Meta token
    covers it — which told the client nothing about Instagram at all. This reads
    the real account: followers, media count, and engagement on recent posts.
    """
    if not access_token or not page_id:
        return {"error": "instagram_not_connected"}
    fields = ("instagram_business_account{username,followers_count,media_count,"
              "media.limit(10){like_count,comments_count,timestamp,caption,media_type}}")
    try:
        r = requests.get(f"{GRAPH}/{page_id}",
                         params={"fields": fields, "access_token": access_token},
                         timeout=HTTP_TIMEOUT)
        if r.status_code >= 400:
            return {"error": f"graph_{r.status_code}", "detail": r.text[:300]}
        ig = (r.json() or {}).get("instagram_business_account")
        if not ig:
            return {"error": "no_instagram_linked",
                    "detail": "this Facebook Page has no Instagram business account linked"}
        media = (ig.get("media") or {}).get("data", [])
        return {
            "username": ig.get("username"),
            "followers": ig.get("followers_count"),
            "media_count": ig.get("media_count"),
            "recent_post_count": len(media),
            "recent_engagement": [
                {"likes": m.get("like_count", 0), "comments": m.get("comments_count", 0),
                 "when": m.get("timestamp"), "type": m.get("media_type")}
                for m in media
            ],
        }
    except requests.RequestException as e:
        return {"error": f"unreachable: {type(e).__name__}"}


# ----------------------------------------------------------------------------
# PUBLISHING CADENCE  (real, from a site's own sitemap or feed)
# ----------------------------------------------------------------------------
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_RSS_DATE = re.compile(r"<pubDate>\s*(.*?)\s*</pubDate>", re.I | re.S)


def _parse_dates_from_sitemap(xml: str) -> list:
    """<lastmod> values, and the <loc> paths they belong to."""
    out = []
    for block in re.findall(r"<url>(.*?)</url>", xml, re.S | re.I):
        loc = re.search(r"<loc>\s*(.*?)\s*</loc>", block, re.S | re.I)
        mod = re.search(r"<lastmod>\s*(.*?)\s*</lastmod>", block, re.S | re.I)
        if mod and _DATE_RE.search(mod.group(1)):
            out.append({"url": loc.group(1) if loc else "",
                        "date": _DATE_RE.search(mod.group(1)).group(0)})
    return out


def fetch_content_cadence(site_url: str, max_entries: int = 400) -> dict:
    """How often a site actually publishes, from its own sitemap or RSS feed.

    This is the honest substitute for 'read their social posts': a site's sitemap
    and feed are published FOR machines to read, so using them is legitimate and
    stable. Social post reads are not available (see the gap raised in
    competitor.py) and are never faked here.
    """
    site_url = normalise(site_url)
    if not site_url:
        return {"error": "no_url"}
    try:
        assert_fetchable(site_url)
    except BlockedURL as e:
        return {"error": f"blocked: {e}"}
    root = f"{urlparse(site_url).scheme}://{urlparse(site_url).netloc}"

    entries, source, errors = [], None, []
    for path in ("/sitemap.xml", "/sitemap_index.xml", "/rss.xml", "/feed", "/blog/feed"):
        try:
            r = safe_get(root + path, headers={"User-Agent": UA},
                         timeout=HTTP_TIMEOUT)
        except BlockedURL:
            errors.append(f"{path}: blocked")
            continue
        except requests.RequestException as e:
            errors.append(f"{path}: {type(e).__name__}")
            continue
        if r.status_code >= 400 or "<" not in r.text[:200]:
            continue
        body = r.text
        # a sitemap index points at more sitemaps — follow the first couple
        if "<sitemapindex" in body[:500].lower():
            for child in re.findall(r"<loc>\s*(.*?)\s*</loc>", body, re.S | re.I)[:2]:
                try:
                    # a sitemap index is attacker-supplied content: its <loc>
                    # entries are URLs we would otherwise fetch unchecked
                    cr = safe_get(child, headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
                    entries += _parse_dates_from_sitemap(cr.text)
                except (requests.RequestException, BlockedURL):
                    continue
            source = source or "sitemap_index"
        elif "<urlset" in body[:500].lower():
            entries += _parse_dates_from_sitemap(body)
            source = source or "sitemap"
        elif "<rss" in body[:500].lower() or "<feed" in body[:500].lower():
            for raw in _RSS_DATE.findall(body):
                m = re.search(r"(\d{1,2})\s+(\w{3})\w*\s+(\d{4})", raw)
                if m:
                    months = {m_: i + 1 for i, m_ in enumerate(
                        ["jan", "feb", "mar", "apr", "may", "jun",
                         "jul", "aug", "sep", "oct", "nov", "dec"])}
                    mon = months.get(m.group(2)[:3].lower())
                    if mon:
                        entries.append({"url": "", "date": f"{m.group(3)}-{mon:02d}-"
                                                           f"{int(m.group(1)):02d}"})
            source = source or "rss"
        if entries:
            break

    if not entries:
        return {"error": "no_sitemap_or_feed", "detail": "; ".join(errors[:3])}

    entries = sorted(entries, key=lambda e: e["date"], reverse=True)[:max_entries]
    return {"source": source, "entries_with_dates": len(entries),
            "most_recent": entries[0]["date"],
            "dates": [e["date"] for e in entries],
            "recent_urls": [e["url"] for e in entries[:20] if e["url"]]}
