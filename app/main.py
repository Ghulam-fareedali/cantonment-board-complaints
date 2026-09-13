"""FastAPI entry for the TechDeva platform (Phase B).

Now the API doesn't just store events — it pushes them onto the queue, and the
worker autonomously runs the pipeline. Create a lead and watch tasks appear and
complete with no human action.

Identity (Build 2): a signed session token issued by /auth/login. The old
X-Tenant-Id / X-Role headers still work for local development, but setting
AUTH_REQUIRED=true disables them everywhere at once — see app/auth.py. RLS
enforces isolation underneath either way, as long as the app connects as the
non-superuser role.
"""
import os
import time
import re
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Cookie, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from .db import tenant_session
from .attribution import (extract_click, merge_click,
                          build_attribution_report)
from .persistence import Persistence
from .auth import AuthError, SESSION_COOKIE, hash_password, resolve_scope, issue_oauth_state, verify_oauth_state
from .portal import make_portal_router
from .business import make_business_router
from .brain import make_brain_router
from .operations import make_operations_router
from .policy import make_policy_router
from .whatsapp import make_whatsapp_router

app = FastAPI(title="TechDeva AIBOS", version="0.4-client-portal")

# Browser access for the Orchestrator Cockpit and the client portal.
# ALLOWED_ORIGINS is a comma-separated list; default "*" is DEV ONLY — set it to
# your real origins before the API is reachable from the internet, because
# allow_credentials + "*" is exactly how a session cookie leaks cross-site.
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
P = Persistence()
app.include_router(make_portal_router(P))
app.include_router(make_business_router())
app.include_router(make_brain_router())
app.include_router(make_operations_router())
app.include_router(make_whatsapp_router())
app.include_router(make_policy_router())

# The two client-facing pages. The API image is built from ./app, so web/ is
# mounted in (see docker-compose.yml) rather than baked into the image; WEB_DIR
# overrides the location if you serve them from somewhere else entirely.
WEB_DIR = Path(os.environ.get("WEB_DIR") or (Path(__file__).resolve().parent.parent / "web"))


def _page(name: str):
    path = WEB_DIR / name
    if not path.is_file():
        # honest failure: say what is missing instead of a 500 stack trace
        raise HTTPException(503, f"{name} is not available on this server "
                                 f"(expected at {path}). Mount ./web or set WEB_DIR.")
    return FileResponse(path)


def scope(x_tenant_id, x_role, authorization=None, session=None):
    """Identity for every legacy route. Delegates to the ONE resolver in auth.py,
    so setting AUTH_REQUIRED=true closes header spoofing across the whole API at
    once rather than route by route."""
    try:
        tid, role, _uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    return tid, role


@app.get("/health")
def health():
    return {"ok": True, "service": "techdeva", "phase": "B"}


