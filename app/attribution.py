"""Attribution — turning an ad click into a revenue figure you can slice.

The problem this solves, stated plainly: spend is recorded per campaign, revenue
was recorded per lead *source* (an enum with five values). So the best the
platform could ever say was "your ads made 12x" — never "*this* ad made 12x".
Every optimisation decision and every profit-share conversation needs the second
sentence, not the first.

THE CHAIN
    click on an ad  ->  click id in the landing/WhatsApp URL
      -> captured on the lead                    (leads.click_id)
      -> resolved to a campaign where possible   (leads.campaign_id)
      -> FROZEN onto the revenue row when money is recorded

WHY THE FREEZE MATTERS
Attribution is written at the moment revenue is earned and never recomputed. If
the click->campaign mapping is corrected next month, last month's ROAS does not
silently change underneath a report someone already acted on. Historical
financial figures are not rewritten — the same rule the CRM audit insisted on.

THE MODEL, NAMED
`first_touch_click`: the campaign that produced the lead gets the credit for
everything that lead ever spends. It is one honest choice among several
(last-touch, linear, time-decay) and the platform states which one it used on
every report rather than leaving the reader to assume.

WHAT IT REFUSES TO DO
  * It never spreads unattributed revenue across campaigns to make a total tidy.
    Revenue with no click stays in an explicit `unattributed` bucket.
  * It never guesses a campaign from timing, geography, or "probably this one".
    No click id, or a click id that matches no known campaign, means NULL.
  * It never treats a missing denominator as zero. No spend => ROAS is None.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, parse_qs

# Click id parameters, in priority order. Each ad platform stamps its own.
# ctwa_clid is Meta's click-to-WhatsApp id — the single most important one here,
# because click-to-WhatsApp is how the clinic funnel actually works.
CLICK_PARAMS: list[tuple[str, str]] = [
    ("ctwa_clid", "meta"),      # Meta click-to-WhatsApp
    ("fbclid", "meta"),         # Meta web click
    ("gclid", "google"),        # Google Ads
    ("gbraid", "google"),       # Google, iOS web-to-app
    ("wbraid", "google"),       # Google, iOS app-to-web
    ("ttclid", "tiktok"),
    ("msclkid", "microsoft"),
    ("li_fat_id", "linkedin"),
    ("twclid", "twitter"),
]

PARAM_TO_SOURCE = {p: s for p, s in CLICK_PARAMS}

# A click id must look like one. This rejects obvious junk without being so
# strict that a platform changing its format silently drops attribution.
CLICK_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{6,512}$")

# Meta's click-to-WhatsApp prefills the first message with a referral block.
# Hermes receives it as plain text, so the id has to be recovered from there.
INLINE_CLICK_RE = re.compile(
    r"\b(ctwa_clid|fbclid|gclid|gbraid|wbraid|ttclid|msclkid|li_fat_id|twclid)"
    r"[=:\s]+([A-Za-z0-9._\-]{6,512})", re.I)


def valid_click_id(value: str | None) -> bool:
    return bool(value) and bool(CLICK_ID_RE.match(str(value).strip()))


def extract_click(url_or_text: str | None) -> dict:
    """Pull a click id out of a landing URL or a raw inbound message.

    Returns {} when there isn't one. An organic lead is a real and common
    outcome, not a failure — and it must never be given a made-up click id.
    """
    if not url_or_text:
        return {}
    raw = str(url_or_text).strip()

    # 1. Proper URL with query parameters.
    try:
        parsed = urlparse(raw)
        if parsed.query:
            qs = parse_qs(parsed.query)
            for param, source in CLICK_PARAMS:
                for key in (param, param.upper()):
                    if key in qs and qs[key]:
                        cid = qs[key][0].strip()
                        if valid_click_id(cid):
                            return {"click_id": cid, "click_source": source,
                                    "param": param, "landing_url": raw[:2000]}
    except (ValueError, AttributeError):
        pass

    # 2. Bare text — e.g. a click-to-WhatsApp referral pasted into a message.
    m = INLINE_CLICK_RE.search(raw)
    if m:
        param = m.group(1).lower()
        cid = m.group(2).strip()
        if valid_click_id(cid):
            return {"click_id": cid,
                    "click_source": PARAM_TO_SOURCE.get(param, "unknown"),
                    "param": param, "landing_url": raw[:2000]}
    return {}


def merge_click(explicit: dict | None, discovered: dict | None) -> dict:
    """An explicitly supplied click id wins over one scraped from text.

    A caller that states `click_id` knows more than a regex does. Discovered
    fields fill in only what the explicit one left blank.
    """
    explicit = {k: v for k, v in (explicit or {}).items() if v}
    if not valid_click_id(explicit.get("click_id")):
        explicit.pop("click_id", None)
        explicit.pop("click_source", None)
    out = dict(discovered or {})
    out.update(explicit)
    return out


# ---------------------------------------------------------------------------
# ROAS / CPL over attributed rows — pure, so every branch is testable
# ---------------------------------------------------------------------------
def _f(v):
    """Coerce to float or None. A missing metric must never become 0."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def campaign_roas(spend, revenue) -> float | None:
    """None when there is no spend to divide by. Never 0, never infinity."""
    s, r = _f(spend), _f(revenue)
    if not s:              # 0 or None -> no denominator
        return None
    if r is None:          # no revenue recorded at all is not the same as zero
        return None
    return round(r / s, 2)


