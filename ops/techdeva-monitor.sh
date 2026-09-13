#!/bin/bash
# Health monitor. Alerts only on STATE CHANGE (fail->ok, ok->fail), never every run.
set -uo pipefail
STATE=/var/lib/techdeva-monitor/state
ALERT=/opt/techdeva-alert.sh          # pluggable notifier; created separately
P=()   # problems

# 1. platform API through the public route
curl -sf -m 15 https://api.techdeva.net/health | grep -q '"ok":true' || P+=("API /health not ok")

# 2. containers running
for c in techdeva-platform-api-1 techdeva-platform-worker-1 techdeva-platform-db-1 \
         techdeva-platform-redis-1 hermes-workspace-ofsd-hermes-agent-clinic-1; do
  [ "$(docker inspect -f '{{.State.Running}}' $c 2>/dev/null)" = "true" ] || P+=("container down: $c")
done

# 3. the exact failure that ran 22 days undetected, and recurred twice last night
ERRS=$(docker logs --since 15m techdeva-platform-worker-1 2>&1 | grep -ci 'error' || true)
[ "${ERRS:-0}" -gt 5 ] && P+=("worker logging $ERRS errors in 15m")

# 4. redis must be master, not a replica (SLAVEOF hijack signature)
R=$(docker exec techdeva-platform-redis-1 redis-cli info replication 2>/dev/null | grep -o 'role:[a-z]*')
[ "$R" = "role:master" ] || P+=("redis $R (expected master)")

# 5. postgres accepting connections
docker exec techdeva-platform-db-1 sh -c 'pg_isready -U "$POSTGRES_USER" -q' 2>/dev/null || P+=("postgres not ready")

# 6. clinic must be serving
systemctl is-active --quiet beauty-dashboard || P+=("clinic service not active")
curl -sf -m 15 -o /dev/null https://crm.techdeva.net/login || P+=("clinic CRM not responding")

# 7. disk
D=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[ "$D" -ge 85 ] && P+=("disk ${D}% full")

# 8. certificates
for h in api.techdeva.net crm.techdeva.net portal.techdeva.net; do
  E=$(echo | timeout 15 openssl s_client -servername $h -connect $h:443 2>/dev/null \
      | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  [ -n "$E" ] || { P+=("$h: no certificate"); continue; }
  DAYS=$(( ( $(date -d "$E" +%s) - $(date +%s) ) / 86400 ))
  [ "$DAYS" -lt 14 ] && P+=("$h cert expires in ${DAYS}d")
done

# 9. backups must be fresh
NEW=$(find /opt/backups/daily -name 'techdeva_*.dump' -mtime -2 2>/dev/null | wc -l)
[ "$NEW" -eq 0 ] && P+=("no platform backup in 48h")

# 10. the AI workforce must be able to think. This is the check that was
#     missing: the model account ran dry and the agents went quiet for weeks
#     while every other check here kept reporting all clear.
AIH=$( (cd /opt/techdeva-platform/techdeva-platform && \
        docker compose exec -T api python -c \
        "from app.ai import health; h=health(); print(h['status'], h['usable'])" 2>/dev/null) || echo "unreachable 0" )
[ "${AIH%% *}" = "ok" ] || P+=("AI workforce degraded ($AIH)")

# Working but not on first choice is not an emergency and should not wake
# anyone -- it is still worth saying out loud in the product, because "slower
# and a bit dumber than usual" is exactly the kind of thing a client notices
# and nobody ever explains.
AIFIRST=$( (cd /opt/techdeva-platform/techdeva-platform && \
            docker compose exec -T api python -c \
            "from app.ai import health; c=health()['chain']; print(c[0]['state'] if c else 'none', (c[0].get('reason') or '-') if c else '-')" 2>/dev/null) || echo "unknown -" )
INC=/opt/techdeva-platform/techdeva-platform/scripts/incident.sh
if [ -x "$INC" ]; then
  if [ "${AIFIRST%% *}" = "resting" ]; then
    "$INC" open ai.fallback warn "AI is running on a fallback model" \
           "First choice is resting (${AIFIRST#* }). Work continues on the next model in the chain and may be slower." || true
  else
    "$INC" clear ai.fallback || true
  fi
fi

NOW="OK"; [ ${#P[@]} -gt 0 ] && NOW="FAIL"
WAS=$(cat "$STATE" 2>/dev/null || echo "OK")
echo "$NOW" > "$STATE"
TS="[$(date -uIs)]"

# Put the finding somewhere the product can show it, not only in this log.
INC=/opt/techdeva-platform/techdeva-platform/scripts/incident.sh
if [ -x "$INC" ]; then
  if [ "$NOW" = "FAIL" ]; then
    "$INC" open platform.monitor critical "Platform check failing" \
           "$(printf '%s; ' "${P[@]}")" || true
  else
    "$INC" clear platform.monitor || true
  fi
fi

if [ "$NOW" = "FAIL" ]; then
  MSG="TechDeva ALERT: $(printf '%s; ' "${P[@]}")"
  echo "$TS $MSG"
  [ "$WAS" = "OK" ] && [ -x "$ALERT" ] && "$ALERT" "$MSG"
else
  echo "$TS all checks passed"
  [ "$WAS" = "FAIL" ] && [ -x "$ALERT" ] && "$ALERT" "TechDeva RECOVERED: all checks passing"
fi
