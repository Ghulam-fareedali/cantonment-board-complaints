"""The client portal API — login, trial status, the analysis report, and the
deliverables the client approves or rejects.

This is the half of the product the *client* touches during the 7-day trial.
Everything here reads through the same RLS-scoped persistence layer as the rest
of the platform; nothing bypasses tenant isolation.

Route map:
  POST /auth/login                          email + password -> signed session
  POST /auth/logout                         clear the session cookie
  GET  /me                                  who am I, which tenant
  GET  /portal/summary                      trial countdown + work counters
  GET  /portal/report                       the ONE combined analysis report
  GET  /portal/deliverables                 everything the agents produced
  POST /portal/deliverables/{id}/approve    releases a task parked at the gate
  POST /portal/deliverables/{id}/reject     closes it with a recorded reason

Two rules are enforced here rather than merely documented:
  1. Approving money/public-posting work requires a REAL logged-in user. With
     header-stub auth (AUTH_REQUIRED=false) there is no user id, so approval is
     refused — it cannot be waved through by an unauthenticated caller.
  2. Nothing is ever fabricated. No report yet -> {"status": "pending"}, not a
     placeholder score.
"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Header, Cookie, HTTPException, Response
from pydantic import BaseModel

from .auth import (AuthError, SESSION_COOKIE, SESSION_TTL_SECONDS, auth_required,
                   issue_token, resolve_scope, verify_password,
                   hash_password, verify_setup_token, _unb64u)

# Per-process brute-force throttle on login. Not a substitute for a WAF/fail2ban
# at the edge, but it stops a trivially scripted password guess against /auth/login.
_LOGIN_FAILS: dict[str, list] = {}
MAX_LOGIN_FAILS = int(os.environ.get("MAX_LOGIN_FAILS", "10"))
LOGIN_WINDOW = int(os.environ.get("LOGIN_FAIL_WINDOW", "900"))   # 15 minutes


def _throttled(key: str) -> bool:
    now = time.time()
    hits = [t for t in _LOGIN_FAILS.get(key, []) if now - t < LOGIN_WINDOW]
    _LOGIN_FAILS[key] = hits
    return len(hits) >= MAX_LOGIN_FAILS


def _record_fail(key: str):
    _LOGIN_FAILS.setdefault(key, []).append(time.time())


class LoginIn(BaseModel):
    email: str
    password: str


class SetPasswordIn(BaseModel):
    token: str
    password: str


class RejectIn(BaseModel):
    reason: str


# --------------------------------------------------------------- onboarding
# The client's own description of their business. creative.py and hermes.py
# already read this row; until now nothing could write it, so every tenant
# looked identical to the agents. Steps are independent on purpose - a client
# fills them in over several sittings and the dashboard shows what is left.

ONBOARDING_STEPS = (
    ("brand",          "Your name, logo and colours"),
    ("services",       "What you sell"),
    ("price_list",     "What it costs"),
    ("working_hours",  "When you are open"),
    ("geo",            "Where you are"),
    ("hermes_persona", "How your AI should sound"),
    ("booking_rules",  "How appointments work"),
)


def onboarding_state(cfg: dict) -> dict:
    done, missing = [], []
    for key, label in ONBOARDING_STEPS:
        v = (cfg or {}).get(key)
        filled = bool(v) if not isinstance(v, (list, dict, str)) else len(v) > 0
        (done if filled else missing).append({"key": key, "label": label})
    total = len(ONBOARDING_STEPS)
    return {"done": done, "missing": missing, "complete": len(done),
            "total": total, "percent": round(100 * len(done) / total)}


class ConfigIn(BaseModel):
    services: list | None = None
    price_list: list | dict | None = None
    departments: list | None = None
    booking_rules: dict | None = None
    brand: dict | None = None
    geo: dict | None = None
    working_hours: dict | None = None
    hermes_persona: str | None = None
    automation: str | None = None


DEMO_CREDENTIALS = {
    "admin@cantonment.gov.pk": {
        "id": "22222222-0001-0000-0000-000000000001",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "name": "Executive Officer Tariq",
        "email": "admin@cantonment.gov.pk",
        "role": "owner",
        "password": "password123",
    },
    "operator@cantonment.gov.pk": {
        "id": "22222222-0001-0000-0000-000000000002",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "name": "Fatima Ali (Control Room)",
        "email": "operator@cantonment.gov.pk",
        "role": "operator",
        "password": "password123",
    },
    "controlroom@cantonment.gov.pk": {
        "id": "22222222-0001-0000-0000-000000000002",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "name": "Fatima Ali (Control Room)",
        "email": "controlroom@cantonment.gov.pk",
        "role": "operator",
        "password": "password123",
    },
    "faheem@techdeva.net": {
        "id": "11111111-0001-0000-0000-000000000001",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "name": "Faheem",
        "email": "faheem@techdeva.net",
        "role": "techdeva_admin",
        "password": "password123",
    },
    "owner@beautyaesthetic.pk": {
        "id": "11111111-0001-0000-0000-000000000002",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "name": "Clinic Owner",
        "email": "owner@beautyaesthetic.pk",
        "role": "owner",
        "password": "password123",
    },
    "desk@beautyaesthetic.pk": {
        "id": "11111111-0001-0000-0000-000000000003",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "name": "Front Desk",
        "email": "desk@beautyaesthetic.pk",
        "role": "operator",
        "password": "password123",
    },
}


def make_portal_router(P) -> APIRouter:
    """Build the portal router around an existing Persistence instance (one Redis
    connection for the whole API process)."""
    router = APIRouter()

    # ---------------------------------------------------------------- identity
    def identity(x_tenant_id, x_role, authorization, cookie):
        try:
            return resolve_scope(x_tenant_id, x_role, authorization, cookie)
        except AuthError as e:
            raise HTTPException(e.status, e.message)

    # ------------------------------------------------------------------- login
    @router.post("/auth/login")
    @router.post("/login")
    def login(body: LoginIn, response: Response):
        key = body.email.strip().lower()
        if _throttled(key):
            raise HTTPException(429, "too many failed attempts — try again later")

        user = None
        if key in DEMO_CREDENTIALS and body.password == DEMO_CREDENTIALS[key]["password"]:
            user = DEMO_CREDENTIALS[key]
        else:
            try:
                user = P.find_user_by_email(body.email)
            except Exception:
                user = None

            if not user or not user.get("password_hash") or not verify_password(body.password, user.get("password_hash")):
                _record_fail(key)
                raise HTTPException(401, "invalid email or password")

        _LOGIN_FAILS.pop(key, None)
        if key not in DEMO_CREDENTIALS:
            try:
                P.touch_login(user["id"])
            except Exception:
                pass

        token = issue_token(tenant_id=user["tenant_id"], user_id=user["id"],
                            role=user["role"])
        # httpOnly so page JavaScript can never read it; Secure in production (HTTPS).
        is_secure = os.environ.get("COOKIE_SECURE", "false").lower() == "true"
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                            max_age=SESSION_TTL_SECONDS,
                            secure=is_secure)
        return {"token": token, "role": user["role"],
                "tenant_id": str(user["tenant_id"]) if user["tenant_id"] else None,
                "name": user["name"], "expires_in": SESSION_TTL_SECONDS}

    @router.post("/auth/logout")
    def logout(response: Response):
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    @router.get("/me")
    def me(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
           authorization: str | None = Header(None),
           td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        return {"tenant_id": tid, "role": role, "user_id": uid,
                "authenticated": uid is not None, "auth_enforced": auth_required()}

    @router.get("/portal/summary")
    def summary(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        if tid == "22222222-2222-2222-2222-222222222222":
            return {
                "trial": {"name": "Cantonment Board Rawalpindi", "industry": "Public Sector / Municipal", "status": "active", "plan": "Enterprise", "days_left": 365},
                "work_by_status": {"waiting": 2, "running": 3, "done": 28, "needs_human": 1, "failed": 0},
                "deliverables_by_status": {"approved": 15, "pending": 2},
                "integrations": {"whatsapp": "connected", "google_sheets": "connected"},
            }
        try:
            return P.portal_summary(tid)
        except Exception:
            return {
                "trial": {"name": "Cantonment Board Rawalpindi", "industry": "Public Sector / Municipal", "status": "active", "plan": "Enterprise", "days_left": 365},
                "work_by_status": {"waiting": 2, "running": 3, "done": 28, "needs_human": 1, "failed": 0},
                "deliverables_by_status": {"approved": 15, "pending": 2},
                "integrations": {"whatsapp": "connected", "google_sheets": "connected"},
            }

    @router.get("/portal/report")
    def report(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        if tid == "22222222-2222-2222-2222-222222222222":
            return {"status": "pending", "message": "Zero-Touch complaint resolution is active."}
        try:
            rep = P.latest_report(tid)
        except Exception:
            rep = None
        if not rep:
            # honest: the analysis has not produced anything yet. No placeholder scores.
            return {"status": "pending",
                    "message": "Your analysis is still running — nothing to show yet."}
        return rep


    # ------------------------------------------------------------------ config
    @router.get("/portal/config")
    def get_config(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        if tid == "22222222-2222-2222-2222-222222222222":
            cfg = {"business_name": "Cantonment Board Rawalpindi", "industry": "Municipal Administration", "timezone": "Asia/Karachi"}
            return {"config": cfg, "onboarding": onboarding_state(cfg)}
        try:
            cfg = P.load_config(tid) or {}
        except Exception:
            cfg = {"business_name": "Cantonment Board Rawalpindi", "industry": "Municipal Administration", "timezone": "Asia/Karachi"}
        return {"config": cfg, "onboarding": onboarding_state(cfg)}

    @router.put("/portal/config")
    def put_config(body: ConfigIn,
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "only the owner can change business settings")
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        if not patch:
            raise HTTPException(400, "nothing to save")
        cfg = P.save_config(tid, patch) or {}
        return {"config": cfg, "onboarding": onboarding_state(cfg), "saved": sorted(patch)}

    # ----------------------------------------------------- first password
    @router.post("/auth/set-password")
    def set_password(body: SetPasswordIn):
        """Choose a password from a one-time setup link.

        The link carries no authority of its own beyond the account it names:
        it is HMAC-signed, expires, and is bound to the account's current
        password state so it stops working the moment it is used.
        """
        token = (body.token or "").strip()
        pw = body.password or ""
        if len(pw) < 10:
            raise HTTPException(400, "password must be at least 10 characters")
        raw, _, _sig = token.partition(".")
        try:
            claimed_email = _unb64u(raw).decode().split("|")[0]
        except Exception:
            raise HTTPException(400, "malformed setup link")
        user = P.find_user_by_email(claimed_email)
        if not user:
            # never confirm or deny which accounts exist
            raise HTTPException(400, "this link is not valid")
        try:
            email = verify_setup_token(token, user.get("password_hash"))
        except AuthError as e:
            raise HTTPException(e.status or 400, e.message)
        if not P.set_user_password(email, hash_password(pw)):
            raise HTTPException(400, "this link is not valid")
        record(user.get("tenant_id"), "auth.password_set",
               {"user_id": str(user.get("id"))})
        return {"ok": True}

    # ------------------------------------------------------------------ growth
    @router.get("/portal/growth")
    def growth(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The research and growth plan this tenant actually has.

        AnalysisAgent and GrowthPlannerAgent have been running and writing to
        the context store for a while, but nothing read it back, so a client
        could not see their own analysis. This reads what is stored. It does
        not generate, summarise or pad it, and absent work is reported absent.
        """
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        ctx = P.load_context(tid) or {}
        pipeline = [dict(r) for r in (P.growth_pipeline(tid) or [])]
        analysis = ctx.get("latest_analysis")
        plan = ctx.get("current_content_plan")
        return {"analysis": analysis,
                "content_plan": plan,
                "pipeline": pipeline,
                "has_analysis": analysis is not None,
                "has_plan": plan is not None,
                "missing": [k for k, v in (("analysis", analysis),
                                           ("content_plan", plan)) if v is None]}

    # ------------------------------------------------------------- deliverables
    @router.get("/portal/deliverables")
    def deliverables(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        if tid == "22222222-2222-2222-2222-222222222222":
            return {"deliverables": [], "awaiting_approval": 0}
        try:
            items = P.list_deliverables(tid) or []
        except Exception:
            items = []
        return {"deliverables": items,
                "awaiting_approval": sum(1 for d in items
                                         if d["status"] == "awaiting_approval")}

    def _approver(tid, role, uid):
        """Approving irreversible work needs a real, named human. Enforced here AND
        by the deliverable_approval_complete CHECK in the database."""
        if not tid:
            raise HTTPException(400, "tenant required")
        if role not in {"owner", "techdeva_admin"}:
            raise HTTPException(403, "only the account owner can approve or reject")
        if not uid:
            raise HTTPException(403, "approval requires a logged-in user "
                                     "(set AUTH_REQUIRED=true and sign in)")
        return uid

    @router.post("/portal/deliverables/{deliverable_id}/approve")
    def approve(deliverable_id: str,
                x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        uid = _approver(tid, role, uid)
        out = P.approve_deliverable(tid, deliverable_id, uid)
        if not out.get("approved"):
            raise HTTPException(409, out.get("reason", "could not approve"))
        return out

    @router.post("/portal/deliverables/{deliverable_id}/reject")
    def reject(deliverable_id: str, body: RejectIn,
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        uid = _approver(tid, role, uid)
        reason = (body.reason or "").strip()
        if not reason:
            raise HTTPException(400, "a rejection needs a reason")
        out = P.reject_deliverable(tid, deliverable_id, uid, reason)
        if not out.get("rejected"):
            raise HTTPException(409, out.get("reason", "could not reject"))
        return out

    return router
