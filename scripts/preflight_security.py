"""Go / no-go check before techdeva.net accepts a single public signup.

Run it ON THE SERVER, with the same environment the API runs with:

    set -a; . /srv/techdeva/.env; set +a
    python3 scripts/preflight_security.py

Every FAIL is something that exposes real client data the moment signup is
public. Exit code is non-zero if any check fails, so it can gate a deploy.

What it cannot check: whether your provider-level (Hostinger) firewall is
actually on. Nothing running on the box can tell you that reliably — a UFW rule
looks fine while Docker publishes the port straight past it. Verify it from
somewhere else entirely (see SECURITY.md), then run this.
"""
import os
import socket
import subprocess
import sys
import time

PASS, FAIL, WARN = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m", "\033[93mWARN\033[0m"
failures, warnings = [], []


def result(ok, name, detail="", fix="", warn_only=False):
    tag = PASS if ok else (WARN if warn_only else FAIL)
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        (warnings if warn_only else failures).append((name, fix))


def env(k, default=""):
    return os.environ.get(k, default).strip()


def truthy(k):
    return env(k).lower() in {"1", "true", "yes"}


# ---------------------------------------------------------------- application
print("\nAPPLICATION")

result(truthy("AUTH_REQUIRED"), "AUTH_REQUIRED=true",
       f"currently {env('AUTH_REQUIRED') or 'unset'}",
       "Set AUTH_REQUIRED=true. Without it, any caller can send "
       "'X-Role: techdeva_admin' and read every client's data.")

secret = env("SESSION_SECRET")
result(len(secret) >= 32, "SESSION_SECRET is set and long enough",
       f"{len(secret)} characters", "openssl rand -base64 48")
if secret:
    result(secret not in {"change-me", "secret", "techdeva", "test-secret-not-for-production"},
           "SESSION_SECRET is not a placeholder", "", "Generate a real one.")

result(env("COOKIE_SECURE", "true").lower() != "false", "COOKIE_SECURE is not disabled",
       "", "Set COOKIE_SECURE=true so the session cookie is HTTPS-only.")

origins = env("ALLOWED_ORIGINS", "*")
result(origins != "*", "ALLOWED_ORIGINS is not a wildcard", f"currently {origins!r}",
       "Set it to your real origins, e.g. https://techdeva.net,https://app.techdeva.net")

# ---------------------------------------------------------------- credentials
print("\nCREDENTIALS AT REST")
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.crypto import enabled as crypto_enabled, HAVE_CRYPTO, is_encrypted
except Exception as e:                                   # pragma: no cover
    print(f"  [{FAIL}] could not import app.crypto — {e}")
    crypto_enabled, HAVE_CRYPTO, is_encrypted = (lambda: False), False, (lambda v: False)
    failures.append(("app.crypto import", "run this from the repository root"))

result(HAVE_CRYPTO, "the 'cryptography' package is installed", "",
       "pip install -r app/requirements.txt")
result(crypto_enabled(), "CREDENTIAL_ENCRYPTION_KEY is configured", "",
       "openssl rand -base64 32 — then run scripts/encrypt_integrations.py")
result(truthy("REQUIRE_CREDENTIAL_ENCRYPTION"), "plaintext credential writes are refused",
       "", "Set REQUIRE_CREDENTIAL_ENCRYPTION=true once the key is in place.",
       warn_only=True)

# ---------------------------------------------------------------- database
print("\nDATABASE")
dsn = env("DATABASE_URL_ADMIN") or env("DATABASE_URL")
if not dsn:
    result(False, "database reachable", "no DATABASE_URL set", "", warn_only=True)
else:
    try:
        import psycopg
        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('app.role','techdeva_admin',true)")
            cur.execute("SELECT credentials FROM integrations")
            rows = [r[0] for r in cur.fetchall()]
            plain = [c for c in rows if c and not is_encrypted(c)]
            result(not plain, "no plaintext credentials remain",
                   f"{len(plain)} of {len(rows)} rows still plaintext",
                   "python3 scripts/encrypt_integrations.py --apply")

            cur.execute("SELECT count(*) FROM users WHERE password_hash IS NULL")
            no_pw = cur.fetchone()[0]
            result(True, f"users without a portal password: {no_pw}",
                   "they simply cannot log in (fail closed)")

            cur.execute("SHOW ssl")
            result(True, f"server ssl = {cur.fetchone()[0]}", "", "", warn_only=True)
    except Exception as e:
        result(False, "database reachable", str(e)[:120], "", warn_only=True)

    # the published default password must not work
    try:
        import psycopg
        host = env("PGHOST") or "localhost"
        db = env("PGDATABASE") or "techdeva"
        psycopg.connect(host=host, port=env("PGPORT") or 5432, dbname=db,
                        user="techdeva_app", password="techdeva",
                        connect_timeout=5).close()
        result(False, "the published default DB password no longer works",
               "connected as techdeva_app with password 'techdeva'",
               "Rotate it: ALTER ROLE techdeva_app PASSWORD '<new>' — see SECURITY.md")
    except Exception:
        result(True, "the published default DB password no longer works")

# ---------------------------------------------------------------- exposure
print("\nNETWORK EXPOSURE  (indicative only — verify from OFF the server)")
try:
    listening = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True, timeout=5).stdout
except Exception:
    listening = ""
for port, name in ((5432, "PostgreSQL"), (6379, "Redis")):
    wide = f"0.0.0.0:{port}" in listening or f"*:{port}" in listening or f":::{port}" in listening
    result(not wide, f"{name} is not bound to all interfaces",
           f"port {port} listening on 0.0.0.0" if wide else "",
           f"Bind {name} to 127.0.0.1 or the docker network, and close {port} at the "
           f"PROVIDER firewall. A UFW rule alone does not stop Docker.",
           warn_only=not wide)

redis_url = env("REDIS_URL", "redis://localhost:6379/0")
if "@" not in redis_url:
    result(False, "Redis requires a password", "REDIS_URL has no credentials",
           "Set requirepass in redis.conf and put it in REDIS_URL.", warn_only=True)
else:
    result(True, "Redis requires a password")

# ---------------------------------------------------------------- backups
print("\nBACKUPS")
bdir = env("BACKUP_DIR", "/var/backups/techdeva")
try:
    dumps = [os.path.join(bdir, f) for f in os.listdir(bdir) if f.endswith(".dump")]
except OSError:
    dumps = []
if not dumps:
    result(False, "a recent backup exists", f"nothing found in {bdir}",
           "Schedule scripts/backup_db.sh — see SECURITY.md")
else:
    newest = max(dumps, key=os.path.getmtime)
    age_h = (time.time() - os.path.getmtime(newest)) / 3600
    result(age_h < 48, "a recent backup exists", f"newest is {age_h:.1f}h old",
           "Check the backup cron is running.")
    result(os.path.exists(newest + ".sha256"), "the backup has a checksum", "",
           "backup_db.sh writes one automatically.")
    print(f"  [{WARN}] restore verified? — this check cannot tell. "
          f"Run scripts/verify_restore.sh")

# ---------------------------------------------------------------- verdict
print("\n" + "=" * 62)
if failures:
    print(f"  NOT READY — {len(failures)} blocking issue(s):\n")
    for name, fix in failures:
        print(f"   • {name}\n     → {fix}")
else:
    print("  No blocking issues found.")
if warnings:
    print(f"\n  {len(warnings)} warning(s):")
    for name, fix in warnings:
        print(f"   • {name}" + (f"\n     → {fix}" if fix else ""))
print("\n  Still not checked by any script: the Hostinger provider firewall.")
print("  Verify it from another machine before opening signup.")
print("=" * 62)
sys.exit(1 if failures else 0)
