# Hermes WhatsApp bridge - the Part 4 session patch

The bridge is a Node process inside each Hermes agent container, living on that
container's data volume at `/opt/data/scripts/whatsapp-bridge/bridge.js`. It is
not part of this repository's build; the copies here are a record of what is
running, so the change can be reviewed, re-applied, and rolled back.

- `bridge.before-p4.js` - the clinic agent's file as found on 13 Sep 2026.
- `bridge.js` - the same file with the Part 4 session patch applied.

## What the patch changes and why

1. **The pairing code is kept, not discarded.** Baileys hands a QR string to
   `connection.update` on every tick while the socket is unpaired. It was drawn
   to stdout with `qrcode-terminal` and then lost, which is the whole reason
   pairing a number required a person with shell access to the box. The string
   is now held in memory with the moment it arrived.

2. **`GET /session`** reports the socket's real state, the held QR, and its age.
   WhatsApp rotates the code about every 20 seconds, so the age matters: the
   response marks anything over 60s stale rather than serving a code that will
   not scan.

3. **`POST /session/logout`** ends the session deliberately.

   Both endpoints require `x-internal-key` to match `HERMES_INTERNAL_KEY` when
   that variable is set. Anything else on the Docker network could otherwise
   read a client's pairing code or log their number out.

4. **A logged-out number no longer kills the process.** The old code called
   `process.exit(1)`, and the gateway restarted it five minutes later only to
   have it exit again. The clinic and salon numbers sat in that loop from July
   until 13 September. The bridge now clears the dead credentials and reconnects,
   which produces a fresh QR instead of a corpse.

## Verified on 13 Sep 2026

Run standalone in the clinic container against a scratch session directory:

    node bridge.js --port 3009 --session /opt/data/whatsapp/p4-pairtest
    curl -s http://127.0.0.1:3009/session

    {"state": "awaiting_scan", "connected": false, "qr_age_seconds": 11,
     "qr_stale": false, ...}   with a 237-character QR string

## Still open

The Hermes gateway tears the bridge down about 13 seconds after starting it when
WhatsApp is not already paired, so the gateway-owned bridge is not somewhere a
client can be sent to scan. The self-service flow therefore runs its own
pairing process for the duration of the scan and hands the credentials back.
That is the next piece of work, not something this patch settles.