@app.get("/leads")
def list_leads(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    with tenant_session(tid, role) as cur:
        cur.execute("""SELECT id, name, phone, service, status, source, priority, est_revenue
                       FROM leads ORDER BY priority, created_at DESC LIMIT 200""")
        return {"leads": cur.fetchall()}


# lead_source is a Postgres enum describing how a lead ENTERED the system, not
# which marketing channel it came from -- the channel lives in click_source, ad_id
# and campaign_external_id. Anything outside this set used to pass validation and
# die at the database as "invalid input value for enum lead_source". Say what is
# allowed, at the boundary, instead.
LEAD_SOURCES = ("hermes", "human", "imported", "api", "manual")


class LeadIn(BaseModel):
    name: str
    phone: str | None = None
    service: str | None = None
    source: str = "manual"

    @field_validator("source")
    @classmethod
    def _known_source(cls, v):
        if v is None or v == "":
            return "manual"
        if v not in LEAD_SOURCES:
            raise ValueError(
                "source must be one of " + ", ".join(LEAD_SOURCES) +
                ". A marketing channel belongs in click_source, not here.")
        return v
    est_revenue: float = 0
    # ---- attribution (all optional; absence means an organic lead) ----
    click_id: str | None = None            # fbclid / gclid / ctwa_clid / ...
    click_source: str | None = None        # 'meta' | 'google' | ...
    ad_id: str | None = None
    campaign_external_id: str | None = None
    landing_url: str | None = None         # parsed for a click id if not given
    first_message: str | None = None        # click-to-WhatsApp referral text


@app.post("/leads")
def create_lead(body: LeadIn, x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    if role == "techdeva_admin":
        raise HTTPException(400, "admin has no tenant to write into")
    with tenant_session(tid, role) as cur:
        cur.execute("""INSERT INTO leads (tenant_id, name, phone, service, source, est_revenue, created_by)
                       VALUES (current_tenant(), %s,%s,%s,%s,%s,%s) RETURNING id""",
                    (body.name, body.phone, body.service, body.source, body.est_revenue, role))
        lead_id = str(cur.fetchone()["id"])

    # ---- attribution capture: the first link in the chain ----
    # An explicit click_id wins; otherwise try to recover one from the landing
    # URL or the click-to-WhatsApp referral text. No click found = organic lead,
    # which is a real outcome and never gets an invented id.
    click = merge_click(
        {"click_id": body.click_id, "click_source": body.click_source,
         "ad_id": body.ad_id, "campaign_external_id": body.campaign_external_id,
         "landing_url": body.landing_url},
        extract_click(body.landing_url) or extract_click(body.first_message))
    campaign_id = None
    if click.get("click_id"):
        try:
            campaign_id = P.attach_click_to_lead(tid, lead_id, click)
        except Exception as e:
            # Attribution must never block lead capture. A lost click costs a
            # report line; a lost lead costs a customer.
            print(f"[attribution] capture failed for lead {lead_id}: {e}", flush=True)

    # fire the pipeline: lead.created -> (worker) -> Hermes qualifies -> ...
    P.emit(tid, "lead.created",
           {"lead_id": lead_id, "service": body.service,
            "est_revenue": body.est_revenue,
            # carried through Hermes so the whole chain keeps the reference
            "click_id": click.get("click_id"),
            "campaign_id": str(campaign_id) if campaign_id else None}, role)
    return {"id": lead_id, "emitted": "lead.created",
            "attributed": campaign_id is not None,
            "click_id": click.get("click_id")}


class ClickIn(BaseModel):
    """A click recorded by the landing page BEFORE the visitor ever messages."""
    click_id: str | None = None
    click_source: str | None = None
    ad_id: str | None = None
    campaign_external_id: str | None = None
    landing_url: str | None = None


@app.post("/attribution/click")
def record_click(body: ClickIn, x_tenant_id: str | None = Header(None),
                 x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    """Park a click so it is not lost in the gap before a lead exists."""
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    if role == "techdeva_admin":
        raise HTTPException(400, "admin has no tenant to write into")
    click = merge_click(body.model_dump(), extract_click(body.landing_url))
    if not click.get("click_id"):
        raise HTTPException(400, "no valid click id supplied or found in landing_url")
    return {"id": P.record_click(tid, click), "click_id": click["click_id"]}


class RevenueIn(BaseModel):
    lead_id: str | None = None
    amount: float
    type: str | None = None


@app.post("/revenue")
def record_revenue(body: RevenueIn, x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    """Record money earned. Attribution is FROZEN onto the row here."""
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    if role == "techdeva_admin":
        raise HTTPException(400, "admin has no tenant to write into")
    if body.amount is None or float(body.amount) < 0:
        raise HTTPException(400, "amount must be zero or positive")
    out = P.record_revenue(tid, body.lead_id, body.amount, body.type,
                           attributed_source="human" if role != "hermes" else "hermes")
    P.emit(tid, "revenue.recorded",
           {"lead_id": body.lead_id, "amount": body.amount,
            "campaign_id": out["campaign_id"]}, role)
    return out


@app.get("/attribution/report")
def attribution_report(x_tenant_id: str | None = Header(None),
                       x_role: str | None = Header(None),
                       authorization: str | None = Header(None),
                       td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    """Per-campaign ROAS and CPL, with an explicit unattributed bucket."""
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    rows = P.attribution_rows(tid)
    return build_attribution_report(rows["campaigns"],
                                    unattributed_revenue=rows["unattributed_revenue"],
                                    total_spend=rows["total_spend"])


class EventIn(BaseModel):
    type: str
    payload: dict = {}


@app.post("/events")
def ingest_event(body: EventIn, x_tenant_id: str | None = Header(None),
                 x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    if not tid:
        raise HTTPException(400, "events need a tenant")
    P.emit(tid, body.type, body.payload, role)
    return {"queued": True, "type": body.type}


@app.get("/tasks")
def list_tasks(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    with tenant_session(tid, role) as cur:
        cur.execute("""SELECT agent, type, status, payload, result
                       FROM tasks ORDER BY created_at DESC LIMIT 50""")
        return {"tasks": cur.fetchall()}


@app.get("/orchestrator/status")
def orchestrator_status(x_tenant_id: str | None = Header(None),
                        x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    if not tid:
        raise HTTPException(400, "status is per-tenant")
    return P.status_snapshot(tid)


@app.get("/techdeva/overview")
def techdeva_overview(x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    # The cross-tenant view. Previously this trusted a raw header, which means any
    # caller could read every client's numbers by claiming to be admin. It now goes
    # through the same resolver as everything else, so with AUTH_REQUIRED=true the
    # role can only come from a token this server signed.
    _tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    if role != "techdeva_admin":
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
                   x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    """A client message (captured by Hermes) -> orchestrator routes to the workforce."""
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    if not tid:
        raise HTTPException(400, "tenant required")
    P.emit(tid, "client.requested", body.model_dump(), "hermes")
    return {"queued": True, "intent": body.intent}


# ======================== Growth Engine — self-serve intake ========================

# --- signup guards -----------------------------------------------------------
# /signup is the one route with no credential in front of it. Everything a
# stranger can reach has to be bounded here.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")
URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]*$", re.I)
WEAK_PASSWORDS = {"password", "password1", "12345678", "123456789", "qwertyui",
                  "letmein1", "changeme", "admin123", "techdeva", "passw0rd"}

SIGNUP_MAX_PER_IP = 3
SIGNUP_MAX_PER_EMAIL = 1
SIGNUP_WINDOW = 3600
_signup_hits: dict = {}


def _rate_ok(key: str, limit: int) -> bool:
    now = time.time()
    hits = [t for t in _signup_hits.get(key, []) if now - t < SIGNUP_WINDOW]
    if len(hits) >= limit:
        _signup_hits[key] = hits
        return False
    hits.append(now)
    _signup_hits[key] = hits
    if len(_signup_hits) > 10000:
        for k in [k for k, v in list(_signup_hits.items())
                  if not v or now - v[-1] > SIGNUP_WINDOW]:
            _signup_hits.pop(k, None)
    return True


def _client_ip(request: Request) -> str:
    """The address Traefik actually saw.

    X-Forwarded-For is caller-controlled up to the last hop: a client can send
    its own header and Traefik appends to it. The LEFTMOST entry is therefore
    attacker-chosen and useless for rate limiting - the RIGHTMOST is the one our
    own proxy wrote. Taking the leftmost would let anyone bypass this limiter by
    sending a different fake header each time.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        parts = [p.strip() for p in fwd.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


class SignupIn(BaseModel):
    business_name: str
    industry: str = "general"
    website_url: str | None = None
    social_links: dict = {}                 # {facebook, linkedin, instagram, ...}
    competitors: list[dict] = []            # [{name?, website?, facebook?}] (optional)
    contact_email: str | None = None
    contact_phone: str | None = None
    contact_name: str | None = None
    password: str | None = None             # the client's own portal login

    @field_validator("business_name")
    @classmethod
    def _check_business_name(cls, v):
        v = (v or "").strip()
        if len(v) < 2:
            raise ValueError("business_name must be at least 2 characters")
        if len(v) > 120:
            raise ValueError("business_name must be 120 characters or fewer")
        if "<" in v or ">" in v:
            raise ValueError("business_name cannot contain < or >")
        return v

    @field_validator("contact_email")
    @classmethod
    def _check_email(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not v:
            return None
        if len(v) > 254 or not EMAIL_RE.match(v):
            raise ValueError("contact_email is not a valid email address")
        return v

    @field_validator("website_url")
    @classmethod
    def _check_url(cls, v):
        if v is None or not v.strip():
            return None
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            v = "https://" + v
        if len(v) > 2000 or not URL_RE.match(v):
            raise ValueError("website_url is not a valid URL")
        return v

    @field_validator("contact_name", "contact_phone", "industry")
    @classmethod
    def _check_short_text(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) > 120:
            raise ValueError("value must be 120 characters or fewer")
        if "<" in v or ">" in v:
            raise ValueError("value cannot contain < or >")
        return v

    @field_validator("password")
    @classmethod
    def _check_password(cls, v):
        if v is None:
            return v
        if len(v) < 10:
            raise ValueError("password must be at least 10 characters")
        if len(v) > 256:
            raise ValueError("password must be 256 characters or fewer")
        if v.lower() in WEAK_PASSWORDS or len(set(v)) < 5:
            raise ValueError("password is too easy to guess - use a longer, less common one")
        return v


@app.post("/signup")
def public_signup(body: SignupIn, request: Request):
    """PUBLIC self-serve signup on techdeva.net. No auth: this is the front door.
    Records the intake, provisions a 14-day trial tenant, creates the client's own
    login, and kicks off analysis. The owner does nothing — the pipeline runs
    itself from here.

    A password is required to get a dashboard login. Without one the tenant is
    still provisioned and analysed, but there is no portal account — we never
    invent a default password."""
    ip = _client_ip(request)
    if not _rate_ok("ip:" + ip, SIGNUP_MAX_PER_IP):
        raise HTTPException(429, "too many signups from this address - try again later")
    if body.contact_email:
        if P.find_user_by_email(body.contact_email):
            raise HTTPException(409, "that email is already registered - log in instead")
        if not _rate_ok("em:" + body.contact_email, SIGNUP_MAX_PER_EMAIL):
            raise HTTPException(429, "a signup for this email is already in progress")

    pw_hash = None
    if body.password:
        if not body.contact_email:
            raise HTTPException(400, "contact_email is required to create a login")
        try:
            pw_hash = hash_password(body.password)
        except AuthError as e:
            raise HTTPException(e.status, e.message)

    signup_id = P.create_signup(
        body.business_name, body.industry, body.website_url,
        body.social_links, body.competitors, body.contact_email, body.contact_phone)
    tenant_id = P.provision_tenant_from_signup(
        signup_id, owner_name=body.contact_name, password_hash=pw_hash)
    return {"signup_id": signup_id, "tenant_id": tenant_id,
            "status": "trial_started",
            "login_created": pw_hash is not None,
            "portal": "/portal",
            "message": "Analysis in progress — your report is being prepared."}


# ---------------------- the pages the client actually sees ----------------------
@app.get("/", include_in_schema=False)
def signup_page():
    """The public front door on techdeva.net."""
    return _page("signup.html")


@app.get("/onboarding", include_in_schema=False)
def onboarding_page():
    """Where a client describes their business. The agents already read this."""
    return _page("onboarding.html")


@app.get("/portal", include_in_schema=False)
def portal_page():
    """The client's trial dashboard: report, deliverables, approvals."""
    return _page("portal.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    """The universal tenant business dashboard.

    One page for every tenant. It carries no tenant identity of its own: the
    browser is handed nothing but HTML, and every figure on it is fetched from
    an endpoint that resolves the tenant from the signed session via
    auth.resolve_scope(). A tenant id supplied by the browser is ignored.
    """
    return _page("dashboard.html")


@app.get("/techdeva", include_in_schema=False)
def techdeva_page():
    """The founder view. The page needs no credential; its data does."""
    return _page("techdeva.html")


@app.get("/techdeva/health")
def techdeva_health(x_tenant_id: str | None = Header(None),
                    x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    """Platform-wide health. Admin-only, same resolver as every other route."""
    _tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    if role != "techdeva_admin":
        raise HTTPException(403, "TechDeva health is admin-only")
    return P.techdeva_health()


@app.get("/report")
def get_report(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    """The client's latest combined analysis report (for their dashboard)."""
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    if not tid:
        raise HTTPException(400, "tenant required")
    rep = P.latest_report(tid)
    if not rep:
        return {"status": "pending", "message": "Your analysis is still being prepared."}
    return rep


@app.get("/events")
def list_events(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    with tenant_session(tid, role) as cur:
        cur.execute("""SELECT type, payload, emitted_by, at
                       FROM events ORDER BY at DESC LIMIT 50""")
        return {"events": cur.fetchall()}


# ======================== OAuth — client account connections ========================
from fastapi.responses import RedirectResponse, HTMLResponse
from .oauth import (meta_auth_url, meta_exchange_code, google_auth_url,
                    google_exchange_code, save_connection)


@app.get("/connect/{provider}")
def connect_start(provider: str,
                  x_tenant_id: str | None = Header(None),
                  x_role: str | None = Header(None),
                  authorization: str | None = Header(None),
                  td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    """Redirect the client to the provider's OAuth screen.
    Usage: send the client a link like /connect/meta?tenant_id=<their-uuid>
    (Hermes can send this on WhatsApp as a "Connect your Facebook" button.)"""
    try:
        tenant_id, _role, _uid = resolve_scope(x_tenant_id, x_role, authorization, td_session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tenant_id:
        raise HTTPException(400, "tenant required")
    if provider == "meta":
        return RedirectResponse(meta_auth_url(issue_oauth_state(tenant_id, "meta")))
    if provider == "google":
        return RedirectResponse(google_auth_url(issue_oauth_state(tenant_id, "google")))
    raise HTTPException(400, f"unsupported provider: {provider}")


@app.get("/oauth/meta/callback")
def meta_callback(code: str = "", state: str = "", error: str = ""):
    """Facebook redirects the client back here after they click Allow."""
    if error or not code:
        return HTMLResponse(f"<h2>Connection failed</h2><p>{error or 'no code'}</p>", 400)
    try:
        tenant_id = verify_oauth_state(state, "meta")
    except AuthError as e:
        return HTMLResponse(f"<h2>Connection failed</h2><p>{e}</p>", 400)
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
    try:
        tenant_id = verify_oauth_state(state, "google")
    except AuthError as e:
        return HTMLResponse(f"<h2>Connection failed</h2><p>{e}</p>", 400)
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


# A connection is a claim about the outside world, and the only evidence the
# platform holds for it is a stored credential plus the moment it was stored.
KNOWN_PROVIDERS = ("whatsapp", "meta", "instagram", "google")


def _integration_view(r):
    proven = bool(r.get("has_credentials")) and r.get("connected_at") is not None
    status = r.get("status") or "never_connected"
    if status == "connected" and not proven:
        status = "never_connected"       # belt and braces; the CHECK already refuses this
    return {
        "provider": r["provider"],
        "status": status,
        "connected": proven and status == "connected",
        "health": r.get("health") or "unknown",
        "external_account": r.get("external_account"),
        "connected_at": r.get("connected_at"),
        "disconnected_at": r.get("disconnected_at"),
        "expires_at": r.get("expires_at"),
        "last_checked_at": r.get("last_checked_at"),
        "scopes": r.get("scopes") or [],
        "last_error": r.get("last_error"),
    }


@app.get("/integrations")
def list_integrations(x_tenant_id: str | None = Header(None),
                      x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
    """Report what this tenant can prove about each integration.

    Part 4 found four rows claiming `connected` while holding no credential at
    all, so nothing here is taken on trust. `connected` is computed from the
    evidence -- a stored credential and a real connection moment -- rather than
    read from the status column, and a provider with no row is reported as
    never_connected instead of silently omitted.
    """
    tid, role = scope(x_tenant_id, x_role, authorization, td_session)
    with tenant_session(tid, role) as cur:
        cur.execute("""SELECT provider, status, external_account, connected_at,
                              disconnected_at, last_error, health, scopes,
                              expires_at, last_checked_at,
                              (credentials <> '{}'::jsonb) AS has_credentials
                       FROM integrations ORDER BY provider""")
        rows = {r["provider"]: dict(r) for r in cur.fetchall()}

    out = []
    for provider in KNOWN_PROVIDERS:
        r = rows.pop(provider, None) or {"provider": provider,
                                         "status": "never_connected",
                                         "has_credentials": False}
        out.append(_integration_view(r))
    for leftover in rows.values():          # a provider added after this list
        out.append(_integration_view(leftover))
    return {"integrations": out}


# ---------------------------------------------------------------------------
# Database errors that are really the caller's fault should say so.
#
# Part 1 found POST /leads returning 500 when `source` was not a valid
# lead_source enum value. The database was right to refuse it; the API was
# wrong to call it a server error. A 500 tells the caller "we broke" and
# invites a retry that can never succeed.
#
# DataError covers invalid enum labels, bad numerics, bad dates and the like.
# IntegrityError covers unique/foreign-key/check violations. Both mean the
# request was unacceptable, so both become 400 with the database's own
# message -- which names the offending value -- while validation itself stays
# exactly as strict as it was.
# ---------------------------------------------------------------------------
import psycopg
from fastapi.responses import JSONResponse


def _db_message(exc: Exception) -> str:
    diag = getattr(exc, "diag", None)
    parts = [getattr(diag, "message_primary", None) or str(exc).strip().splitlines()[0]]
    detail = getattr(diag, "message_detail", None)
    if detail:
        parts.append(detail)
    hint = getattr(diag, "message_hint", None)
    if hint:
        parts.append(hint)
    return " ".join(p for p in parts if p)[:400]


@app.exception_handler(psycopg.errors.DataError)
def psycopg_error_handler(request: Request, exc: psycopg.errors.DataError):
    return JSONResponse(status_code=400, content={"detail": _db_message(exc)})


@app.exception_handler(psycopg.errors.IntegrityError)
def psycopg_integrity_handler(request: Request, exc: psycopg.errors.IntegrityError):
    # A signed session outlives the tenant it names. When the tenant is gone every
    # write fails on its tenant_id foreign key, and the owner sees a database error
    # for what is really an expired session. Say the true thing instead.
    text = _db_message(exc)
    if "tenant_id_fkey" in text and "not present in table" in text:
        return JSONResponse(
            status_code=401,
            content={"detail": "This session is no longer valid. Please sign in again."})
    return JSONResponse(status_code=400, content={"detail": text})
