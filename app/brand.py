"""The client's own logo and colour, held by the platform.

Onboarding used to ask for a "Logo image URL". A salon owner has their logo on
their phone, not on a CDN, so that field stayed empty and every dashboard looked
the same. This takes the file.

The logo is served back from the tenant's own row, so one business can never be
handed another's mark, and it is what the dashboard uses as its background.
"""

import base64
import binascii

from fastapi import APIRouter, Body, Cookie, Header, HTTPException
from fastapi.responses import Response

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

# A logo is a logo. A megabyte is already generous for one, and the browser
# shrinks it before it gets here.
MAX_BYTES = 1_000_000
ALLOWED = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}


def _identity(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role, uid


def _decode(data_url: str):
    """Take a browser data: URL apart, and refuse anything that is not an image."""
    if not data_url or not data_url.startswith("data:"):
        raise HTTPException(400, "Send the image as a data URL from the browser.")
    try:
        header, b64 = data_url.split(",", 1)
        mime = header[5:].split(";", 1)[0].strip().lower()
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(400, "That image could not be read.")
    if mime not in ALLOWED:
        raise HTTPException(400, "Use a PNG, JPEG, WEBP or SVG.")
    if not raw:
        raise HTTPException(400, "That image is empty.")
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "That image is larger than 1 MB. Try a smaller one.")
    return mime, raw


def make_brand_router():
    router = APIRouter(prefix="/brand", tags=["brand"])

    @router.get("/logo")
    def get_logo(x_tenant_id: str | None = Header(None),
                 x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT mime, bytes FROM brand_assets
                            WHERE kind = 'logo' AND tenant_id = %s""", (tid,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, "No logo has been uploaded yet.")
        return Response(content=bytes(row["bytes"]), media_type=row["mime"],
                        headers={"Cache-Control": "private, max-age=300"})

    @router.post("/logo")
    def put_logo(body: dict = Body(...),
                 x_tenant_id: str | None = Header(None),
                 x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The owner sets how their own business is presented."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can change the logo.")
        mime, raw = _decode(body.get("image") or "")
        with tenant_session(tid, role) as cur:
            cur.execute("""INSERT INTO brand_assets (tenant_id, kind, mime, bytes, byte_size)
                           VALUES (%s, 'logo', %s, %s, %s)
                           ON CONFLICT (tenant_id, kind)
                           DO UPDATE SET mime = EXCLUDED.mime,
                                         bytes = EXCLUDED.bytes,
                                         byte_size = EXCLUDED.byte_size,
                                         updated_at = now()""",
                        (tid, mime, raw, len(raw)))
            record(cur, action="brand.logo", resource="brand_assets", resource_id=str(tid),
                   actor=str(uid), actor_type="human", status="ok",
                   detail={"mime": mime, "bytes": len(raw)}, error=None)
        return {"ok": True, "mime": mime, "bytes": len(raw)}

    @router.delete("/logo")
    def remove_logo(x_tenant_id: str | None = Header(None),
                    x_role: str | None = Header(None),
                    authorization: str | None = Header(None),
                    td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can change the logo.")
        with tenant_session(tid, role) as cur:
            cur.execute("DELETE FROM brand_assets WHERE kind='logo' AND tenant_id=%s", (tid,))
        return {"ok": True}

    @router.get("")
    def brand(x_tenant_id: str | None = Header(None),
              x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What the dashboard needs to dress itself: does a logo exist, and the
        accent colour the client chose during onboarding."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT byte_size, updated_at FROM brand_assets
                            WHERE kind='logo' AND tenant_id=%s""", (tid,))
            asset = cur.fetchone()
            # tenant_config is a column per topic, not a key/value bag.
            cur.execute("SELECT brand FROM tenant_config WHERE tenant_id = %s", (tid,))
            cfg = cur.fetchone()
        b = (cfg or {}).get("brand") or {}
        if isinstance(b, str):
            b = {}
        return {
            "has_logo": bool(asset),
            "logo_updated_at": asset["updated_at"] if asset else None,
            "name": b.get("name"),
            "tagline": b.get("tagline"),
            "website": b.get("website"),
            "accent": (b.get("colors") or {}).get("accent"),
            "logo_url": b.get("logo") or None,
        }

    return router