def campaign_cpl(spend, leads) -> float | None:
    s, l = _f(spend), _f(leads)
    if not l:
        return None
    if s is None:
        return None
    return round(s / l, 2)


def build_attribution_report(campaign_rows: list[dict],
                             unattributed_revenue=None,
                             total_spend=None,
                             currency: str | None = None) -> dict:
    """One report: per-campaign ROAS/CPL plus an explicit unattributed bucket.

    `campaign_rows` are real rows: id, name, platform, spend, leads, revenue.
    Nothing is inferred and nothing is redistributed.
    """
    campaigns = []
    attributed_rev = 0.0
    any_rev = False

    for row in campaign_rows:
        spend, leads = _f(row.get("spend")), _f(row.get("leads"))
        revenue = _f(row.get("revenue"))
        if revenue is not None:
            attributed_rev += revenue
            any_rev = True
        campaigns.append({
            "campaign_id": row.get("id"),
            "name": row.get("name") or row.get("external_id") or "unnamed campaign",
            "platform": row.get("platform"),
            "spend": spend,
            "leads": int(leads) if leads is not None else None,
            "revenue": revenue,
            "roas": campaign_roas(spend, revenue),
            "cpl": campaign_cpl(spend, leads),
        })

    unattributed = _f(unattributed_revenue)

    # Total revenue exists if EITHER side has a figure. The case that matters:
    # every campaign null but organic revenue present. Reporting total=None there
    # would hide a real 0% coverage behind "no data", which reads as reassuring
    # when it is the opposite.
    if any_rev or unattributed is not None:
        total_rev = (attributed_rev if any_rev else 0.0) + (unattributed or 0.0)
    else:
        total_rev = None

    spend_total = _f(total_spend)
    if spend_total is None:
        spends = [c["spend"] for c in campaigns if c["spend"] is not None]
        spend_total = sum(spends) if spends else None

    # Coverage is the honesty metric: what share of revenue this report can
    # actually place. A low number means the ROAS figures are thin, and the
    # reader is told rather than left to assume.
    coverage = None
    if total_rev:            # a zero total has no meaningful coverage
        coverage = round((attributed_rev if any_rev else 0.0) / total_rev, 2)

    return {
        "attribution_model": "first_touch_click",
        "currency": currency,
        "campaigns": sorted(campaigns,
                            key=lambda c: (c["roas"] is None, -(c["roas"] or 0))),
        "attributed_revenue": attributed_rev if any_rev else None,
        "unattributed_revenue": unattributed,
        "total_revenue": total_rev,
        "total_spend": spend_total,
        "account_roas": campaign_roas(spend_total, total_rev),
        "attribution_coverage": coverage,
        "note": ("Revenue is credited to the campaign that produced the lead "
                 "(first touch). Revenue with no click id is reported as "
                 "unattributed and is never spread across campaigns."),
    }
