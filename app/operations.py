"""Operations: the parts of the money and appointment layer that are not
ordinary CRUD (that lives in business.py, on the same whitelisted router).

What is here is the work that needs to be *computed* rather than stored:
an invoice with its lines and running balance, stock on hand derived from a
ledger, a day's closing measured against what actually came in, and a profit
and loss that adds up from real rows.

Nothing in this module invents a number. Where a business has no data, the
answer is zero or null with a reason, never an estimate.
"""

from datetime import date

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

MAX_ITEMS = 200


def _identity(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role, uid


def _num(v, field, *, minimum=None, allow_none=True):
    if v is None:
        if allow_none:
            return None
        raise HTTPException(400, "{0} is required".format(field))
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise HTTPException(400, "{0} must be a number".format(field))
    if minimum is not None and n < minimum:
        raise HTTPException(400, "{0} must be at least {1}".format(field, minimum))
    return n


def _next_invoice_number(cur):
    """INV-0001 upward, per tenant. RLS scopes the MAX, so two tenants never
    share a sequence and neither can see the other's."""
    cur.execute("SELECT number FROM invoices WHERE number ~ '^INV-[0-9]+$' "
                "ORDER BY (substring(number from 5))::int DESC LIMIT 1")
    row = cur.fetchone()
    nxt = 1 if not row else int(row["number"][4:]) + 1
    return "INV-{0:04d}".format(nxt)


def _invoice_totals(cur, invoice_id):
    cur.execute("SELECT COALESCE(SUM(line_total), 0) AS subtotal FROM invoice_items "
                "WHERE invoice_id = %s", (invoice_id,))
    subtotal = float(cur.fetchone()["subtotal"])
    cur.execute("SELECT discount, tax FROM invoices WHERE id = %s", (invoice_id,))
    inv = cur.fetchone() or {}
    discount = float(inv.get("discount") or 0)
    tax = float(inv.get("tax") or 0)
    total = max(0.0, subtotal - discount + tax)
    return subtotal, total


def _paid_so_far(cur, invoice_id):
    cur.execute("SELECT COALESCE(SUM(amount), 0) AS paid FROM payments WHERE invoice_id = %s",
                (invoice_id,))
    return float(cur.fetchone()["paid"])


def _settle(cur, invoice_id, uid):
    """Recompute totals and move the invoice's status to match reality.

    Status is derived from what has actually been paid rather than set by hand,
    so an invoice cannot be marked paid while money is still outstanding.
    """
    subtotal, total = _invoice_totals(cur, invoice_id)
    paid = _paid_so_far(cur, invoice_id)
    cur.execute("SELECT status, voided_at FROM invoices WHERE id = %s", (invoice_id,))
    cur_row = cur.fetchone() or {}
    if cur_row.get("voided_at"):
        status = "void"
    elif total <= 0:
        status = "draft"
    elif paid <= 0:
        status = "issued"
    elif paid + 0.009 < total:
        status = "partial"
    else:
        status = "paid"
    cur.execute("UPDATE invoices SET subtotal = %s, total = %s, status = %s, "
                "updated_at = now() WHERE id = %s", (subtotal, total, status, invoice_id))
    return {"subtotal": subtotal, "total": total, "paid": paid,
            "balance": round(total - paid, 2), "status": status}


def make_operations_router():
    router = APIRouter()

    def who(x_tenant_id, x_role, authorization, td_session):
        return _identity(x_tenant_id, x_role, authorization, td_session)

    # ------------------------------------------------------------ appointments
    @router.get("/operations/appointments")
    def appointments(when: str = "upcoming", limit: int = 200,
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Appointments with the names attached, split into upcoming and past."""
        if when not in ("upcoming", "past", "all"):
            raise HTTPException(400, "when must be upcoming, past or all")
        tid, role, _uid = who(x_tenant_id, x_role, authorization, td_session)
        clause = {"upcoming": "AND a.starts_at >= now()",
                  "past": "AND a.starts_at < now()", "all": ""}[when]
        order = "ASC" if when == "upcoming" else "DESC"
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT a.id, a.starts_at, a.ends_at, a.status, a.price, a.currency, "
                "a.notes, a.service AS service_text, "
                "c.name AS customer_name, c.phone AS customer_phone, c.id AS customer_id, "
                "s.name AS service_name, s.id AS service_id, "
                "st.name AS staff_name, st.id AS staff_id, "
                "l.name AS location_name "
                "FROM appointments a "
                "LEFT JOIN customers c ON c.id = a.customer_id "
                "LEFT JOIN services  s ON s.id = a.service_id "
                "LEFT JOIN staff    st ON st.id = a.staff_id "
                "LEFT JOIN locations l ON l.id = a.location_id "
                "WHERE true {0} ORDER BY a.starts_at {1} LIMIT %s".format(clause, order),
                (min(max(limit, 1), 500),))
            rows = cur.fetchall()
        return {"when": when, "count": len(rows), "appointments": rows}

    # --------------------------------------------------------------- inventory
    @router.get("/operations/inventory")
    def inventory(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Stock on hand, summed from the movement ledger rather than stored."""
        tid, role, _uid = who(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT p.id, p.name, p.category, p.sku, p.price, p.currency, p.active, "
                "p.track_stock, p.low_stock_threshold, "
                "COALESCE(SUM(m.quantity_delta), 0) AS on_hand, "
                "COUNT(m.id) AS movement_count, "
                "MAX(m.occurred_at) AS last_movement "
                "FROM products p LEFT JOIN stock_movements m ON m.product_id = p.id "
                "GROUP BY p.id ORDER BY p.track_stock DESC, p.name")
            rows = cur.fetchall()
        # A product with movements is being tracked whether or not the flag was set.
        for r in rows:
            r["track_stock"] = bool(r["track_stock"]) or r["movement_count"] > 0
        tracked = [r for r in rows if r["track_stock"]]
        low = [r for r in tracked
               if r["low_stock_threshold"] is not None
               and float(r["on_hand"]) <= float(r["low_stock_threshold"])]
        return {"products": rows, "tracked_count": len(tracked),
                "low_stock": low, "low_stock_count": len(low)}

    # ---------------------------------------------------------------- invoices
    @router.post("/operations/invoices")
    def create_invoice(body: dict = Body(...),
                       x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Create an invoice and its lines in one transaction.

        Each line's description and unit price are stored as given: they are a
        record of what was charged, not a live pointer at today's price list.
        """
        tid, role, uid = who(x_tenant_id, x_role, authorization, td_session)
        items = body.get("items")
        if not isinstance(items, list) or not items:
            raise HTTPException(400, "an invoice needs at least one line item")
        if len(items) > MAX_ITEMS:
            raise HTTPException(400, "too many line items (max {0})".format(MAX_ITEMS))

        clean = []
        for i, it in enumerate(items, 1):
            if not isinstance(it, dict):
                raise HTTPException(400, "line {0} is not an object".format(i))
            desc = (it.get("description") or "").strip()
            if not desc:
                raise HTTPException(400, "line {0} needs a description".format(i))
            qty = _num(it.get("quantity", 1), "line {0} quantity".format(i), minimum=0.01)
            unit = _num(it.get("unit_price"), "line {0} unit_price".format(i),
                        minimum=0, allow_none=False)
            clean.append({"description": desc[:300], "quantity": qty, "unit_price": unit,
                          "line_total": round(qty * unit, 2),
                          "service_id": it.get("service_id"), "product_id": it.get("product_id"),
                          "package_id": it.get("package_id")})

        discount = _num(body.get("discount", 0), "discount", minimum=0) or 0
        tax = _num(body.get("tax", 0), "tax", minimum=0) or 0

        with tenant_session(tid, role) as cur:
            number = (body.get("number") or "").strip() or _next_invoice_number(cur)
            cur.execute(
                "INSERT INTO invoices (tenant_id, customer_id, location_id, number, "
                "issued_on, due_on, currency, discount, tax, notes) VALUES "
                "(current_tenant(), %s, %s, %s, COALESCE(%s, CURRENT_DATE), %s, "
                "COALESCE(%s, 'PKR'), %s, %s, %s) RETURNING id",
                (body.get("customer_id"), body.get("location_id"), number,
                 body.get("issued_on"), body.get("due_on"), body.get("currency"),
                 discount, tax, body.get("notes")))
            inv_id = cur.fetchone()["id"]
            for it in clean:
                cur.execute(
                    "INSERT INTO invoice_items (tenant_id, invoice_id, service_id, product_id, "
                    "package_id, description, quantity, unit_price, line_total) VALUES "
                    "(current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s)",
                    (inv_id, it["service_id"], it["product_id"], it["package_id"],
                     it["description"], it["quantity"], it["unit_price"], it["line_total"]))
            money = _settle(cur, inv_id, uid)
            record(cur, action="create", resource="invoices", resource_id=inv_id, actor=uid,
                   detail={"number": number, "lines": len(clean), "total": money["total"]})
        return {"invoice_id": inv_id, "number": number, **money}

    @router.get("/operations/invoices/{invoice_id}")
    def get_invoice(invoice_id: str,
                    x_tenant_id=Header(default=None), x_role=Header(default=None),
                    authorization=Header(default=None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """One invoice with its lines, its payments, and what is still owed."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT i.*, c.name AS customer_name, c.phone AS customer_phone, "
                "       l.name AS location_name "
                "FROM invoices i "
                "LEFT JOIN customers c ON c.id = i.customer_id "
                "LEFT JOIN locations l ON l.id = i.location_id "
                "WHERE i.id = %s", (invoice_id,))
            inv = cur.fetchone()
            if not inv:
                raise HTTPException(status_code=404, detail="Invoice not found")
            cur.execute(
                "SELECT id, description, quantity, unit_price, line_total, "
                "       service_id, product_id, package_id "
                "FROM invoice_items WHERE invoice_id = %s ORDER BY description",
                (invoice_id,))
            items = cur.fetchall()
            cur.execute(
                "SELECT id, amount, currency, method, reference, notes, paid_at "
                "FROM payments WHERE invoice_id = %s ORDER BY paid_at", (invoice_id,))
            pays = cur.fetchall()
            paid = _paid_so_far(cur, invoice_id)
        total = float(inv.get("total") or 0)
        return {"invoice": inv, "items": items, "payments": pays,
                "paid": paid, "balance": round(total - paid, 2)}

    @router.post("/operations/invoices/{invoice_id}/pay")
    def pay_invoice(invoice_id: str, body: dict = Body(...),
                    x_tenant_id=Header(default=None), x_role=Header(default=None),
                    authorization=Header(default=None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Record money received. Status follows the money, never the other way round."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        amount = _num(body.get("amount"), "amount", minimum=0.01, allow_none=False)
        method = (body.get("method") or "cash").strip().lower()
        if method not in ("cash", "card", "bank", "online", "other"):
            raise HTTPException(status_code=400,
                                detail="method must be cash, card, bank, online or other")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT customer_id, location_id, currency, voided_at, total "
                        "FROM invoices WHERE id = %s", (invoice_id,))
            inv = cur.fetchone()
            if not inv:
                raise HTTPException(status_code=404, detail="Invoice not found")
            if inv.get("voided_at"):
                raise HTTPException(status_code=409,
                                    detail="This invoice is void. Payments cannot be added to it.")
            outstanding = round(float(inv.get("total") or 0) - _paid_so_far(cur, invoice_id), 2)
            if amount - outstanding > 0.009:
                raise HTTPException(
                    status_code=409,
                    detail="Payment of {0} is more than the {1} still owed on this invoice."
                           .format(amount, outstanding))
            cur.execute(
                "INSERT INTO payments (tenant_id, invoice_id, customer_id, location_id, "
                "amount, currency, method, reference, notes) "
                "VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (invoice_id, inv.get("customer_id"), inv.get("location_id"), amount,
                 inv.get("currency") or "PKR", method,
                 body.get("reference"), body.get("notes")))
            pay_id = cur.fetchone()["id"]
            money = _settle(cur, invoice_id, uid)
            record(cur, action="pay", resource="invoices", resource_id=invoice_id, actor=uid,
                   detail={"payment_id": str(pay_id), "amount": amount, "method": method,
                           "status": money["status"]})
        return {"payment_id": pay_id, **money}

    @router.post("/operations/invoices/{invoice_id}/void")
    def void_invoice(invoice_id: str, body: dict = Body(...),
                     x_tenant_id=Header(default=None), x_role=Header(default=None),
                     authorization=Header(default=None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Void, never delete. The invoice and its history stay on the record."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "admin", "techdeva_admin"):
            raise HTTPException(status_code=403, detail="Only an owner can void an invoice")
        reason = (body.get("reason") or "").strip()
        if len(reason) < 3:
            raise HTTPException(status_code=400, detail="A void needs a written reason")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT voided_at FROM invoices WHERE id = %s", (invoice_id,))
            inv = cur.fetchone()
            if not inv:
                raise HTTPException(status_code=404, detail="Invoice not found")
            if inv.get("voided_at"):
                raise HTTPException(status_code=409, detail="This invoice is already void")
            if _paid_so_far(cur, invoice_id) > 0:
                raise HTTPException(
                    status_code=409,
                    detail="This invoice has payments against it. Refund or remove them first.")
            cur.execute("UPDATE invoices SET voided_at = now(), void_reason = %s, "
                        "status = 'void', updated_at = now() WHERE id = %s",
                        (reason, invoice_id))
            record(cur, action="void", resource="invoices", resource_id=invoice_id,
                   actor=uid, detail={"reason": reason})
        return {"invoice_id": invoice_id, "status": "void", "reason": reason}

    def _day_money(cur, on_day, location_id=None):
        """What the books say happened on one day, before anyone counts the drawer."""
        where = "paid_at::date = %s"
        args = [on_day]
        if location_id:
            where += " AND location_id = %s"
            args.append(location_id)
        cur.execute("SELECT method, COALESCE(SUM(amount), 0) AS amount, COUNT(*) AS n "
                    "FROM payments WHERE " + where + " GROUP BY method", tuple(args))
        by_method = {r["method"]: float(r["amount"]) for r in cur.fetchall()}
        ewhere = "spent_on = %s"
        eargs = [on_day]
        if location_id:
            ewhere += " AND location_id = %s"
            eargs.append(location_id)
        cur.execute("SELECT COALESCE(SUM(amount), 0) AS amount FROM expenses WHERE " + ewhere,
                    tuple(eargs))
        expenses = float(cur.fetchone()["amount"] or 0)
        expected = round(sum(by_method.values()), 2)
        return {"date": str(on_day), "by_method": by_method,
                "expected_total": expected, "expenses_total": round(expenses, 2)}

    @router.get("/operations/closing/preview")
    def closing_preview(on: str = None, location_id: str = None,
                        x_tenant_id=Header(default=None), x_role=Header(default=None),
                        authorization=Header(default=None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What the system expects in the drawer today. The human still counts it."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        on_day = on or date.today().isoformat()
        with tenant_session(tid, role) as cur:
            money = _day_money(cur, on_day, location_id)
            cur.execute("SELECT id, counted_total, variance, closed_at FROM daily_closing "
                        "WHERE closing_date = %s AND location_id IS NOT DISTINCT FROM %s",
                        (on_day, location_id))
            existing = cur.fetchone()
        money["already_closed"] = existing or None
        return money

    @router.post("/operations/closing")
    def close_day(body: dict = Body(...),
                  x_tenant_id=Header(default=None), x_role=Header(default=None),
                  authorization=Header(default=None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Close the day: counted money in, variance recorded, nothing overwritten silently."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        on_day = (body.get("closing_date") or date.today().isoformat())
        location_id = body.get("location_id") or None
        cash = _num(body.get("cash_counted"), "cash_counted", minimum=0) or 0
        card = _num(body.get("card_counted"), "card_counted", minimum=0) or 0
        bank = _num(body.get("bank_counted"), "bank_counted", minimum=0) or 0
        other = _num(body.get("other_counted"), "other_counted", minimum=0) or 0
        counted = round(cash + card + bank + other, 2)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT id FROM daily_closing WHERE closing_date = %s "
                        "AND location_id IS NOT DISTINCT FROM %s", (on_day, location_id))
            if cur.fetchone():
                raise HTTPException(
                    status_code=409,
                    detail="This day is already closed. Closings are kept as they were made.")
            money = _day_money(cur, on_day, location_id)
            variance = round(counted - money["expected_total"], 2)
            cur.execute(
                "INSERT INTO daily_closing (tenant_id, location_id, closing_date, "
                "cash_counted, card_counted, bank_counted, other_counted, "
                "expected_total, counted_total, expenses_total, variance, notes, closed_by) "
                "VALUES (current_tenant(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (location_id, on_day, cash, card, bank, other,
                 money["expected_total"], counted, money["expenses_total"], variance,
                 body.get("notes"), uid))
            closing_id = cur.fetchone()["id"]
            record(cur, action="close", resource="daily_closing", resource_id=closing_id,
                   actor=uid, detail={"date": str(on_day), "counted": counted,
                                      "expected": money["expected_total"],
                                      "variance": variance})
        return {"closing_id": closing_id, "expected_total": money["expected_total"],
                "counted_total": counted, "variance": variance,
                "expenses_total": money["expenses_total"]}

    @router.get("/operations/pnl")
    def pnl(start: str = None, end: str = None, location_id: str = None,
            x_tenant_id=Header(default=None), x_role=Header(default=None),
            authorization=Header(default=None), td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Money in, money out, for a period. Collected and invoiced are reported apart:
        an invoice that has not been paid is not income yet."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role == "operator":
            raise HTTPException(status_code=403, detail="Financial reports are owner-only")
        end = end or date.today().isoformat()
        start = start or str(date.today().replace(day=1))
        loc_p = " AND location_id = %s" if location_id else ""
        extra = (location_id,) if location_id else ()
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT COALESCE(SUM(amount), 0) AS a, COUNT(*) AS n FROM payments "
                        "WHERE paid_at::date BETWEEN %s AND %s" + loc_p,
                        (start, end) + extra)
            row = cur.fetchone()
            collected, payment_count = float(row["a"] or 0), row["n"]
            cur.execute("SELECT COALESCE(SUM(total), 0) AS a, COUNT(*) AS n FROM invoices "
                        "WHERE issued_on BETWEEN %s AND %s AND voided_at IS NULL" + loc_p,
                        (start, end) + extra)
            row = cur.fetchone()
            invoiced, invoice_count = float(row["a"] or 0), row["n"]
            cur.execute("SELECT COALESCE(SUM(amount), 0) AS a FROM expenses "
                        "WHERE spent_on BETWEEN %s AND %s" + loc_p, (start, end) + extra)
            expenses = float(cur.fetchone()["a"] or 0)
            cur.execute("SELECT category, COALESCE(SUM(amount), 0) AS a FROM expenses "
                        "WHERE spent_on BETWEEN %s AND %s" + loc_p +
                        " GROUP BY category ORDER BY a DESC", (start, end) + extra)
            by_category = [{"category": r["category"], "amount": float(r["a"])}
                           for r in cur.fetchall()]
            cur.execute("SELECT method, COALESCE(SUM(amount), 0) AS a FROM payments "
                        "WHERE paid_at::date BETWEEN %s AND %s" + loc_p +
                        " GROUP BY method ORDER BY a DESC", (start, end) + extra)
            by_method = [{"method": r["method"], "amount": float(r["a"])}
                         for r in cur.fetchall()]
            cur.execute("SELECT COALESCE(SUM(i.total), 0) - COALESCE(("
                        "SELECT SUM(p.amount) FROM payments p JOIN invoices i2 ON i2.id = p.invoice_id "
                        "WHERE i2.voided_at IS NULL), 0) AS a "
                        "FROM invoices i WHERE i.voided_at IS NULL")
            outstanding = float(cur.fetchone()["a"] or 0)
        return {"start": start, "end": end,
                "collected": round(collected, 2), "payment_count": payment_count,
                "invoiced": round(invoiced, 2), "invoice_count": invoice_count,
                "expenses": round(expenses, 2),
                "net": round(collected - expenses, 2),
                "outstanding_all_time": round(outstanding, 2),
                "expenses_by_category": by_category,
                "collected_by_method": by_method}

    return router
