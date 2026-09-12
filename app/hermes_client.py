"""Client for the Hermes agent gateway.

HISTORY / WHY THIS FILE CHANGED (12 Sep 2026)
---------------------------------------------
This module used to POST a TechDeva-shaped body to a single configurable
path (HERMES_REPLY_PATH, default "/reply") and expect {"reply": "..."} back.

No such endpoint has ever existed. The Hermes agent is nousresearch/hermes-agent
and its gateway (/opt/hermes/gateway/platforms/api_server.py) exposes a
session-based API instead. Verified on the live container 12 Sep 2026:

    GET  /health                         -> 200 {"status":"ok",...}
    GET  /  /reply  /api/reply /webhook  -> 404
    POST /api/sessions                   -> 201 | 409 (session exists)
    POST /api/sessions/{id}/chat         -> 200 one synchronous agent turn

So the platform now speaks the gateway's native protocol. The public surface
of this module is unchanged: call_hermes(payload) -> {"reply": str, "meta": dict}.

Mapping, so tenant isolation is structural rather than incidental:

    session id  = "td-<tenant_id>-<conversation_id>", sanitised to [A-Za-z0-9._-]
                  and capped at 64 chars (the gateway's own limit). Two tenants
                  can never collide on a session even if they reuse a
                  conversation_id, because tenant_id is inside the key.
    system      = persona + tenant business context, sent on EVERY turn so that
                  a changed Business Brain takes effect immediately rather than
                  being frozen at session-creation time.
    message     = payload["message"]
    reply       = response["message"]["content"]

Set HERMES_PROTOCOL=reply to fall back to the legacy single-POST behaviour
without editing code (it will 404 until some bot implements that contract).
"""

import json
import os
import re
import time
import urllib.error
import urllib.request


class HermesNotConfigured(Exception):
    """HERMES_URL is not set - caller should use the dev stub."""


class HermesUnavailable(Exception):
    """Live Hermes could not be reached / returned an error - park for a human."""


_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _session_id(payload: dict) -> str:
    tenant = str(payload.get("tenant_id") or "notenant")
    conv = str(payload.get("conversation_id") or payload.get("lead_id") or "noconv")
    raw = "td-{0}-{1}".format(_SAFE.sub("", tenant)[:24], _SAFE.sub("", conv))
    return raw[:64]


def _system_prompt(payload: dict) -> str:
    """Persona + tenant business context. This is the Business Brain injection point."""
    parts = []
    persona = payload.get("persona")
    if persona:
        parts.append(str(persona))
    ctx = payload.get("context")
    if isinstance(ctx, dict) and ctx:
        parts.append("Business context (authoritative - never invent values):")
        for k, v in ctx.items():
            parts.append("- {0}: {1}".format(k, v))
    elif ctx:
        parts.append(str(ctx))
    task_type = payload.get("task_type")
    if task_type:
        parts.append("Task type: {0}".format(task_type))
    return "\n".join(parts)


def _request(url: str, body: dict, headers: dict, timeout: float):
    """Returns (status, parsed_json_or_None). Raises urllib errors to the caller."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode()
        try:
            return resp.status, (json.loads(raw) if raw else {})
        except ValueError:
            raise HermesUnavailable(
                "Hermes returned non-JSON from {0}: {1!r}".format(url, raw[:200])
            )


def _post_with_retries(url, body, headers, timeout, retries, ok_status=(200, 201)):
    """POST with backoff. 5xx and network faults retry; 4xx is our bug, so it does not."""
    last = None
    for attempt in range(retries + 1):
        try:
            status, out = _request(url, body, headers, timeout)
            if status in ok_status or 200 <= status < 300:
                return status, out
            last = "unexpected status {0}".format(status)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:200]
            except Exception:
                pass
            if 400 <= e.code < 500:
                raise HermesUnavailable(
                    "Hermes {0} at {1}: {2}".format(e.code, url, detail)
                )
            last = "HTTP {0}: {1}".format(e.code, detail)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = "{0}: {1}".format(type(e).__name__, e)
        if attempt < retries:
            time.sleep(0.5 * (2 ** attempt))
    raise HermesUnavailable(
        "unreachable after {0} attempts at {1}: {2}".format(retries + 1, url, last)
    )


def _ensure_session(base, sid, system, headers, timeout, retries):
    """Create the session. 409 means it already exists, which is the normal path
    for every turn after the first - not an error."""
    body = {"id": sid, "title": sid}
    if system:
        body["system_prompt"] = system
    try:
        _post_with_retries(
            base + "/api/sessions", body, headers, timeout, retries, ok_status=(201,)
        )
    except HermesUnavailable as e:
        if "409" in str(e):
            return
        raise


def _legacy_call(payload, base, headers, timeout, retries):
    """Pre-12-Sep single-POST contract. Kept behind HERMES_PROTOCOL=reply so the
    old behaviour is one env var away, not one git revert away."""
    url = base + os.environ.get("HERMES_REPLY_PATH", "/reply")
    _status, out = _post_with_retries(url, payload, headers, timeout, retries)
    out = out or {}
    if "reply" not in out:
        out = {"reply": out.get("text", ""), "meta": out}
    out.setdefault("meta", {})
    return out


def call_hermes(payload: dict) -> dict:
    """Send one turn to Hermes and return {"reply": str, "meta": dict}.

    Never returns an empty reply dressed up as success - if Hermes produces no
    assistant content, this raises HermesUnavailable so the orchestrator parks
    the task for a human instead of recording a silent blank.
    """
    base = os.environ.get("HERMES_URL")
    if not base:
        raise HermesNotConfigured()
    base = base.rstrip("/")

    headers = {"Content-Type": "application/json"}
    key = os.environ.get("HERMES_API_KEY")
    if key:
        headers["Authorization"] = "Bearer {0}".format(key)

    timeout = float(os.environ.get("HERMES_TIMEOUT", "8"))
    chat_timeout = float(os.environ.get("HERMES_CHAT_TIMEOUT", "90"))
    retries = int(os.environ.get("HERMES_RETRIES", "2"))

    if os.environ.get("HERMES_PROTOCOL", "session").strip().lower() == "reply":
        return _legacy_call(payload, base, headers, timeout, retries)

    message = payload.get("message") or ""
    if not str(message).strip():
        raise HermesUnavailable("refusing to call Hermes with an empty message")

    sid = _session_id(payload)
    system = _system_prompt(payload)

    _ensure_session(base, sid, system, headers, timeout, retries)

    body = {"message": message}
    if system:
        body["system_message"] = system

    _status, out = _post_with_retries(
        base + "/api/sessions/{0}/chat".format(sid), body, headers, chat_timeout, retries
    )
    out = out or {}
    msg = out.get("message")
    reply = msg.get("content") if isinstance(msg, dict) else None

    if not isinstance(reply, str) or not reply.strip():
        raise HermesUnavailable(
            "Hermes returned no assistant content for session {0} (response keys: {1})".format(
                sid, sorted(out.keys())
            )
        )

    return {
        "reply": reply,
        "meta": {
            "session_id": out.get("session_id") or sid,
            "object": out.get("object"),
            "usage": out.get("usage"),
            "protocol": "session",
        },
    }
