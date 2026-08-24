#!/bin/bash
# ============================================================================
# Nightly backup. Dump, checksum, prune, and report — nothing clever.
#
#   ./scripts/backup_db.sh
#
# Environment:
#   PGHOST PGPORT PGUSER PGDATABASE   standard libpq vars
#   PGPASSWORD                        password (NEVER put it in a DSN)
#   BACKUP_DIR                        default /var/backups/techdeva
#   BACKUP_KEEP_DAYS                  default 14
#
# Cron (as a user that can read PGPASSWORD from a 600 env file):
#   15 2 * * *  . /etc/techdeva/backup.env && /srv/techdeva/scripts/backup_db.sh \
#               >> /var/log/techdeva-backup.log 2>&1
#
# A backup that has never been restored is not a backup. scripts/verify_restore.sh
# loads the newest dump into a scratch database and checks the data is really
# there — run it on a schedule too, not just once.
# ============================================================================
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/techdeva}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
DB="${PGDATABASE:-techdeva}"
STAMP="$(date -u +%Y%m%d_%H%M%SZ)"
OUT="$BACKUP_DIR/${DB}_${STAMP}.dump"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "[$(date -uIs)] backing up $DB -> $OUT"

# custom format: compressed, and restorable selectively with pg_restore
pg_dump --format=custom --compress=9 --no-owner --file="$OUT" "$DB"
chmod 600 "$OUT"

sha256sum "$OUT" > "$OUT.sha256"
SIZE=$(stat -c %s "$OUT")

# A dump far smaller than the last one usually means it failed while "succeeding"
PREV=$(ls -1t "$BACKUP_DIR"/${DB}_*.dump 2>/dev/null | sed -n 2p || true)
if [ -n "${PREV:-}" ]; then
  PREV_SIZE=$(stat -c %s "$PREV")
  if [ "$SIZE" -lt $((PREV_SIZE / 2)) ]; then
    echo "[$(date -uIs)] WARNING: this dump ($SIZE bytes) is less than half the previous" \
         "($PREV_SIZE bytes). Investigate before trusting it." >&2
  fi
fi

# verify the archive is readable at all (catches truncation immediately)
pg_restore --list "$OUT" > /dev/null
echo "[$(date -uIs)] ok: $SIZE bytes, archive readable, sha256 recorded"

DELETED=$(find "$BACKUP_DIR" -name "${DB}_*.dump*" -mtime "+$KEEP_DAYS" -print -delete | wc -l)
echo "[$(date -uIs)] pruned $DELETED file(s) older than $KEEP_DAYS days"
echo "[$(date -uIs)] backups on disk: $(ls -1 "$BACKUP_DIR"/${DB}_*.dump 2>/dev/null | wc -l)"
