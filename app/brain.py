"""The tenant Business Brain (Part 2).

WHAT WAS ALREADY HERE, AND IS NOT REPLACED
------------------------------------------
The platform already delivers per-tenant business context to agents:

    persistence.load_config(tid) -> SELECT ... FROM tenant_config
                                    WHERE tenant_id = current_tenant()
    worker.py                    -> Orchestrator(load_config=p.load_config, ...)
    orchestrator/core.py         -> config=self.load_config(tenant_id)
    agents/creative.py           -> cfg.get("price_list")

That pipeline works and is left alone. tenant_config stays the Business Brain
DOCUMENT that agents read. Part 2 adds a better way to fill it: the relational
business tables are the operational source of truth, and publish() folds them
back into tenant_config -- so every existing agent gets richer context with
zero agent code changed.

THE RULE THAT PROTECTS THE OWNER'S DATA
---------------------------------------
publish() overwrites a tenant_config key only when the matching table actually
has rows. An empty services table never blanks a price list the owner typed by
hand. Extraction proposes; only a human write makes anything authoritative.
"""

import json
import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def _identity(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, _uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role


def _money(v):
    return None if v is None else float(v)


def assemble(cur):
    """Build the brain from what this tenant has actually defined.

    Runs inside a tenant_session cursor, so RLS scopes every read. Sections the
    tenant has not filled in come back empty rather than invented.
    """
    brain = {}

    cur.execute("SELECT name, industry, status, plan FROM tenants WHERE id = current_tenant()")
    brain["identity"] = cur.fetchone() or {}

    cur.execute("SELECT name, description, category, price, currency, duration_minutes "
                "FROM services WHERE active ORDER BY sort_order, name")
    brain["services"] = [
        {"name": r["name"], "description": r["description"], "category": r["category"],
         "price": _money(r["price"]), "currency": r["currency"],
         "duration_minutes": r["duration_minutes"]} for r in cur.fetchall()]

    cur.execute("SELECT name, description, category, price, currency FROM products "
                "WHERE active ORDER BY name")
    brain["products"] = [
        {"name": r["name"], "description": r["description"], "category": r["category"],
         "price": _money(r["price"]), "currency": r["currency"]} for r in cur.fetchall()]

    cur.execute("SELECT id, name, description, price, currency, validity_days, terms "
                "FROM packages WHERE active ORDER BY name")
    packages = cur.fetchall()
    for p in packages:
        cur.execute("SELECT COALESCE(s.name, pr.name) AS item, pi.quantity FROM package_items pi "
                    "LEFT JOIN services s ON s.id = pi.service_id "
                    "LEFT JOIN products pr ON pr.id = pi.product_id "
                    "WHERE pi.package_id = %s", (p["id"],))
        p["includes"] = cur.fetchall()
        p["price"] = _money(p["price"])
        p.pop("id", None)
    brain["packages"] = packages

    cur.execute("SELECT name, role, department FROM staff WHERE active ORDER BY name")
    brain["staff"] = cur.fetchall()

    cur.execute("SELECT name, address, city, country, phone, timezone, is_primary "
                "FROM locations WHERE active ORDER BY is_primary DESC, name")
    brain["locations"] = cur.fetchall()

    cur.execute("SELECT day_of_week, opens, closes, is_closed FROM business_hours "
                "ORDER BY day_of_week")
    brain["hours"] = [
        {"day": DAYS[r["day_of_week"]], "closed": r["is_closed"],
         "opens": str(r["opens"]) if r["opens"] else None,
         "closes": str(r["closes"]) if r["closes"] else None} for r in cur.fetchall()]

    cur.execute("SELECT kind, title, body FROM business_policies WHERE active ORDER BY kind")
    brain["policies"] = cur.fetchall()

    cur.execute("SELECT metric, label, target, period FROM business_goals WHERE active "
                "ORDER BY created_at DESC")
    brain["goals"] = [
        {"metric": r["metric"], "label": r["label"], "target": _money(r["target"]),
         "period": r["period"]} for r in cur.fetchall()]

    # the pre-existing document: brand, geo, persona and booking rules stay
    # exactly where they have always lived
    cur.execute("SELECT brand, geo, hermes_persona, booking_rules FROM tenant_config "
                "WHERE tenant_id = current_tenant()")
    cfg = cur.fetchone() or {}
    brain["brand"] = cfg.get("brand")
    brain["geo"] = cfg.get("geo")
    brain["persona"] = cfg.get("hermes_persona")
    brain["booking_rules"] = cfg.get("booking_rules")

    filled = sum(1 for k in ("services", "staff", "locations", "hours", "policies", "goals")
                 if brain.get(k))
    brain["completeness"] = {"sections_filled": filled, "sections_total": 6,
                             "percent": round(100 * filled / 6)}
    return brain


def publish(cur, brain):
    """Fold the relational tables back into tenant_config.

    Only keys whose source table actually has rows are written, so publishing a
    half-filled business can never blank something the owner entered by hand.
    Returns the list of keys written and what was skipped, so the caller can
    show the owner exactly what changed.
    """
    patch, skipped = {}, []

    if brain.get("services"):
        patch["services"] = json.dumps([
            {"name": s["name"], "price": s["price"], "currency": s["currency"],
             "duration_minutes": s["duration_minutes"], "category": s["category"]}
            for s in brain["services"]])
        patch["price_list"] = json.dumps(
            {s["name"]: s["price"] for s in brain["services"] if s["price"] is not None})
    else:
        skipped.append("services (no active services defined)")

    if brain.get("hours"):
        patch["working_hours"] = json.dumps(
            {h["day"]: ("closed" if h["closed"] else "{0}-{1}".format(h["opens"], h["closes"]))
             for h in brain["hours"]})
    else:
        skipped.append("working_hours (no hours defined)")

    if brain.get("staff"):
        depts = sorted({s["department"] for s in brain["staff"] if s.get("department")})
        if depts:
            patch["departments"] = json.dumps(depts)
        else:
            skipped.append("departments (staff defined but none have a department)")
    else:
        skipped.append("departments (no active staff)")

    if not patch:
        return {"written": [], "skipped": skipped,
                "message": "Nothing to publish yet - define services, hours or staff first."}

    cols = list(patch.keys())
    cur.execute("SELECT 1 FROM tenant_config WHERE tenant_id = current_tenant()")
    if cur.fetchone():
        sets = ", ".join("{0} = %s".format(c) for c in cols)
        cur.execute("UPDATE tenant_config SET {0} WHERE tenant_id = current_tenant()".format(sets),
                    [patch[c] for c in cols])
    else:
        cur.execute(
            "INSERT INTO tenant_config (tenant_id, {0}) VALUES (current_tenant(), {1})".format(
                ", ".join(cols), ", ".join(["%s"] * len(cols))),
            [patch[c] for c in cols])

    return {"written": cols, "skipped": skipped,
            "message": "Your AI workforce now reads this business information."}


# --------------------------------------------------------------------------
# Ingestion.
#
# A deliberate design choice: extraction writes NOTHING. It returns proposals
# that a human reviews, edits and saves through the normal /business routes.
# That is the approval gate -- and it holds whoever is proposing, a parser
# today or a model later.
#
# This parser is rule-based, not a model call. The platform's LLM provider is
# currently out of credits, and a extractor that silently returns nothing would
# look like "your paste had no services in it". A parser that works is more
# honest than a model that cannot run.
# --------------------------------------------------------------------------

_PRICE = re.compile(
    r"^(?P<name>[^:—\-–|]{2,80}?)\s*[:—\-–|]+\s*"
    r"(?:(?P<cur>PKR|Rs\.?|USD|AED|\$)\s*)?(?P<amount>\d[\d,\.]*)\s*"
    r"(?:(?P<cur2>PKR|Rs\.?|USD|AED)\b)?\s*"
    r"(?:[/\-]?\s*(?P<mins>\d{1,3})\s*(?:min|mins|minutes))?\s*$",
    re.IGNORECASE)


def extract(text: str) -> dict:
    """Turn pasted business text into reviewable service proposals."""
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(400, "nothing to read - paste your services and prices")
    if len(text) > 20000:
        raise HTTPException(400, "text too long - paste up to 20,000 characters at a time")

    proposals, unparsed, category = [], [], None
    for raw in text.splitlines():
        line = raw.strip().strip("*").strip("-").strip()
        if not line:
            continue
        m = _PRICE.match(line)
        if m:
            amount = m.group("amount").replace(",", "")
            try:
                price = float(amount)
            except ValueError:
                unparsed.append(line)
                continue
            name = m.group("name").strip()
            if not name:
                unparsed.append(line)
                continue
            proposals.append({
                "name": name[:80],
                "price": price,
                "currency": (m.group("cur") or m.group("cur2") or "PKR").upper().rstrip("."),
                "duration_minutes": int(m.group("mins")) if m.group("mins") else None,
                "category": category,
                "source_line": line[:120],
            })
        elif len(line) <= 60 and not any(ch.isdigit() for ch in line):
            # a short, number-free line reads as a heading for what follows
            category = line
        else:
            unparsed.append(line)

    return {
        "proposals": proposals,
        "unparsed": unparsed[:50],
        "counts": {"found": len(proposals), "not_understood": len(unparsed)},
        "extractor": "rule-based",
        "note": ("Nothing has been saved. Review these, correct anything wrong, "
                 "then save the ones you want."),
    }


def make_brain_router():
    router = APIRouter()

    @router.get("/brain")
    def read_brain(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Exactly what this tenant's AI workforce knows about the business."""
        tid, role = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return {"brain": assemble(cur)}

    @router.post("/brain/extract")
    def extract_brain(body: dict = Body(...),
                      x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Read pasted business text and PROPOSE services. Writes nothing."""
        _identity(x_tenant_id, x_role, authorization, td_session)
        return extract(body.get("text"))

    @router.post("/brain/publish")
    def publish_brain(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                      authorization: str | None = Header(None),
                      td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Make the current business definition authoritative for the AI workforce.

        Owner-only, matching PUT /portal/config: this is the act that changes
        what agents tell customers, so an operator cannot do it.
        """
        tid, role = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "only the owner can publish the business brain")
        with tenant_session(tid, role) as cur:
            brain = assemble(cur)
            result = publish(cur, brain)
        result["completeness"] = brain["completeness"]
        return result

    return router
