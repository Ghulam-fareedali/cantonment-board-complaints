"""Hermes conformance test.

Run this against your LIVE Hermes bot to confirm it speaks the platform contract
before you point the orchestrator at it. It does NOT need the platform — just the
bot's URL.

Usage:
    HERMES_URL=http://your-bot:8090 python -m app.hermes_conformance
    # optional: HERMES_API_KEY, HERMES_REPLY_PATH

Passes if the bot returns 200 with a JSON body containing a non-empty "reply".
"""
import os
import sys
from .hermes_client import call_hermes, HermesNotConfigured, HermesUnavailable

SAMPLE = {
    "tenant_id": "11111111-1111-1111-1111-111111111111",
    "conversation_id": "conf-test-conv",
    "lead_id": "conf-test-lead",
    "task_type": "qualify",
    "message": "Hi, how much is HydraFacial?",
    "persona": "Zara — warm, doctor-led clinic tone. Hinglish.",
    "context": {"service": "HydraFacial", "price": 12000},
}


def main() -> int:
    print("→ POSTing sample qualify to Hermes:", os.environ.get("HERMES_URL", "(unset)"))
    try:
        out = call_hermes(SAMPLE)
    except HermesNotConfigured:
        print("✗ HERMES_URL is not set. Export it and retry.")
        return 2
    except HermesUnavailable as e:
        print(f"✗ Hermes unreachable / errored: {e}")
        return 1

    reply = (out or {}).get("reply", "")
    if isinstance(reply, str) and reply.strip():
        print("✓ PASS — Hermes replied:", reply[:160])
        print("  meta:", out.get("meta", {}))
        print("\nYour bot speaks the contract. Set HERMES_URL in the platform .env to go live.")
        return 0
    print("✗ FAIL — response had no non-empty 'reply'. Got:", out)
    print("  Expected: {\"reply\": \"<text>\", \"meta\": {...}}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
