#!/bin/sh
# Tâches planifiées sans dépendance supplémentaire : génération des alertes toutes les heures
# (idempotent grâce à la clé de déduplication) et purge technique quotidienne.
set -u
last_purge=""
while true; do
  flask notifications run || echo "Échec de la génération des notifications" >&2
  today=$(date +%F)
  if [ "$today" != "$last_purge" ]; then
    flask rgpd purger || echo "Échec de la purge" >&2
    last_purge="$today"
  fi
  sleep "${SCHEDULER_INTERVAL_SECONDS:-3600}"
done
