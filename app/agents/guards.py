"""Guards on generated output — the checks that stand between a language model
and a client's dashboard.

Two independent jobs, both fail-closed:

1. `unbacked_claims()` — the platform's oldest rule ("never invent facts") applied
   to generated copy. A model asked to write an ad will happily write "50% off,
   starting at Rs 5,000, results guaranteed". If a number or promise is not in the
   tenant's own config, the agent must NOT ship it. The task parks as needs_human
   instead, with the offending lines named.

2. `sanitize_svg()` — the Design agent's artwork is rendered INSIDE the client's
   logged-in dashboard, so untrusted markup there is a cross-site scripting hole.
   Scripts, event handlers, embedded objects and external references are stripped
   before the deliverable is ever stored.

Both are pure functions with no I/O, so they are cheap to test and impossible to
skip by mistake — the agents call them on every generation.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"

# ---------------------------------------------------------------------------
# 1. never invent facts
# ---------------------------------------------------------------------------
MONEY = re.compile(r"(?:rs\.?|pkr|usd|aed|\$|₨|€|£)\s?([\d][\d,]*(?:\.\d+)?)", re.I)
BARE_AMOUNT = re.compile(r"\b(\d{1,3}(?:,\d{3})+(?:\.\d+)?)\b")     # 12,000 / 1,250.50
PERCENT_OFFER = re.compile(r"\b(\d{1,3})\s?%\s*(?:off|discount|cheaper|less)\b", re.I)
PROMISES = re.compile(
    r"\b(guarantee[ds]?|money[- ]back|100%\s+(?:results|satisfaction|safe)|"
    r"no\.?\s?1\b|#1\b|best in (?:the )?(?:city|country|world)|"
    r"award[- ]winning|certified|licen[cs]ed|clinically proven|fda[- ]approved)\b", re.I)


def _config_numbers(config: dict) -> set:
    """Every number the tenant's own configuration actually states."""
    blob = json.dumps(config or {}, default=str)
    nums = set()
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", blob):
        try:
            nums.add(float(raw.replace(",", "")))
        except ValueError:
            pass
    return nums


def unbacked_claims(text: str, config: dict) -> list[str]:
    """Return every claim in `text` that the tenant config does not support.

    Empty list = safe to ship. Anything else and the caller must park the work
    rather than publish a number nobody authorised.
    """
    if not text:
        return []
    found: list[str] = []
    allowed = _config_numbers(config)
    blob = json.dumps(config or {}, default=str).lower()

    def amount_ok(raw: str) -> bool:
        try:
            return float(raw.replace(",", "")) in allowed
        except ValueError:
            return False

    for m in MONEY.finditer(text):
        if not amount_ok(m.group(1)):
            found.append(f"price not in config: {m.group(0).strip()}")
    for m in BARE_AMOUNT.finditer(text):
        if not amount_ok(m.group(1)):
            found.append(f"unsourced figure: {m.group(1)}")
    for m in PERCENT_OFFER.finditer(text):
        if m.group(1) not in blob:
            found.append(f"offer not in config: {m.group(0).strip()}")
    for m in PROMISES.finditer(text):
        if m.group(0).lower() not in blob:
            found.append(f"unverifiable promise: {m.group(0).strip()}")
    # stable, de-duplicated
    return sorted(set(found))


def scan_plan(obj, config: dict) -> list[str]:
    """Walk any generated structure and collect claims from every string in it."""
    out: list[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            out += scan_plan(v, config)
    elif isinstance(obj, list):
        for v in obj:
            out += scan_plan(v, config)
    elif isinstance(obj, str):
        out += unbacked_claims(obj, config)
    return sorted(set(out))


# ---------------------------------------------------------------------------
# 2. artwork that renders in a logged-in dashboard must be inert
# ---------------------------------------------------------------------------
BANNED_TAGS = {"script", "foreignobject", "iframe", "object", "embed", "link",
               "handler", "audio", "video", "use"}
URL_ATTRS = {"href", "{http://www.w3.org/1999/xlink}href", "src", "xlink:href"}


class UnsafeSVG(Exception):
    pass


def _local(tag: str) -> str:
    return tag.split("}")[-1].lower() if isinstance(tag, str) else ""


def sanitize_svg(svg: str) -> tuple[str, list[str]]:
    """Return (safe_svg, removed) or raise UnsafeSVG if it isn't usable at all.

    Removed items are reported, not hidden, so a suspicious generation is visible
    in the deliverable record instead of silently cleaned up.
    """
    if not svg or "<svg" not in svg:
        raise UnsafeSVG("not an SVG document")
    try:
        ET.register_namespace("", SVG_NS)
        ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
        root = ET.fromstring(svg.strip())
    except ET.ParseError as e:
        raise UnsafeSVG(f"malformed SVG: {e}")
    if _local(root.tag) != "svg":
        raise UnsafeSVG("root element is not <svg>")

    removed: list[str] = []

    def clean(node):
        for child in list(node):
            if _local(child.tag) in BANNED_TAGS:
                removed.append(f"<{_local(child.tag)}>")
                node.remove(child)
                continue
            clean(child)
        for attr in list(node.attrib):
            low = attr.lower()
            val = str(node.attrib[attr])
            if low.startswith("on"):                       # onclick, onload, ...
                removed.append(attr); del node.attrib[attr]
            elif low in URL_ATTRS and not val.startswith("#"):
                # only same-document references survive; no http(s):, data:, file:
                removed.append(f"{attr}={val[:40]}"); del node.attrib[attr]
            elif "javascript:" in val.lower():
                removed.append(f"{attr}(javascript:)"); del node.attrib[attr]
            elif low == "style" and re.search(r"url\s*\(|expression\s*\(|@import", val, re.I):
                removed.append("style(url/expression)"); del node.attrib[attr]

    clean(root)
    # <style> blocks can pull remote resources too
    for st in root.iter():
        if _local(st.tag) == "style" and st.text and re.search(r"@import|url\s*\(", st.text, re.I):
            st.text = re.sub(r"@import[^;]+;|url\s*\([^)]*\)", "", st.text, flags=re.I)
            removed.append("style-block(remote)")

    out = ET.tostring(root, encoding="unicode")
    if "<script" in out.lower() or "javascript:" in out.lower():   # belt and braces
        raise UnsafeSVG("script content survived sanitisation")
    return out, sorted(set(removed))
