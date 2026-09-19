"""Authentication for the client portal — real, fail-closed, zero new dependencies.

Two jobs:

1. **Passwords.** PBKDF2-HMAC-SHA256 with a per-user random salt, stored as
   `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`. Verification is
   constant-time. Nothing is ever stored, logged, or returned in plaintext.

2. **Sessions.** A compact HMAC-SHA256 signed token: `<payload_b64>.<sig_b64>`.
   The payload carries tenant_id, user_id, role and an expiry. It is *signed*,
   not encrypted — it holds no secret, and any tampering invalidates it.

Why stdlib and not PyJWT/passlib/bcrypt: this must run inside the existing
`python:3.12-slim` image with the pinned requirements. Adding a native-build
dependency to ship a login screen is how a deploy breaks on the VPS at the worst
moment. `hashlib.pbkdf2_hmac` is C-backed and entirely sufficient here.

--------------------------------------------------------------------------
THE SECURITY CHANGE THAT MATTERS
--------------------------------------------------------------------------
Until now every route trusted the `X-Tenant-Id` / `X-Role` headers the caller
sent. That is fine on a closed VPS with the API unexposed. It is *not* fine the
moment techdeva.net has a public signup form, because anyone could send
`X-Role: techdeva_admin` and read the cross-tenant overview.

`AUTH_REQUIRED=true` closes that: headers are ignored entirely and identity comes
only from a signed token this server issued. RLS still enforces isolation
underneath, as it always did — this stops the caller from *choosing* which tenant
RLS scopes them to.

Set `AUTH_REQUIRED=true` and a strong `SESSION_SECRET` before the API is
reachable from the internet. With AUTH_REQUIRED=true and no SESSION_SECRET the
module refuses to issue or accept tokens rather than falling back to a guessable
default — fail closed, never fake.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

# ---------------------------------------------------------------- configuration
PBKDF2_ITERATIONS = int(os.environ.get("PBKDF2_ITERATIONS", "240000"))
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(12 * 3600)))
SESSION_COOKIE = "td_session"

# A dev-only ephemeral secret so `docker compose up` works out of the box. It is
# regenerated every process start, so tokens die on restart — visibly, honestly,
# rather than silently sharing a hard-coded key across every deployment.
_EPHEMERAL_SECRET = secrets.token_urlsafe(48)


class AuthError(Exception):
    """Raised on any failed authentication. The API layer maps this to 401/403.

    Deliberately not a FastAPI HTTPException so this module (and its tests) can
    run with no web framework installed.
    """

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.message = message
        self.status = status


def auth_required() -> bool:
    return os.environ.get("AUTH_REQUIRED", "true").strip().lower() not in {"0", "false", "no"}


def _secret() -> bytes:
    s = os.environ.get("SESSION_SECRET", "").strip()
    if s:
        return s.encode()
    if auth_required():
        # fail closed: enforcing auth with an unset secret would mean every
        # restart silently invalidates sessions, or worse, a shared default key.
        raise AuthError("SESSION_SECRET is not set but AUTH_REQUIRED=true", 500)
    return _EPHEMERAL_SECRET.encode()


# ---------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    """Return a self-describing PBKDF2 hash. Never store or log the password."""
    if not password or len(password) < 8:
        raise AuthError("password must be at least 8 characters", 400)
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time verify. A NULL/blank stored hash is always False —
    a user provisioned before this migration cannot log in, by design."""
    if not stored or not password:
        return False
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 base64.b64decode(salt_b64), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, base64.b64decode(hash_b64))


# ------------------------------------------------------------------------ tokens
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(*, tenant_id: str | None, user_id: str, role: str,
                ttl: int | None = None) -> str:
    """Sign a session token. `tenant_id` is None only for techdeva_admin."""
    claims = {
        "t": str(tenant_id) if tenant_id else None,
        "u": str(user_id),
        "r": role,
        "exp": int(time.time()) + int(ttl or SESSION_TTL_SECONDS),
    }
    payload = _b64u(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64u(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str) -> dict:
    """Return the claims, or raise AuthError. Rejects tampering and expiry."""
    if not token or "." not in token:
        raise AuthError("malformed token")
    payload, _, sig = token.partition(".")
    expected = _b64u(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise AuthError("bad signature")
    try:
        claims = json.loads(_unb64u(payload))
    except (ValueError, TypeError):
        raise AuthError("malformed token")
    if int(claims.get("exp", 0)) < time.time():
        raise AuthError("session expired")
    if claims.get("r") not in {"techdeva_admin", "owner", "operator"}:
        raise AuthError("bad role in token")
    return claims


def token_from(authorization: str | None, cookie: str | None) -> str | None:
    """Accept either `Authorization: Bearer <t>` or the httpOnly session cookie."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return cookie or None


# --------------------------------------------------------------- scope resolution
def resolve_scope(x_tenant_id: str | None, x_role: str | None,
                  authorization: str | None = None,
                  cookie: str | None = None) -> tuple[str | None, str, str | None]:
    """The single place identity is decided. Returns (tenant_id, role, user_id).

    AUTH_REQUIRED=true  -> ONLY a signed token is accepted; headers are ignored.
    AUTH_REQUIRED=false -> a valid token still wins; otherwise fall back to the
                           X-Tenant-Id / X-Role dev stub the platform shipped with,
                           so local development and the existing recipes keep working.
    """
    token = token_from(authorization, cookie)
    if token:
        claims = verify_token(token)
        return claims["t"], claims["r"], claims["u"]

    if auth_required():
        raise AuthError("authentication required")

    role = x_role or "owner"
    if role not in {"techdeva_admin", "owner", "operator"}:
        raise AuthError("bad role", 400)
    if role != "techdeva_admin" and not x_tenant_id:
        raise AuthError("tenant required", 400)
    return (None if role == "techdeva_admin" else x_tenant_id), role, None


# --- OAuth state -------------------------------------------------------------
# The provider hands this back to us on the callback. It must be signed, or a
# caller can choose which tenant an incoming connection is attributed to.
OAUTH_STATE_TTL = 600


def issue_oauth_state(tenant_id: str, provider: str) -> str:
    exp = str(int(time.time()) + OAUTH_STATE_TTL)
    payload = "%s:%s:%s" % (tenant_id, provider, exp)
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    return _b64u(payload.encode()) + "." + _b64u(sig)


def verify_oauth_state(state: str, provider: str) -> str:
    try:
        body, sig = state.split(".", 1)
        payload = _unb64u(body).decode()
        tenant_id, prov, exp = payload.split(":")
    except Exception:
        raise AuthError("bad oauth state", 400)
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _unb64u(sig)):
        raise AuthError("bad oauth state", 400)
    if prov != provider:
        raise AuthError("oauth state provider mismatch", 400)
    if int(exp) < int(time.time()):
        raise AuthError("oauth state expired", 400)
    return tenant_id
