"""HTTP client to the live Hermes bot (VPS or desktop).

The platform stays decoupled from Hermes' internals: it POSTs a small JSON
envelope and expects a reply back. Hermes only has to expose ONE endpoint.

Contract
--------
Request  POST {HERMES_URL}{HERMES_REPLY_PATH}   (default path /reply)
  {
    "tenant_id": "...", "conversation_id": "...", "lead_id": "...",
    "task_type": "qualify|followup|book",
    "message": "<incoming customer message, if any>",
    "persona": "<tenant hermes_persona>",
    "context": { "service": "...", "price": 12000, ... }
  }
Response  200 JSON
  { "reply": "<text Hermes would send>", "meta": { ... optional ... } }
  (a bare {"text": "..."} is also accepted and normalized)

Config (env)
  HERMES_URL          base url, e.g. http://127.0.0.1:8090   (unset => dev stub)
  HERMES_REPLY_PATH   default "/reply"
  HERMES_API_KEY      optional; sent as "Authorization: Bearer <key>"
  HERMES_TIMEOUT      seconds, default 8
  HERMES_RETRIES      transient retries, default 2

Uses only the Python standard library (no extra dependency).
"""
import os
import json
import time
import urllib.request
import urllib.error


class HermesNotConfigured(Exception):
    """HERMES_URL is not set — caller should use the dev stub."""


class HermesUnavailable(Exception):
    """Live Hermes could not be reached / returned an error — park for a human."""


def call_hermes(payload: dict) -> dict:
    base = os.environ.get("HERMES_URL")
    if not base:
        raise HermesNotConfigured()

    url = base.rstrip("/") + os.environ.get("HERMES_REPLY_PATH", "/reply")
    timeout = float(os.environ.get("HERMES_TIMEOUT", "8"))
    retries = int(os.environ.get("HERMES_RETRIES", "2"))

    headers = {"Content-Type": "application/json"}
    key = os.environ.get("HERMES_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(payload).encode()

    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode()
            out = json.loads(body) if body else {}
            if "reply" not in out:                       # normalize {"text": ...}
                out = {"reply": out.get("text", ""), "meta": out}
            out.setdefault("meta", {})
            return out
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:                       # our bug — don't retry
                raise HermesUnavailable(f"hermes {e.code} client error") from e
            last = e                                      # 5xx — retry
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))               # small linear backoff
    raise HermesUnavailable(f"unreachable after {retries + 1} attempts: {last}")
