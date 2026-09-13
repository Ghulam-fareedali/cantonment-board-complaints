#!/usr/bin/env bash
# Record or clear a platform incident. Called by /opt/techdeva-monitor.sh.
#
#   incident.sh open  <kind> <severity> <title> [detail]
#   incident.sh clear <kind>
#
# Opening the same kind twice bumps the count and the timestamp rather than
# making a second row: a check that fails every five minutes all day is one
# problem, not 288 of them.
set -euo pipefail
cd /opt/techdeva-platform/techdeva-platform

q() { printf "%s" "${1//\'/\'\'}"; }

case "${1:-}" in
  open)
    kind="$(q "${2:?kind}")"; sev="$(q "${3:?severity}")"
    title="$(q "${4:?title}")"; detail="$(q "${5:-}")"
    docker compose exec -T db psql -U postgres -d techdeva -q -c "
      INSERT INTO incidents (tenant_id, kind, severity, title, detail)
      VALUES (NULL, '$kind', '$sev', '$title', NULLIF('$detail',''))
      ON CONFLICT (kind) WHERE status = 'open' AND tenant_id IS NULL
      DO UPDATE SET last_seen_at = now(),
                    seen_count = incidents.seen_count + 1,
                    severity = EXCLUDED.severity,
                    detail = EXCLUDED.detail;" >/dev/null
    ;;
  clear)
    kind="$(q "${2:?kind}")"
    docker compose exec -T db psql -U postgres -d techdeva -q -c "
      UPDATE incidents SET status = 'resolved', resolved_at = now()
       WHERE status = 'open' AND tenant_id IS NULL AND kind = '$kind';" >/dev/null
    ;;
  *)
    echo "usage: incident.sh open <kind> <severity> <title> [detail] | clear <kind>" >&2
    exit 2
    ;;
esac
