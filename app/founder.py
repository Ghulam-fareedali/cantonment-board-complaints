"""The founder's book: who is onboard, what they pay, what has arrived.

This is TechDeva's own side of the relationship, not the client's business.
Every figure here is a recorded fact - a fee somebody agreed, a payment
somebody made. Nothing is projected and nothing is estimated, so a zero on
this page means 'nothing recorded', which is the truth worth seeing.
"""
from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

router = APIRouter()


def _admin(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if role != "techdeva_admin":
        raise HTTPException(403, "the founder's book is admin-only")
    return uid


CLIENTS_SQL = """
SELECT t.id, t.name, t.industry, t.status::text AS status, t.plan::text AS plan,
       t.billing::text AS billing, t.trial_ends_at, t.created_at,
       COALESCE(a.monthly_fee,0)     AS monthly_fee,
       COALESCE(a.setup_fee,0)       AS setup_fee,
       COALESCE(a.performance_pct,0) AS performance_pct,
       a.started_on, a.next_invoice_on,
       COALESCE(a.contact,'') AS contact, COALESCE(a.notes,'') AS notes,
       (SELECT COALESCE(SUM(p.amount),0) FROM client_payments p
         WHERE p.tenant_id = t.id) AS paid_total,
       (SELECT COALESCE(SUM(p.amount),0) FROM client_payments p
         WHERE p.tenant_id = t.id
           AND p.paid_on >= date_trunc('month', current_date)) AS paid_this_month,
       (SELECT MAX(p.paid_on) FROM client_payments p WHERE p.tenant_id = t.id)
            AS last_paid_on,
       (SELECT COUNT(*) FROM leads l WHERE l.tenant_id = t.id) AS leads,
       (SELECT COUNT(*) FROM tasks k WHERE k.tenant_id = t.id
          AND k.updated_at > now() - interval '7 days') AS tasks_7d,
       (SELECT COUNT(*) FROM deliverables d WHERE d.tenant_id = t.id
          AND d.status = 'awaiting_approval') AS awaiting,
       (SELECT MAX(e.at) FROM events e WHERE e.tenant_id = t.id)
            AS last_event_at
  FROM tenants t
  LEFT JOIN client_accounts a ON a.tenant_id = t.id
 WHERE NOT t.is_test
 ORDER BY COALESCE(a.monthly_fee,0) DESC, t.created_at
"""


@router.get("/techdeva/book")
def book(x_tenant_id: str | None = Header(None),
         x_role: str | None = Header(None),
         authorization: str | None = Header(None),
         td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    _admin(x_tenant_id, x_role, authorization, td_session)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute(CLIENTS_SQL)
        clients = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT to_char(paid_on,'YYYY-MM') AS month,
                              SUM(amount) AS amount, COUNT(*) AS n
                         FROM client_payments p
                         JOIN tenants t ON t.id = p.tenant_id AND NOT t.is_test
                        WHERE paid_on > current_date - interval '12 months'
                        GROUP BY 1 ORDER BY 1""")
        months = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT p.id, p.tenant_id, t.name, p.amount, p.paid_on,
                              p.method, p.reference
                         FROM client_payments p JOIN tenants t ON t.id = p.tenant_id
                        WHERE NOT t.is_test
                        ORDER BY p.paid_on DESC, p.created_at DESC LIMIT 12""")
        recent = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) c FROM tenants WHERE is_test")
        hidden = cur.fetchone()["c"]

    paying = [c for c in clients if float(c["monthly_fee"]) > 0 and c["status"] == "active"]
    mrr = sum(float(c["monthly_fee"]) for c in paying)
    return {
        "mrr": mrr,
        "paying": len(paying),
        "onboard": len([c for c in clients if c["status"] in ("active", "trial")]),
        "trials": len([c for c in clients if c["status"] == "trial"]),
        "collected_this_month": sum(float(c["paid_this_month"]) for c in clients),
        "collected_all_time": sum(float(c["paid_total"]) for c in clients),
        "unpaid_this_month": sum(float(c["monthly_fee"]) for c in paying
                                 if float(c["paid_this_month"]) <= 0),
        "awaiting_approval": sum(int(c["awaiting"]) for c in clients),
        "hidden_test_tenants": hidden,
        "clients": clients, "months": months, "recent_payments": recent,
    }


@router.put("/techdeva/clients/{tenant_id}/account")
def set_account(tenant_id: str, body: dict = Body(...),
                x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    uid = _admin(x_tenant_id, x_role, authorization, td_session)
    def num(k):
        try:
            return max(0.0, float(body.get(k) or 0))
        except (TypeError, ValueError):
            raise HTTPException(400, "%s must be a number" % k)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""INSERT INTO client_accounts (tenant_id,monthly_fee,setup_fee,
                         performance_pct,started_on,next_invoice_on,contact,notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (tenant_id) DO UPDATE SET
                         monthly_fee=EXCLUDED.monthly_fee, setup_fee=EXCLUDED.setup_fee,
                         performance_pct=EXCLUDED.performance_pct,
                         started_on=EXCLUDED.started_on,
                         next_invoice_on=EXCLUDED.next_invoice_on,
                         contact=EXCLUDED.contact, notes=EXCLUDED.notes,
                         updated_at=now()
                       RETURNING tenant_id""",
                    (tenant_id, num("monthly_fee"), num("setup_fee"),
                     num("performance_pct"), body.get("started_on") or None,
                     body.get("next_invoice_on") or None,
                     (body.get("contact") or "")[:200], (body.get("notes") or "")[:2000]))
        if not cur.fetchone():
            raise HTTPException(404, "no such client")
        record(cur, action="techdeva.account.set", resource="client_accounts",
               resource_id=tenant_id, actor=uid)
    return {"ok": True}


@router.post("/techdeva/clients/{tenant_id}/payment")
def add_payment(tenant_id: str, body: dict = Body(...),
                x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    uid = _admin(x_tenant_id, x_role, authorization, td_session)
    try:
        amount = float(body.get("amount") or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        raise HTTPException(400, "a payment needs an amount")
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""INSERT INTO client_payments (tenant_id,amount,paid_on,method,
                         reference,notes)
                       VALUES (%s,%s,COALESCE(%s::date,current_date),%s,%s,%s)
                       RETURNING id""",
                    (tenant_id, amount, body.get("paid_on") or None,
                     (body.get("method") or "bank")[:40],
                     (body.get("reference") or "")[:200],
                     (body.get("notes") or "")[:1000]))
        pid = cur.fetchone()["id"]
        record(cur, action="techdeva.payment.add", resource="client_payments",
               resource_id=str(pid), actor=uid, detail={"amount": amount})
    return {"ok": True, "id": str(pid)}


@router.post("/techdeva/clients/{tenant_id}/hide")
def hide(tenant_id: str, body: dict = Body(default={}),
         x_tenant_id: str | None = Header(None),
         x_role: str | None = Header(None),
         authorization: str | None = Header(None),
         td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    """Mark a tenant as test so the book stops counting it. Never deletes."""
    uid = _admin(x_tenant_id, x_role, authorization, td_session)
    hidden = bool(body.get("hidden", True))
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("UPDATE tenants SET is_test=%s WHERE id=%s RETURNING id",
                    (hidden, tenant_id))
        if not cur.fetchone():
            raise HTTPException(404, "no such client")
        record(cur, action="techdeva.client.hide", resource="tenants",
               resource_id=tenant_id, actor=uid, detail={"hidden": hidden})
    return {"ok": True, "hidden": hidden}
