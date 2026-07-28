"""FastAPI entry for the TechDeva platform (Phase B).

Now the API doesn't just store events — it pushes them onto the queue, and the
worker autonomously runs the pipeline. Create a lead and watch tasks appear and
complete with no human action.

Auth is stubbed via X-Tenant-Id / X-Role headers; RLS enforces isolation
regardless of what the caller claims, as long as the app connects as the
non-superuser role.
"""
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .db import tenant_session
from .persistence import Persistence

app = FastAPI(title="TechDeva AIBOS", version="0.3-orchestrator")

# allow the Orchestrator Cockpit (and other dashboards) to call the API from a browser.
# Tighten allow_origins to your dashboard's domain in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
P = Persistence()


def scope(x_tenant_id, x_role):
    role = x_role or "owner"
    if role not in {"techdeva_admin", "owner", "operator"}:
        raise HTTPException(400, "bad role")
    if role != "techdeva_admin" and not x_tenant_id:
        raise HTTPException(400, "tenant required")
    return (None if role == "techdeva_admin" else x_tenant_id), role


@app.get("/health")
def health():
    return {"ok": True, "service": "techdeva", "phase": "B"}


@app.get("/leads")
def list_leads(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None)):
    tid, role = scope(x_tenant_id, x_role)
    with tenant_session(tid, role) as cur:
        cur.execute("""SELECT id, name, phone, service, status, source, priority, est_revenue
                       FROM leads ORDER BY priority, created_at DESC LIMIT 200""")
        return {"leads": cur.fetchall()}


class LeadIn(BaseModel):
    name: str
    phone: str | None = None
    service: str | None = None
    source: str = "manual"
    est_revenue: float = 0


@app.post("/leads")
def create_lead(body: LeadIn, x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None)):
    tid, role = scope(x_tenant_id, x_role)
    if role == "techdeva_admin":
        raise HTTPException(400, "admin has no tenant to write into")
    with tenant_session(tid, role) as cur:
        cur.execute("""INSERT INTO leads (tenant_id, name, phone, service, source, est_revenue, created_by)
                       VALUES (current_tenant(), %s,%s,%s,%s,%s,%s) RETURNING id""",
                    (body.name, body.phone, body.service, body.source, body.est_revenue, role))
        lead_id = str(cur.fetchone()["id"])
    # fire the pipeline: lead.created -> (worker) -> Hermes qualifies -> ...
    P.emit(tid, "lead.created",
           {"lead_id": lead_id, "service": body.service, "est_revenue": body.est_revenue}, role)
    return {"id": lead_id, "emitted": "lead.created"}


class EventIn(BaseModel):
    type: str
    payload: dict = {}


@app.post("/events")
def ingest_event(body: EventIn, x_tenant_id: str | None = Header(None),
                 x_role: str | None = Header(None)):
    tid, role = scope(x_tenant_id, x_role)
    if not tid:
        raise HTTPException(400, "events need a tenant")
    P.emit(tid, body.type, body.payload, role)
    return {"queued": True, "type": body.type}


@app.get("/tasks")
def list_tasks(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None)):
    tid, role = scope(x_tenant_id, x_role)
    with tenant_session(tid, role) as cur:
        cur.execute("""SELECT agent, type, status, payload, result
                       FROM tasks ORDER BY created_at DESC LIMIT 50""")
        return {"tasks": cur.fetchall()}


@app.get("/orchestrator/status")
def orchestrator_status(x_tenant_id: str | None = Header(None),
                        x_role: str | None = Header(None)):
    tid, role = scope(x_tenant_id, x_role)
    if not tid:
        raise HTTPException(400, "status is per-tenant")
    return P.status_snapshot(tid)


@app.get("/techdeva/overview")
def techdeva_overview(x_role: str | None = Header(None)):
    if (x_role or "") != "techdeva_admin":
        raise HTTPException(403, "TechDeva overview is admin-only")
    return P.techdeva_overview()


class ClientRequest(BaseModel):
    intent: str                      # campaign | content | post | query
    service: str | None = None
    brief: str | None = None
    budget: str | None = None
    lead_id: str | None = None
    conversation_id: str | None = None


