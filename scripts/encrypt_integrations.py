"""One-off migration: encrypt the OAuth tokens already sitting in the database.

Existing rows in `integrations.credentials` are plaintext. New connections are
encrypted automatically once a key is set; this converts the rows that are already
there. It is deliberately cautious, because these are live client tokens — losing
them means every client has to reconnect their accounts.

    export CREDENTIAL_ENCRYPTION_KEY="$(openssl rand -base64 32)"   # KEEP THIS SAFE
    export DATABASE_URL_ADMIN='postgresql://postgres@localhost:5432/techdeva'

    python3 scripts/encrypt_integrations.py            # dry run — changes nothing
    python3 scripts/encrypt_integrations.py --apply    # writes

Safety properties:
  * dry run by default; --apply is required to write anything
  * every row is verified by DECRYPTING IT BACK before the transaction commits;
    if a single row fails to round-trip, the whole thing rolls back
  * already-encrypted rows are skipped, so re-running is harmless
  * nothing is deleted, and no other column is touched
  * a JSON backup of the current values is written first, unless --no-backup

If you lose the key, the tokens are gone. Store it in your secret manager BEFORE
running this, and keep the backup file until every client has reconnected at
least once.
"""
import argparse
import json
import os
import sys
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.crypto import encrypt_json, decrypt_json, is_encrypted, enabled  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--no-backup", action="store_true", help="skip the plaintext backup file")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL_ADMIN") or os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("set DATABASE_URL_ADMIN (a role that can read/write integrations)")
    if not enabled():
        sys.exit("set CREDENTIAL_ENCRYPTION_KEY first:  openssl rand -base64 32")

    conn = psycopg.connect(dsn, row_factory=dict_row)
    with conn.cursor() as cur:
        # techdeva_admin sees across tenants; RLS still applies to the app role
        cur.execute("SELECT set_config('app.role','techdeva_admin',true)")
        cur.execute("""SELECT id, tenant_id, provider, credentials
                       FROM integrations ORDER BY tenant_id, provider""")
        rows = cur.fetchall()

    todo = [r for r in rows if r["credentials"] and not is_encrypted(r["credentials"])]
    done = [r for r in rows if is_encrypted(r["credentials"])]
    print(f"{len(rows)} integration rows: {len(todo)} plaintext, {len(done)} already encrypted")
    if not todo:
        print("nothing to do.")
        return

    for r in todo:
        keys = list((r["credentials"] or {}).keys())
        print(f"  {r['provider']:<10} tenant {str(r['tenant_id'])[:8]}…  fields: {keys}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply when you are ready.")
        return

    if not args.no_backup:
        path = f"integrations_plaintext_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump([{**r, "id": str(r["id"]), "tenant_id": str(r["tenant_id"])}
                       for r in todo], fh, indent=2, default=str)
        os.chmod(path, 0o600)
        print(f"\nplaintext backup written to {path} (mode 600) — delete it once verified")

    converted = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.role','techdeva_admin',true)")
            for r in todo:
                env = encrypt_json(r["credentials"], r["tenant_id"], r["provider"])
                # verify BEFORE committing: a token we cannot read back is a lost token
                back = decrypt_json(env, r["tenant_id"], r["provider"])
                if back != r["credentials"]:
                    raise RuntimeError(f"round-trip mismatch on {r['provider']} "
                                       f"for tenant {r['tenant_id']} — rolling back")
                cur.execute("UPDATE integrations SET credentials=%s WHERE id=%s",
                            (Json(env), r["id"]))
                converted += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        sys.exit(f"FAILED, nothing was changed: {e}")

    print(f"\nencrypted {converted} rows, each verified by decrypting it back.")
    print("Now confirm the app still reads them (GET /integrations) before deleting the backup.")


if __name__ == "__main__":
    main()
