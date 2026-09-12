"""Tenant-scoped CRUD for the business-definition layer (Part 2).

Every route resolves the tenant through auth.resolve_scope() -- the one
resolver -- then runs inside db.tenant_session(), so Postgres RLS
(is_admin() OR tenant_id = current_tenant()) is what actually enforces
isolation. No route accepts a tenant id from the caller and no query
interpolates one by hand: a row simply is not visible if RLS says so.

Columns are whitelisted per resource, so every table and column name in every
statement is ours, never the caller's. An unknown field is refused with a
readable 400 rather than silently dropped -- a typo that vanishes is how a
price quietly fails to save.
"""

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

_TS = "created_at, updated_at"


class Spec:
    """One tenant-owned resource: what may be read, written and ordered by."""

    def __init__(self, table, read, writable, order, required=()):
        self.table = table
        self.read = read
        self.writable = writable
        self.order = order
        self.required = required

RESOURCES: dict = {
    "customers": Spec(
        table="customers",
        read="id, lead_id, name, phone, email, source, status, tags, notes, "
             "first_seen_at, last_interaction_at, " + _TS,
        writable=["lead_id", "name", "phone", "email", "source", "status",
                  "tags", "notes", "last_interaction_at"],
        order="created_at DESC",
        required=("name",)),
    "services": Spec(
        table="services",
        read="id, name, description, category, price, currency, duration_minutes, "
             "active, sort_order, " + _TS,
        writable=["name", "description", "category", "price", "currency",
                  "duration_minutes", "active", "sort_order"],
        order="sort_order ASC, name ASC",
        required=("name",)),
    "products": Spec(
        table="products",
        read="id, name, description, category, sku, price, currency, active, " + _TS,
        writable=["name", "description", "category", "sku", "price", "currency", "active"],
        order="name ASC",
        required=("name",)),
    "packages": Spec(
        table="packages",
        read="id, name, description, price, currency, validity_days, terms, active, " + _TS,
        writable=["name", "description", "price", "currency", "validity_days",
                  "terms", "active"],
        order="name ASC",
        required=("name",)),
    "package_items": Spec(
        table="package_items",
        read="id, package_id, service_id, product_id, quantity",
        writable=["package_id", "service_id", "product_id", "quantity"],
        order="id ASC",
        required=("package_id",)),
    "staff": Spec(
        table="staff",
        read="id, name, role, department, email, phone, active, " + _TS,
        writable=["name", "role", "department", "email", "phone", "active"],
        order="name ASC",
        required=("name",)),
    "staff_services": Spec(
        table="staff_services",
        read="id, staff_id, service_id",
        writable=["staff_id", "service_id"],
        order="id ASC",
        required=("staff_id", "service_id")),
    "locations": Spec(
        table="locations",
        read="id, name, address, city, country, phone, timezone, is_primary, active, " + _TS,
        writable=["name", "address", "city", "country", "phone", "timezone",
                  "is_primary", "active"],
        order="is_primary DESC, name ASC",
        required=("name",)),
    "business_hours": Spec(
        table="business_hours",
        read="id, location_id, day_of_week, opens, closes, is_closed",
        writable=["location_id", "day_of_week", "opens", "closes", "is_closed"],
        order="day_of_week ASC",
        required=("day_of_week",)),
    "business_policies": Spec(
        table="business_policies",
        read="id, kind, title, body, active, " + _TS,
        writable=["kind", "title", "body", "active"],
        order="kind ASC, title ASC",
        required=("kind", "title", "body")),
    "business_goals": Spec(
        table="business_goals",
        read="id, metric, label, target, period, starts_on, ends_on, active, " + _TS,
        writable=["metric", "label", "target", "period", "starts_on", "ends_on", "active"],
        order="created_at DESC",
        required=("metric", "target")),
}


