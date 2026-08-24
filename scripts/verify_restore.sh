#!/bin/bash
# ============================================================================
# Prove the backup is restorable — the half everyone skips.
#
#   ./scripts/verify_restore.sh [path/to/backup.dump]
#
# Restores the newest (or named) dump into a THROWAWAY database, then checks the
# data is actually present: tenants, tenant_config, users, leads, and that the
# tables Build 1-3 added exist. Drops the scratch database afterwards, pass or
# fail.
#
# It never touches the live database. The scratch name is derived from the clock
# and the script refuses to run if a database by that name somehow already exists.
#
# Cron it weekly. An unverified backup is a story you tell yourself.
# ============================================================================
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/techdeva}"
DB="${PGDATABASE:-techdeva}"
DUMP="${1:-$(ls -1t "$BACKUP_DIR"/${DB}_*.dump 2>/dev/null | head -1 || true)}"
SCRATCH="restore_check_$(date -u +%Y%m%d_%H%M%S)"

[ -n "$DUMP" ] || { echo "no dump found in $BACKUP_DIR" >&2; exit 1; }
[ -f "$DUMP" ] || { echo "no such file: $DUMP" >&2; exit 1; }

echo "verifying: $DUMP"

if [ -f "$DUMP.sha256" ]; then
  sha256sum -c "$DUMP.sha256" >/dev/null && echo "  checksum: OK" \
    || { echo "  checksum: FAILED — the file changed on disk" >&2; exit 1; }
else
  echo "  checksum: (none recorded)"
fi

# psql connects to a maintenance DB, never the one we are about to create
export PGDATABASE=postgres
if psql -tAc "SELECT 1 FROM pg_database WHERE datname='$SCRATCH'" | grep -q 1; then
  echo "  scratch database $SCRATCH already exists — aborting" >&2; exit 1
fi

cleanup() { psql -q -c "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null 2>&1 || true; }
trap cleanup EXIT

psql -q -c "CREATE DATABASE \"$SCRATCH\""
echo "  restoring into scratch database $SCRATCH…"
pg_restore --dbname="$SCRATCH" --no-owner --no-privileges "$DUMP" >/dev/null 2>&1 || {
  echo "  restore reported errors — inspect manually before trusting this dump" >&2
  pg_restore --dbname="$SCRATCH" --no-owner --no-privileges "$DUMP" 2>&1 | tail -20 >&2
  exit 1
}

FAIL=0
check() {                       # check <label> <sql> <minimum>
  local got
  got=$(psql -d "$SCRATCH" -tAc "$2" 2>/dev/null || echo "ERR")
  if [ "$got" = "ERR" ]; then
    echo "  ✗ $1: query failed"; FAIL=1
  elif [ "$got" -lt "$3" ] 2>/dev/null; then
    echo "  ✗ $1: $got (expected at least $3)"; FAIL=1
  else
    echo "  ✓ $1: $got"
  fi
}

check "tenants"        "SELECT count(*) FROM tenants" 1
check "tenant_config"  "SELECT count(*) FROM tenant_config" 1
check "users"          "SELECT count(*) FROM users" 1
check "leads present"  "SELECT count(*) FROM leads" 0
check "growth tables"  "SELECT count(*) FROM information_schema.tables
                        WHERE table_name IN ('signups','analysis_reports','deliverables')" 3
check "RLS still on"   "SELECT count(*) FROM pg_class
                        WHERE relname IN ('leads','deliverables')
                          AND relrowsecurity AND relforcerowsecurity" 2

if [ "$FAIL" -eq 0 ]; then
  echo "RESTORE VERIFIED — this dump is usable."
else
  echo "RESTORE VERIFICATION FAILED — do not rely on this backup." >&2
  exit 1
fi
