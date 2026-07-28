"""OAuth flow for client account connections (Meta / Google).

Each client clicks "Connect Facebook" or "Connect Google" → redirected to the
provider's auth screen → grants permission → redirected back to our callback →
token stored in `integrations` (RLS-scoped per tenant).

Config (env):
  META_APP_ID, META_APP_SECRET     — from developers.facebook.com
  GOOGLE_CLIENT_ID, GOOGLE_SECRET  — from Google Cloud Console
  OAUTH_REDIRECT_BASE              — e.g. https://api.techdeva.net (public URL of this API)

The client never shares their password with TechDeva. Tokens are stored per-tenant
and isolated by Row-Level Security — one client's credentials can never leak to another.
"""
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ---------- config ----------
META_APP_ID = os.environ.get("META_APP_ID", "")
META_APP_SECRET = os.environ.get("META_APP_SECRET", "")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_SECRET = os.environ.get("GOOGLE_SECRET", "")
OAUTH_REDIRECT_BASE = os.environ.get("OAUTH_REDIRECT_BASE", "http://localhost:8000")

# Meta permissions needed for ads + publishing + insights
META_SCOPES = "ads_management,pages_manage_posts,pages_read_engagement,instagram_basic,business_management"
# Google scopes for Ads API + Business Profile
GOOGLE_SCOPES = "https://www.googleapis.com/auth/adwords https://www.googleapis.com/auth/business.manage"


# ---------- Meta OAuth ----------
def meta_auth_url(tenant_id: str) -> str:
    """URL to redirect the client to for Facebook login + permission grant."""
    params = urllib.parse.urlencode({
        "client_id": META_APP_ID,
        "redirect_uri": f"{OAUTH_REDIRECT_BASE}/oauth/meta/callback",
        "scope": META_SCOPES,
        "response_type": "code",
        "state": tenant_id,   # carry tenant_id through the redirect so we know who connected
    })
    return f"https://www.facebook.com/v21.0/dialog/oauth?{params}"


def meta_exchange_code(code: str) -> dict:
    """Exchange the auth code for a long-lived token."""
    # step 1: code → short-lived token
    params = urllib.parse.urlencode({
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "redirect_uri": f"{OAUTH_REDIRECT_BASE}/oauth/meta/callback",
        "code": code,
    })
    url = f"https://graph.facebook.com/v21.0/oauth/access_token?{params}"
    with urllib.request.urlopen(url) as r:
        short = json.loads(r.read())

    # step 2: short-lived → long-lived token (~60 days)
    params2 = urllib.parse.urlencode({
        "grant_type": "fb_exchange_token",
        "client_id": META_APP_ID,
        "client_secret": META_APP_SECRET,
        "fb_exchange_token": short["access_token"],
    })
    url2 = f"https://graph.facebook.com/v21.0/oauth/access_token?{params2}"
    with urllib.request.urlopen(url2) as r:
        long = json.loads(r.read())

    # fetch the ad accounts this token has access to
    ad_accounts = []
    try:
        with urllib.request.urlopen(
            f"https://graph.facebook.com/v21.0/me/adaccounts?"
            f"fields=name,account_id&access_token={long['access_token']}"
        ) as r:
            ad_accounts = json.loads(r.read()).get("data", [])
    except Exception:
        pass  # non-fatal; the client may not have an ad account yet

    return {
        "access_token": long["access_token"],
        "token_type": "long_lived",
        "expires_in": long.get("expires_in"),
        "ad_accounts": ad_accounts,
    }


# ---------- Google OAuth ----------
def google_auth_url(tenant_id: str) -> str:
    params = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{OAUTH_REDIRECT_BASE}/oauth/google/callback",
        "scope": GOOGLE_SCOPES,
        "response_type": "code",
        "access_type": "offline",        # get a refresh_token
        "prompt": "consent",
        "state": tenant_id,
    })
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


def google_exchange_code(code: str) -> dict:
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_SECRET,
        "redirect_uri": f"{OAUTH_REDIRECT_BASE}/oauth/google/callback",
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# ---------- store the connection ----------
def save_connection(persist, tenant_id: str, provider: str, token_data: dict,
                    external_account: str = ""):
    """Write the OAuth token into the integrations table for this tenant."""
    from .db import tenant_session
    with tenant_session(tenant_id, "owner") as cur:
        cur.execute("""
            INSERT INTO integrations (tenant_id, provider, status, external_account,
                                       credentials, connected_at)
            VALUES (current_tenant(), %s, 'connected', %s, %s, now())
            ON CONFLICT (tenant_id, provider)
            DO UPDATE SET status='connected', credentials=EXCLUDED.credentials,
                          external_account=EXCLUDED.external_account,
                          connected_at=now(), last_error=NULL
        """, (provider, external_account,
              json.dumps(token_data)))
    # emit an event so the orchestrator (and TechDeva view) knows
    persist.emit(tenant_id, "integration.connected",
                 {"provider": provider, "account": external_account}, "oauth")