@app.post("/client/request")
def client_request(body: ClientRequest,
                   x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None)):
    """A client message (captured by Hermes) -> orchestrator routes to the workforce."""
    tid, role = scope(x_tenant_id, x_role)
    if not tid:
        raise HTTPException(400, "tenant required")
    P.emit(tid, "client.requested", body.model_dump(), "hermes")
    return {"queued": True, "intent": body.intent}


@app.get("/events")
def list_events(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None)):
    tid, role = scope(x_tenant_id, x_role)
    with tenant_session(tid, role) as cur:
        cur.execute("""SELECT type, payload, emitted_by, at
                       FROM events ORDER BY at DESC LIMIT 50""")
        return {"events": cur.fetchall()}


# ======================== OAuth — client account connections ========================
from fastapi.responses import RedirectResponse, HTMLResponse
from .oauth import (meta_auth_url, meta_exchange_code, google_auth_url,
                    google_exchange_code, save_connection)


@app.get("/connect/{provider}")
def connect_start(provider: str, tenant_id: str):
    """Redirect the client to the provider's OAuth screen.
    Usage: send the client a link like /connect/meta?tenant_id=<their-uuid>
    (Hermes can send this on WhatsApp as a "Connect your Facebook" button.)"""
    if provider == "meta":
        return RedirectResponse(meta_auth_url(tenant_id))
    if provider == "google":
        return RedirectResponse(google_auth_url(tenant_id))
    raise HTTPException(400, f"unsupported provider: {provider}")


@app.get("/oauth/meta/callback")
def meta_callback(code: str = "", state: str = "", error: str = ""):
    """Facebook redirects the client back here after they click Allow."""
    if error or not code:
        return HTMLResponse(f"<h2>Connection failed</h2><p>{error or 'no code'}</p>", 400)
    tenant_id = state
    if not tenant_id:
        return HTMLResponse("<h2>Missing tenant</h2>", 400)
    try:
        token_data = meta_exchange_code(code)
    except Exception as e:
        return HTMLResponse(f"<h2>Token exchange failed</h2><p>{e}</p>", 500)
    ad_accts = token_data.get("ad_accounts", [])
    ext = ad_accts[0]["account_id"] if ad_accts else ""
    save_connection(P, tenant_id, "meta", token_data, ext)
    # also mark instagram as connected (same token gives IG access)
    save_connection(P, tenant_id, "instagram", {"via": "meta_token"}, "")
    return HTMLResponse(
        "<html><body style='background:#0A0E1A;color:#EAF1FF;font-family:sans-serif;text-align:center;padding:60px'>"
        "<h1 style='color:#5FD44A'>✓ Facebook & Instagram connected!</h1>"
        f"<p>Ad account: {ext or 'will be set up'}</p>"
        "<p>You can close this window. TechDeva is ready to run your campaigns.</p>"
        "</body></html>"
    )


@app.get("/oauth/google/callback")
def google_callback(code: str = "", state: str = "", error: str = ""):
    if error or not code:
        return HTMLResponse(f"<h2>Connection failed</h2><p>{error or 'no code'}</p>", 400)
    tenant_id = state
    if not tenant_id:
        return HTMLResponse("<h2>Missing tenant</h2>", 400)
    try:
        token_data = google_exchange_code(code)
    except Exception as e:
        return HTMLResponse(f"<h2>Token exchange failed</h2><p>{e}</p>", 500)
    save_connection(P, tenant_id, "google", token_data, "")
    return HTMLResponse(
        "<html><body style='background:#0A0E1A;color:#EAF1FF;font-family:sans-serif;text-align:center;padding:60px'>"
        "<h1 style='color:#5FD44A'>✓ Google connected!</h1>"
        "<p>Google Ads and Business Profile are now linked.</p>"
        "<p>You can close this window.</p>"
        "</body></html>"
    )


@app.get("/integrations")
def list_integrations(x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None)):
    """List a tenant's connected accounts (or all if admin)."""
    tid, role = scope(x_tenant_id, x_role)
    with tenant_session(tid, role) as cur:
        cur.execute("""SELECT provider, status, external_account, connected_at, last_error
                       FROM integrations ORDER BY provider""")
        return {"integrations": cur.fetchall()}