def _identity(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, _uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role


def _spec(resource):
    spec = RESOURCES.get(resource)
    if not spec:
        raise HTTPException(404, "unknown business resource: {0}".format(resource))
    return spec


def _clean(spec, body, creating):
    if not isinstance(body, dict):
        raise HTTPException(400, "expected a JSON object")
    unknown = [k for k in body if k not in spec.writable]
    if unknown:
        raise HTTPException(400, "unknown field(s): {0}. allowed: {1}".format(
            ", ".join(sorted(unknown)), ", ".join(spec.writable)))
    data = {k: v for k, v in body.items() if k in spec.writable}
    if creating:
        for field in spec.required:
            v = data.get(field)
            if v is None or (isinstance(v, str) and not v.strip()):
                raise HTTPException(400, "{0} is required".format(field))
    if not data:
        raise HTTPException(400, "nothing to save")
    return data


def make_business_router():
    router = APIRouter()

    @router.get("/business/resources")
    def resources(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What this tenant can define. Requires a session like everything else."""
        _identity(x_tenant_id, x_role, authorization, td_session)
        return {"resources": {k: {"writable": v.writable, "required": list(v.required)}
                              for k, v in RESOURCES.items()}}

    @router.get("/business/{resource}")
    def list_items(resource: str,
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        spec = _spec(resource)
        tid, role = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT {0} FROM {1} ORDER BY {2} LIMIT 500".format(
                spec.read, spec.table, spec.order))
            rows = cur.fetchall()
        return {"resource": resource, "count": len(rows), "items": rows}

    @router.post("/business/{resource}")
    def create_item(resource: str, body: dict = Body(...),
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        spec = _spec(resource)
        tid, role = _identity(x_tenant_id, x_role, authorization, td_session)
        data = _clean(spec, body, True)
        cols = ["tenant_id"] + list(data.keys())
        vals = [tid] + list(data.values())
        sql = "INSERT INTO {0} ({1}) VALUES ({2}) RETURNING {3}".format(
            spec.table, ", ".join(cols), ", ".join(["%s"] * len(cols)), spec.read)
        with tenant_session(tid, role) as cur:
            cur.execute(sql, vals)
            row = cur.fetchone()
        return {"resource": resource, "item": row}

    @router.patch("/business/{resource}/{item_id}")
    def update_item(resource: str, item_id: str, body: dict = Body(...),
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        spec = _spec(resource)
        tid, role = _identity(x_tenant_id, x_role, authorization, td_session)
        data = _clean(spec, body, False)
        sets = ", ".join("{0} = %s".format(k) for k in data)
        if "updated_at" not in spec.read:
            sql = "UPDATE {0} SET {1} WHERE id = %s RETURNING {2}"
        else:
            sql = "UPDATE {0} SET {1}, updated_at = now() WHERE id = %s RETURNING {2}"
        sql = sql.format(spec.table, sets, spec.read)
        with tenant_session(tid, role) as cur:
            cur.execute(sql, list(data.values()) + [item_id])
            row = cur.fetchone()
        # RLS makes another tenant's row invisible, so "not found" is also the
        # correct answer to "someone guessed an id that isn't theirs".
        if not row:
            raise HTTPException(404, "not found")
        return {"resource": resource, "item": row}

    @router.delete("/business/{resource}/{item_id}")
    def delete_item(resource: str, item_id: str,
                    x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        spec = _spec(resource)
        tid, role = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("DELETE FROM {0} WHERE id = %s RETURNING id".format(spec.table), (item_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, "not found")
        return {"resource": resource, "deleted": row["id"]}

    # ---------------------------------------------------------------- revenue
    # Part 1 found POST /revenue with no way to read it back. revenue_events
    # already holds amount / type / attributed_source / occurred_at, so this
    # reads the existing table -- no new revenue store.
    @router.get("/revenue")
    def read_revenue(days: int = 90,
                     x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role = _identity(x_tenant_id, x_role, authorization, td_session)
        if days < 1 or days > 1095:
            raise HTTPException(400, "days must be between 1 and 1095")
        with tenant_session(tid, role) as cur:
            cur.execute(
                "SELECT id, lead_id, amount, type, attributed_source, occurred_at, "
                "campaign_id, attribution_model FROM revenue_events "
                "WHERE occurred_at >= now() - make_interval(days => %s) "
                "ORDER BY occurred_at DESC LIMIT 500", (days,))
            events = cur.fetchall()
            cur.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n "
                "FROM revenue_events WHERE occurred_at >= now() - make_interval(days => %s)",
                (days,))
            totals = cur.fetchone() or {}
            cur.execute(
                "SELECT attributed_source::text AS source, COALESCE(SUM(amount), 0) AS total "
                "FROM revenue_events WHERE occurred_at >= now() - make_interval(days => %s) "
                "GROUP BY 1 ORDER BY 2 DESC", (days,))
            by_source = cur.fetchall()
        return {"window_days": days, "total": totals.get("total"),
                "count": totals.get("n"), "by_source": by_source, "events": events}

    return router
