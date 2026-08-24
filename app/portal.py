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
                   issue_token, resolve_scope, verify_password)

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
    def login(body: LoginIn, response: Response):
        key = body.email.strip().lower()
        if _throttled(key):
            raise HTTPException(429, "too many failed attempts — try again later")

        user = P.find_user_by_email(body.email)
        # one generic message for both branches: never reveal whether an account exists
        if not user or not verify_password(body.password, user.get("password_hash")):
            _record_fail(key)
            raise HTTPException(401, "invalid email or password")

        _LOGIN_FAILS.pop(key, None)
        P.touch_login(user["id"])
        token = issue_token(tenant_id=user["tenant_id"], user_id=user["id"],
                            role=user["role"])
        # httpOnly so page JavaScript can never read it; Secure in production (HTTPS).
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                            max_age=SESSION_TTL_SECONDS,
                            secure=os.environ.get("COOKIE_SECURE", "true").lower() == "true")
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

    # ------------------------------------------------------------- trial + report
    @router.get("/portal/summary")
    def summary(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        return P.portal_summary(tid)

    @router.get("/portal/report")
    def report(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        rep = P.latest_report(tid)
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
        cfg = P.load_config(tid) or {}
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

    # ------------------------------------------------------------- deliverables
    @router.get("/portal/deliverables")
    def deliverables(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                     authorization: str | None = Header(None),
                     td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = identity(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        items = P.list_deliverables(tid)
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
