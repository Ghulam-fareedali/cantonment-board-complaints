"""WhatsApp self-service: the client pairs their own number.

Until Part 4 a number was paired by a person with shell access to this server,
watching a QR code drawn in a terminal. That does not scale past one client and
it is not something to sell.

Three rules shape this module:

* The pairing control URL is read from the tenant's own row and never accepted
  from the caller. Which agent a tenant lives on is a TechDeva decision; a
  request body that could name it would be a way into another tenant's number.
* Nothing here reports a connection it cannot prove. A number counts as
  connected when the bridge hands back the number it actually paired.
* The QR is passed through, never cached. WhatsApp rotates it about every
  twenty seconds, and a stale code is worse than none: it fails silently in the
  client's hand and they blame the product.
"""

import json
import os
import urllib.error
import urllib.request

import io

import qrcode
import qrcode.image.svg

from fastapi import APIRouter, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

# Read at call time, not import time, so rotating the key means restarting the
# API rather than rebuilding it.
def _key():
    return os.environ.get("HERMES_INTERNAL_KEY", "")

PAIRCTL_TIMEOUT = 8


def _svg(data):
    """Draw the pairing code as an SVG.

    Server side on purpose. The browser gets a picture it can show and nothing
    it has to interpret, and the dashboard needs no third-party script loaded
    over the client's shoulder to display their own WhatsApp code.
    """
    if not data:
        return None
    q = qrcode.QRCode(box_size=8, border=2)
    q.add_data(data)
    q.make(fit=True)
    img = q.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def _identity(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role, uid


def _row(cur, tenant_id):
    cur.execute("""SELECT id, agent_key, pairctl_url, status, phone_number,
                          display_name, connected_at, disconnected_at, last_error
                     FROM whatsapp_connections WHERE tenant_id = %s""", (tenant_id,))
    r = cur.fetchone()
    if not r:
        raise HTTPException(409, "No WhatsApp agent has been assigned to this business yet. "
                                 "TechDeva assigns one during onboarding.")
    return r


def _public(r):
    """What the client is allowed to see. The pairctl URL is not in this."""
    return {
        "status": r["status"],
        "connected": r["status"] == "connected",
        "phone_number": r.get("phone_number"),
        "display_name": r.get("display_name"),
        "connected_at": r.get("connected_at"),
        "disconnected_at": r.get("disconnected_at"),
        "last_error": r.get("last_error"),
    }


def _pairctl(url, path, method="GET"):
    key = _key()
    if not key:
        raise HTTPException(503, "Pairing is not configured on this server.")
    req = urllib.request.Request(
        url.rstrip("/") + path,
        method=method,
        headers={"x-internal-key": key},
        data=b"" if method == "POST" else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=PAIRCTL_TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise HTTPException(502, "The pairing service refused the request (%s)." % e.code)
    except Exception:
        # The agent may simply be down. Say so plainly rather than 500.
        raise HTTPException(503, "The pairing service for this business is not reachable.")


def make_whatsapp_router():
    router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

    @router.get("")
    def status(x_tenant_id: str | None = Header(None),
               x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            return _public(_row(cur, tid))

    @router.post("/connect")
    def connect(x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Open a pairing window. Owners only - this puts an AI in front of the
        business's customers, which is not an operator's decision to make."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can connect a WhatsApp number.")
        with tenant_session(tid, role) as cur:
            r = _row(cur, tid)
            _pairctl(r["pairctl_url"], "/pair/start", "POST")
            cur.execute("""UPDATE whatsapp_connections
                              SET status = 'pairing', last_error = NULL, updated_at = now()
                            WHERE tenant_id = %s""", (tid,))
            record(cur, action="whatsapp.connect", resource="whatsapp_connections",
                   resource_id=str(r["id"]), actor=str(uid), actor_type="human",
                   status="ok", detail={"agent": r["agent_key"]}, error=None)
        return {"status": "pairing"}

    @router.get("/qr")
    def qr(x_tenant_id: str | None = Header(None),
           x_role: str | None = Header(None),
           authorization: str | None = Header(None),
           td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Poll while the client has their phone out. Returns the live code."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            r = _row(cur, tid)
            s = _pairctl(r["pairctl_url"], "/pair/status")

            state = s.get("state")
            if state == "connected" and s.get("number"):
                cur.execute("""UPDATE whatsapp_connections
                                  SET status = 'connected', phone_number = %s,
                                      display_name = %s, connected_at = now(),
                                      disconnected_at = NULL, last_error = NULL,
                                      updated_at = now()
                                WHERE tenant_id = %s""",
                            (s.get("number"), s.get("name"), tid))
                record(cur, action="whatsapp.paired", resource="whatsapp_connections",
                       resource_id=str(r["id"]), actor=str(uid), actor_type="human",
                       status="ok", detail={"number": s.get("number")}, error=None)
                return {"state": "connected", "qr": None, "phone_number": s.get("number")}

            if s.get("qr"):
                cur.execute("""UPDATE whatsapp_connections
                                  SET last_qr_at = now(), updated_at = now()
                                WHERE tenant_id = %s""", (tid,))

        return {
            "state": state or "starting",
            "qr_svg": _svg(s.get("qr")),
            "qr_age_seconds": s.get("qr_age_seconds"),
            "qr_stale": s.get("qr_stale"),
        }

    @router.post("/disconnect")
    def disconnect(x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can disconnect the WhatsApp number.")
        with tenant_session(tid, role) as cur:
            r = _row(cur, tid)
            _pairctl(r["pairctl_url"], "/pair/stop", "POST")
            cur.execute("""UPDATE whatsapp_connections
                              SET status = 'disconnected', disconnected_at = now(),
                                  updated_at = now()
                            WHERE tenant_id = %s""", (tid,))
            record(cur, action="whatsapp.disconnect", resource="whatsapp_connections",
                   resource_id=str(r["id"]), actor=str(uid), actor_type="human",
                   status="ok", detail={"agent": r["agent_key"]}, error=None)
        return {"status": "disconnected"}

    @router.post("/assign")
    def assign(body: dict,
               x_tenant_id: str | None = Header(None),
               x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """TechDeva assigns a tenant to an agent. Admin only, by design: this is
        the mapping the rest of the module refuses to take from a client."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role != "techdeva_admin":
            raise HTTPException(403, "Assigning an agent is a TechDeva action.")
        target = body.get("tenant_id") or tid
        agent_key = (body.get("agent_key") or "").strip()
        pairctl_url = (body.get("pairctl_url") or "").strip()
        if not agent_key or not pairctl_url:
            raise HTTPException(400, "agent_key and pairctl_url are both required")
        with tenant_session(tid, role) as cur:
            cur.execute("""INSERT INTO whatsapp_connections (tenant_id, agent_key, pairctl_url)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (tenant_id) DO UPDATE
                             SET agent_key = EXCLUDED.agent_key,
                                 pairctl_url = EXCLUDED.pairctl_url,
                                 updated_at = now()
                           RETURNING id""", (target, agent_key, pairctl_url))
            new_id = cur.fetchone()["id"]
        return {"ok": True, "id": str(new_id), "tenant_id": str(target), "agent_key": agent_key}

    return router
